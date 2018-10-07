import sql
import formulas
from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.tools import cursor_dict
from trytond.pyson import PYSONEncoder
from .shine import FIELD_TYPE_TRYTON, FIELD_TYPE_CAST


class Adapter:
    def __getattr__(self, name):
        fields = self.get_fields()
        return getattr(fields, name)

    def __contains__(self, key):
        fields = self.get_fields()
        return fields.__contains__(key)

    def __iter__(self):
        fields = self.get_fields()
        return fields.__iter__()

    def __getitem__(self, name):
        fields = self.get_fields()
        return fields.__getitem__(name)

    def get_fields(self):
        # TODO: Cache
        Data = Pool().get('shine.data')
        table = Data.get_table()
        if not table:
            return Data._previous_fields
        res = {}
        for field in table.fields:
            if field.type == 'char':
                obj = fields.Char(field.string)
            elif field.type == 'multiline':
                obj = fields.Text(field.string)
            elif field.type == 'integer':
                obj = fields.Integer(field.string)
            elif field.type == 'float':
                obj = fields.Float(field.string)
            elif field.type == 'boolean':
                obj = fields.Boolean(field.string)
            elif field.type == 'numeric':
                obj = fields.Numeric(field.string)
            elif field.type == 'date':
                obj = fields.Date(field.string)
            elif field.type == 'datetime':
                obj = fields.DateTime(field.string)
            elif field.type == 'timestamp':
                obj = fields.Timestamp(field.string)
            elif field.type == 'many2one':
                obj = fields.Many2One(field.related_model.model, field.string)
            elif field.type in ('binary', 'icon'):
                obj = fields.Binary(field.string)
            obj.name = field.name
            res[field.name] = obj
        if not 'id' in res:
            obj = fields.Integer('ID')
            obj.name = 'id'
            res[field.name] = obj

        return res


