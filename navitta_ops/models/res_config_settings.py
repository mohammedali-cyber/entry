from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    navitta_tz = fields.Selection(related="company_id.navitta_tz", readonly=False)
    navitta_shift_start = fields.Float(related="company_id.navitta_shift_start", readonly=False)
    navitta_shift_end = fields.Float(related="company_id.navitta_shift_end", readonly=False)
    navitta_am_start = fields.Float(related="company_id.navitta_am_start", readonly=False)
    navitta_am_end = fields.Float(related="company_id.navitta_am_end", readonly=False)
    # days without a morning shift (company.navitta_no_am_days), one box per weekday
    navitta_no_am_0 = fields.Boolean("Sunday")
    navitta_no_am_1 = fields.Boolean("Monday")
    navitta_no_am_2 = fields.Boolean("Tuesday")
    navitta_no_am_3 = fields.Boolean("Wednesday")
    navitta_no_am_4 = fields.Boolean("Thursday")
    navitta_no_am_5 = fields.Boolean("Friday")
    navitta_no_am_6 = fields.Boolean("Saturday")
    navitta_two_shift_branch_ids = fields.Many2many("navitta.branch", string="Branches with a morning shift",
                                                    compute="_compute_navitta_two_shift", inverse="_inverse_navitta_two_shift")
    navitta_grace = fields.Integer(related="company_id.navitta_grace", readonly=False)
    navitta_break_max = fields.Integer(related="company_id.navitta_break_max", readonly=False)
    navitta_chances = fields.Integer(related="company_id.navitta_chances", readonly=False)
    navitta_report_email = fields.Char(related="company_id.navitta_report_email", readonly=False)
    navitta_min_roll_invoice = fields.Integer(related="company_id.navitta_min_roll_invoice", readonly=False)
    navitta_min_roll_card = fields.Integer(related="company_id.navitta_min_roll_card", readonly=False)
    navitta_min_bags = fields.Integer(related="company_id.navitta_min_bags", readonly=False)
    navitta_vat_rate = fields.Float(related="company_id.navitta_vat_rate", readonly=False)
    navitta_checkin_photo = fields.Boolean(related="company_id.navitta_checkin_photo", readonly=False)
    navitta_task_start_to = fields.Float(related="company_id.navitta_task_start_to", readonly=False)
    navitta_task_mid_from = fields.Float(related="company_id.navitta_task_mid_from", readonly=False)
    navitta_task_mid_to = fields.Float(related="company_id.navitta_task_mid_to", readonly=False)
    navitta_task_end_from = fields.Float(related="company_id.navitta_task_end_from", readonly=False)
    navitta_admin_pw_set = fields.Boolean(compute="_compute_navitta_admin_pw_set")

    @api.model
    def get_values(self):
        res = super().get_values()
        days = self.env.company._navitta_no_am()
        res.update({"navitta_no_am_%d" % i: i in days for i in range(7)})
        return res

    def set_values(self):
        super().set_values()
        for s in self:
            s.company_id.navitta_no_am_days = ",".join(str(i) for i in range(7) if s["navitta_no_am_%d" % i])

    @api.depends("company_id")
    def _compute_navitta_two_shift(self):
        for s in self:
            s.navitta_two_shift_branch_ids = self.env["navitta.branch"].search([("two_shifts", "=", True)])

    def _inverse_navitta_two_shift(self):
        Branch = self.env["navitta.branch"]
        for s in self:
            on = s.navitta_two_shift_branch_ids
            Branch.search([("two_shifts", "=", True), ("id", "not in", on.ids)]).write({"two_shifts": False})
            (on - Branch.search([("two_shifts", "=", True)])).write({"two_shifts": True})

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
