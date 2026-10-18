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
                                        help="This point of sale's sales count for this Navitta branch")
    navitta_employee_id = fields.Many2one("navitta.employee", "Owner employee (her own device)", index=True, ondelete="set null",
                                          domain="[('branch_id', '=', navitta_branch_id)]",
                                          help="This point of sale is this employee's own device: every sale on it counts for her, "
                                               "whoever is picked as cashier. The cashier list of the point of sale is not changed")
    navitta_target_share = fields.Float(
        "Share of the branch target (%)", digits=(5, 2), default=0.0,
        help="This device's share of its branch's daily target: device target = branch target × share / 100. "
             "Left empty (0) on every device of the branch, the target is split equally between them; the devices left "
             "empty share what remains of 100% equally. Targets are still set per branch")
    navitta_target_share_eff = fields.Float("Effective share (%)", digits=(5, 2), compute="_compute_navitta_target_share_eff",
                                            help="The share of the branch target this device gets, after the equal split")
    navitta_session_label = fields.Char("Current session status", compute="_compute_navitta_session_label")

    @api.constrains("navitta_employee_id", "navitta_branch_id")
    def _check_navitta_owner(self):
        for c in self.filtered("navitta_employee_id"):
            if c.navitta_employee_id.branch_id != c.navitta_branch_id:
                raise ValidationError(_("The owner of “%(c)s” must be an employee of its Navitta branch", c=c.name))
            other = self.sudo().search([("id", "!=", c.id), ("navitta_employee_id", "=", c.navitta_employee_id.id)], limit=1)
            if other:
                raise ValidationError(_("%(e)s already owns the point of sale “%(o)s”", e=c.navitta_employee_id.name, o=other.name))

    @api.constrains("navitta_target_share")
    def _check_navitta_target_share(self):
        for c in self:
            if not 0 <= c.navitta_target_share <= 100:
                raise ValidationError(_("The share of the branch target of “%s” must be between 0 and 100%%", c.name))

    @api.depends("navitta_target_share", "navitta_branch_id", "navitta_branch_id.pos_config_ids.navitta_target_share")
    def _compute_navitta_target_share_eff(self):
        for c in self:
            c.navitta_target_share_eff = c.navitta_branch_id._pos_target_shares().get(c.id, 0.0) if c.navitta_branch_id else 0.0

    @api.depends("current_session_state")
    def _compute_navitta_session_label(self):
        for c in self:
            st = c.current_session_state
            c.navitta_session_label = _state_label(c.env, st) if st else _("No session")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        linked = recs.filtered("navitta_branch_id")
        if linked:
            self.env.registry.clear_cache()         # the branch devices' access rules (their cashiers)
            self.env["navitta.branch"].sudo()._pos_backfill(configs=linked, force=True)
        return recs

    def write(self, vals):
        before = {c.id: c.navitta_branch_id for c in self}
        owners = {c.id: c.navitta_employee_id for c in self}
        res = super().write(vals)
        if {"navitta_branch_id", "basic_employee_ids", "advanced_employee_ids"} & set(vals):
            self.env.registry.clear_cache()         # the branch devices' access rules (their cashiers)
        if {"navitta_branch_id", "navitta_employee_id"} & set(vals):
            # linked to a branch, moved, or a new owner: every day of its orders is counted again (the history too)
            moved = self.filtered(lambda c: before[c.id] != c.navitta_branch_id or owners[c.id] != c.navitta_employee_id)
            if moved:
                old = self.env["navitta.branch"].sudo().browse({before[c.id].id for c in moved if before[c.id]})
                self.env["navitta.branch"].sudo()._pos_backfill(configs=moved, force=True, old_branches=old - moved.navitta_branch_id)
        return res


class ResConfigSettings(models.TransientModel):
    """Point of Sale → Settings: the Navitta branch and the owner of the selected point of sale."""
    _inherit = "res.config.settings"

    pos_navitta_branch_id = fields.Many2one(related="pos_config_id.navitta_branch_id", readonly=False)
    pos_navitta_employee_id = fields.Many2one(related="pos_config_id.navitta_employee_id", readonly=False,
                                              domain="[('branch_id', '=', pos_navitta_branch_id)]")


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
