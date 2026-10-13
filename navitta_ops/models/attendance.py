import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import fmt_time, hours_to_min


class NavittaAttendance(models.Model):
    _name = "navitta.attendance"
    _description = "حضور وانصراف"
    _order = "day desc, check_in desc"

    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="الفرع")
    day = fields.Date("اليوم", required=True, index=True)
    check_in = fields.Datetime("الحضور", required=True)
    check_out = fields.Datetime("الانصراف")

    _sql_constraints = [("emp_day_uniq", "unique(employee_id, day)", "تم تسجيل الحضور مسبقًا لهذا اليوم")]


class NavittaBreak(models.Model):
    _name = "navitta.break"
    _description = "استراحة"
    _order = "started_at desc"

    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="الفرع")
    day = fields.Date("اليوم", required=True, index=True)
    started_at = fields.Datetime("بدأت", required=True)
    ended_at = fields.Datetime("انتهت")
    duration_min = fields.Float("المدة (دقائق)", compute="_compute_duration", store=True)

    @api.depends("started_at", "ended_at")
    def _compute_duration(self):
        for b in self:
            b.duration_min = (b.ended_at - b.started_at).total_seconds() / 60 if b.ended_at else 0.0

    @api.model
    def _used_minutes(self, employee, day):
        """Break minutes of the day, rounded up (an open break counts until now)."""
        now = fields.Datetime.now()
        secs = sum(((b.ended_at or now) - b.started_at).total_seconds()
                   for b in self.sudo().search([("employee_id", "=", employee.id), ("day", "=", day)]))
        return math.ceil(secs / 60 - 1e-6) if secs > 0 else 0

    def _end(self):
        """Ends the break now (server time); over the daily limit opens a break case (no grace)."""
        self.ensure_one()
        b = self.sudo()
        if b.ended_at:
            return
        b.ended_at = fields.Datetime.now()
        company = self.env.company
        used = self._used_minutes(b.employee_id, b.day)
        limit = company.navitta_break_max
        if used > limit:
            Case = self.env["navitta.late.case"].sudo()
            case = Case.search([("employee_id", "=", b.employee_id.id), ("day", "=", b.day), ("kind", "=", "break")], limit=1)
            if not case:
                Case.create({"employee_id": b.employee_id.id, "day": b.day, "kind": "break", "minutes": used - limit, "break_used": used})
            elif case.state in ("reply", "review"):
                case.write({"minutes": used - limit, "break_used": used})
            b.employee_id._notify(
                _("تنبيه: تجاوز وقت الاستراحة"),
                _("مرحبًا %(n)s، تجاوزتِ وقت الاستراحة بـ %(o)s دقيقة (الاستراحة %(u)s د والمسموح %(m)s د). "
                  "يجب ذكر السبب أو طلب تعديل المدة من «تنبيهاتي». إدارة نافيتا",
                  n=b.employee_id.name, o=used - limit, u=used, m=limit))


class NavittaPenaltyTier(models.Model):
    _name = "navitta.penalty.tier"
    _description = "جدول المخالفات"
    _order = "max_min"

    max_min = fields.Integer("حتى (دقيقة)", required=True)
    label = fields.Char("الشريحة", required=True)
    p1 = fields.Char("المرة الأولى", required=True)
    p2 = fields.Char("المرة الثانية", required=True)
    p3 = fields.Char("المرة الثالثة", required=True)
    p4 = fields.Char("الرابعة فأكثر", required=True)

    _sql_constraints = [("max_min_uniq", "unique(max_min)", "الشريحة موجودة")]

    @api.model
    def _tier_for(self, minutes):
        tiers = self.sudo().search([], order="max_min")
        return next((t for t in tiers if t.max_min >= minutes), tiers[-1:] if tiers else tiers)


