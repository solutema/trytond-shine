# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import datetime
import formulas
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.model import ModelSQL, ModelView, fields

__all__ = ['Function']


def formulas_sheet(alias):
    Sheet = Pool().get('shine.sheet')
    sheets = Sheet.search([('alias', '=', alias)], limit=1)
    if sheets:
        return sheets[0]

def formulas_sheet_records(alias):
    Data = Pool().get('shine.data')

    sheet = formulas_sheet(alias)
    if not sheet:
        return

    if not sheet.current_table:
        return

    with Transaction().set_context({'shine_table': sheet.current_table.id}):
        records = Data.search([])
        if not records:
            return
        records = Data.read([x.id for x in records])
    return records

def sheet_value(alias, formula):
    records = formulas_sheet_records(alias)
    if not records:
        return
    record = records[0]
    return record[formula]

def sheet_values(alias, formula):
    records = formulas_sheet_records(alias)
    if not records:
        return
    return [x[formula] for x in records]

def year(text):
    if not text:
        return None
    text = str(text)
    return text[0:4]


def year_month(text):
    if not text:
        return None
    text = str(text)
    return text[0:4] + '-' + text[5:7]


def year_month_day(text):
    if not text:
        return None
    text = str(text)
    return text[0:10]


def month(text):
    if not text:
        return None
    text = str(text)
    return text[5:7]


def day(text):
    if not text:
        return None
    text = str(text)
    return text[8:10]


def week(text):
    if not text:
        return None
    return datetime.datetime.strptime(year_month_day(text),
        '%Y-%m-%d').strftime('%W')

def tryton_value(model, field):
    Model = Pool().get(model)
    records = Model.search([], limit=1)
    if not records:
        return
    record, = records
    try:
        return getattr(record, field)
    except AttributeError:
        return

def tryton_values(model, field):
    Model = Pool().get(model)
    records = Model.search([])
    if not records:
        return
    try:
        return [getattr(x, field) for x in records]
    except AttributeError:
        return

FUNCTIONS = formulas.get_functions()
FUNCTIONS['SHEET_VALUE'] = sheet_value
FUNCTIONS['SHEET_VALUES'] = sheet_values
FUNCTIONS['TRYTON_VALUE'] = tryton_value
FUNCTIONS['TRYTON_VALUES'] = tryton_values
FUNCTIONS['YEAR'] = year
FUNCTIONS['YEAR_MONTH'] = year_month
FUNCTIONS['YEAR_MONTH_DAY'] = year_month_day
FUNCTIONS['MONTH'] = month
FUNCTIONS['DAY'] = day
FUNCTIONS['WEEK'] = week


class Function(ModelSQL, ModelView):
    'Shine Function'
    __name__ = 'shine.function'
    name = fields.Char('Name', required=True)
    parameters = fields.Char('Parameters')
    help = fields.Text('Help')
    code = fields.Text('Code')

    def get_rec_name(self, name):
        return '%s(%s)' % (self.name, self.parameters)

    #@classmethod
    #def search_rec_name(self, name, clause):
        #return [

    @classmethod
    def eval_context(cls):
        res = {}
        for function in cls.search([]):
            res[function.name] = eval(function.code)
        return res
