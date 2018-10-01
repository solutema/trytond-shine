import sql
from decimal import Decimal
from datetime import datetime
from trytond import backend
from trytond.model import (Workflow, ModelSQL, ModelView, fields,
    sequence_ordered)
from trytond.pyson import Eval, Bool
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.tools import cursor_dict
from trytond.pyson import PYSONEncoder
from .function import evaluate

__all__ = ['Sheet', 'DataSet', 'Formula', 'View', 'ViewTableFormula', 'Data',
    'Table', 'TableField', 'TableView']

FIELD_TYPES = [
    ('char', 'Char', 'fields.Char', 'VARCHAR'),
    ('integer', 'Integer', 'fields.Integer', 'INTEGER'),
    ('float', 'Float', 'fields.Float', 'FLOAT'),
    ('numeric', 'Numeric', 'fields.Numeric', 'NUMERIC'),
    ('boolean', 'Boolean', 'fields.Boolean', 'BOOLEAN'),
    ('many2one', 'Many To One', 'fields.Many2One', 'INTEGER'),
    ('date', 'Date', 'fields.Date', 'DATE'),
    ('datetime', 'Date Time', 'fields.DateTime', 'DATETIME'),
    ('time', 'Time', 'fields.Time', 'TIME'),
    ('timestamp', 'Timestamp', 'fields.Timestamp', 'TIMESTAMP'),
    ('timedelta', 'Time Interval', 'fields.TimeDelta', 'INTERVAL'),
    ]

FIELD_TYPE_SELECTION = [(x[0], x[1]) for x in FIELD_TYPES]
FIELD_TYPE_SQL = dict([(x[0], x[3]) for x in FIELD_TYPES])
FIELD_TYPE_TRYTON = dict([(x[0], x[2]) for x in FIELD_TYPES])

VALID_FIRST_SYMBOLS = 'abcdefghijklmnopqrstuvwxyz'
VALID_NEXT_SYMBOLS = '_0123456789'
VALID_SYMBOLS = VALID_FIRST_SYMBOLS + VALID_NEXT_SYMBOLS

def convert_to_symbol(text):
    if not text:
        return 'x'
    text = text.lower()
    first = text[0]
    symbol = first
    if first not in VALID_FIRST_SYMBOLS:
        symbol = '_'
        if symbol in VALID_SYMBOLS:
            symbol += first
    for x in text[1:]:
        if not x in VALID_SYMBOLS and symbol[-1] != '_':
            symbol += '_'
        else:
            symbol += x
    return symbol


class Record(dict):
    def __init__(self, dictionary):
        self.dictionary = dictionary

    def __getattr__(self, name):
        return self.dictionary[name]


def cursor_object(cursor, size=None):
    size = cursor.arraysize if size is None else size
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        for row in rows:
            yield Record({d[0]: v for d, v in zip(cursor.description, row)})


class ModelEmulation:
    __doc__ = None
    _table = None
    __name__ = None


class TimeoutException(Exception):
    pass


class TimeoutChecker:
    def __init__(self, timeout, callback):
        self._timeout = timeout
        self._callback = callback
        self._start = datetime.now()

    def check(self):
        elapsed = (datetime.now() - self._start).seconds
        if elapsed > self._timeout:
            self._callback()


