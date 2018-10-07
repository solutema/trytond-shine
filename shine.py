import sql
import formulas
import unidecode
from collections import OrderedDict
from decimal import Decimal
from datetime import datetime, date, time
from dateutil import relativedelta
from trytond import backend
from trytond.model import (Workflow, ModelSQL, ModelView, fields,
    sequence_ordered, Unique)
from trytond.pyson import PYSONEncoder, PYSONDecoder, PYSON, Eval, Bool
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.tools import cursor_dict
from trytond.config import config
from .tag import TaggedMixin

__all__ = ['Sheet', 'DataSet', 'Formula', 'View', 'ViewTableFormula', 'Table',
    'TableField', 'TableView']

RECORD_CACHE_SIZE = config.get('cache', 'record')

FIELD_TYPES = [
    # (Internal selection name, Tryton field name, String, fields.Class, DB
    # TYPE, python conversion method, SQL cast when reading from db)
    ('char', 'char', 'Text (single-line)', 'fields.Char', 'VARCHAR', str, None),
    ('multiline', 'text', 'Text (multi-line)', 'fields.Text', 'VARCHAR',
        str, None),
    ('integer', 'integer', 'Integer', 'fields.Integer', 'INTEGER', int, None),
    ('float', 'float', 'Float', 'fields.Float', 'FLOAT', float, None),
    ('numeric', 'numeric', 'Numeric', 'fields.Numeric', 'NUMERIC', Decimal,
        None),
    ('boolean', 'boolean', 'Boolean', 'fields.Boolean', 'BOOLEAN', bool, None),
    ('many2one', 'many2one', 'Many To One', 'fields.Many2One', 'INTEGER', int,
        None),
    ('date', 'date', 'Date', 'fields.Date', 'DATE', date, None),
    ('datetime', 'datetime', 'Date Time', 'fields.DateTime', 'DATETIME',
        datetime, None),
    ('time', 'time', 'Time', 'fields.Time', 'TIME', time, None),
    ('timestamp', 'timestamp', 'Timestamp', 'fields.Timestamp', 'TIMESTAMP',
        datetime, None),
    ('timedelta', 'timedelta', 'Time Interval', 'fields.TimeDelta', 'INTERVAL',
        relativedelta, None),
    ('icon', 'char', 'Icon', 'fields.Char', 'VARCHAR', str, None),
    ('image', 'binary', 'Image', 'fields.Binary', 'BLOB', bytes, bytearray),
    ('binary', 'binary', 'File', 'fields.Binary', 'BLOB', bytes, bytearray),
    ('reference', 'reference', 'Reference', 'fields.Reference', 'VARCHAR', str,
        None),
    ]

FIELD_TYPE_SELECTION = [(x[0], x[2]) for x in FIELD_TYPES]
FIELD_TYPE_SQL = dict([(x[0], x[4]) for x in FIELD_TYPES])
FIELD_TYPE_CLASS = dict([(x[0], x[3]) for x in FIELD_TYPES])
FIELD_TYPE_PYTHON = dict([(x[0], x[5]) for x in FIELD_TYPES])
FIELD_TYPE_TRYTON = dict([(x[0], x[1]) for x in FIELD_TYPES])
FIELD_TYPE_CAST = dict([(x[0], x[6]) for x in FIELD_TYPES])

VALID_FIRST_SYMBOLS = 'abcdefghijklmnopqrstuvwxyz'
VALID_NEXT_SYMBOLS = '_0123456789'
VALID_SYMBOLS = VALID_FIRST_SYMBOLS + VALID_NEXT_SYMBOLS

def convert_to_symbol(text):
    if not text:
        return 'x'
    text = unidecode.unidecode(text)
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

SHEET_STATES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('canceled', 'Canceled'),
    ]

