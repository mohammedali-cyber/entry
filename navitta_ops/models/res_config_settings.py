from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    navitta_tz = fields.Selection(related="company_id.navitta_tz", readonly=False)
    navitta_shift_start = fields.Float(related="company_id.navitta_shift_start", readonly=False)
    navitta_shift_end = fields.Float(related="company_id.navitta_shift_end", readonly=False)
    navitta_grace = fields.Integer(related="company_id.navitta_grace", readonly=False)
    navitta_break_max = fields.Integer(related="company_id.navitta_break_max", readonly=False)
    navitta_chances = fields.Integer(related="company_id.navitta_chances", readonly=False)
    navitta_report_email = fields.Char(related="company_id.navitta_report_email", readonly=False)
    navitta_min_roll_invoice = fields.Integer(related="company_id.navitta_min_roll_invoice", readonly=False)
    navitta_min_roll_card = fields.Integer(related="company_id.navitta_min_roll_card", readonly=False)
    navitta_min_bags = fields.Integer(related="company_id.navitta_min_bags", readonly=False)
    navitta_vat_rate = fields.Float(related="company_id.navitta_vat_rate", readonly=False)
    navitta_checkin_photo = fields.Boolean(related="company_id.navitta_checkin_photo", readonly=False)
    navitta_cash_tasks = fields.Boolean(related="company_id.navitta_cash_tasks", readonly=False)
    navitta_admin_pw_set = fields.Boolean(compute="_compute_navitta_admin_pw_set")

    @api.depends("company_id")
    def _compute_navitta_admin_pw_set(self):
        for s in self:
            s.navitta_admin_pw_set = s.company_id._navitta_admin_password_is_set()

    def action_navitta_admin_password(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "navitta.admin.password.wizard",
            "view_mode": "form",
            "target": "new",
        }