class Data(ModelSQL, ModelView):
    'Shine Data'
    __name__ = 'shine.data'

    @classmethod
    def __post_setup__(cls):
        super(Data, cls).__post_setup__()
        cls._previous_fields = cls._fields
        cls._fields = Adapter()

    @classmethod
    def __table__(cls):
        # TODO: Check if we can drop create(), read(), write(), delete() &
        # search()
        return cls.get_sql_table()

    @classmethod
    def default_get(cls, fields_names, with_rec_name=True):
        return {}

    def on_change_with(self, fieldnames):
        table = self.get_table()
        res = {}
        for field in table.fields:
            if not field.name in fieldnames:
                continue
            ast = field.get_ast()
            inputs = field.inputs.split()
            inputs = [getattr(self, x) for x in inputs]
            value = ast(*inputs)
            if not isinstance(value, str):
                value = value.tolist()
            if isinstance(value, formulas.tokens.operand.XlError):
                value = None
            res[field.name] = value
        return res

    @classmethod
    def fields_get(cls, fields_names=None):
        Model = Pool().get('ir.model')
        res = super(Data, cls).fields_get(fields_names)
        table = cls.get_table()
        for field in table.fields:
            res[field.name] = {
                    'name': field.name,
                    'string': field.string,
                    'type': FIELD_TYPE_TRYTON[field.type],
                    'relation': (field.related_model.model if
                        field.related_model else None),
                    'readonly': bool(field.formula),
                    }
            if field.inputs:
                res[field.name]['on_change_with'] = field.inputs.split()
            if field.type == 'reference':
                selection = []
                for model in Model.search([]):
                    selection.append((model.model, model.name))
                res[field.name]['selection'] = selection
            if field.type == 'timestamp':
                res[field.name]['format'] = PYSONEncoder().encode('%H:%M:%S.%f')
        return res

    @classmethod
    def get_tree_view(cls, table, view):
        fields = []
        current_icon = None
        for field in table.fields:
            if field.type in ('datetime', 'timestamp'):
                fields.append('<field name="%s" widget="date"/>\n' %
                    field.name)
                fields.append('<field name="%s" widget="time"/>\n' %
                    field.name)
                continue
            if field.type == 'icon':
                current_icon = field.name
                continue


            attributes = []
            if field.type in ('integer', 'float', 'numeric'):
                attributes.append('sum="Total %s"' % field.string)
            if current_icon:
                attributes.append('icon="%s"' % current_icon)
                current_icon = None
            if field.type == 'image':
                attributes.append('widget="image"')

            fields.append('<field name="%s" %s/>\n' % (field.name,
                    ' '.join(attributes)))

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
            if field.type == 'icon':
                fields.append('<field name="%s" icon="%s"/>\n' % (field.name,
                        field.name))
                continue

            attributes = []
            if field.type == 'image':
                attributes.append('widget="image"')

            fields.append('<field name="%s" %s/>\n' % (field.name,
                    ' '.join(attributes)))

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
        table = cls.get_table()
        view = cls.get_view()

        #if view:
            #fields, xml = cls.get_from_view(table, view)

        #if sheet and sheet.type == 'singleton':
            #view_type = 'form'

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
        if not cls.get_table():
            return super(Data, cls).search(domain, offset, limit, order, count,
                query)
        table = cls.get_sql_table()
        cursor = Transaction().connection.cursor()
        # Get domain clauses
        tables, expression = cls.search_domain(domain,
            tables={None: (table, None)})

        select = table.select(table.id, where=expression, limit=limit,
            offset=offset)
        if query:
            return select
        cursor.execute(*select)
        res = [x[0] for x in cursor.fetchall()]
        return cls.browse(res)

    @classmethod
    def read(cls, ids, fields_names=None):
        sql_table = cls.get_sql_table()
        table = cls.get_table()

        if not ids:
            return []

        cursor = Transaction().connection.cursor()
        cursor.execute(*sql_table.select(where=sql_table.id.in_(ids)))
        fetchall = list(cursor_dict(cursor))

        to_cast = {}
        for field in table.fields:
            if fields_names and not field.name in fields_names:
                continue
            cast = FIELD_TYPE_CAST[field.type]
            if cast:
                to_cast[field.name] = cast

        if to_cast:
            for record in fetchall:
                for field, cast in to_cast.items():
                    record[field] = cast(record[field])
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
        records = cls.browse(ids)
        cls.update_formulas(records)
        return records

    @classmethod
    def update_formulas(cls, records=None):
        table = cls.get_table()
        if not records:
            records = cls.search([])

        formula_fields = [x.name for x in table.fields if x.formula]
        if not formula_fields:
            return
        actions = []
        for record in records:
            actions.append([record])
            actions.append(record.on_change_with(formula_fields))
        cls.write(*actions)

    @classmethod
    def write(cls, *args):
        table = cls.get_table()
        formula_fields = [x.name for x in table.fields if x.formula]

        table = cls.get_sql_table()
        cursor = Transaction().connection.cursor()

        has_formulas = False
        all_records = []
        actions = iter(args)
        for records, values in zip(actions, actions):
            all_records += records
            fields = []
            to_update = []
            for key, value in values.items():
                fields.append(sql.Column(table, key))
                to_update.append(value)
                if key in formula_fields:
                    has_formulas = True
            query = table.update(fields, to_update,
                where=table.id.in_([x.id for x in records]))
            cursor.execute(*query)

        if not has_formulas and formula_fields:
            cls.update_formulas(all_records)


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
        if view.id:
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
            if view.id:
                table = view.current_table.id
        if table:
            return Table(table)

    @classmethod
    def get_sql_table(cls):
        table = cls.get_table()
        if table:
            return sql.Table(table.name)
        return super(Data, cls).__table__()

    @classmethod
    def copy(cls, records, default=None):
        records = cls.read([x.id for x in records if x.id])
        for record in records:
            del record['id']
        return cls.create(records)
