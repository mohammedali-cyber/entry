from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    lost_finder_warehouse_id = fields.Many2one(
        'stock.warehouse', string="Branch (Lost Products)",
        help="Branch of the employee in the Lost Products app: lost sales are recorded for "
             "this warehouse and the employee only sees the lost sales of this branch.")