class Sheet(Workflow, ModelSQL, ModelView):
    'Shine Sheet'
    __name__ = 'shine.sheet'
    name = fields.Char('Name', required=True)
    revision = fields.Integer('Revision', required=True, readonly=True)
    alias = fields.Char('Alias')
    type = fields.Selection([
            ('sheet', 'Sheet'),
            ('singleton', 'Singleton'),
            ], 'Type', states={
            'readonly': Eval('state') != 'draft',
            }, required=True)
    state = fields.Selection([
            ('draft', 'Draft'),
            ('active', 'Active'),
            ('canceled', 'Canceled'),
            ], 'State', readonly=True, required=True)
    formulas = fields.One2Many('shine.formula', 'sheet', 'Formulas', states={
            'readonly': Eval('state') != 'draft',
            }, depends=['state'])
    dataset = fields.Many2One('shine.dataset', 'Data Set', states={
            'readonly': Eval('state') != 'draft',
            }, depends=['state'])
    timeout = fields.Integer('Timeout (s)', required=True, states={
            'invisible': ~Bool(Eval('dataset')),
            }, help='Maximum amount of time allowed for computing sheet data.')
    views = fields.One2Many('shine.view', 'sheet', 'Views')
    tags = fields.Many2Many('shine.sheet.tag', 'sheet', 'tag', 'Tags')
    tags_char = fields.Function(fields.Char('Tags'), 'on_change_with_tags_char',
        searcher='search_tags_char')
    current_table = fields.Many2One('shine.table', 'Table')
    python_code = fields.Function(fields.Text('Python Code'), 'get_python_code')

    @staticmethod
    def default_quick_edition():
        return True

    @staticmethod
    def default_type():
        return 'sheet'

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_revision():
        return 1

    @staticmethod
    def default_timeout():
        Config = Pool().get('shine.configuration')
        return Config(0).default_timeout

    @classmethod
    def __setup__(cls):
        super(Sheet, cls).__setup__()
        cls._transitions |= set((
                ('draft', 'active'),
                ('draft', 'canceled'),
                ('active', 'draft'),
                ('active', 'canceled'),
                ('canceled', 'draft'),
                ))
        cls._buttons.update({
                'activate': {
                    'icon': 'tryton-ok',
                    'invisible': Eval('state') != 'draft',
                    },
                'draft': {
                    'icon': 'tryton-undo',
                    'invisible': Eval('state') != 'active',
                    },
                'open': {
                    'icon': 'tryton-forward',
                    'invisible': Eval('state') != 'active',
                    'depends': ['current_table']
                    },
                'compute': {
                    'icon': 'tryton-refresh',
                    'invisible': ((Eval('state') != 'active') |
                        ~Bool(Eval('dataset'))),
                    },
                'update_formulas': {
                    'icon': 'tryton-refresh',
                    'invisible': ((Eval('state') != 'draft') |
                        ~Bool(Eval('dataset'))),
                    },
                })

    @fields.depends('name', 'alias')
    def on_change_name(self):
        if self.alias:
            return
        self.alias = convert_to_symbol(self.name)

    @fields.depends('tags')
    def on_change_with_tags_char(self, name=None):
        return ', '.join(sorted([x.name for x in self.tags]))

    @classmethod
    def search_tags_char(cls, name, clause):
        # TODO: Implement
        return []

    @classmethod
    @ModelView.button
    @Workflow.transition('active')
    def activate(cls, sheets):
        pool = Pool()
        Table = pool.get('shine.table')
        Field = pool.get('shine.table.field')

        for sheet in sheets:
            sheet.revision += 1
            table = Table()
            table.name = sheet.data_table_name
            fields = []
            for formula in sheet.formulas:
                if not formula.type:
                    continue
                if not formula.store:
                    continue
                fields.append(Field(
                        name=formula.alias,
                        string=formula.name,
                        type=formula.type,
                        ))
            table.fields = fields
            table.create_table()
            table.save()
            sheet.current_table = table
        cls.save(sheets)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, sheets):
        pass

    @classmethod
    @ModelView.button_action('shine.act_open_sheet_form')
    def open(cls, sheets):
        pass

    @classmethod
    @ModelView.button
    def compute(cls, sheets):
        for sheet in sheets:
            sheet.compute_sheet()

    @classmethod
    @ModelView.button
    def update_formulas(cls, sheets):
        Formula = Pool().get('shine.formula')
        formulas = []
        for sheet in sheets:
            if not sheet.dataset:
                return
            current_formulas = [x.alias for x in sheet.formulas]
            for field in sheet.dataset.get_fields():
                if field['alias'] in current_formulas:
                    continue
                formula = Formula()
                formula.sheet = sheet
                formula.name = field['name']
                formula.alias = field['alias']
                formula.type = field['type']
                formula.store = True
                formulas.append(formula)
        if formulas:
            Formula.save(formulas)

    @property
    def data_table_name(self):
        return ('shine.sheet.%d.%d' % (self.id or 0,
                self.revision)).replace('.', '_')

    def compute_sheet(self):
        Function = Pool().get('shine.function')
        cursor = Transaction().connection.cursor()

        table = sql.Table(self.data_table_name)
        cursor.execute(*table.delete())

        eval_context = Function.eval_context()

        #fields = [x.field_name for x in self.formulas]
        direct_fields = [x.alias for x in self.formulas if not
            x.expression]
        formula_fields = [x.alias for x in self.formulas if x.expression]
        fields = [sql.Column(table, x) for x in direct_fields + formula_fields]

        insert_values = []
        checker = TimeoutChecker(self.timeout, self.timeout_exception)
        for records in self.dataset.get_data():
            checker.check()
            for record in records:
                values = []
                if direct_fields:
                    values += [getattr(record, x) for x in direct_fields]
                    #values += [record.get(x) for x in direct_fields]
                if formula_fields:
                    code = self.formulas_code(record)
                    values += evaluate(code, eval_context,
                        return_var='stored_formula_values')
                insert_values.append(values)
            cursor.execute(*table.insert(fields, insert_values))

    def formulas_code(self, record=None):
        if not record:
            record = {}
        code = ''
        stored = []
        for formula in self.formulas:
            if not record and not formula.expression:
                continue
            if formula.expression:
                expr = '%s = %s' % (formula.alias, formula.expression)
            else:
                value = record.get(formula.alias)
                if isinstance(value, str):
                    value = value.replace('"', 'x').replace('\n', '')
                expr = '%s = "%s"' % (formula.alias, value)
            code += expr + '\n'
            if formula.expression and formula.store:
                stored.append(formula.alias)
        if stored:
            code += 'stored_formula_values = (%s,)\n' % ', '.join(stored)
        return code

    def get_python_code(self, name):
        models = []
        if self.type == 'singleton':
            models.append('ModelSingleton')
        models += ['ModelSQL', 'ModelView']

        class_name = ''.join([x.capitalize() for x in self.alias.split('_')])
        code = []
        code.append('class %s(%s):' % (class_name, ', '.join(models)))
        code.append('    "%s"' % self.name)
        code.append('    __name__ = "%s"' % self.alias.replace('_', '.'))
        for formula in self.formulas:
            code.append('    %s = fields.%s("%s")' % (formula.alias,
                FIELD_TYPE_TRYTON[formula.type], formula.name))
        return '\n'.join(code)

    def timeout_exception(self):
        raise TimeoutException


