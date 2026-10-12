from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    lost_finder_warehouse_id = fields.Many2one(
        'stock.warehouse', string="Branch (Lost Sales)",
        help="Branch of the employee in the Lost Sales app: lost sales are recorded for "
             "this warehouse and the employee only sees the lost sales of this branch.")
