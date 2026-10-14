from datetime import timedelta

from odoo import _, api, fields, models
from odoo.addons.navitta_ops.models.common import iso, local_dt


class PosConfig(models.Model):
    _inherit = "pos.config"

    navitta_branch_id = fields.Many2one("navitta.branch", "فرع نافيتا", index=True, ondelete="set null",
                                        help="مبيعات نقطة البيع هذه تنحسب لهذا الفرع، وكاشيراتها موظفات الفرع")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.mapped("navitta_branch_id")._navitta_sync_cashiers()
        return recs

    def write(self, vals):
        before = self.mapped("navitta_branch_id")
        res = super().write(vals)
        if "navitta_branch_id" in vals and not self.env.context.get("navitta_skip"):
            (before | self.mapped("navitta_branch_id")).with_context(navitta_skip=True)._navitta_sync_cashiers()
        return res


class PosSession(models.Model):
    _inherit = "pos.session"

    navitta_opened_by_id = fields.Many2one("hr.employee", "فتحت الكاشير", readonly=True, copy=False,
                                           help="الكاشير اللي فتحت الجلسة")

    def _set_opening_control_data(self, cashbox_value, notes):
        super()._set_opening_control_data(cashbox_value, notes)
        for s in self:
            s.navitta_opened_by_id = s.employee_id

    def action_pos_session_closing_control(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        res = super().action_pos_session_closing_control(balancing_account, amount_to_balance, bank_payment_method_diffs)
        for s in self.filtered(lambda x: x.config_id.navitta_branch_id):
            start = local_dt(self.env, s.start_at or fields.Datetime.now()).date()
            end = local_dt(self.env, fields.Datetime.now()).date()
            days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            s.config_id.navitta_branch_id._system_sync(days, force=True)
        return res

    def _navitta_status(self):
        self.ensure_one()
        nav = self.env["navitta.employee"].sudo().with_context(active_test=False)

        def who(hr):
            if not hr:
                return False
            e = nav.search([("hr_employee_id", "=", hr.id)], limit=1)
            return e.name if e else hr.name
        states = {"opening_control": _("قيد الفتح"), "opened": _("مفتوحة"), "closing_control": _("قيد الإغلاق"), "closed": _("مغلقة")}
        opener = self.navitta_opened_by_id
        linked = bool(opener) and bool(nav.search_count([("hr_employee_id", "=", opener.id)], limit=1))
        return {"name": self.config_id.name, "session": self.name, "state": self.state, "state_label": states.get(self.state, self.state),
                "opened_at": iso(self.start_at), "closed_at": iso(self.stop_at),
                "opened_by": who(opener) or self.user_id.name, "cashier": who(self.employee_id) or "",
                "general": not linked}
