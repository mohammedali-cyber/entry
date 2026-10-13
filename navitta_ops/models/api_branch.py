"""Server API for the branch device screen.

Every method checks that the caller is a branch-device user and that the employee / record belongs to
that user's branch, then works with sudo(). Times always come from the server clock. Branch-device users
have read-only access rights, so the screen can't change records except through these methods.
"""
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .common import (EXCHANGE_REASONS, LEAVE_TYPES, LOST_REASONS, OUT_OF_STOCK, TOPICS, SUPPLIES,
                     EXPENSE_CATEGORIES, GOODS_REASONS, EMERGENCIES, SHELF, app_minute, app_today, fmt_time,
                     hours_to_min, iso, label)

PHONE_RE = re.compile(r"^(05\d{8}|9665\d{8})$")

ERRORS = {
    "wrong_pin": "الرمز غير صحيح",
    "locked": "محاولات كثيرة خاطئة، حاولي بعد قليل",
    "no_pin": "لم تحدد الإدارة رمزك بعد",
    "forbidden": "ليس لديك صلاحية",
    "already_in": "تم تسجيل الحضور مسبقًا اليوم",
    "not_in": "لا يوجد تسجيل حضور اليوم",
    "reply_required": "اذكري سبب التأخير أو تجاوز الاستراحة أولًا من «تنبيهاتي»",
    "on_break": "أنهي الاستراحة أولًا",
    "open_visits": "أوقفي واحفظي الزيارات المفتوحة أولًا",
    "reason_required": "اكتبي السبب",
    "bad_minutes": "المدة غير صحيحة",
    "overlap": "عندك إجازة أو طلب في نفس الفترة",
    "bad_dates": "تأكدي من التواريخ",
    "no_chances": "استُخدمت فرص المسامحة لهذا الشهر",
    "already_off": "هذا اليوم أصلًا إجازة لها",
    "not_found": "الطلب غير موجود أو تم التعامل معه",
    "body_required": "اكتبي الرسالة",
    "wrong_pw": "كلمة السر غير صحيحة",
    "no_target_pw": "الإدارة ما حددت كلمة السر بعد",
    "pw_short": "كلمة السر لازم 4 خانات أو أكثر",
    "bad_amount": "المبلغ غير صحيح",
    "month_over": "هذا الشهر انتهى",
    "bad_qty": "الكمية غير صحيحة",
    "model_required": "اكتبي رقم الموديل",
    "bad_file": "الصورة غير صالحة، صوريها مرة ثانية",
    "expired": "انتهى الكود، اطلبي كود جديد من جهاز الفرع",
    "no_employee": "الموظفة غير موجودة",
    "note_required": "اشرحي وش تبي العميلة بالضبط",
    "bad_phone": "رقم الجوال غير صحيح، اكتبيه مثل 05XXXXXXXX",
    "color_required": "اختاري اللون المطلوب",
    "size_required": "اختاري المقاس المطلوب",
    "outcome_required": "اختاري نتيجة الزيارة",
    "not_checked_in": "سجلي حضورك أولًا",
    "break_used": "استُخدمت الاستراحة اليوم",
    "pending_edit": "فيه طلب تعديل بانتظار الإدارة",
    "visit_running": "اضغطي «خرجت العميلة» أولًا",
    "name_required": "اختاري اسم الموظفة اللي تكتب التقرير",
    "bad_exchange": "استبدال: اكتبي السبب بالتفصيل ومن باعت ومن استبدلت",
    "bad_lost": "المبيعات الضائعة: اكتبي اللون أو المقاس",
    "not_edit": "هذا ليس طلب تعديل مدة",
    "bad_decision": "قرار غير معروف",
    "pick_cover": "اختاري الزميلة اللي بتغطي الفرع",
}