class DataSet(ModelSQL, ModelView):
    'Shine Data Set'
    __name__ = 'shine.dataset'
    name = fields.Char('Name', required=True)
    type = fields.Selection([
            ('sheet', 'Sheet'),
            ('singleton', 'Singleton'),
            ], 'Type', required=True)
    source = fields.Selection([
            ('model', 'Model'),
            ('sheet', 'Sheet'),
            ('sql', 'SQL'),
            ], 'Source', required=True)
    model = fields.Many2One('ir.model', 'Model', states={
            'invisible': Eval('source') != 'model',
            }, depends=['source'])
    sheet = fields.Many2One('shine.sheet', 'Sheet', domain=[
            ('type', '=', Eval('type')),
            ], states={
            'invisible': Eval('source') != 'sheet',
            })
    query = fields.Text('SQL Query', states={
            'invisible': Eval('source') != 'sql',
            })

    @staticmethod
    def default_type():
        return 'sheet'

    @staticmethod
    def default_source():
        return 'model'


    def get_fields_model(self):
        res = []
        if self.source == 'model':
            for field in self.model.fields:
                if not field.ttype in FIELD_TYPE_SQL:
                    continue
                res.append({
                        'name': field.field_description,
                        'alias': field.name,
                        'type': field.ttype,
                        })
        return res

    def get_fields_sheet(self):
        res = []
        for formula in self.sheet.formulas:
            res.append({
                    'name': formula.name,
                    'alias': formula.alias,
                    'type': formula.type,
                    })
        return res

    def get_fields_sql(self):
        def value_to_type(value):
            TYPES = {
                None: 'char',
                int: 'integer',
                str: 'char',
                float: 'float',
                bool: 'boolean',
                Decimal: 'numeric',
                datetime: 'datetime',
                }
            return TYPES.get(type(value), 'char')

        res = []
        cursor = Transaction().connection.cursor()
        cursor.execute(self.query)
        fetchall = list(cursor_dict(cursor, size=1))
        for record in fetchall:
            res = [{
                    'name': key,
                    'alias': key,
                    'type': value_to_type(value),
                    } for key, value in record.items()]
            break
        return res

    def get_fields(self):
        return getattr(self, 'get_fields_%s' % self.source)()

    def get_data_model(self):
        pool = Pool()
        Model = pool.get(self.model.model)
        limit = 2000
        offset = 0
        while True:
            records = Model.search([], offset=offset, limit=limit,
                order=[('id', 'ASC')])
            if records:
                yield records
            if len(records) < limit:
                break

    def get_data_sheet(self):
        query = 'SELECT * FROM "%s"' % self.sheet.data_table_name
        yield self.get_data_cursor(query)

    def get_data_sql(self):
        yield self.get_data_cursor(self.query)

    def get_data_cursor(self, query):
        cursor = Transaction().connection.cursor()
        cursor.execute(query)
        return cursor_object(cursor)

    def get_data(self):
        return getattr(self, 'get_data_%s' % self.source)()


