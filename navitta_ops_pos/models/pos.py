from datetime import timedelta

from odoo import _, api, fields, models
from odoo.addons.navitta_ops.models.common import iso, local_dt
from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

# short labels of pos.session states for the Navitta screens (translated with env._ when used)
SESSION_STATES = {"opening_control": _lt("Opening"), "opened": _lt("Open"), "closing_control": _lt("Closing"),
                  "closed": _lt("Closed")}


def _state_label(env, state):
    text = SESSION_STATES.get(state)
    return env._(text) if text is not None else state


class PosConfig(models.Model):
    _inherit = "pos.config"

    navitta_branch_id = fields.Many2one("navitta.branch", "Navitta branch", index=True, ondelete="set null",
                                        help="This point of sale's sales count for this branch, and its cashiers are the branch's employees")
    navitta_employee_id = fields.Many2one("navitta.employee", "Owner employee (her own device)", index=True, ondelete="set null",
                                          domain="[('branch_id', '=', navitta_branch_id)]",
                                          help="This point of sale is this employee's own device: every sale on it counts for her, "
                                               "whoever is picked as cashier, and she is its only cashier")
    navitta_session_label = fields.Char("Current session status", compute="_compute_navitta_session_label")

    @api.constrains("navitta_employee_id", "navitta_branch_id")
    def _check_navitta_owner(self):
        for c in self.filtered("navitta_employee_id"):
            if c.navitta_employee_id.branch_id != c.navitta_branch_id:
                raise ValidationError(_("The owner of “%(c)s” must be an employee of its Navitta branch", c=c.name))
            other = self.sudo().search([("id", "!=", c.id), ("navitta_employee_id", "=", c.navitta_employee_id.id)], limit=1)
            if other:
                raise ValidationError(_("%(e)s already owns the point of sale “%(o)s”", e=c.navitta_employee_id.name, o=other.name))

    @api.depends("current_session_state")
    def _compute_navitta_session_label(self):
        for c in self:
            st = c.current_session_state
            c.navitta_session_label = _state_label(c.env, st) if st else _("No session")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.mapped("navitta_branch_id")._navitta_sync_cashiers()
        return recs

    def write(self, vals):
        before = self.mapped("navitta_branch_id")
        res = super().write(vals)
        if {"navitta_branch_id", "navitta_employee_id"} & set(vals) and not self.env.context.get("navitta_skip"):
            (before | self.mapped("navitta_branch_id")).with_context(navitta_skip=True)._navitta_sync_cashiers()
        return res


class PosOrder(models.Model):
    _inherit = "pos.order"

    navitta_visit_id = fields.Many2one("navitta.visit", "Counter visit", index=True, ondelete="set null", copy=False, readonly=True,
                                       help="The counter visit this sale belongs to (or the automatic visit «sold without the counter»)")
    navitta_checked = fields.Boolean("Matched with the counter", copy=False, readonly=True)


class PosSession(models.Model):
    _inherit = "pos.session"

    navitta_opened_by_id = fields.Many2one("hr.employee", "Opened by cashier", readonly=True, copy=False,
                                           help="The cashier who opened the session")

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
        opener = self.navitta_opened_by_id
        owner = self.config_id.navitta_employee_id
        linked = bool(owner) or (bool(opener) and bool(nav.search_count([("hr_employee_id", "=", opener.id)], limit=1)))
        return {"name": self.config_id.name, "session": self.name, "state": self.state, "state_label": _state_label(self.env, self.state),
                "opened_at": iso(self.start_at), "closed_at": iso(self.stop_at),
                "opened_by": who(opener) or self.user_id.name, "cashier": who(self.employee_id) or "",
                # an employee's own device: every sale counts for her, so nothing is «shared account»
                "owner": owner.name or "", "general": not linked}
