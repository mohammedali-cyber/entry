from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import LOST_REASONS


class NavittaVisit(models.Model):
    _name = "navitta.visit"
    _description = "Customer visit"
    _order = "started_at desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, index=True, ondelete="restrict")
    day = fields.Date("Day", required=True, index=True)
    started_at = fields.Datetime("Came in", required=True)
    ended_at = fields.Datetime("Left")
    duration_sec = fields.Integer("Duration (seconds)")
    duration_min = fields.Float("Duration (minutes)", compute="_compute_duration_min", store=True, aggregator="avg")
    bought = fields.Boolean("Bought")
    exchanged = fields.Boolean("Exchange")
    lost_reason = fields.Selection(LOST_REASONS, "Reason for not buying")
    other_text = fields.Char("Reason (other)")
    note = fields.Text("Note")
    lost_model = fields.Char("Requested model")
    lost_color = fields.Char("Requested color")
    lost_size = fields.Char("Requested size")
    saved = fields.Boolean("Saved", index=True)
    sat = fields.Integer("Customer satisfaction (1-5)", aggregator="avg",
                         help="Optional, chosen with the visit outcome on the counter (0 = not rated). The daily report shows "
                              "the day's average in «From the counter (automatic)».")
    outcome = fields.Selection([("buy", "Bought"), ("exchange", "Exchange"), ("buy_exchange", "Bought and exchange"), ("lost", "Didn't buy")],
                               compute="_compute_outcome", store=True, string="Outcome")
    visit_count = fields.Integer("Number of visits", default=1, readonly=True, help="For reports")
    bought_count = fields.Integer("Bought (count)", compute="_compute_outcome", store=True)
    lost_sale_id = fields.One2many("navitta.lost.sale", "visit_id")
    # a sale recorded by the cash register system while the counter was off (navitta_ops_pos): added as a visit, the
    # employee confirms it on the counter (with an optional note) before she can check out
    auto = fields.Boolean("Sold without the counter", readonly=True, index=True)
    auto_ack = fields.Boolean("Confirmed by the employee", readonly=True)
    auto_ack_at = fields.Datetime("Confirmed at", readonly=True)
    amount = fields.Float("Invoice amount", digits=(12, 2), readonly=True, help="Total of the cash register invoices linked to this visit")

    @api.depends("duration_sec")
    def _compute_duration_min(self):
        for v in self:
            v.duration_min = (v.duration_sec or 0) / 60.0

    @api.depends("bought", "exchanged", "lost_reason")
    def _compute_outcome(self):
        for v in self:
            v.outcome = ("buy_exchange" if v.bought and v.exchanged else "buy" if v.bought else
                         "exchange" if v.exchanged else "lost" if v.lost_reason else False)
            v.bought_count = 1 if v.bought else 0

    def _ack_auto(self, note=None):
        """The employee saw the sale made without the counter (and may add a note)."""
        self.ensure_one()
        v = self.sudo()
        if not v.auto or v.auto_ack:
            return "not_found"
        v.write({"auto_ack": True, "auto_ack_at": fields.Datetime.now(), "note": (note or "").strip() or v.note or False})
        return None

    @api.model
    def _unacked_autos(self, employee, day):
        return self.sudo().search([("employee_id", "=", employee.id), ("day", "=", day), ("auto", "=", True), ("auto_ack", "=", False)])

    def _outcome_list(self):
        """Values used by the screens: 'buy', 'exchange' and/or the lost reason key."""
        self.ensure_one()
        return (["buy"] if self.bought else []) + (["exchange"] if self.exchanged else []) + ([self.lost_reason] if self.lost_reason else [])


class NavittaVisitEditRequest(models.Model):
    _name = "navitta.visit.edit.request"
    _description = "Visit duration change request"
    _order = "create_date desc"
    _rec_name = "visit_id"

    visit_id = fields.Many2one("navitta.visit", "Visit", required=True, ondelete="cascade", index=True)
    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="Branch")
    old_sec = fields.Integer("Current duration (s)", required=True)
    new_sec = fields.Integer("Requested duration (s)", required=True)
    reason = fields.Text("Reason", required=True)
    state = fields.Selection([("pending", "Waiting for reply"), ("approved", "Approved"), ("rejected", "Rejected")],
                             "Status", default="pending", required=True, index=True)
    reply = fields.Text("Management reply")
    decided_at = fields.Datetime("Decision time")

    def _decide(self, approve, reply=None):
        self.ensure_one()
        r = self.sudo()
        if r.state != "pending":
            return "not_found"
        r.write({"state": "approved" if approve else "rejected", "reply": reply, "decided_at": fields.Datetime.now()})
        if approve:
            r.visit_id.duration_sec = r.new_sec
        msg = (_("Management accepted your request and the visit duration is now %(m)s:%(s)02d", m=r.new_sec // 60, s=r.new_sec % 60) if approve
               else _("Management rejected the visit duration change request"))
        r.employee_id._notify(_("Reply to the visit duration change request"),
                              _("Hello %(n)s, %(m)s.%(x)s", n=r.employee_id.name, m=msg, x=_(" Management note: %s", reply) if reply else ""))
        return None

    def action_approve(self):
        for r in self:
            r._decide(True, r.reply)

    def action_reject(self):
        for r in self:
            r._decide(False, r.reply)


class NavittaLostSale(models.Model):
    _name = "navitta.lost.sale"
    _description = "Lost sale"
    _order = "create_date desc"
    _rec_name = "model"

    visit_id = fields.Many2one("navitta.visit", "Visit", ondelete="cascade", index=True)
    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    employee_id = fields.Many2one("navitta.employee", "Employee", ondelete="set null")
    day = fields.Date("Day", required=True, index=True)
    reason = fields.Selection(LOST_REASONS, "Reason", required=True)
    item_no = fields.Char("Item number", index=True)
    model = fields.Char("Model")
    color = fields.Char("Color")
    size = fields.Char("Size")
    price = fields.Float("Price", digits=(10, 2))
    custom = fields.Boolean("Color or size not in the models file")
    note = fields.Text("Note")
    contact_name = fields.Char("Customer name")
    contact_phone = fields.Char("Customer mobile")
    contacted_at = fields.Datetime("Contact time")
    contacted_by = fields.Char("Contacted by")
    waiting = fields.Boolean("Waiting for contact", compute="_compute_waiting", store=True)
    request_id = fields.Many2one("navitta.branch.request", "Out-of-stock request", ondelete="set null")
    lost_count = fields.Integer("Count", default=1, readonly=True)

    _sql_constraints = [("visit_uniq", "unique(visit_id)", "This visit already has a lost sale recorded")]

    @api.depends("contact_phone", "contacted_at")
    def _compute_waiting(self):
        for r in self:
            r.waiting = bool(r.contact_phone and not r.contacted_at)

    def _mark_contacted(self, by):
        self.ensure_one()
        r = self.sudo()
        if r.contacted_at:
            return "not_found"
        r.write({"contacted_at": fields.Datetime.now(), "contacted_by": (by or "").strip() or False})
        return None

    def action_contacted(self):
        for r in self:
            if r._mark_contacted(_("Management")):
                raise UserError(_("Already contacted"))