class Formula(sequence_ordered(), ModelSQL, ModelView):
    'Shine Formula'
    __name__ = 'shine.formula'
    sheet = fields.Many2One('shine.sheet', 'Sheet', required=True,
        ondelete='CASCADE')
    name = fields.Char('Name', required=True)
    alias = fields.Char('Alias', required=True)
    field_name = fields.Function(fields.Char('Field Name'), 'get_field_name')
    expression = fields.Char('Expression')
    current_value = fields.Function(fields.Char('Value'),
        'on_change_with_current_value')
    type = fields.Selection([(None, '')] + FIELD_TYPE_SELECTION, 'Field Type',
        required=False)
    store = fields.Boolean('Store')

    @staticmethod
    def default_store():
        return True

    @classmethod
    def __setup__(cls):
        super(Formula, cls).__setup__()
        cls._error_messages.update({
                'invalid_alias': ('Invalid symbol "%(symbol)s" in formula '
                    '"%(name)s".'),
                'invalid_store': ('Formula "%s" cannot be stored because type '
                    'is not set.'),
                })

    @classmethod
    def validate(cls, formulas):
        for formula in formulas:
            formula.check_alias()
            formula.check_store()

    def check_alias(self):
        for symbol in self.alias:
            if not symbol in VALID_SYMBOLS:
                self.raise_user_error('invalid_alias', {
                        'symbol': symbol,
                        'name': self.name,
                        })

    def check_store(self):
        if not self.type and self.store:
            self.raise_user_error('invalid_store', self.rec_name)

    @fields.depends('type')
    def on_change_with_store(self):
        return True if self.type else False

    @fields.depends('expression', '_parent_sheet.values')
    def on_change_with_current_value(self, name=None):
        return self.expression

    def get_field_name(self, name):
        return 'field_%d' % self.id

    @fields.depends('name', 'alias')
    def on_change_name(self):
        if self.alias:
            return
        self.alias = convert_to_symbol(self.name)


