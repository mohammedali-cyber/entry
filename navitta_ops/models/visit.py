from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import LOST_REASONS


class NavittaVisit(models.Model):
    _name = "navitta.visit"
    _description = "زيارة عميلة"
    _order = "started_at desc"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, index=True, ondelete="restrict")
    day = fields.Date("اليوم", required=True, index=True)
    started_at = fields.Datetime("دخلت", required=True)
    ended_at = fields.Datetime("خرجت")
    duration_sec = fields.Integer("المدة (ثانية)")
    duration_min = fields.Float("المدة (دقائق)", compute="_compute_duration_min", store=True, aggregator="avg")
    bought = fields.Boolean("اشترت")
    exchanged = fields.Boolean("استبدال")
    lost_reason = fields.Selection(LOST_REASONS, "سبب عدم الشراء")
    other_text = fields.Char("السبب (أخرى)")
    note = fields.Text("ملاحظة")
    lost_model = fields.Char("الموديل المطلوب")
    lost_color = fields.Char("اللون المطلوب")
    lost_size = fields.Char("المقاس المطلوب")
    saved = fields.Boolean("محفوظة", index=True)
    outcome = fields.Selection([("buy", "اشترت"), ("exchange", "استبدال"), ("buy_exchange", "شراء واستبدال"), ("lost", "ما اشترت")],
                               compute="_compute_outcome", store=True, string="النتيجة")
    visit_count = fields.Integer("عدد الزيارات", default=1, readonly=True, help="للتقارير")
    bought_count = fields.Integer("اشترت (عدد)", compute="_compute_outcome", store=True)
    lost_sale_id = fields.One2many("navitta.lost.sale", "visit_id")

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

    def _outcome_list(self):
        """Values used by the screens: 'buy', 'exchange' and/or the lost reason key."""
        self.ensure_one()
        return (["buy"] if self.bought else []) + (["exchange"] if self.exchanged else []) + ([self.lost_reason] if self.lost_reason else [])


class NavittaVisitEditRequest(models.Model):
    _name = "navitta.visit.edit.request"
    _description = "طلب تعديل مدة زيارة"
    _order = "create_date desc"
    _rec_name = "visit_id"

    visit_id = fields.Many2one("navitta.visit", "الزيارة", required=True, ondelete="cascade", index=True)
    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="الفرع")
    old_sec = fields.Integer("المدة الحالية (ث)", required=True)
    new_sec = fields.Integer("المدة المطلوبة (ث)", required=True)
    reason = fields.Text("السبب", required=True)
    state = fields.Selection([("pending", "بانتظار الرد"), ("approved", "مقبول"), ("rejected", "مرفوض")],
                             "الحالة", default="pending", required=True, index=True)
    reply = fields.Text("رد الإدارة")
    decided_at = fields.Datetime("وقت القرار")

    def _decide(self, approve, reply=None):
        self.ensure_one()
        r = self.sudo()
        if r.state != "pending":
            return "not_found"
        r.write({"state": "approved" if approve else "rejected", "reply": reply, "decided_at": fields.Datetime.now()})
        if approve:
            r.visit_id.duration_sec = r.new_sec
        msg = (_("قبلت الإدارة طلبك وصارت مدة الزيارة %(m)s:%(s)02d", m=r.new_sec // 60, s=r.new_sec % 60) if approve
               else _("رفضت الإدارة طلب تعديل مدة الزيارة"))
        r.employee_id._notify(_("رد على طلب تعديل مدة الزيارة"),
                              _("مرحبًا %(n)s، %(m)s.%(x)s", n=r.employee_id.name, m=msg, x=_(" ملاحظة الإدارة: %s", reply) if reply else ""))
        return None

    def action_approve(self):
        for r in self:
            r._decide(True, r.reply)

    def action_reject(self):
        for r in self:
            r._decide(False, r.reply)


class NavittaLostSale(models.Model):
    _name = "navitta.lost.sale"
    _description = "مبيعة ضائعة"
    _order = "create_date desc"
    _rec_name = "model"

    visit_id = fields.Many2one("navitta.visit", "الزيارة", ondelete="cascade", index=True)
    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    employee_id = fields.Many2one("navitta.employee", "الموظفة", ondelete="set null")
    day = fields.Date("اليوم", required=True, index=True)
    reason = fields.Selection(LOST_REASONS, "السبب", required=True)
    item_no = fields.Char("رقم المؤسسة", index=True)
    model = fields.Char("الموديل")
    color = fields.Char("اللون")
    size = fields.Char("المقاس")
    price = fields.Float("السعر", digits=(10, 2))
    custom = fields.Boolean("لون أو مقاس غير موجود في الملف")
    note = fields.Text("ملاحظة")
    contact_name = fields.Char("اسم العميلة")
    contact_phone = fields.Char("جوال العميلة")
    contacted_at = fields.Datetime("وقت التواصل")
    contacted_by = fields.Char("تواصلت")
    waiting = fields.Boolean("تنتظر التواصل", compute="_compute_waiting", store=True)
    request_id = fields.Many2one("navitta.branch.request", "طلب النواقص", ondelete="set null")
    lost_count = fields.Integer("العدد", default=1, readonly=True)

    _sql_constraints = [("visit_uniq", "unique(visit_id)", "الزيارة لها مبيعة ضائعة مسجلة")]

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
            if r._mark_contacted(_("الإدارة")):
                raise UserError(_("تم التواصل مسبقًا"))