class Sheet(TaggedMixin, Workflow, ModelSQL, ModelView):
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
    state = fields.Selection(SHEET_STATES, 'State', readonly=True,
        required=True)
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
    tags = fields.Many2Many('shine.sheet.tag', 'sheet', 'tag', 'Tags', domain=[
            ('view', '=', False),
            ])
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
        cls._error_messages.update({
                'consecutive_icons': ('There cannot be two consecutive '
                    'fields of Icon type in sheet "%s".'),
                'last_icon': ('The last formula cannot be of type Icon in '
                    'sheet "%s".'),
                'invalid_formula': ('Invalid formula "%(formula)s" in Sheet '
                    '"%(sheet)s".'),
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
        return [('tags.name',) + tuple(clause[1:])]

    @classmethod
    @ModelView.button
    @Workflow.transition('active')
    def activate(cls, sheets):
        pool = Pool()
        Table = pool.get('shine.table')
        Field = pool.get('shine.table.field')

        for sheet in sheets:
            sheet.check_formulas()
            sheet.check_icons()

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
                        related_model=formula.related_model,
                        ))
            table.fields = fields
            table.create_table()
            table.save()

            if (not sheet.dataset
                    and sheet.current_table
                    and sheet.current_table.count()):
                table.copy_from(sheet.current_table)

            sheet.current_table = table
        cls.save(sheets)

    def check_formulas(self):
        for formula in self.formulas:
            icon = formula.expression_icon
            if icon and icon != 'green':
                self.raise_user_error('invalid_formula', {
                        'sheet': self.rec_name,
                        'formula': formula.rec_name,
                        })

    def check_icons(self):
        was_icon = False
        for formula in self.formulas:
            if formula.type == 'icon':
                if was_icon:
                    self.raise_user_error('consecutive_icons',
                        self.rec_name)
                was_icon = True
            else:
                was_icon = False
        if was_icon:
            self.raise_user_error('last_icon', self.rec_name)

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
        pool = Pool()
        Formula = pool.get('shine.formula')
        Model = pool.get('ir.model')
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
                if field.get('related_model'):
                    related_model, = Model.search([
                            ('model', '=', field['related_model']),
                            ])
                    formula.related_model = related_model
                formula.store = True
                formulas.append(formula)
        if formulas:
            Formula.save(formulas)

    @property
    def data_table_name(self):
        return ('shine.sheet.%d.%d' % (self.id or 0,
                self.revision)).replace('.', '_')

    def compute_sheet(self):
        cursor = Transaction().connection.cursor()

        table = sql.Table(self.data_table_name)
        cursor.execute(*table.delete())

        #fields = dict([(x.alias, x) for x in self.formulas])
        direct_fields = [x.alias for x in self.formulas if not
            x.expression]
        formula_fields = [x.alias for x in self.formulas if x.expression]
        sql_fields = [sql.Column(table, x) for x in direct_fields + formula_fields]

        parser = formulas.Parser()
        formula_fields = [(x, parser.ast(x.expression)[1].compile() if
                x.expression.startswith('=') else '') for x in self.formulas if
            x.expression]

        insert_values = []
        checker = TimeoutChecker(self.timeout, self.timeout_exception)
        if not formula_fields:
            # If there are no formula_fields we can make the loop faster as
            # we don't use OrderedDict and don't evaluate formulas
            for records in self.dataset.get_data():
                checker.check()
                for record in records:
                    insert_values.append([getattr(record, x) for x in
                            direct_fields])
        else:
            for records in self.dataset.get_data():
                checker.check()
                for record in records:
                    values = OrderedDict()
                    if direct_fields:
                        values.update(OrderedDict([(x, getattr(record, x)) for
                                    x in direct_fields]))
                    if formula_fields:
                        for field, ast in formula_fields:
                            if field.expression.startswith('='):
                                inputs = []
                                for input_ in ast.inputs.keys():
                                    # TODO: Check if input_ exists and raise proper
                                    # user error Indeed, we should check de
                                    # formulas when we move to active state
                                    inputs.append(values[input_.lower()])
                                value = ast(inputs)[0]
                            else:
                                value = field.expression
                            ftype = FIELD_TYPE_PYTHON[field.type]
                            values[field] = ftype(value)
                    insert_values.append(list(values.values()))
        if insert_values:
            cursor.execute(*table.insert(sql_fields, insert_values))

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
            if not formula.type:
                continue
            code.append('    %s = fields.%s("%s")' % (formula.alias,
                FIELD_TYPE_CLASS[formula.type], formula.name))
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
    model_name = fields.Function(fields.Char('Model Name'),
        'on_change_with_model_name')
    model_view_search = fields.Many2One('ir.ui.view_search', 'Search', domain=[
            ('model', '=', Eval('model_name')),
            ], states={
            'invisible': Eval('source') != 'model',
            }, depends=['model_name', 'source'])
    model_domain = fields.Char('Domain', states={
            'invisible': Eval('source') != 'model',
            }, depends=['source'])
    model_context = fields.Char('Context', states={
            'invisible': Eval('source') != 'model',
            }, depends=['source'])
    model_order = fields.Char('Order', states={
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

    @fields.depends('model')
    def on_change_with_model_name(self, name=None):
        return self.model.model if self.model else None

    @fields.depends('model_view_search')
    def on_change_model_view_search(self):
        if self.model_view_search:
            self.model_domain = self.model_view_search.domain

    @classmethod
    def __setup__(cls):
        super(DataSet, cls).__setup__()
        cls._error_messages.update({
                'invalid_domain': ('Invalid domain "%(domain)s" on Data Set '
                    '"%(dataset)s".'),
                'invalid_context': ('Invalid context "%(context)s" on Data Set '
                    '"%(dataset)s".'),
                })

    @classmethod
    def validate(cls, datasets):
        for dataset in datasets:
            dataset.check_domain()
            dataset.check_context()

    def check_domain(self):
        if not self.model_domain:
            return
        try:
            value = PYSONDecoder().decode(self.model_domain)
        except Exception:
            self.raise_user_error('invalid_domain', {
                    'domain': self.model_domain,
                    'dataset': self.rec_name,
                    })
        if isinstance(value, PYSON):
            if not value.types() == set([list]):
                self.raise_user_error('invalid_domain', {
                        'domain': self.model_domain,
                        'dataset': self.rec_name,
                        })
        elif not isinstance(value, list):
            self.raise_user_error('invalid_domain', {
                    'domain': self.model_domain,
                    'dataset': self.rec_name,
                    })
        else:
            try:
                fields.domain_validate(value)
            except Exception:
                self.raise_user_error('invalid_domain', {
                        'domain': self.model_domain,
                        'dataset': self.rec_name,
                        })

    def check_context(self):
        if not self.model_context:
            return
        try:
            value = PYSONDecoder().decode(self.model_context)
        except Exception:
            self.raise_user_error('invalid_context', {
                    'context': self.model_context,
                    'dataset': self.rec_name,
                    })
        if isinstance(value, PYSON):
            if not value.types() == set([dict]):
                self.raise_user_error('invalid_context', {
                        'context': self.model_context,
                        'dataset': self.rec_name,
                        })
        elif not isinstance(value, dict):
            self.raise_user_error('invalid_context', {
                    'context': self.model_context,
                    'dataset': self.rec_name,
                    })
        else:
            try:
                fields.context_validate(value)
            except Exception:
                self.raise_user_error('invalid_context', {
                        'context': self.model_context,
                        'dataset': self.rec_name,
                        })

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
                        'related_model': field.relation,
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
        domain = []
        if self.model_domain:
            domain = PYSONDecoder().decode(self.model_domain)
        context = {}
        if self.model_context:
            context = PYSONDecoder().decode(self.model_context)
        order = [('id', 'ASC')]
        if self.model_order:
            order = PYSONDecoder().decode(self.model_order)
        limit = RECORD_CACHE_SIZE
        offset = 0
        with Transaction().set_context(context):
            while True:
                records = Model.search(domain, offset=offset, limit=limit,
                    order=order)
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
        ondelete='CASCADE', states={
            'readonly': (Eval('sheet_state') != 'draft') & Eval('sheet'),
            }, depends=['sheet_state'])
    name = fields.Char('Name', required=True, states={
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    alias = fields.Char('Alias', required=True, states={
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    field_name = fields.Function(fields.Char('Field Name'), 'get_field_name')
    expression = fields.Char('Formula', states={
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    expression_icon = fields.Function(fields.Char('Expression Icon'),
        'on_change_with_expression_icon')
    current_value = fields.Function(fields.Char('Value'),
        'on_change_with_current_value')
    type = fields.Selection([(None, '')] + FIELD_TYPE_SELECTION, 'Field Type',
        states={
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    store = fields.Boolean('Store', states={
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    related_model = fields.Many2One('ir.model', 'Related Model', states={
            'required': Eval('type') == 'many2one',
            'invisible': Eval('type') != 'many2one',
            'readonly': Eval('sheet_state') != 'draft',
            }, depends=['sheet_state'])
    sheet_state = fields.Function(fields.Selection(SHEET_STATES, 'Sheet State'),
        'on_change_with_sheet_state')

    @staticmethod
    def default_store():
        return True

    @classmethod
    def __setup__(cls):
        super(Formula, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('sheet_alias_uniq', Unique(t, t.sheet, sql.Column(t, 'alias')),
                'There cannot be two formulas with the same alias in a sheet.')
            ]
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

    @fields.depends('sheet', '_parent_sheet.state')
    def on_change_with_sheet_state(self, name=None):
        if self.sheet:
            return self.sheet.state

    def formula_error(self):
        if not self.expression:
            return
        if not self.expression.startswith('='):
            return
        parser = formulas.Parser()
        try:
            ast = parser.ast(self.expression)[1].compile()
            missing = (set([x.lower() for x in ast.inputs]) -
                self.previous_formulas())
            if not missing:
                return
            return ('warning', 'Referenced alias "%s" not found.' %
                ', '.join(missing))
        except formulas.errors.FormulaError as error:
            msg = error.msg.replace('\n', ' ')
            if error.args[1:]:
                msg = msg % error.args[1:]
            return ('error', msg)

    def previous_formulas(self):
        res = []
        for formula in self.sheet.formulas:
            if formula == self:
                break
            res.append(formula.alias)
        return set(res)

    @fields.depends('expression')
    def on_change_with_expression_icon(self, name=None):
        if not self.expression:
            return ''
        if not self.expression.startswith('='):
            return ''
        error = self.formula_error()
        if not error:
            return 'green'
        if error[0] == 'warning':
            return 'orange'
        return 'red'

    @fields.depends('expression', '_parent_sheet.values')
    def on_change_with_current_value(self, name=None):
        res = self.formula_error()
        if not res:
            return
        return res[1]

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

    @fields.depends('type', 'chart_type')
    def on_change_type(self):
        if self.type == 'chart' and not self.chart_type:
            self.chart_type = 'vbar'

    @fields.depends('chart_type', 'chart_interpolation')
    def on_change_chart_type(self):
        if self.chart_type == 'line' and not self.chart_interpolation:
            self.chart_interpolation = 'linear'

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

        attributes = ''
        if self.chart_interpolation:
            attributes = 'interpolation="%s"' % self.chart_interpolation
        y = '<field name="%s" %s/>\n' % (self.chart_value.alias, attributes)

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
                '': self.chart_interpolation,
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
    fields = fields.One2Many('shine.table.field', 'table',
        'Fields')

    @classmethod
    def __setup__(cls):
        super(Table, cls).__setup__()
        cls._error_messages.update({
                'copy_from_warning': ('Data from the The following fields will '
                    'be lost because they no longer exist or their type has '
                    'changed:\n\n'
                    '%(fields)s\n\n'
                    'Are you sure you want to copy data from '
                    '"%(from_table)s" to "%(table)s"?')
                })

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

    def drop_table(self):
        transaction = Transaction()
        TableHandler = backend.get('TableHandler')
        TableHandler.drop_table('', self.name, cascade=True)
        transaction.database.sequence_delete(transaction.connection,
            self.name + '_id_seq')

    def copy_from(self, from_table):
        fields = {x.name for x in self.fields}
        from_fields = {x.name for x in from_table.fields}
        missing = sorted(list(from_fields - fields))
        print('Fields: ', fields)
        print('From Fields: ', from_fields)
        print('Missing: ', missing)

        existing = fields & from_fields
        fields = {}
        for field in self.fields:
            if field.name in existing:
                fields[field.name] = field.type

        different_types = []
        for field in from_table.fields:
            if field.name in existing:
                if (FIELD_TYPE_TRYTON[field.type] !=
                        FIELD_TYPE_TRYTON[fields[field.name]]):
                    different_types.append("%s (%s -> %s)" % (field.name,
                            field.type, fields[field.name]))
                    existing.remove(field.name)

        if missing or different_types:
            message = ['- %s' % x for x in (missing + different_types)]
            self.raise_user_warning('shine_copy_from_warning.%s.%s' %
                (self.name, from_table.id), 'copy_from_warning', {
                'fields': '\n'.join(message),
                'from_table': from_table.rec_name,
                'table': self.rec_name,
                })

        existing = sorted(list(existing))
        table = sql.Table(from_table.name)
        subquery = table.select()
        subquery.columns = [sql.Column(table, x) for x in existing]
        table = sql.Table(self.name)
        query = table.insert([sql.Column(table, x) for x in existing], subquery)

        cursor = Transaction().connection.cursor()
        cursor.execute(*query)

    def count(self):
        table = sql.Table(self.name)
        cursor = Transaction().connection.cursor()
        query = table.select(sql.aggregate.Count(1))
        cursor.execute(*query)
        return cursor.fetchone()[0]

    @classmethod
    def remove_old_tables(cls, days=0):
        Sheet = Pool().get('shine.sheet')
        current_table_ids = [x.current_table.id for x in Sheet.search([
                    ('current_table', '!=', None)])]
        tables = cls.search([
                ('create_date', '<', datetime.now() -
                    relativedelta.relativedelta(days=days)),
                ('id', 'not in', current_table_ids),
                ])
        cls.delete(tables)

    @classmethod
    def delete(cls, tables):
        for table in tables:
            table.drop_table()
        super(Table, cls).delete(tables)


class TableField(ModelSQL, ModelView):
    'Shine Table Field'
    __name__ = 'shine.table.field'
    table = fields.Many2One('shine.table', 'Table', required=True,
        ondelete='CASCADE')
    name = fields.Char('Name', required=True)
    string = fields.Char('String', required=True)
    type = fields.Selection([(None, '')] + FIELD_TYPE_SELECTION, 'Field Type',
        required=False)
    related_model = fields.Many2One('ir.model', 'Related Model')


class TableView(ModelSQL, ModelView):
    'Shine Table View'
    __name__ = 'shine.table.view'
    arch = fields.Text('Arch')