class View(ModelSQL, ModelView):
    'Shine View'
    __name__ = 'shine.view'
    name = fields.Char('Name', required=True)
    sheet = fields.Many2One('shine.sheet', 'Sheet', required=True,
        ondelete='CASCADE')
    current_table = fields.Function(fields.Many2One('shine.table',
            'Current Table'), 'get_current_table')
    type = fields.Selection([
            ('table', 'Table'),
            ('chart', 'Chart'),
            ('dynamic_table', 'Dynamic Table'),
            ('custom', 'Custom'),
            ], 'View Type', required=True, sort=False)
    action = fields.Many2One('ir.action.act_window', 'Action', readonly=True)
    arch = fields.Function(fields.Text('Architecture'), 'get_arch')
    chart_type = fields.Selection([
            (None, ''),
            ('vbar', 'Vertical Bars'),
            ('hbar', 'Horizontal Bars'),
            ('line', 'Line'),
            ('pie', 'Pie'),
            ], 'Chart Type', states={
            'required': Eval('type') == 'chart',
            'invisible': Eval('type') != 'chart',
            }, depends=['type'], sort=False)
    chart_interpolation = fields.Selection([
            (None, ''),
            ('linear', 'Linear'),
            ('constant-center', 'Constant Center'),
            ('constant-left', 'Constant Left'),
            ('constant-right', 'Constant Right'),
            ], 'Interpolation', states={
            'required': ((Eval('type') == 'chart') & (Eval('chart_type') ==
                    'line')),
            'invisible': ((Eval('type') != 'chart') | (Eval('chart_type') !=
                    'line')),
            }, depends=['type', 'chart_type'], sort=False)
    chart_legend = fields.Boolean('Show Legend', states={
            'invisible': Eval('type') != 'chart',
            })
    chart_group = fields.Many2One('shine.formula', 'Group', domain=[
            ('sheet', '=', Eval('sheet')),
            ], states={
            'required': Eval('type') == 'chart',
            'invisible': Eval('type') != 'chart',
            })
    chart_value = fields.Many2One('shine.formula', 'Value', domain=[
            ('sheet', '=', Eval('sheet')),
            ('type', 'in', ['integer', 'float', 'numeric']),
            ], states={
            'required': Eval('type') == 'chart',
            'invisible': Eval('type') != 'chart',
            })
    table_formulas = fields.One2Many('shine.view.table.formula', 'view',
        'Formulas', states={
            'invisible': Eval('type') != 'table',
            }, depends=['sheet', 'type'])
    custom_type = fields.Selection([
            (None, ''),
            ('tree', 'Tree'),
            ('form', 'Form'),
            ('calendar', 'Calendar'),
            ('chart', 'Chart'),
            ], 'Custom Type', states={
            'required': Eval('type') == 'custom',
            'invisible': Eval('type') != 'custom',
            }, depends=['type'], sort=False)
    custom_arch = fields.Text('Architecture', states={
            'required': Eval('type') == 'custom',
            'invisible': Eval('type') != 'custom',
            }, depends=['type'])
    custom_parent = fields.Many2One('shine.formula', 'Parent', domain=[
            ('type', '=', 'many2one'),
            ], states={
            'invisible': Eval('type') != 'custom',
            }, depends=['type'])

    @staticmethod
    def default_chart_legend():
        return True

    @classmethod
    def __setup__(cls):
        super(View, cls).__setup__()
        cls._buttons.update({
                'table_update_formulas': {
                    'icon': 'tryton-refresh',
                    'invisible': Eval('type') != 'table',
                    },
                'open': {
                    'icon': 'tryton-forward',
                    'depends': ['current_table'],
                    },
                })

    def get_current_table(self, name):
        return self.sheet.current_table.id if self.sheet.current_table else None

    @classmethod
    @ModelView.button
    def table_update_formulas(cls, views):
        ViewTableFormula = Pool().get('shine.view.table.formula')
        to_save = []
        for view in views:
            for formula in view.sheet.formulas:
                to_save.append(ViewTableFormula(view=view, formula=formula))
        ViewTableFormula.save(to_save)

    @classmethod
    @ModelView.button_action('shine.act_open_view_form')
    def open(cls, sheets):
        pass

    @classmethod
    def create(cls, vlist):
        res = super(View, cls).create(vlist)
        cls.update_actions(res)
        return res

    @classmethod
    def write(cls, *args):
        super(View, cls).write(*args)
        actions = iter(args)
        to_update = []
        for views, values in zip(actions, actions):
            to_update += views
        cls.update_actions(to_update)

    @classmethod
    def delete(cls, views):
        super(View, cls).delete(views)
        cls.delete_actions(views)

    @classmethod
    def update_actions(cls, views):
        ActWindow = Pool().get('ir.action.act_window')

        for view in views:
            action = view.action
            if not action:
                action = ActWindow()
            action.name = view.name
            action.res_model = 'shine.data'
            action.usage = 'dashboard'
            action.context = PYSONEncoder().encode({
                    'shine_view': view.id,
                    'shine_sheet': view.sheet.id,
                    'shine_table': view.sheet.current_table.id,
                    })
            action.save()
            if not view.action:
                # TODO: Saving the view will call update_actions() again
                view.action = action
                view.save()

    @classmethod
    def delete_actions(cls, elements):
        ActWindow = Pool().get('ir.action.act_window')
        to_delete = [x.action for x in elements if x.action]
        if to_delete:
            ActWindow.delete(to_delete)

    def get_view_info_table(self):
        # TODO: Duplicated from get_tree_view() but this one is not editable
        fields = []
        for line in self.table_formulas:
            formula = line.formula
            if formula.type in ('datetime', 'timestamp'):
                fields.append('<field name="%s" widget="date"/>\n' %
                    formula.alias)
                fields.append('<field name="%s" widget="time"/>\n' %
                    formula.alias)
                continue

            attributes = ''
            if formula.type in ('integer', 'float', 'numeric'):
                attributes = 'sum="Total %s"' % formula.name
            fields.append('<field name="%s" %s/>\n' % (formula.alias,
                    attributes))

        xml = ('<?xml version="1.0"?>\n'
            '<tree>\n'
            '%s'
            '</tree>') % '\n'.join(fields)
        return {
            'type': 'tree',
            'fields': fields,
            'arch': xml,
            }

    def get_view_info_chart(self):
        x = '<field name="%s"/>\n' % self.chart_group.alias
        y = '<field name="%s"/>\n' % self.chart_value.alias

        xml = ('<?xml version="1.0"?>\n'
            '<graph type="%(type)s" legend="%(legend)s">\n'
            '    <x>'
            '        %(x)s'
            '    </x>'
            '    <y>'
            '        %(y)s'
            '    </y>'
            '</graph>') % {
                'type': self.chart_type,
                'legend': self.chart_legend and '1' or '0',
                'x': x,
                'y': y,
                }
        return {
            'type': 'graph',
            'fields': [self.chart_group.alias, self.chart_value.alias],
            'arch': xml,
            }

    def get_view_info_dynamic_table(self):
        return {
            'type': 'tree',
            'children': 'children',
            }

    def get_view_info_custom(self):
        return {
            'type': self.custom_type,
            'arch': self.custom_arch,
            }

    def get_arch(self, name):
        return getattr(self, 'get_arch_%s' % self.type)()

    def get_view_info(self):
        return getattr(self, 'get_view_info_%s' % self.type)()


