from trytond.model import ModelSQL, ModelView, fields, tree

__all__ = ['Tag', 'SheetTag']


class Tag(tree(separator=' / '), ModelSQL, ModelView):
    'Shine Tag'
    __name__ = 'shine.tag'
    name = fields.Char('Name', required=True)
    parent = fields.Many2One('shine.tag', 'Parent', select=True)
    children = fields.One2Many('shine.tag', 'parent', 'Children')

    @classmethod
    def __setup__(cls):
        super(Tag, cls).__setup__()
        cls._order.insert(0, ('name', 'ASC'))


class SheetTag(ModelSQL):
    'Shine Sheet - Tag'
    __name__ = 'shine.sheet.tag'
    tag = fields.Many2One('shine.tag', 'Tag', required=True,
        ondelete='CASCADE')
    sheet = fields.Many2One('shine.sheet', 'Sheet', required=True,
        ondelete='CASCADE')
