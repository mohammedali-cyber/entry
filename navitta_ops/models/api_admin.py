"""Server API for the management screens (dashboard, sales, requests, lost sales, day log) and the nightly summary."""
import base64
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .common import (LEAVE_TYPES, LOST_REASONS, TOPICS, app_today, company_tz, hours_to_min, iso, label)

_logger = logging.getLogger(__name__)


class NavittaApiAdmin(models.AbstractModel):
    _inherit = "navitta.api"

    @api.model
    def _admin(self):
        if not self.env.user.has_group("navitta_ops.group_navitta_admin"):
            raise AccessError(_("هذه الشاشة لإدارة الفروع فقط"))

    @api.model
    def _d(self, s):
        return fields.Date.to_date(s) if s else app_today(self.env)

    @api.model
    def _branches(self):
        return [{"id": b.id, "name": b.name, "code": b.code} for b in self.env["navitta.branch"].sudo().search([])]

    @api.model
    def _employees(self):
        return [{"id": e.id, "branch_id": e.branch_id.id, "slot": e.slot, "name": e.name, "email": e.email or "",
                 "weekly_off": e.weekly_off, "has_pin": e.has_pin}
                for e in self.env["navitta.employee"].sudo().search([("active", "=", True)])]

    # ------------------------------------------------------------------ dashboard
    @api.model
    def admin_dashboard(self, period=7):
        self._admin()
        day = app_today(self.env)
        d0 = day - timedelta(days=max(1, int(period or 7)) - 1)
        S = self.env
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        reps = S["navitta.daily.report"].sudo().search([("day", ">=", d0), ("day", "<=", day)])
        visits = S["navitta.visit"].sudo().search([("day", ">=", d0), ("day", "<=", day), ("saved", "=", True)])
        att, brk = self._today_bits(emps, day)
        cases = S["navitta.late.case"].sudo().search([("state", "in", ("reply", "review"))])
        month_cases = S["navitta.late.case"].sudo().search([("day", ">=", day.replace(day=1))])
        lreq = S["navitta.leave.request"].sudo().search([], limit=30)
        ereq = S["navitta.visit.edit.request"].sudo().search([("state", "=", "pending")])
        threads = S["navitta.thread"].sudo().search([], limit=30)
        notif = S["navitta.notification"].sudo().search([], limit=15)
        leaves = S["navitta.leave"].sudo().search([("from_day", "<=", day), ("to_day", ">=", day)])
        best = None
        for r in S["navitta.daily.sales"].sudo().search([("day", "=", day - timedelta(days=1)), ("target", ">", 0), ("has_sales", "=", True)]):
            p = round(r.sales * 100.0 / r.target)
            if not best or p > best["p"]:
                best = {"name": r.branch_id.name, "p": p}
        return {
            "best": best,
            "day": fields.Date.to_string(day), "settings": self._settings(), "branches": self._branches(),
            "employees": self._employees(), "off": {e.id: e._is_off(day) for e in emps},
            "reports": [{"branch_id": r.branch_id.id, "day": fields.Date.to_string(r.day), "employee_name": r.employee_name,
                         "sales": r.sales, "invoices": r.invoices, "sat": r.sat} for r in reps],
            "visits": [{"branch_id": v.branch_id.id, "employee_id": v.employee_id.id, "day": fields.Date.to_string(v.day),
                        "duration_sec": v.duration_sec, "bought": v.bought} for v in visits],
            "attendance": att, "breaks": brk,
            "cases": [self._case_dict(c) for c in cases],
            "month_cases": [{"employee_id": c.employee_id.id, "state": c.state, "kind": c.kind} for c in month_cases],
            "leave_requests": [{
                "id": r.id, "employee_id": r.employee_id.id, "type": label(LEAVE_TYPES, r.type),
                "from_day": fields.Date.to_string(r.from_day), "to_day": fields.Date.to_string(r.to_day), "reason": r.reason,
                "state": r.state, "cover_employee_id": r.cover_employee_id.id or False, "cover_note": r.cover_note or "",
                "decided_at": iso(r.decided_at)} for r in lreq],
            "edit_requests": [{"id": r.id, "employee_id": r.employee_id.id, "old_sec": r.old_sec, "new_sec": r.new_sec,
                               "reason": r.reason, "started_at": iso(r.visit_id.started_at)} for r in ereq],
            "threads": [{"id": t.id, "employee_id": t.employee_id.id, "topic": label(TOPICS, t.topic), "state": t.state,
                         "unread": t.unread, "last_at": iso(t.last_at),
                         "messages": [{"id": m.id, "sender": m.sender, "body": m.body, "created_at": iso(m.create_date)}
                                      for m in t.message_ids]} for t in threads],
            "notifications": [{"id": n.id, "subject": n.subject, "employee_id": n.employee_id.id or False,
                               "to_email": n.to_email or "", "state": n.state, "error": n.failure_reason or "",
                               "created_at": iso(n.create_date)} for n in notif],
            "leaves": [{"employee_id": l.employee_id.id} for l in leaves],
        }

    @api.model
    def admin_decide_case(self, case_id, decision, note=None):
        self._admin()
        c = self.env["navitta.late.case"].sudo().browse(int(case_id)).exists()
        err = c._decide(decision, note or None) if c else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_decide_leave(self, req_id, approve, note=None):
        self._admin()
        r = self.env["navitta.leave.request"].sudo().browse(int(req_id)).exists()
        err = r._decide(bool(approve), note or None) if r else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_decide_visit_edit(self, req_id, approve, reply=None):
        self._admin()
        r = self.env["navitta.visit.edit.request"].sudo().browse(int(req_id)).exists()
        err = r._decide(bool(approve), reply or None) if r else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_reply_thread(self, thread_id, body):
        self._admin()
        t = self.env["navitta.thread"].sudo().browse(int(thread_id)).exists()
        err = t._admin_reply(body) if t else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_thread_read(self, thread_id):
        self._admin()
        self.env["navitta.thread"].sudo().browse(int(thread_id)).exists().admin_read_at = fields.Datetime.now()
        return {"ok": True}

    @api.model
    def admin_thread_toggle(self, thread_id):
        self._admin()
        t = self.env["navitta.thread"].sudo().browse(int(thread_id)).exists()
        if t:
            t.state = "closed" if t.state == "open" else "open"
        return {"ok": True}

    # ------------------------------------------------------------------ sales and targets
    @api.model
    def employee_summary(self, date_from, date_to, branch_id=False):
        self._admin()
        d0, d1 = self._d(date_from), self._d(date_to)
        self.env["navitta.daily.report.employee"].flush_model()
        self.env["navitta.daily.sales"].flush_model()
        cr = self.env.cr
        cr.execute("""
            SELECT l.branch_id, l.employee_id, e.name, e.slot, SUM(l.amount), SUM(l.invoices), SUM(l.items), SUM(l.exchanged),
                   COALESCE(SUM(l.amount) FILTER (WHERE l.invoices > 0), 0)
              FROM navitta_daily_report_employee l JOIN navitta_employee e ON e.id = l.employee_id
             WHERE l.day BETWEEN %s AND %s AND (%s IS NULL OR l.branch_id = %s)
             GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC""", [d0, d1, branch_id or None, branch_id or None])
        rows = [{"branch_id": b, "employee_id": e, "name": n, "slot": s, "amount": a or 0, "invoices": i or 0, "items": it or 0,
                 "exchanged": x or 0, "amount_inv": ai or 0} for b, e, n, s, a, i, it, x, ai in cr.fetchall()]
        cr.execute("""SELECT branch_id, SUM(target) FROM navitta_daily_sales
                       WHERE day BETWEEN %s AND %s AND day <= %s GROUP BY 1""", [d0, d1, app_today(self.env)])
        targets = dict(cr.fetchall())
        return {"rows": rows, "targets": targets}

    @api.model
    def admin_sales(self, branch_id, date_from, date_to, year, m_from, m_to):
        self._admin()
        d0, d1, m0, m1 = self._d(date_from), self._d(date_to), self._d(m_from), self._d(m_to)
        today = app_today(self.env)
        self.env["navitta.daily.sales"].flush_model()
        cr = self.env.cr
        cr.execute("""
            SELECT branch_id, SUM(target), COALESCE(SUM(target) FILTER (WHERE day <= %s), 0), COALESCE(SUM(sales), 0),
                   COALESCE(SUM(invoices), 0), COALESCE(SUM(items), 0), COALESCE(SUM(returns), 0)
              FROM navitta_daily_sales WHERE day BETWEEN %s AND %s GROUP BY branch_id""", [today, d0, d1])
        summary = [{"branch_id": b, "target": t or 0, "target_to_date": ttd or 0, "sales": s or 0, "invoices": i, "items": it,
                    "returns": r} for b, t, ttd, s, i, it, r in cr.fetchall()]
        cr.execute("""
            SELECT EXTRACT(month FROM day)::int, SUM(target), COALESCE(SUM(target) FILTER (WHERE day <= %s), 0), COALESCE(SUM(sales), 0)
              FROM navitta_daily_sales WHERE EXTRACT(year FROM day) = %s AND (%s IS NULL OR branch_id = %s)
             GROUP BY 1 ORDER BY 1""", [today, int(year), branch_id or None, branch_id or None])
        monthly = [{"month": m, "target": t or 0, "target_to_date": ttd or 0, "sales": s or 0} for m, t, ttd, s in cr.fetchall()]
        dom = [("day", ">=", m0), ("day", "<=", m1)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        daily = [{"day": fields.Date.to_string(r.day), "branch_id": r.branch_id.id, "target": r.target,
                  "sales": r.sales if r.has_sales else None, "invoices": r.invoices, "items": r.items, "returns": r.returns,
                  "cash": r.cash, "card": r.card, "closed_by": r.closed_by or ""}
                 for r in self.env["navitta.daily.sales"].sudo().search(dom, order="day, branch_id")]
        return {"branches": self._branches(), "summary": summary, "monthly": monthly, "daily": daily,
                "employees": self.employee_summary(date_from, date_to, branch_id), "settings": self._settings(),
                "day": fields.Date.to_string(today)}

    @api.model
    def admin_target_rows(self, branch_id):
        self._admin()
        return self._target_rows(self.env["navitta.branch"].sudo().browse(int(branch_id)))

    @api.model
    def admin_target_set_days(self, branch_id, from_day, days, amount):
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(int(branch_id))
        err = self.env["navitta.daily.sales"]._set_target_days(b, fields.Date.to_date(from_day), int(days or 1), float(amount or 0), True)
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_target_set_month(self, branch_id, month, amount):
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(int(branch_id))
        err, per, n = self.env["navitta.daily.sales"]._set_target_month(b, fields.Date.to_date(month), float(amount or 0), True)
        return self._err(err) if err else {"ok": True, "per_day": per, "days": n}

    # ------------------------------------------------------------------ requests, supplies, expenses
    @api.model
    def admin_requests(self):
        self._admin()
        day = app_today(self.env)
        m0 = day.replace(day=1)
        prev0 = (m0 - timedelta(days=1)).replace(day=1)
        branches = self.env["navitta.branch"].sudo().search([])
        reqs = self.env["navitta.branch.request"].sudo().search([], limit=200)
        exps = self.env["navitta.expense"].sudo().search([("day", ">=", prev0)])
        return {
            "day": fields.Date.to_string(day), "month_start": fields.Date.to_string(m0), "settings": self._settings(),
            "branches": self._branches(), "employees": self._employees(),
            "requests": [self._request_dict(r) for r in reqs], "expenses": [self._expense_dict(x) for x in exps],
            "stock": {str(k): v for k, v in branches._stock_summary().items()},
            "cash": {str(k): v for k, v in branches._cash_summary().items()},
        }

    @api.model
    def admin_request_set(self, req_id, state, note=None):
        self._admin()
        r = self.env["navitta.branch.request"].sudo().browse(int(req_id)).exists()
        err = r._set_state(state, note) if r else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_expense_decide(self, exp_id, ok, note=None):
        self._admin()
        x = self.env["navitta.expense"].sudo().browse(int(exp_id)).exists()
        err = x._decide(bool(ok), note) if x else "not_found"
        return self._err(err) if err else {"ok": True}

    # ------------------------------------------------------------------ lost sales
    @api.model
    def admin_lost(self, branch_id, date_from, date_to):
        self._admin()
        dom = [("day", ">=", self._d(date_from)), ("day", "<=", self._d(date_to))] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        Lost = self.env["navitta.lost.sale"].sudo()
        wdom = [("waiting", "=", True)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        return {"branches": self._branches(), "rows": [self._lost_dict(r) for r in Lost.search(dom)],
                "waiting": [self._lost_dict(r) for r in Lost.search(wdom, order="create_date", limit=300)],
                "reasons": LOST_REASONS, "day": fields.Date.to_string(app_today(self.env))}

    @api.model
    def admin_lost_contacted(self, lost_id):
        self._admin()
        w = self.env["navitta.lost.sale"].sudo().browse(int(lost_id)).exists()
        err = w._mark_contacted(_("الإدارة")) if w else "not_found"
        return self._err(err) if err else {"ok": True}

    # ------------------------------------------------------------------ day log
    @api.model
    def admin_day(self, day):
        self._admin()
        d = self._d(day)
        S = self.env
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        reps = S["navitta.daily.report"].sudo().search([("day", "=", d)])
        visits = S["navitta.visit"].sudo().search([("day", "=", d), ("saved", "=", True)])
        att, brk = self._today_bits(emps, d)
        cases = S["navitta.late.case"].sudo().search([("day", "=", d)])
        return {
            "day": fields.Date.to_string(d), "today": fields.Date.to_string(app_today(self.env)), "branches": self._branches(),
            "employees": self._employees(), "off": {e.id: e._is_off(d) for e in emps},
            "reports": [dict(self._report_dict(r), branch_id=r.branch_id.id,
                             events=[{"level": lv, "title": t, "text": x} for lv, t, x in r._events()],
                             exch_labels=[{"reason": label(r.exchange_ids._fields["reason"].selection, x.reason), "detail": x.detail,
                                           "soldBy": x.sold_by, "by": x.exchanged_by} for x in r.exchange_ids]) for r in reps],
            "visits": [self._visit_dict(v) for v in visits],
            "attendance": att, "breaks": brk, "cases": [self._case_dict(c) for c in cases],
        }

    # ------------------------------------------------------------------ nightly summary
    @api.model
    def _summary_data(self, day):
        """Everything the nightly 'branches summary' PDF shows for one day."""
        S = self.env
        branches = S["navitta.branch"].sudo().search([])
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        reps = S["navitta.daily.report"].sudo().search([("day", "=", day)])
        prev = S["navitta.daily.report"].sudo().search([("day", "=", day - timedelta(days=7))])
        visits = S["navitta.visit"].sudo().search([("day", "=", day), ("saved", "=", True)])
        cases = S["navitta.late.case"].sudo().search([("day", "=", day)])
        lreq = S["navitta.leave.request"].sudo().search([("state", "=", "pending")])
        sales = sum(reps.mapped("sales"))
        invoices = sum(reps.mapped("invoices"))
        buys = len(visits.filtered("bought"))
        rows = []
        for b in branches:
            rs = reps.filtered(lambda r: r.branch_id == b)
            vs = visits.filtered(lambda v: v.branch_id == b)
            bc = cases.filtered(lambda c: c.employee_id.branch_id == b)
            rows.append({
                "name": b.name, "sales": sum(rs.mapped("sales")), "invoices": sum(rs.mapped("invoices")), "visits": len(vs),
                "buy_pct": (100.0 * len(vs.filtered("bought")) / len(vs)) if vs else None, "sat": rs[:1].sat or None,
                "late": len(bc.filtered(lambda c: c.kind == "late")), "over": len(bc.filtered(lambda c: c.kind == "break")),
                "lost": sum(len(r.lost_ids) for r in rs) if rs else len(vs.filtered(lambda v: v.lost_color or v.lost_size)),
                "sent": bool(rs),
            })
        review = [{"who": "%s · %s" % (c.employee_id.name, c.employee_id.branch_id.name), "state": c.state,
                   "what": (_("☕ تجاوز %s د", c.minutes) if c.kind == "break" else _("تأخير %s د", c.minutes)),
                   "reason": c.reason or _("لم تذكر السبب بعد")}
                  for c in S["navitta.late.case"].sudo().search([("state", "in", ("reply", "review"))], limit=8)]
        review += [{"who": r.employee_id.name, "state": "leave", "what": _("طلب إجازة"),
                    "reason": _("%(t)s من %(a)s إلى %(b)s", t=label(LEAVE_TYPES, r.type), a=r.from_day, b=r.to_day)} for r in lreq]
        events = []
        for r in reps:
            for lv, t, x in r._events():
                events.append({"level": lv, "title": t, "text": "%s: %s" % (r.branch_id.name, x)})
        lost = defaultdict(lambda: {"n": 0, "branches": set()})
        for r in reps:
            for l in r.lost_ids:
                k = (l.model or "—", l.color or "—", l.size or "—")
                lost[k]["n"] += 1
                lost[k]["branches"].add(r.branch_id.name)
        for v in visits.filtered(lambda v: (v.lost_color or v.lost_size) and v.branch_id not in reps.mapped("branch_id")):
            k = (v.lost_model or "—", v.lost_color or "—", v.lost_size or "—")
            lost[k]["n"] += 1
            lost[k]["branches"].add(v.branch_id.name)
        lost_rows = sorted(({"model": k[0], "color": k[1], "size": k[2], "n": x["n"], "branches": "، ".join(sorted(x["branches"]))}
                            for k, x in lost.items()), key=lambda x: -x["n"])[:6]
        c = self.env.company
        return {
            "day": day, "branches": rows, "missing": [b.name for b in branches if b not in reps.mapped("branch_id")],
            "sales": sales, "prev_sales": sum(prev.mapped("sales")), "invoices": invoices, "visits": len(visits),
            "buy_pct": (100.0 * buys / len(visits)) if visits else None,
            "pending": len(cases.filtered(lambda x: x.state == "review")) + len(lreq),
            "late": len(cases.filtered(lambda x: x.kind == "late")), "over": len(cases.filtered(lambda x: x.kind == "break")),
            "review": review, "events": events, "lost": lost_rows,
            "shift": "%s – %s" % (self._fmt12(hours_to_min(c.navitta_shift_start)), self._fmt12(hours_to_min(c.navitta_shift_end))),
            "emp_count": len(emps),
        }

    @staticmethod
    def _fmt12(m):
        from .common import fmt_time
        return fmt_time(m)

    @api.model
    def _cron_daily_summary(self):
        """23:45: email the branches summary PDF to the report address."""
        to = self.env.company.navitta_report_email
        if not to:
            _logger.info("Navitta daily summary: no report email set, skipping")
            return
        day = self._d(self.env.context.get("navitta_day"))
        report = self.env.ref("navitta_ops.action_report_daily_summary")
        pdf, _fmt = report.sudo()._render_qweb_pdf(report.report_name, data={"day": fields.Date.to_string(day)})
        name = "ملخص-الفروع-%s.pdf" % day
        att = self.env["ir.attachment"].sudo().create({"name": name, "datas": base64.b64encode(pdf), "mimetype": "application/pdf"})
        self.env["navitta.notification"]._send(to, _("ملخص الفروع اليومي · %s", day), _("مرفق ملخص الفروع ليوم %s.", day), attachments=att)

    @api.model
    def _set_report_cron_time(self):
        """Next 23:45 local time, stored in UTC."""
        cron = self.env.ref("navitta_ops.cron_daily_summary", raise_if_not_found=False)
        if not cron:
            return
        tz = company_tz(self.env)
        now = datetime.now(tz)
        nxt = tz.localize(datetime.combine(now.date(), time(23, 45)))
        if nxt <= now:
            nxt = tz.localize(datetime.combine(now.date() + timedelta(days=1), time(23, 45)))
        cron.sudo().nextcall = nxt.astimezone(pytz.utc).replace(tzinfo=None)
