from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    navitta_branch_id = fields.Many2one(
        "navitta.branch", string="Navitta branch",
        help="Branch device account: sees this branch's data only. Leave empty for management accounts.")
