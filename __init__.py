# This file is part shine module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import configuration
from . import shine
from . import tag
from . import dashboard
from . import function

def register():
    Pool.register(
        configuration.Configuration,
        function.Function,
        shine.Sheet,
        shine.DataSet,
        shine.Formula,
        shine.View,
        shine.ViewTableFormula,
        shine.Data,
        shine.Table,
        shine.TableField,
        shine.TableView,
        tag.Tag,
        tag.SheetTag,
        dashboard.Dashboard,
        dashboard.DashboardElement,
        dashboard.DashboardMockup,
        module='shine', type_='model')