class ViewTableFormula(sequence_ordered(), ModelSQL, ModelView):
    'Shine View Table Formula'
    __name__ = 'shine.view.table.formula'

    view = fields.Many2One('shine.view', 'View', required=True,
        ondelete='CASCADE')
    formula = fields.Many2One('shine.formula', 'Formula', domain=[
            ('sheet', '=', Eval('_parent_view', {}).get('sheet')),
            ], required=True, ondelete='CASCADE')


class Table(ModelSQL, ModelView):
    'Shine Table'
    __name__ = 'shine.table'
    name = fields.Char('Name', required=True)
    fields = fields.One2Many('shine.table.field', 'model',
        'Fields')

    def create_table(self):
        TableHandler = backend.get('TableHandler')

        model = ModelEmulation()
        model.__doc__ = self.name
        model._table = self.name

        if TableHandler.table_exist(self.name):
            TableHandler.drop_table('', self.name)

        table = TableHandler(model)

        for name, field in (('create_uid', fields.Integer),
                ('write_uid', fields.Integer),
                ('create_date', fields.Timestamp),
                ('write_date', fields.Timestamp)):
            sql_type = field._sql_type
            table.add_column(name, sql_type)

        for field in self.fields:
            sql_type = FIELD_TYPE_SQL[field.type]
            table.add_column(field.name, sql_type)
        return table


class TableField(ModelSQL, ModelView):
    'Shine Table Field'
    __name__ = 'shine.table.field'
    model = fields.Many2One('shine.table', 'Table', required=True)
    name = fields.Char('Name', required=True)
    string = fields.Char('String', required=True)
    type = fields.Selection([(None, '')] + FIELD_TYPE_SELECTION, 'Field Type',
        required=False)


class TableView(ModelSQL, ModelView):
    'Shine Table View'
    __name__ = 'shine.table.view'
    arch = fields.Text('Arch')