class NavittaLateCase(models.Model):
    _name = "navitta.late.case"
    _description = "تأخير أو تجاوز استراحة"
    _order = "day desc, id desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="الفرع")
    day = fields.Date("اليوم", required=True, index=True)
    kind = fields.Selection([("late", "تأخير حضور"), ("break", "تجاوز استراحة")], "النوع", required=True)
    minutes = fields.Integer("الدقائق", required=True)
    check_in = fields.Datetime("وقت الحضور")
    break_used = fields.Integer("مدة الاستراحة (د)")
    old_break_used = fields.Integer("المدة قبل التعديل (د)")
    reason = fields.Text("سبب الموظفة")
    request = fields.Selection([("forgive", "طلب فرصة مسامحة"), ("edit", "طلب تعديل مدة الاستراحة")], "الطلب")
    requested_minutes = fields.Integer("المدة المطلوبة (د)")
    state = fields.Selection([
        ("reply", "يحتاج رد الموظفة"), ("review", "بانتظار قرار الإدارة"), ("forgiven", "احتُسبت فرصة"),
        ("rejected", "مرفوض"), ("edited", "عُدّلت المدة"),
    ], "الحالة", default="reply", required=True, index=True)
    penalty = fields.Char("العقوبة")
    penalty_occ = fields.Integer("رقم المرة")
    note = fields.Text("ملاحظة الإدارة")
    decided_at = fields.Datetime("وقت القرار")

    _sql_constraints = [("emp_day_kind_uniq", "unique(employee_id, day, kind)", "يوجد تنبيه لنفس اليوم")]

    @api.model
    def _chances_used(self, employee, day):
        m0 = day.replace(day=1)
        m1 = (m0 + timedelta(days=32)).replace(day=1)
        return self.sudo().search_count([("employee_id", "=", employee.id), ("state", "=", "forgiven"),
                                         ("day", ">=", m0), ("day", "<", m1)])

    def _penalty(self):
        """Penalty text by tier and by how many times this tier was rejected in the last 180 days."""
        self.ensure_one()
        Tier = self.env["navitta.penalty.tier"]
        tier = Tier._tier_for(self.minutes)
        if not tier:
            return {"text": _("حسب لائحة الجزاءات"), "occ": 1, "tier": ""}
        others = self.sudo().search([
            ("employee_id", "=", self.employee_id.id), ("kind", "=", self.kind), ("state", "=", "rejected"),
            ("id", "!=", self.id), ("day", ">=", self.day - timedelta(days=180)), ("day", "<=", self.day)])
        n = len(others.filtered(lambda c: Tier._tier_for(c.minutes) == tier))
        return {"text": [tier.p1, tier.p2, tier.p3, tier.p4][min(n, 3)], "occ": n + 1, "tier": tier.label}

    def _kind_text(self):
        return _("التأخير") if self.kind == "late" else _("تجاوز الاستراحة")

    def _decide(self, decision, note=None):
        """Management decision on a case waiting for review. Returns an error code or None."""
        self.ensure_one()
        c = self.sudo()
        if c.state != "review":
            return "not_found"
        company = self.env.company
        emp = c.employee_id
        extra = _(" ملاحظة الإدارة: %s", note) if note else ""
        if decision == "forgive":
            if self._chances_used(emp, c.day) >= company.navitta_chances:
                return "no_chances"
            c.write({"state": "forgiven", "note": note, "decided_at": fields.Datetime.now()})
            emp._notify(_("قرار الإدارة: احتُسبت فرصة مسامحة"),
                        _("مرحبًا %(n)s، احتُسبت لك فرصة مسامحة على %(k)s يوم %(d)s (%(u)s من %(m)s هذا الشهر).%(x)s إدارة نافيتا",
                          n=emp.name, k=c._kind_text(), d=c.day, u=self._chances_used(emp, c.day), m=company.navitta_chances, x=extra))
        elif decision == "reject":
            pen = c._penalty()
            c.write({"state": "rejected", "penalty": pen["text"], "penalty_occ": pen["occ"], "note": note,
                     "decided_at": fields.Datetime.now()})
            emp._notify(_("قرار الإدارة: رُفض العذر"),
                        _("مرحبًا %(n)s، رُفض العذر على %(k)s يوم %(d)s، والعقوبة: %(p)s (المرة %(o)s، %(t)s).%(x)s إدارة نافيتا",
                          n=emp.name, k=c._kind_text(), d=c.day, p=pen["text"], o=pen["occ"], t=pen["tier"], x=extra))
        elif decision == "edit":
            if c.kind != "break" or c.request != "edit":
                return "not_edit"
            breaks = self.env["navitta.break"].sudo().search(
                [("employee_id", "=", emp.id), ("day", "=", c.day), ("ended_at", "!=", False)], order="ended_at desc")
            diff = sum((b.ended_at - b.started_at).total_seconds() for b in breaks) - c.requested_minutes * 60
            if breaks and diff > 0:
                last = breaks[0]
                last.ended_at = max(last.started_at, last.ended_at - timedelta(seconds=diff))
            ok = c.requested_minutes <= company.navitta_break_max
            c.write({
                "old_break_used": c.break_used, "break_used": c.requested_minutes,
                "minutes": max(0, c.requested_minutes - company.navitta_break_max),
                "state": "edited" if ok else "review", "request": c.request if ok else False,
                "note": note, "decided_at": fields.Datetime.now() if ok else False,
            })
            emp._notify(_("قبول طلب تعديل مدة الاستراحة"),
                        _("مرحبًا %(n)s، قبلت الإدارة طلبك: مدة استراحتك يوم %(d)s صارت %(m)s دقيقة.%(x)s إدارة نافيتا",
                          n=emp.name, d=c.day, m=c.requested_minutes, x=extra))
        else:
            return "bad_decision"
        return None

    # buttons on the backend form
    def _button(self, decision):
        for c in self:
            err = c._decide(decision, c.note)
            if err:
                raise UserError(self.env["navitta.api"]._err_text(err))

    def action_forgive(self):
        self._button("forgive")

    def action_reject(self):
        self._button("reject")

    def action_accept_edit(self):
        self._button("edit")

    @api.model
    def _cron_late_check(self):
        """Every few minutes during the shift: an employee with no check-in after the grace gets a late case."""
        from .common import app_minute, app_today
        company = self.env.company
        start, end = hours_to_min(company.navitta_shift_start), hours_to_min(company.navitta_shift_end)
        day, m = app_today(self.env), app_minute(self.env)
        if m <= start + company.navitta_grace or m >= end:
            return 0
        n = 0
        Att = self.env["navitta.attendance"].sudo()
        for e in self.env["navitta.employee"].sudo().search([("active", "=", True)]):
            if e._is_off(day) or Att.search_count([("employee_id", "=", e.id), ("day", "=", day)], limit=1):
                continue
            if self.sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("kind", "=", "late")], limit=1):
                continue
            self.sudo().create({"employee_id": e.id, "day": day, "kind": "late", "minutes": m - start})
            e._notify(_("تنبيه: لم يُسجَّل حضورك"),
                      _("مرحبًا %(n)s، دوامك يبدأ الساعة %(s)s ولم يُسجَّل حضورك حتى %(t)s. سجلي حضورك واذكري سبب التأخير من «تنبيهاتي». إدارة نافيتا",
                        n=e.name, s=fmt_time(start), t=fmt_time(m)))
            n += 1
        return n
