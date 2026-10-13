from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    navitta_branch_id = fields.Many2one(
        "navitta.branch", string="فرع نافيتا",
        help="حساب جهاز الفرع: يرى بيانات هذا الفرع فقط. اتركه فارغًا لحسابات الإدارة.")