class Data(ModelSQL, ModelView):
    'Shine Data'
    __name__ = 'shine.data'

    @classmethod
    def default_get(cls, fields_names, with_rec_name=True):
        return {}

    @classmethod
    def fields_get(cls, fields_names=None):
        res = super(Data, cls).fields_get(fields_names)
        table = cls.get_table()
        for field in table.fields:
            res[field.name] = {
                    'name': field.name,
                    'string': field.string,
                    'type': field.type,
                    }
        return res

    @classmethod
    def get_tree_view(cls, table, view):
        fields = []
        for field in table.fields:
            if field.type in ('datetime', 'timestamp'):
                fields.append('<field name="%s" widget="date"/>\n' %
                    field.name)
                fields.append('<field name="%s" widget="time"/>\n' %
                    field.name)
                continue

            attributes = ''
            if field.type in ('integer', 'float', 'numeric'):
                attributes = 'sum="Total %s"' % field.string
            fields.append('<field name="%s" %s/>\n' % (field.name,
                    attributes))

        xml = ('<?xml version="1.0"?>\n'
            '<tree editable="bottom">\n'
            '%s'
            '</tree>') % '\n'.join(fields)
        return fields, xml

    @classmethod
    def get_form_view(cls, table, view):
        fields = []
        for field in table.fields:
            fields.append('<label name="%s"/>' % field.name)
            if field.type in ('datetime', 'timestamp'):
                fields.append('<group col="2">'
                    '<field name="%s" widget="date"/>'
                    '<field name="%s" widget="time"/>'
                    '</group>' % (field.name, field.name))
                continue
            fields.append('<field name="%s"/>' % field.name)

        xml = ('<?xml version="1.0"?>\n'
            '<form>\n'
            '%s'
            '</form>') % '\n'.join(fields)
        return fields, xml

    @classmethod
    def get_from_view(cls, table, view):
        return

    @classmethod
    def fields_view_get(cls, view_id=None, view_type='form'):
        #sheet = cls.get_sheet()
        print('VIEW GET')
        table = cls.get_table()
        view = cls.get_view()

        #if view:
            #fields, xml = cls.get_from_view(table, view)

        #if sheet and sheet.type == 'singleton':
            #view_type = 'form'

        print('VIEW', view)
        if not view.id:
            if view_type == 'tree':
                fields, arch = cls.get_tree_view(table, view)
            elif view_type == 'form':
                fields, arch = cls.get_form_view(table, view)
            children = None
        else:
            info = view.get_view_info()
            view_type = info.get('type', view_type)
            arch = info.get('arch')
            children = info.get('children')
            fields = info.get('fields')
        res = {
            'type': view_type,
            'view_id': view_id,
            'field_childs': children,
            'arch': arch,
            'fields': cls.fields_get(fields),
            }
        return res

    @classmethod
    def search(cls, domain, offset=0, limit=None, order=None, count=False,
            query=False):
        table = cls.get_sql_table()

        cursor = Transaction().connection.cursor()
        # Get domain clauses
        tables, expression = cls.search_domain(domain)

        select = table.select(table.id, where=expression, limit=limit,
            offset=offset)
        if query:
            return select
        cursor.execute(*select)
        res=  [x[0] for x in cursor.fetchall()]
        return res

    @classmethod
    def read(cls, ids, fields_names=None):
        table = cls.get_sql_table()

        cursor = Transaction().connection.cursor()
        cursor.execute(*table.select())
        fetchall = list(cursor_dict(cursor))
        return fetchall

    @classmethod
    def create(cls, vlist):
        table = cls.get_sql_table()

        cursor = Transaction().connection.cursor()
        ids = []
        for record in vlist:
            fields = []
            values = []
            for key, value in record.items():
                fields.append(sql.Column(table, key))
                values.append(value)

            query = table.insert(fields, values=[values], returning=[table.id])
            cursor.execute(*query)
            ids.append(cursor.fetchone()[0])
        return ids

    @classmethod
    def write(cls, *args):
        table = cls.get_sql_table()
        cursor = Transaction().connection.cursor()

        actions = iter(args)
        for records, values in zip(actions, actions):
            fields = []
            to_update = []
            for key, value in values.items():
                fields.append(sql.Column(table, key))
                to_update.append(value)
            query = table.update(fields, to_update)
            cursor.execute(*query)

    @classmethod
    def delete(cls, records):
        table = cls.get_sql_table()
        cursor = Transaction().connection.cursor()
        ids = [x.id for x in records if x.id > 0]
        if ids:
            query = table.delete(where=table.id.in_(ids))
            cursor.execute(*query)

    @classmethod
    def get_sheet(cls):
        Sheet = Pool().get('shine.sheet')
        sheet_id = Transaction().context.get('shine_sheet') or 0
        if sheet_id:
            return Sheet(sheet_id)
        view = cls.get_view()
        if view:
            return view.sheet

    @classmethod
    def get_view(cls):
        View = Pool().get('shine.view')
        return View(Transaction().context.get('shine_view') or 0)

    @classmethod
    def get_table(cls):
        Table = Pool().get('shine.table')
        table = Transaction().context.get('shine_table')
        if not table:
            sheet = cls.get_sheet()
            if sheet:
                table = sheet.current_table
        if not table:
            view = cls.get_view()
            if view:
                table = view.current_table
        return Table(table)

    @classmethod
    def get_sql_table(cls):
        return sql.Table(cls.get_table().name)

    # TODO: copy()
