import sql
from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.tools import cursor_dict
from .shine import FIELD_TYPE_TRYTON, FIELD_TYPE_CAST

class ClassProperty(property):
    def __get__(self, cls, owner):
        print('GETTING')
        return self.fget.__get__(None, owner)()

    def __contains__(self, cls, key):
        print('CONTAINS?')
        return key in self.numbers


class Data(ModelSQL, ModelView):
    'Shine Data'
    __name__ = 'shine.data'

    def get_fields(cls):
        print('WERE HERE')
        table = cls.get_table()
        res = {}
        for field in table.fields:
            res[field.name] = fields.Char('')
        return res

    @classmethod
    def __setup__(cls):
        super(Data, cls).__setup__()
        cls._fields = classmethod(cls.get_fields)

    @classmethod
    def default_get(cls, fields_names, with_rec_name=True):
        return {}

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
                    }
            if field.type == 'reference':
                selection = []
                for model in Model.search([]):
                    selection.append((model.model, model.name))
                res[field.name]['selection'] = selection
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

    def __getattr__(cls, name):
        print('GOT KEY, ', name)

    def __getattribute__(self, name):
        print('GOT ATR', name)

    @classmethod
    def search(cls, domain, offset=0, limit=None, order=None, count=False,
            query=False):
        table = cls.get_sql_table()

        #print('CURRENT _fields', cls._fields)
        #cls._fields = classmethod(cls.get_fields)
        #cls._fields = property(cls.get_fields)
        #print('NOW _fields', cls._fields)
        #del cls._fields
        print('GET: ', cls._fields)

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
        sql_table = cls.get_sql_table()
        table = cls.get_table()

        cursor = Transaction().connection.cursor()
        cursor.execute(*sql_table.select())
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
