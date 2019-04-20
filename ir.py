from trytond.pool import PoolMeta

__all__ = ['Cron']


class Cron(metaclass=PoolMeta):
     __name__ = 'ir.cron'

     @classmethod
     def __setup__(cls):
         super().__setup__()
         cls.method.selection.extend([
                 ('shine.table|remove_old_tables', 'Remove Old Shine Tables'),
                 ])