class NavittaApi(models.AbstractModel):
    _name = "navitta.api"
    _description = "Navitta screens API"

    # ------------------------------------------------------------------ helpers
    @api.model
    def _err_text(self, code):
        return _(ERRORS.get(code, code))

    @api.model
    def _err(self, code):
        return {"error": code, "message": self._err_text(code)}

    @api.model
    def _is_device(self):
        user = self.env.user
        return bool(user.navitta_branch_id) and user.has_group("navitta_ops.group_navitta_branch")

    @api.model
    def _branch(self):
        """The branch the screen works on: a device account's own branch (always), or for management the branch
        picked on the screen (context key navitta_branch)."""
        if self._is_device():
            return self.env.user.navitta_branch_id.sudo()
        if self.env.user.has_group("navitta_ops.group_navitta_admin"):
            b = self.env["navitta.branch"].sudo().browse(int(self.env.context.get("navitta_branch") or 0)).exists()
            if b:
                return b
            raise AccessError(_("اختر الفرع أولًا"))
        raise AccessError(_("هذه الشاشة لأجهزة الفروع فقط. اربط حساب الجهاز بفرع من الإعدادات."))

    @api.model
    def branch_access(self):
        """How the branch screen opens: a device account gets its branch; management gets the branch list to pick from."""
        if self._is_device():
            b = self.env.user.navitta_branch_id
            return {"device": True, "branch": {"id": b.id, "name": b.name}}
        if self.env.user.has_group("navitta_ops.group_navitta_admin"):
            return {"device": False, "branches": [{"id": b.id, "name": b.name} for b in self.env["navitta.branch"].sudo().search([])]}
        return self._err("forbidden")

    @api.model
    def _emp(self, branch, emp_id):
        e = self.env["navitta.employee"].sudo().browse(int(emp_id or 0)).exists()
        if not e or e.branch_id != branch or not e.active:
            raise AccessError(_("الموظفة ليست من هذا الفرع"))
        return e

    @api.model
    def _rec(self, model, rec_id, branch, field="branch_id"):
        r = self.env[model].sudo().browse(int(rec_id or 0)).exists()
        if not r or r[field] != branch:
            raise AccessError(_("السجل ليس من هذا الفرع"))
        return r

    @api.model
    def _auth(self, emp_id, pin):
        """(employee, error) — the PIN of an employee of the caller's branch."""
        b = self._branch()
        e = self._emp(b, emp_id)
        return e, e._check_pin(str(pin or ""))

    @api.model
    def _settings(self):
        c = self.env.company
        return {"shift_start": hours_to_min(c.navitta_shift_start), "shift_end": hours_to_min(c.navitta_shift_end),
                "grace": c.navitta_grace, "break_max": c.navitta_break_max, "chances": c.navitta_chances,
                "min_roll_invoice": c.navitta_min_roll_invoice, "min_roll_card": c.navitta_min_roll_card,
                "min_bags": c.navitta_min_bags, "tz": c.navitta_tz or "Asia/Riyadh", "vat_rate": c.navitta_vat_rate,
                "checkin_photo": c.navitta_checkin_photo}

    @api.model
    def _visit_dict(self, v):
        return {"id": v.id, "employee_id": v.employee_id.id, "branch_id": v.branch_id.id, "day": fields.Date.to_string(v.day),
                "started_at": iso(v.started_at), "ended_at": iso(v.ended_at), "duration_sec": v.duration_sec,
                "saved": v.saved, "outcomes": v._outcome_list(), "other_text": v.other_text or "", "note": v.note or "",
                "lost_model": v.lost_model or "", "lost_color": v.lost_color or "", "lost_size": v.lost_size or ""}

    @api.model
    def _case_dict(self, c):
        return {"id": c.id, "employee_id": c.employee_id.id, "day": fields.Date.to_string(c.day), "kind": c.kind,
                "minutes": c.minutes, "check_in": iso(c.check_in), "break_used": c.break_used,
                "old_break_used": c.old_break_used, "reason": c.reason or "", "request": c.request or "",
                "requested_minutes": c.requested_minutes, "state": c.state, "penalty": c.penalty or "",
                "penalty_occ": c.penalty_occ, "note": c.note or "", "decided_at": iso(c.decided_at)}

    @api.model
    def _today_bits(self, employees, day):
        att = self.env["navitta.attendance"].sudo().search([("employee_id", "in", employees.ids), ("day", "=", day)])
        brk = self.env["navitta.break"].sudo().search([("employee_id", "in", employees.ids), ("day", "=", day)])
        return (
            [{"employee_id": a.employee_id.id, "check_in": iso(a.check_in), "check_out": iso(a.check_out),
              "in_source": a.in_source, "out_source": a.out_source or False,
              "in_photo_id": a.in_photo_id.id or False, "out_photo_id": a.out_photo_id.id or False} for a in att],
            [{"id": b.id, "employee_id": b.employee_id.id, "started_at": iso(b.started_at), "ended_at": iso(b.ended_at)} for b in brk],
        )

    # ------------------------------------------------------------------ counter screen
    @api.model
    def branch_state(self):
        b = self._branch()
        day = app_today(self.env)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id), ("active", "=", True)], order="slot")
        att, brk = self._today_bits(emps, day)
        visits = self.env["navitta.visit"].sudo().search([("branch_id", "=", b.id), ("day", "=", day)], order="started_at desc")
        cases = self.env["navitta.late.case"].sudo().search([("employee_id", "in", emps.ids), ("day", "=", day)])
        covers = self.env["navitta.leave.request"].sudo().search([("employee_id.branch_id", "=", b.id), ("state", "=", "cover")])
        ereq = self.env["navitta.visit.edit.request"].sudo().search([("branch_id", "=", b.id)], limit=50)
        lines = self.env["navitta.daily.report.employee"].sudo().search([("branch_id", "=", b.id), ("day", "=", day)])
        target = self.env["navitta.daily.sales"].sudo().search([("branch_id", "=", b.id), ("day", "=", day)], limit=1).target
        return {
            "server_now": iso(fields.Datetime.now()), "day": fields.Date.to_string(day),
            "branch": {"id": b.id, "name": b.name}, "settings": self._settings(),
            "employees": [{"id": e.id, "slot": e.slot, "name": e.name, "weekly_off": e.weekly_off, "off": e._is_off(day)} for e in emps],
            "attendance": att, "breaks": brk,
            "visits": [self._visit_dict(v) for v in visits],
            "cases": [self._case_dict(c) for c in cases],
            "covers": [{"id": r.id, "cover_employee_id": r.cover_employee_id.id, "requester": r.employee_id.name,
                        "type": label(LEAVE_TYPES, r.type), "from_day": fields.Date.to_string(r.from_day),
                        "to_day": fields.Date.to_string(r.to_day)} for r in covers],
            "edit_requests": [{"visit_id": r.visit_id.id, "state": r.state} for r in ereq],
            "emp_sales": {l.employee_id.id: l.amount for l in lines},
            "day_target": target or 0.0,
        }

    @api.model
    def _punch_photo(self, branch, data):
        """The selfie the branch screen took while the PIN was typed (base64 JPEG). Saved only after the PIN is
        right; a missing or broken picture doesn't block the punch (the attendance is marked «بدون صورة»)."""
        if not data or not isinstance(data, str):
            return None
        photo, err = self.env["navitta.photo"]._from_b64(branch, data, "image/jpeg")
        return None if err else photo

    @api.model
    def check_in(self, emp_id, pin, photo=None):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        day = app_today(self.env)
        Att = self.env["navitta.attendance"].sudo()
        if Att.search_count([("employee_id", "=", e.id), ("day", "=", day)]):
            return self._err("already_in")
        _att, late = Att._register_in(e, fields.Datetime.now(), "ipad", self._punch_photo(e.branch_id, photo))
        return {"ok": True, "late": late}

    @api.model
    def check_out(self, emp_id, pin, photo=None):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        day = app_today(self.env)
        if self.env["navitta.late.case"].sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("state", "=", "reply")]):
            return self._err("reply_required")
        if self.env["navitta.break"].sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("ended_at", "=", False)]):
            return self._err("on_break")
        if self.env["navitta.visit"].sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("saved", "=", False)]):
            return self._err("open_visits")
        att = self.env["navitta.attendance"].sudo().search([("employee_id", "=", e.id), ("day", "=", day), ("check_out", "=", False)], limit=1)
        if not att:
            return self._err("not_in")
        att._register_out(fields.Datetime.now(), "ipad", self._punch_photo(e.branch_id, photo))
        return {"ok": True}

    @api.model
    def undo_checkout(self, emp_id, pw):
        b = self._branch()
        e = self._emp(b, emp_id)
        err = b._check_admin_password(pw)
        if err:
            return self._err(err)
        att = self.env["navitta.attendance"].sudo().search(
            [("employee_id", "=", e.id), ("day", "=", app_today(self.env)), ("check_out", "!=", False)], limit=1)
        if not att:
            return self._err("not_found")
        att.check_out = False
        self.env["navitta.notification"]._notify_admin(
            _("إلغاء انصراف: %s", e.name), _("%s سجلت انصراف بالغلط ورجعت للدوام بكلمة سر الإدارة.", e.name))
        return {"ok": True}

    def _working(self, e, day):
        att = self.env["navitta.attendance"].sudo().search([("employee_id", "=", e.id), ("day", "=", day)], limit=1)
        return bool(att and not att.check_out)

    @api.model
    def break_start(self, emp_id):
        b = self._branch()
        e = self._emp(b, emp_id)
        day = app_today(self.env)
        if not self._working(e, day):
            return self._err("not_checked_in")
        Brk = self.env["navitta.break"].sudo()
        if Brk.search_count([("employee_id", "=", e.id), ("day", "=", day), ("ended_at", "=", False)]):
            return {"ok": True}
        if self.env["navitta.visit"].sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("saved", "=", False)]):
            return self._err("open_visits")
        if Brk._used_minutes(e, day) >= self.env.company.navitta_break_max:
            return self._err("break_used")
        Brk.create({"employee_id": e.id, "day": day, "started_at": fields.Datetime.now()})
        return {"ok": True}

    @api.model
    def break_end(self, break_id):
        b = self._branch()
        brk = self._rec("navitta.break", break_id, b)
        brk._end()
        used = brk._used_minutes(brk.employee_id, brk.day)
        return {"ok": True, "over": max(0, used - self.env.company.navitta_break_max)}

    @api.model
    def visit_start(self, emp_id):
        b = self._branch()
        e = self._emp(b, emp_id)
        day = app_today(self.env)
        if not self._working(e, day):
            return self._err("not_checked_in")
        if self.env["navitta.break"].sudo().search_count([("employee_id", "=", e.id), ("day", "=", day), ("ended_at", "=", False)]):
            return self._err("on_break")
        v = self.env["navitta.visit"].sudo().create({"branch_id": b.id, "employee_id": e.id, "day": day,
                                                    "started_at": fields.Datetime.now()})
        return {"ok": True, "id": v.id}

    @api.model
    def visit_end(self, visit_id):
        v = self._rec("navitta.visit", visit_id, self._branch())
        if not v.ended_at:
            now = fields.Datetime.now()
            v.write({"ended_at": now, "duration_sec": int((now - v.started_at).total_seconds())})
        return {"ok": True}

    @api.model
    def visit_delete(self, visit_id):
        v = self._rec("navitta.visit", visit_id, self._branch())
        if v.saved:
            return self._err("not_found")
        v.unlink()
        return {"ok": True}

    @api.model
    def visit_save(self, visit_id, data):
        """Saves the visit result. data: buy, exch, no, reason, other, note, lost{…}, request."""
        b = self._branch()
        v = self._rec("navitta.visit", visit_id, b)
        if v.saved:
            return self._err("not_found")
        if not v.ended_at:
            return self._err("visit_running")
        data = data or {}
        buy, exch, no = bool(data.get("buy")), bool(data.get("exch")), bool(data.get("no"))
        reason = data.get("reason") or False
        lost = data.get("lost") or {}
        model = (lost.get("model") or "").strip()
        color = (lost.get("color") or "").strip()
        size = (lost.get("size") or "").strip()
        other = (data.get("other") or "").strip()
        phone = re.sub(r"\D", "", lost.get("contact_phone") or "")
        if not (buy or exch or no):
            return self._err("outcome_required")
        if no:
            buy = exch = False
            if reason not in dict(LOST_REASONS):
                return self._err("reason_required")
            if reason == "other" and not other:
                return self._err("reason_required")
            if reason in OUT_OF_STOCK + ("manufacture",) and not model:
                return self._err("model_required")
            if reason == "color_na" and not color:
                return self._err("color_required")
            if reason == "size_na" and not size:
                return self._err("size_required")
            if reason == "manufacture" and not (lost.get("note") or "").strip():
                return self._err("note_required")
            if phone and (reason not in OUT_OF_STOCK or not PHONE_RE.match(phone)):
                return self._err("bad_phone")
            req = self.env["navitta.branch.request"]
            if data.get("request") and reason in OUT_OF_STOCK:
                item_no = (lost.get("item_no") or "").strip()
                req = req.sudo().create({
                    "branch_id": b.id, "kind": "goods", "employee_id": v.employee_id.id,
                    "model": model + (" (%s)" % item_no if item_no else ""), "color": color or False, "size": size or False,
                    "qty": 1, "reason": label(GOODS_REASONS, "asked"), "note": _("من مبيعة ضائعة")})
                self.env["navitta.notification"]._notify_admin(
                    _("طلب جديد من %s", b.name),
                    _("%(w)s طلبت بضاعة: %(m)s (زبونة طلبته)", w=v.employee_id.name, m=" · ".join(x for x in (model, color, size) if x)))
            vals = {
                "visit_id": v.id, "branch_id": b.id, "employee_id": v.employee_id.id, "day": v.day, "reason": reason,
                "item_no": (lost.get("item_no") or "").strip() or False, "model": model or False, "color": color or False,
                "size": size or False, "price": float(lost.get("price") or 0.0), "custom": bool(lost.get("custom")),
                "note": (lost.get("note") or "").strip() or (other if reason == "other" else False) or False,
                "contact_name": (lost.get("contact_name") or "").strip() if phone else False,
                "contact_phone": phone or False,
            }
            Lost = self.env["navitta.lost.sale"].sudo()
            existing = Lost.search([("visit_id", "=", v.id)], limit=1)
            if existing:
                vals["request_id"] = existing.request_id.id or req.id or False
                existing.write(vals)
            else:
                vals["request_id"] = req.id or False
                Lost.create(vals)
        v.write({
            "duration_sec": int((v.ended_at - v.started_at).total_seconds()),
            "bought": buy, "exchanged": exch, "lost_reason": reason if no else False,
            "other_text": other if no and reason == "other" else False,
            "note": (data.get("note") or "").strip() or False,
            "lost_model": model if no and model else False, "lost_color": color if no and color else False,
            "lost_size": size if no and size else False, "saved": True,
        })
        return {"ok": True}

    @api.model
    def visit_edit_request(self, visit_id, new_sec, reason):
        v = self._rec("navitta.visit", visit_id, self._branch())
        new_sec = int(new_sec or 0)
        reason = (reason or "").strip()
        if not v.saved:
            return self._err("not_found")
        if not reason:
            return self._err("reason_required")
        if new_sec <= 0 or new_sec == v.duration_sec:
            return self._err("bad_minutes")
        Req = self.env["navitta.visit.edit.request"].sudo()
        if Req.search_count([("visit_id", "=", v.id), ("state", "=", "pending")]):
            return self._err("pending_edit")
        Req.create({"visit_id": v.id, "employee_id": v.employee_id.id, "old_sec": v.duration_sec or 0,
                    "new_sec": new_sec, "reason": reason})
        return {"ok": True}

    # ------------------------------------------------------------------ employee inbox (PIN)
    @api.model
    def inbox(self, emp_id, pin):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        day = app_today(self.env)
        cases = self.env["navitta.late.case"].sudo().search([("employee_id", "=", e.id), ("day", ">", day - timedelta(days=90))])
        lreqs = self.env["navitta.leave.request"].sudo().search([("employee_id", "=", e.id)])
        covers = self.env["navitta.leave.request"].sudo().search([("cover_employee_id", "=", e.id), ("state", "=", "cover")])
        defs = self.env["navitta.deferred.off"].sudo().search([("employee_id", "=", e.id), "|", ("new_day", "=", False), ("new_day", ">=", day)])
        threads = self.env["navitta.thread"].sudo().search([("employee_id", "=", e.id)])
        res = {
            "employee": {"id": e.id, "name": e.name},
            "mates": [{"id": m.id, "name": m.name} for m in e._mates()],
            "chances": {"used": self.env["navitta.late.case"]._chances_used(e, day), "max": self.env.company.navitta_chances},
            "cases": [self._case_dict(c) for c in cases],
            "leave_requests": [{
                "id": r.id, "type": label(LEAVE_TYPES, r.type), "from_day": fields.Date.to_string(r.from_day),
                "to_day": fields.Date.to_string(r.to_day), "reason": r.reason, "state": r.state,
                "cover_name": r.cover_employee_id.name or "", "cover_note": r.cover_note or "", "note": r.note or ""} for r in lreqs],
            "cover_requests": [{"id": r.id, "type": label(LEAVE_TYPES, r.type), "from_day": fields.Date.to_string(r.from_day),
                                "to_day": fields.Date.to_string(r.to_day), "requester": r.employee_id.name} for r in covers],
            "deferred": [{"id": x.id, "original_day": fields.Date.to_string(x.original_day),
                          "new_day": x.new_day and fields.Date.to_string(x.new_day)} for x in defs],
            "threads": [{"id": t.id, "topic": label(TOPICS, t.topic), "state": t.state,
                         "messages": [{"id": m.id, "sender": m.sender, "body": m.body, "created_at": iso(m.create_date)} for m in t.message_ids]}
                        for t in threads],
            "leave_types": LEAVE_TYPES, "topics": TOPICS,
        }
        threads.write({"emp_read_at": fields.Datetime.now()})
        return res

    @api.model
    def reply_case(self, emp_id, pin, case_id, reason, request="forgive", minutes=None):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        c = self.env["navitta.late.case"].sudo().browse(int(case_id or 0)).exists()
        if not c or c.employee_id != e or c.state != "reply":
            return self._err("not_found")
        reason = (reason or "").strip()
        if not reason:
            return self._err("reason_required")
        minutes = int(minutes) if minutes not in (None, "", False) else None
        if request == "edit" and (c.kind != "break" or minutes is None or minutes < 1 or minutes >= c.break_used):
            return self._err("bad_minutes")
        c.write({"reason": reason, "request": request if c.kind == "break" and request in ("forgive", "edit") else "forgive",
                 "requested_minutes": minutes if request == "edit" else 0, "state": "review"})
        return {"ok": True}

    @api.model
    def leave_request(self, emp_id, pin, type_, from_day, to_day, reason, cover):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        try:
            d0, d1 = fields.Date.to_date(from_day), fields.Date.to_date(to_day)
        except (TypeError, ValueError):
            return self._err("bad_dates")
        if not d0 or not d1 or d1 < d0 or type_ not in dict(LEAVE_TYPES):
            return self._err("bad_dates")
        reason = (reason or "").strip()
        if not reason:
            return self._err("reason_required")
        overlap = [("employee_id", "=", e.id), ("from_day", "<=", d1), ("to_day", ">=", d0)]
        if (self.env["navitta.leave"].sudo().search_count(overlap)
                or self.env["navitta.leave.request"].sudo().search_count(overlap + [("state", "in", ("cover", "pending"))])):
            return self._err("overlap")
        # cover: False (no cover), a colleague's id, or True when she has a single colleague
        mates, mate = e._mates(), self.env["navitta.employee"]
        if cover:
            if cover is True:
                if len(mates) != 1:
                    return self._err("pick_cover")
                mate = mates
            else:
                mate = mates.filtered(lambda m: m.id == int(cover))
                if not mate:
                    return self._err("pick_cover")
        r = self.env["navitta.leave.request"].sudo().create({
            "employee_id": e.id, "type": type_, "from_day": d0, "to_day": d1, "reason": reason,
            "cover_employee_id": mate.id or False, "state": "cover" if mate else "pending"})
        if mate:
            mate._notify(_("طلب تغطية من زميلتك"),
                         _("مرحبًا %(m)s، %(n)s تطلب تغطيتك أثناء إجازتها %(t)s من %(a)s إلى %(b)s. افتحي «تنبيهاتي» برمزك السري للموافقة أو الاعتذار.",
                           m=mate.name, n=e.name, t=label(LEAVE_TYPES, type_), a=d0, b=d1))
        return {"ok": True, "id": r.id, "cover": bool(mate)}

    @api.model
    def cover_respond(self, emp_id, pin, req_id, ok, note=None):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        r = self.env["navitta.leave.request"].sudo().browse(int(req_id or 0)).exists()
        if not r or r.cover_employee_id != e or r.state != "cover":
            return self._err("not_found")
        r.write({"state": "pending" if ok else "cover_no", "cover_note": (note or "").strip() or False})
        r.employee_id._notify(
            _("زميلتك وافقت على التغطية") if ok else _("زميلتك اعتذرت عن التغطية"),
            _("وافقت زميلتك على تغطية الفرع أثناء إجازتك، وطلبك الحين عند الإدارة.") if ok else
            _("اعتذرت زميلتك عن تغطية الفرع أثناء إجازتك. قدّمي طلبًا جديدًا بتاريخ آخر أو بدون تغطية."))
        return {"ok": True}

    @api.model
    def send_message(self, emp_id, pin, topic, body):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        body = (body or "").strip()
        if not body:
            return self._err("body_required")
        now = fields.Datetime.now()
        t = self.env["navitta.thread"].sudo().create({"employee_id": e.id, "topic": topic if topic in dict(TOPICS) else "other",
                                                     "emp_read_at": now, "last_at": now})
        self.env["navitta.message"].sudo().create({"thread_id": t.id, "sender": "emp", "body": body})
        return {"ok": True, "id": t.id}

    @api.model
    def reply_thread(self, emp_id, pin, thread_id, body):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        t = self.env["navitta.thread"].sudo().browse(int(thread_id or 0)).exists()
        if not t or t.employee_id != e or t.state != "open":
            return self._err("not_found")
        body = (body or "").strip()
        if not body:
            return self._err("body_required")
        self.env["navitta.message"].sudo().create({"thread_id": t.id, "sender": "emp", "body": body})
        now = fields.Datetime.now()
        t.write({"last_at": now, "emp_read_at": now})
        return {"ok": True}

    # ------------------------------------------------------------------ targets
    @api.model
    def _target_rows(self, branch):
        day = app_today(self.env)
        m0 = day.replace(day=1)
        m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        rows = self.env["navitta.daily.sales"].sudo().search([("branch_id", "=", branch.id), ("day", ">=", m0), ("day", "<=", m1)])
        log = self.env["navitta.target.log"].sudo().search([("branch_id", "=", branch.id)], limit=1)
        return {
            "day": fields.Date.to_string(day),
            "rows": [{"day": fields.Date.to_string(r.day), "target": r.target, "sales": r.sales if r.has_sales else None,
                      "invoices": r.invoices} for r in rows],
            "log": {"by_admin": log.by_admin, "changed_at": iso(log.create_date), "scope": log.scope, "ref": log.ref,
                    "old_amount": log.old_amount, "new_amount": log.new_amount} if log else False,
            "shift_end": hours_to_min(self.env.company.navitta_shift_end),
        }

    @api.model
    def target_bar(self):
        return self._target_rows(self._branch())

    @api.model
    def target_check(self, pw):
        err = self._branch()._check_admin_password(pw)
        return self._err(err) if err else {"ok": True}

    @api.model
    def target_set_days(self, pw, from_day, days, amount):
        b = self._branch()
        err = b._check_admin_password(pw)
        if err:
            return self._err(err)
        err = self.env["navitta.daily.sales"]._set_target_days(b, fields.Date.to_date(from_day), int(days or 1), float(amount or 0), False)
        return self._err(err) if err else {"ok": True}

    @api.model
    def target_set_month(self, pw, month, amount):
        b = self._branch()
        err = b._check_admin_password(pw)
        if err:
            return self._err(err)
        err, per, n = self.env["navitta.daily.sales"]._set_target_month(b, fields.Date.to_date(month), float(amount or 0), False)
        return self._err(err) if err else {"ok": True, "per_day": per, "days": n}

    # ------------------------------------------------------------------ requests, supplies, expenses, waiting customers
    @api.model
    def requests_state(self):
        b = self._branch()
        day = app_today(self.env)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id), ("active", "=", True)], order="slot")
        reqs = self.env["navitta.branch.request"].sudo().search([("branch_id", "=", b.id)], limit=40)
        exps = self.env["navitta.expense"].sudo().search([("branch_id", "=", b.id), ("day", ">=", day.replace(day=1))])
        wait = self.env["navitta.lost.sale"].sudo().search([("branch_id", "=", b.id), ("contact_phone", "!=", False)], limit=80)
        cash = b._cash_summary()[b.id]
        return {
            "day": fields.Date.to_string(day), "settings": self._settings(),
            "employees": [{"id": e.id, "slot": e.slot, "name": e.name} for e in emps],
            "requests": [self._request_dict(r) for r in reqs],
            "stock": b._stock_summary()[b.id], "cash": cash,
            "expenses": [self._expense_dict(x) for x in exps],
            "waiting": [self._lost_dict(w) for w in wait],
            "choices": {"supplies": SUPPLIES, "goods_reasons": GOODS_REASONS, "expense_categories": EXPENSE_CATEGORIES},
        }

    @api.model
    def _request_dict(self, r):
        return {"id": r.id, "branch_id": r.branch_id.id, "kind": r.kind, "supply": r.supply or "", "model": r.model or "",
                "color": r.color or "", "size": r.size or "", "qty": r.qty, "reason": r.reason or "", "note": r.note or "",
                "urgent": r.urgent, "photo_id": r.photo_id.id or False, "state": r.state, "admin_note": r.admin_note or "",
                "employee": r.employee_id.name or "", "employee_id": r.employee_id.id or False,
                "received_by": r.received_by.name or "", "created_at": iso(r.create_date), "what": r._what()}

    @api.model
    def _expense_dict(self, x):
        return {"id": x.id, "branch_id": x.branch_id.id, "day": fields.Date.to_string(x.day), "amount": x.amount,
                "category": x.category, "category_label": label(EXPENSE_CATEGORIES, x.category), "details": x.details,
                "receipt_id": x.receipt_id.id or False, "state": x.state, "admin_note": x.admin_note or "",
                "employee": x.employee_id.name or "", "employee_id": x.employee_id.id or False}

    @api.model
    def _lost_dict(self, w):
        return {"id": w.id, "branch_id": w.branch_id.id, "employee": w.employee_id.name or "", "employee_id": w.employee_id.id or False,
                "day": fields.Date.to_string(w.day), "reason": w.reason, "item_no": w.item_no or "", "model": w.model or "",
                "color": w.color or "", "size": w.size or "", "price": w.price or 0.0, "custom": w.custom, "note": w.note or "",
                "contact_name": w.contact_name or "", "contact_phone": w.contact_phone or "",
                "contacted_at": iso(w.contacted_at), "contacted_by": w.contacted_by or "", "created_at": iso(w.create_date)}

    @api.model
    def _photo(self, branch, photo_id):
        if not photo_id:
            return self.env["navitta.photo"]
        p = self.env["navitta.photo"].sudo().browse(int(photo_id)).exists()
        if not p or p.branch_id != branch:
            raise AccessError(_("الصورة ليست من هذا الفرع"))
        return p

    @api.model
    def create_request(self, emp_id, pin, vals):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        vals = vals or {}
        kind, supply = vals.get("kind"), vals.get("supply") or False
        qty = int(vals.get("qty") or 0)
        model = (vals.get("model") or "").strip()
        if qty < 1:
            return self._err("bad_qty")
        if kind == "goods" and not model:
            return self._err("model_required")
        if kind == "supply" and supply not in dict(SUPPLIES):
            return self._err("bad_qty")
        if kind not in ("goods", "supply"):
            return self._err("not_found")
        if kind == "supply" and supply == "other" and not model:
            return self._err("model_required")
        b = e.branch_id
        photo = self._photo(b, vals.get("photo_id"))
        reason = vals.get("reason")
        r = self.env["navitta.branch.request"].sudo().create({
            "branch_id": b.id, "kind": kind, "supply": supply if kind == "supply" else False, "employee_id": e.id,
            "model": model or False, "color": (vals.get("color") or "").strip() or False, "size": (vals.get("size") or "").strip() or False,
            "qty": qty, "reason": label(GOODS_REASONS, reason) if reason else False, "note": (vals.get("note") or "").strip() or False,
            "urgent": bool(vals.get("urgent")), "photo_id": photo.id or False})
        self.env["navitta.notification"]._notify_admin(
            _("طلب جديد من %s", b.name),
            _("%(n)s طلبت %(k)s: %(w)s%(u)s", n=e.name, k=_("بضاعة") if kind == "goods" else _("مستلزمات"), w=r._what(),
              u=_(" (عاجل)") if r.urgent else ""))
        return {"ok": True, "id": r.id}

    @api.model
    def request_received(self, emp_id, pin, req_id):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        r = self.env["navitta.branch.request"].sudo().browse(int(req_id or 0)).exists()
        if not r:
            return self._err("not_found")
        err = r._receive(e)
        return self._err(err) if err else {"ok": True}

    @api.model
    def create_expense(self, emp_id, pin, amount, category, details, receipt_id=None):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return self._err("bad_amount")
        details = (details or "").strip()
        if not details:
            return self._err("reason_required")
        if category not in dict(EXPENSE_CATEGORIES):
            category = "other"
        b = e.branch_id
        receipt = self._photo(b, receipt_id)
        x = self.env["navitta.expense"].sudo().create({
            "branch_id": b.id, "day": app_today(self.env), "employee_id": e.id, "amount": amount,
            "category": category, "details": details, "receipt_id": receipt.id or False})
        self.env["navitta.notification"]._notify_admin(
            _("مصروف جديد بانتظار اعتمادك"),
            "%s: %s ر.س · %s · %s%s" % (b.name, amount, label(EXPENSE_CATEGORIES, category), details,
                                         "" if receipt else _(" (⚠️ بدون إيصال)")))
        return {"ok": True, "id": x.id}

    @api.model
    def lost_contacted(self, lost_id, by=None):
        w = self._rec("navitta.lost.sale", lost_id, self._branch())
        err = w._mark_contacted(by)
        return self._err(err) if err else {"ok": True}

    # ------------------------------------------------------------------ photos
    @api.model
    def photo_upload(self, data, mime="image/jpeg"):
        photo, err = self.env["navitta.photo"]._from_b64(self._branch(), data, mime)
        return self._err(err) if err else {"id": photo.id}

    @api.model
    def token_new(self):
        t = self.env["navitta.upload.token"]._new(self._branch())
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return {"token": t.token, "expires_at": iso(t.expires_at), "url": "%s/navitta/r?t=%s" % (base, t.token)}

    @api.model
    def token_poll(self, token):
        b = self._branch()
        t = self.env["navitta.upload.token"].sudo().search([("token", "=", token or ""), ("branch_id", "=", b.id)], limit=1)
        if not t:
            return {"photo_id": False, "expired": True}
        return {"photo_id": t.photo_id.id or False, "expired": t.expires_at < fields.Datetime.now()}

    # ------------------------------------------------------------------ daily report
    @api.model
    def _report_dict(self, r):
        return {
            "employee_name": r.employee_name, "sales": r.sales, "invoices": r.invoices, "items": r.items,
            "cash": r.cash, "card": r.card, "deposit": r.deposit,
            "exch": [{"reason": x.reason, "detail": x.detail, "soldBy": x.sold_by, "by": x.exchanged_by} for x in r.exchange_ids],
            "emp": [{"employee_id": l.employee_id.id, "amount": l.amount, "invoices": l.invoices, "items": l.items,
                     "exchanged": l.exchanged} for l in r.employee_line_ids],
            "used": {"roll_invoice": r.used_roll_invoice, "roll_card": r.used_roll_card, "bags": r.used_bags},
            "visitors": r.visitors, "nonBuy": r.non_buy, "noBuy": r.nobuy_reasons or {},
            "lost": [{"model": l.model or "", "color": l.color or "", "size": l.size or ""} for l in r.lost_ids],
            "type": r.product_type or "", "color": r.top_color or "", "size": r.top_size or "",
            "tried_model": r.tried_model or "", "tried_reason": r.tried_reason or "", "sat": r.sat or 4,
            "phrase": r.phrase or "", "tone": r.tone or "positive", "complaint": r.complaint, "complaint_text": r.complaint_text or "",
            "influ": r.influ, "influ_text": r.influ_text or "", "short": r.short, "short_text": r.short_text or "",
            "shelf": r.shelf or "good", "damage": r.damage, "damage_text": r.damage_text or "",
            "emergency": r.emergency or "none", "improve": r.improve or "", "suggest": r.suggest or "",
        }

    @api.model
    def report_get(self):
        b = self._branch()
        day = app_today(self.env)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id), ("active", "=", True)], order="slot")
        rep = self.env["navitta.daily.report"].sudo().search([("branch_id", "=", b.id), ("day", "=", day)], limit=1)
        visits = self.env["navitta.visit"].sudo().search([("branch_id", "=", b.id), ("day", "=", day), ("saved", "=", True)])
        buys = len(visits.filtered("bought"))
        return {
            "day": fields.Date.to_string(day),
            "employees": [{"id": e.id, "slot": e.slot, "name": e.name} for e in emps],
            "report": self._report_dict(rep) if rep else False,
            "from_visits": {
                "visitors": len(visits), "invoices": buys, "nonBuy": len(visits) - buys,
                "lost": [{"model": v.lost_model or "", "color": v.lost_color or "", "size": v.lost_size or ""}
                         for v in visits if v.lost_color or v.lost_size],
            },
            "choices": {"exchange_reasons": EXCHANGE_REASONS, "lost_reasons": [r for r in LOST_REASONS if r[0] not in ("manufacture",)],
                        "shelf": SHELF, "emergencies": EMERGENCIES},
        }

    @api.model
    def report_save(self, data):
        b = self._branch()
        day = app_today(self.env)
        data = data or {}
        name = (data.get("employee_name") or "").strip()
        if not name:
            return self._err("name_required")
        exch = data.get("exch") or []
        if any(not (str(x.get("detail") or "").strip() and x.get("soldBy") and x.get("by")) for x in exch):
            return self._err("bad_exchange")
        lost = data.get("lost") or []
        if any(not (x.get("color") or x.get("size")) for x in lost):
            return self._err("bad_lost")
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id)])

        def num(k, f=float):
            try:
                return f(data.get(k) or 0)
            except (TypeError, ValueError):
                return f(0)

        used = data.get("used") or {}
        emp_by_name = {e.name: e for e in emps}
        vals = {
            "branch_id": b.id, "day": day, "employee_name": name, "employee_id": emp_by_name.get(name, emps[:0]).id or False,
            "sales": num("sales"), "invoices": num("invoices", int), "items": num("items", int),
            "cash": num("cash"), "card": num("card"), "deposit": num("deposit"),
            "used_roll_invoice": int(used.get("roll_invoice") or 0), "used_roll_card": int(used.get("roll_card") or 0),
            "used_bags": int(used.get("bags") or 0),
            "visitors": num("visitors", int), "non_buy": num("nonBuy", int),
            "nobuy_reasons": {k: int(v or 0) for k, v in (data.get("noBuy") or {}).items() if k in dict(LOST_REASONS)},
            "product_type": data.get("type") or False, "top_color": data.get("color") or False, "top_size": data.get("size") or False,
            "tried_model": data.get("tried_model") or False, "tried_reason": data.get("tried_reason") or False,
            "sat": min(5, max(1, num("sat", int) or 4)), "phrase": data.get("phrase") or False,
            "tone": data.get("tone") if data.get("tone") in ("positive", "negative") else False,
            "complaint": bool(data.get("complaint")), "complaint_text": data.get("complaint_text") or False,
            "influ": bool(data.get("influ")), "influ_text": data.get("influ_text") or False,
            "short": bool(data.get("short")), "short_text": data.get("short_text") or False,
            "shelf": data.get("shelf") if data.get("shelf") in dict(SHELF) else False,
            "damage": bool(data.get("damage")), "damage_text": data.get("damage_text") or False,
            "emergency": data.get("emergency") if data.get("emergency") in dict(EMERGENCIES) else "none",
            "improve": data.get("improve") or False, "suggest": data.get("suggest") or False,
            "exchange_ids": [(5, 0, 0)] + [(0, 0, {
                "reason": x.get("reason") if x.get("reason") in dict(EXCHANGE_REASONS) else "size",
                "detail": str(x["detail"]).strip(), "sold_by": x["soldBy"], "exchanged_by": x["by"]}) for x in exch],
            "lost_ids": [(5, 0, 0)] + [(0, 0, {"model": x.get("model") or False, "color": x.get("color") or False,
                                                "size": x.get("size") or False}) for x in lost],
            "employee_line_ids": [(5, 0, 0)] + [(0, 0, {
                "employee_id": int(x["employee_id"]), "amount": float(x.get("amount") or 0), "invoices": int(x.get("invoices") or 0),
                "items": int(x.get("items") or 0), "exchanged": int(x.get("exchanged") or 0)})
                for x in (data.get("emp") or []) if int(x.get("employee_id") or 0) in emps.ids],
        }
        Rep = self.env["navitta.daily.report"].sudo()
        rep = Rep.search([("branch_id", "=", b.id), ("day", "=", day)], limit=1)
        if rep:
            rep.write(vals)
        else:
            Rep.create(vals)
        return {"ok": True}

    # ------------------------------------------------------------------ models file
    @api.model
    def catalog_find(self, q):
        self._branch_or_admin()
        return self.env["navitta.catalog.item"]._find(q)

    @api.model
    def catalog_waiting(self, item_nos):
        dom = [("item_no", "in", list(item_nos or [])[:20]), ("waiting", "=", True)]
        if not self.env.user.has_group("navitta_ops.group_navitta_admin"):
            dom.append(("branch_id", "=", self._branch().id))
        rows = self.env["navitta.lost.sale"].sudo().search(dom, order="create_date", limit=20)
        return [self._lost_dict(w) for w in rows]

    @api.model
    def _branch_or_admin(self):
        if self.env.user.has_group("navitta_ops.group_navitta_admin"):
            return None
        return self._branch()
