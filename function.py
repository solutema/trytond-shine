# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal
import datetime
import math
#from simpleeval import simple_eval
from dateutil.relativedelta import relativedelta
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.model import ModelSQL, ModelView, fields

__all__ = ['Function']


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


def date(text):
    if not text:
        return None
    return datetime.datetime.strptime(year_month_day(text), '%Y-%m-%d').date()

def today():
    return datetime.date.today()

def now():
    return datetime.datetime.now()


def shine_eval(expression, obj, convert_none='empty'):
    objects = {
        # Tryton objects
        'o': obj,
        'Pool': Pool,
        'Transaction': Transaction,
        # Date Time methods
        'y': year,
        'm': month,
        'd': day,
        'w': week,
        'ym': year_month,
        'ymd': year_month_day,
        'date': date,
        'now': datetime.datetime.now,
        'today': datetime.date.today,
        'relativedelta': relativedelta,
        # Conversion methods
        'int': int,
        'float': float,
        'str': str,
        # Aggregate methods
        'sum': sum,
        'min': min,
        'max': max,
        # Modules and objects
        'math': math,
        'Decimal': Decimal,
        }
    #value = simple_eval(expression, functions = objects)
    value = eval(expression, objects)
    if (value is False or value is None):
        if convert_none == 'empty':
            # TODO: Make translatable
            value = '(empty)'
        elif convert_none == 'zero':
            value = '0'
        else:
            value = convert_none
    return value

def evaluate(code, context, return_var=None):
    print('ABOUT TO EVAL: ', code, context)
    exec(code)
    if return_var:
        return eval(return_var)


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
