from trytond.model import ModelSQL, ModelView, fields, tree
from trytond.pyson import Eval
from trytond.pool import Pool
from trytond.i18n import gettext
from trytond.exceptions import UserError

__all__ = ['Tag', 'SheetTag']


class TaggedMixin(object):
    __slots__ = ()

    @classmethod
    def validate(cls, sheets):
        cls.check_tags(sheets)

    @classmethod
    def check_tags(cls, sheets):
        Tag = Pool().get('shine.tag')

        required_tags = Tag.search([
                ('required', '=', True),
                ('view', '=', True),
                ])
        required_children = {}
        for required in required_tags:
            children = Tag.search([
                    ('parent', 'child_of', [required.id]),
                    ('id', '!=', required.id),
                    ])
            required_children[required] = {x.id for x in children}

        unique_tags = Tag.search([
                ('unique', '=', True),
                ('view', '=', True),
                ])
        unique_children = {}
        for unique in unique_tags:
            children = Tag.search([
                    ('parent', 'child_of', [unique.id]),
                    ('id', '!=', unique.id),
                    ])
            unique_children[unique] = {x.id for x in children}

        for sheet in sheets:
            sheet_tag_ids = {x.id for x in sheet.tags}
            for view, children in required_children.items():
                if not (sheet_tag_ids & children):
                    raise UserError(gettext('shine.missing_tags',
                        record=sheet.rec_name, tag=view.rec_name))

            for view, children in unique_children.items():
                if len(sheet_tag_ids & children) > 1:
                    raise UserError(gettext('shine.repeated_tags',
                        record=sheet.rec_name, tag=view.rec_name))


class Tag(tree(separator=' / '), ModelSQL, ModelView):
    'Shine Tag'
    __name__ = 'shine.tag'
    name = fields.Char('Name', required=True)
    parent = fields.Many2One('shine.tag', 'Parent', select=True)
    children = fields.One2Many('shine.tag', 'parent', 'Children')
    view = fields.Boolean('View', help='View Tags cannot be used in Sheets or '
        'Dashboards and they are used only for structuring a tag hierarchy')
    unique = fields.Boolean('Unique', states={
            'invisible': ~Eval('view'),
            }, depends=['view'], help='Only one of the children of this tag '
        'can be used in a Sheet or Dashboard')
    required = fields.Boolean('Required', states={
            'invisible': ~Eval('view'),
            }, depends=['view'], help='At least one of the children tags of '
        'the current tag must be used in all Sheets or Dashboards')

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
