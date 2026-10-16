"""Server API for the management screens (dashboard, sales, requests, lost sales, day log) and the nightly summary."""
import base64
import logging
import secrets
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import config

from .common import (app_today, cashier_text, company_tz, dow, fmt_time, hours_to_min, iso,
                     is_shared_cashier, sel_label, selection)

_logger = logging.getLogger(__name__)


class NavittaApiAdmin(models.AbstractModel):
    _inherit = "navitta.api"

    @api.model
    def _admin(self):
        if not self.env.user.has_group("navitta_ops.group_navitta_admin"):
            raise AccessError(_("This screen is for branch management only"))

    @api.model
    def _d(self, s):
        return fields.Date.to_date(s) if s else app_today(self.env)

    @api.model
    def _wd(self, weekdays):
        """Weekdays to keep (0 = Sunday … 6 = Saturday); [] = every day."""
        wd = sorted({int(x) for x in (weekdays or []) if str(x).lstrip("-").isdigit() and 0 <= int(x) <= 6})
        return wd if 0 < len(wd) < 7 else []

    @api.model
    def _day_filter(self, d0, d1, weekdays=None, field="day"):
        dom = [(field, ">=", d0), (field, "<=", d1)]
        wd = self._wd(weekdays)
        if wd:
            days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
            dom.append((field, "in", [d for d in days if dow(d) in wd] or [False]))
        return dom

    @api.model
    def _branches(self):
        return [{"id": b.id, "name": b.name, "code": b.code} for b in self.env["navitta.branch"].sudo().search([])]

    @api.model
    def _employees(self):
        day = app_today(self.env)
        return [{"id": e.id, "branch_id": e.branch_id.id, "slot": e.slot, "name": e.name, "email": e.email or "",
                 "weekly_off": e.weekly_off, "has_pin": e.has_pin, "shift": self._shift_dict(e, day)}
                for e in self.env["navitta.employee"].sudo().search([("active", "=", True)])]

    @api.model
    def _case_review(self, c):
        """A case waiting for a decision, with the preview of the chances and of the penalty if rejected."""
        return dict(self._case_dict(c), preview=c._preview())

    @api.model
    def admin_filters(self):
        self._admin()
        return {"branches": self._branches(), "settings": self._settings(), "day": fields.Date.to_string(app_today(self.env)),
                "login": self._login_stamp()}

    # ------------------------------------------------------------------ first-time setup
    @api.model
    def admin_setup(self):
        """Checklist of what's still missing before the branches can use the screens."""
        self._admin()
        S, c = self.env, self.env.company
        no_pin = S["navitta.employee"].sudo().search([("active", "=", True)]).filtered(lambda e: not e.has_pin)
        no_dev = S["navitta.branch"].sudo().search([]).filtered(lambda b: not b.user_ids)
        smtp = S["ir.mail_server"].sudo().search_count([]) or (config.get("smtp_server") not in (False, None, "", "localhost"))
        items = [
            {"key": "pins", "done": not no_pin, "title": _("Employees' PINs"),
             "text": _("%s employees without a PIN (they can't check in)", len(no_pin)) if no_pin else _("All employees have PINs")},
            {"key": "devices", "done": not no_dev, "title": _("Branch device accounts"),
             "text": _("Branches without a device account: %s", _(", ").join(no_dev.mapped("name"))) if no_dev else _("Every branch has a device account")},
            {"key": "admin_pw", "done": c._navitta_admin_password_is_set(), "title": _("Management password"),
             "text": _("Used on the branch device to change targets and undo a check-out")},
            {"key": "email", "done": bool(c.navitta_report_email), "title": _("Management email"),
             "text": _("Receives the daily summary and request alerts")},
            {"key": "mail_server", "done": bool(smtp), "title": _("Outgoing mail server"),
             "text": _("Without it no emails are sent (Gmail with an app password)")},
            {"key": "catalog", "done": bool(S["navitta.catalog.item"].sudo().search_count([], limit=1)), "title": _("Models file"),
             "text": _("To search models and barcodes in lost sales")},
        ]
        return {"items": items, "done": all(i["done"] for i in items)}

    @api.model
    def admin_setup_pins(self):
        """New PIN for every active employee without one. The PINs are returned once, to be handed to the employees."""
        self._admin()
        out = []
        emps = self.env["navitta.employee"].sudo().search([("active", "=", True)], order="branch_id, slot")
        for e in emps.filtered(lambda x: not x.has_pin):
            pin, mail = e._new_pin()
            out.append({"name": e.name, "branch": e.branch_id.name, "pin": pin, "mail": mail})
        _logger.info("Navitta: %s PINs generated by user %s", len(out), self.env.uid)
        return {"rows": out}

    @api.model
    def admin_setup_devices(self):
        """A device account for every branch without one. Logins and passwords are returned once."""
        self._admin()
        S = self.env
        groups = [S.ref("base.group_user").id, S.ref("navitta_ops.group_navitta_branch").id]
        Users = S["res.users"].sudo().with_context(no_reset_password=True)
        out = []
        for b in S["navitta.branch"].sudo().search([]).filtered(lambda x: not x.user_ids):
            base = "navitta-%s" % (b.code or b.id)
            login, n = base, 1
            while Users.with_context(active_test=False).search_count([("login", "=", login)]):
                n += 1
                login = "%s-%s" % (base, n)
            pw = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _i in range(8))  # no look-alike characters
            Users.create({
                "name": _("%s device", b.name), "login": login, "password": pw, "navitta_branch_id": b.id,
                "company_id": b.company_id.id or S.company.id, "company_ids": [(6, 0, [b.company_id.id or S.company.id])],
                "groups_id": [(6, 0, groups)], "tz": S.company.navitta_tz or "Asia/Riyadh", "lang": S.user.lang,
                "action_id": S.ref("navitta_ops.action_branch_app").id,
            })
            out.append({"branch": b.name, "login": login, "password": pw})
        _logger.info("Navitta: %s branch device accounts created by user %s", len(out), self.env.uid)
        return {"rows": out}

    # ------------------------------------------------------------------ dashboard
    @api.model
    def admin_dashboard(self, date_from=7, date_to=None, weekdays=None):
        """date_from / date_to: the period of the figures (an int = the last N days, as before)."""
        self._admin()
        day = app_today(self.env)
        if isinstance(date_from, int) or not date_from:
            d0, d1 = day - timedelta(days=max(1, int(date_from or 7)) - 1), day
        else:
            d0, d1 = self._d(date_from), self._d(date_to)
        S = self.env
        S["navitta.branch"].sudo().search([])._system_sync([day])
        dom = self._day_filter(d0, d1, weekdays)
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        reps = S["navitta.daily.report"].sudo().search(dom)
        visits = S["navitta.visit"].sudo().search(dom + [("saved", "=", True)])
        sales_rows = S["navitta.daily.sales"].sudo().search(dom + [("has_sales", "=", True)])
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
            "best": best, "from": fields.Date.to_string(d0), "to": fields.Date.to_string(d1),
            "sales": [{"branch_id": r.branch_id.id, "day": fields.Date.to_string(r.day), "sales": r.sales, "invoices": r.invoices,
                       "items": r.items, "tax": r.vat_amount, "returns": r.returns, "system": r._is_system()} for r in sales_rows],
            "registers": {str(k): v for k, v in S["navitta.branch"].sudo().search([])._system_status(day).items()},
            "day": fields.Date.to_string(day), "settings": self._settings(), "branches": self._branches(),
            "employees": self._employees(), "off": {e.id: e._is_off(day) for e in emps},
            "reports": [{"branch_id": r.branch_id.id, "day": fields.Date.to_string(r.day), "employee_name": r.employee_name,
                         "sales": r.sales, "invoices": r.invoices, "sat": r.sat} for r in reps],
            "visits": [{"branch_id": v.branch_id.id, "employee_id": v.employee_id.id, "day": fields.Date.to_string(v.day),
                        "duration_sec": v.duration_sec, "bought": v.bought} for v in visits],
            "attendance": att, "breaks": brk,
            "cases": [self._case_review(c) if c.state == "review" else self._case_dict(c) for c in cases],
            "month_cases": [{"employee_id": c.employee_id.id, "state": c.state, "kind": c.kind} for c in month_cases],
            "leave_requests": [{
                "id": r.id, "employee_id": r.employee_id.id, "type": sel_label(self.env, "navitta.leave.request", "type", r.type),
                "from_day": fields.Date.to_string(r.from_day), "to_day": fields.Date.to_string(r.to_day), "reason": r.reason,
                "state": r.state, "cover_employee_id": r.cover_employee_id.id or False, "cover_note": r.cover_note or "",
                "decided_at": iso(r.decided_at)} for r in lreq],
            "edit_requests": [{"id": r.id, "employee_id": r.employee_id.id, "old_sec": r.old_sec, "new_sec": r.new_sec,
                               "reason": r.reason, "started_at": iso(r.visit_id.started_at)} for r in ereq],
            "threads": [{"id": t.id, "employee_id": t.employee_id.id, "topic": sel_label(self.env, "navitta.thread", "topic", t.topic), "state": t.state,
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
    def _cashier_label(self, cashier):
        if not cashier:
            return _("No cashier (shared account)")
        if is_shared_cashier(cashier):
            return cashier_text(self.env, cashier)
        return _("%s (cashier not linked to an employee)", cashier_text(self.env, cashier))

    @api.model
    def employee_summary(self, date_from, date_to, branch_id=False, weekdays=None):
        """Each employee's sales in the period: from the cash register system (by the cashier who made the sale) when the
        branch has one, else from the daily reports. Sales without a cashier are shown as «بدون كاشير». Also her counter
        visits, to check the counter against the sales records."""
        self._admin()
        d0, d1 = self._d(date_from), self._d(date_to)
        dom = self._day_filter(d0, d1, weekdays)
        wd = self._wd(weekdays)
        lines = self.env["navitta.employee.sales"]._preferred_domain(d0, d1, branch_id)
        if wd:
            lines = lines.filtered(lambda l: dow(l.day) in wd)
        rows = {}
        for l in lines:
            key = (l.branch_id.id, l.employee_id.id or 0, "" if l.employee_id else (l.cashier or ""))
            r = rows.setdefault(key, {"branch_id": l.branch_id.id, "employee_id": l.employee_id.id or False,
                                      "name": l.employee_id.name or self._cashier_label(l.cashier),
                                      "slot": l.employee_id.slot or 99, "amount": 0.0, "tax": 0.0, "invoices": 0, "items": 0,
                                      "exchanged": 0, "returns_amount": 0.0, "amount_inv": 0.0, "source": l.source,
                                      "visits": 0, "bought": 0})
            r["amount"] += l.amount
            r["tax"] += l.tax
            r["invoices"] += l.invoices
            r["items"] += l.items
            r["exchanged"] += l.returns
            r["returns_amount"] += l.returns_amount
            r["amount_inv"] += l.amount if l.invoices else 0.0
        vdom = self._day_filter(d0, d1, weekdays) + [("saved", "=", True)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        for v in self.env["navitta.visit"].sudo().search(vdom):
            key = (v.branch_id.id, v.employee_id.id, "")
            r = rows.get(key)
            if r is None:
                r = rows[key] = {"branch_id": v.branch_id.id, "employee_id": v.employee_id.id, "name": v.employee_id.name,
                                 "slot": v.employee_id.slot, "amount": 0.0, "tax": 0.0, "invoices": 0, "items": 0, "exchanged": 0,
                                 "returns_amount": 0.0, "amount_inv": 0.0, "source": False, "visits": 0, "bought": 0}
            r["visits"] += 1
            r["bought"] += 1 if v.bought else 0
        today = app_today(self.env)
        targets = {}
        for ds in self.env["navitta.daily.sales"].sudo().search(dom + [("day", "<=", today)]):
            targets[ds.branch_id.id] = targets.get(ds.branch_id.id, 0.0) + ds.target
        return {"rows": sorted(rows.values(), key=lambda r: -r["amount"]), "targets": targets}

    @api.model
    def admin_sales(self, branch_id, date_from, date_to, year, m_from, m_to, weekdays=None):
        self._admin()
        d0, d1, m0, m1 = self._d(date_from), self._d(date_to), self._d(m_from), self._d(m_to)
        today = app_today(self.env)
        branches = self.env["navitta.branch"].sudo().search([])
        branches._system_sync([today])
        wd = self._wd(weekdays) or None
        self.env["navitta.daily.sales"].flush_model()
        cr = self.env.cr
        cr.execute("""
            SELECT branch_id, SUM(target), COALESCE(SUM(target) FILTER (WHERE day <= %s), 0), COALESCE(SUM(sales), 0),
                   COALESCE(SUM(invoices), 0), COALESCE(SUM(items), 0), COALESCE(SUM(returns), 0),
                   COALESCE(SUM(vat_amount), 0), COALESCE(SUM(returns_amount), 0), COALESCE(SUM(cash), 0), COALESCE(SUM(card), 0)
              FROM navitta_daily_sales
             WHERE day BETWEEN %s AND %s AND (%s::int[] IS NULL OR EXTRACT(dow FROM day)::int = ANY(%s::int[]))
             GROUP BY branch_id""", [today, d0, d1, wd, wd])
        summary = [{"branch_id": b, "target": t or 0, "target_to_date": ttd or 0, "sales": s or 0, "invoices": i, "items": it,
                    "returns": r, "tax": tx or 0, "returns_amount": ra or 0, "cash": ca or 0, "card": cd or 0}
                   for b, t, ttd, s, i, it, r, tx, ra, ca, cd in cr.fetchall()]
        cr.execute("""
            SELECT EXTRACT(month FROM day)::int, SUM(target), COALESCE(SUM(target) FILTER (WHERE day <= %s), 0), COALESCE(SUM(sales), 0)
              FROM navitta_daily_sales WHERE EXTRACT(year FROM day) = %s AND (%s IS NULL OR branch_id = %s)
             GROUP BY 1 ORDER BY 1""", [today, int(year), branch_id or None, branch_id or None])
        monthly = [{"month": m, "target": t or 0, "target_to_date": ttd or 0, "sales": s or 0} for m, t, ttd, s in cr.fetchall()]
        dom = self._day_filter(m0, m1, weekdays) + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        marks = self._day_marks(m0, m1, branch_id)
        daily = []
        for r in self.env["navitta.daily.sales"].sudo().search(dom, order="day, branch_id"):
            mk = marks.get((r.branch_id.id, r.day)) or {}
            daily.append({"id": r.id, "day": fields.Date.to_string(r.day), "branch_id": r.branch_id.id, "target": r.target,
                          "sales": r.sales if r.has_sales else None, "invoices": r.invoices, "items": r.items, "returns": r.returns,
                          "returns_amount": r.returns_amount, "tax": r.vat_amount, "cash": r.cash, "card": r.card,
                          "closed_by": r.closed_by or "", "system": r._is_system(), "source": r.source or False,
                          "edited": r.source == "admin", "changed": mk.get("changed", 0),
                          # the counted cash and card of the daily report against the system (only once the report is in)
                          "check": self._cash_check(mk.get("report"), r)})
        return {"branches": self._branches(), "summary": summary, "monthly": monthly, "daily": daily,
                "employees": self.employee_summary(date_from, date_to, branch_id, weekdays), "settings": self._settings(),
                "day": fields.Date.to_string(today)}

    @api.model
    def admin_compare(self, date_from, date_to, weekdays=None):
        """Branches side by side for the period: target, sales (cash register system or daily report), tax, invoices,
        average invoice, items, returns, customers counted at the door, and checks between the three sources."""
        self._admin()
        d0, d1 = self._d(date_from), self._d(date_to)
        today = app_today(self.env)
        S = self.env
        branches = S["navitta.branch"].sudo().search([])
        branches._system_sync([today])
        dom = self._day_filter(d0, d1, weekdays)
        res = {b.id: {"branch_id": b.id, "name": b.name, "system": b._has_system(), "days": 0, "target": 0.0, "sales": 0.0,
                      "tax": 0.0, "net": 0.0, "invoices": 0, "items": 0, "returns": 0, "returns_amount": 0.0, "cash": 0.0, "card": 0.0,
                      "visits": 0, "bought": 0, "report_sales": 0.0, "report_invoices": 0, "report_days": 0, "sales_days": 0}
               for b in branches}
        n_days = len([1 for i in range((min(d1, today) - d0).days + 1)
                      if not self._wd(weekdays) or dow(d0 + timedelta(days=i)) in self._wd(weekdays)]) if d0 <= today else 0
        for r in S["navitta.daily.sales"].sudo().search(dom):
            x = res.get(r.branch_id.id)
            if not x:
                continue
            if r.day <= today:
                x["target"] += r.target
            if r.has_sales:
                x["sales_days"] += 1
            for k in ("sales", "invoices", "items", "returns", "returns_amount", "cash", "card"):
                x[k] += r[k]
            x["tax"] += r.vat_amount
            x["net"] += r.net_sales
        for v in S["navitta.visit"].sudo().search(dom + [("saved", "=", True)]):
            x = res.get(v.branch_id.id)
            if x:
                x["visits"] += 1
                x["bought"] += 1 if v.bought else 0
        sys_rows = {(r.branch_id.id, r.day): r for r in S["navitta.daily.sales"].sudo().search(dom) if r._is_system()}
        for rep in S["navitta.daily.report"].sudo().search(dom):
            x = res.get(rep.branch_id.id)
            if x:
                x["report_sales"] += rep.sales
                x["report_invoices"] += rep.invoices
                x["report_days"] += 1
                sr = sys_rows.get((rep.branch_id.id, rep.day))
                if sr:          # only the days that have both: what was typed vs the invoices
                    x["report_gap_sum"] = x.get("report_gap_sum", 0.0) + (rep.sales - sr.sales)
                    x["report_gap_days"] = x.get("report_gap_days", 0) + 1
        rows = []
        for x in res.values():
            x["days"] = n_days
            x["achieved"] = round(x["sales"] * 100.0 / x["target"], 1) if x["target"] else None
            x["avg_invoice"] = round(x["sales"] / x["invoices"], 2) if x["invoices"] else 0.0
            x["items_per_invoice"] = round(x["items"] / x["invoices"], 2) if x["invoices"] else 0.0
            x["conversion"] = round(x["invoices"] * 100.0 / x["visits"], 1) if x["visits"] else None
            x["counter_gap"] = x["invoices"] - x["bought"]          # >0: invoices without a counter visit
            x["report_gap"] = round(x.pop("report_gap_sum", 0.0), 2) if x.get("report_gap_days") else None
            x["report_gap_days"] = x.get("report_gap_days", 0)
            x["missing_reports"] = max(0, n_days - x["report_days"])
            rows.append(x)
        status = branches._system_status(today)
        return {"rows": sorted(rows, key=lambda r: -r["sales"]), "branches": self._branches(), "settings": self._settings(),
                "day": fields.Date.to_string(today), "from": fields.Date.to_string(d0), "to": fields.Date.to_string(d1),
                "registers": {str(k): v for k, v in status.items()}}

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
    def admin_lost(self, branch_id, date_from, date_to, weekdays=None):
        self._admin()
        dom = self._day_filter(self._d(date_from), self._d(date_to), weekdays) + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        Lost = self.env["navitta.lost.sale"].sudo()
        wdom = [("waiting", "=", True)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        return {"branches": self._branches(), "rows": [self._lost_dict(r) for r in Lost.search(dom)],
                "waiting": [self._lost_dict(r) for r in Lost.search(wdom, order="create_date", limit=300)],
                "reasons": selection(self.env, "navitta.visit", "lost_reason"), "day": fields.Date.to_string(app_today(self.env))}

    @api.model
    def admin_lost_contacted(self, lost_id):
        self._admin()
        w = self.env["navitta.lost.sale"].sudo().browse(int(lost_id)).exists()
        err = w._mark_contacted(_("Management")) if w else "not_found"
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
        S["navitta.branch"].sudo().search([])._system_sync([d])
        sales = {r.branch_id.id: r for r in S["navitta.daily.sales"].sudo().search([("day", "=", d)])}
        changed = {}
        for c in S["navitta.sales.change.log"].sudo().search_read([("day", "=", d)], ["branch_id"]):
            changed[c["branch_id"][0]] = changed.get(c["branch_id"][0], 0) + 1
        return {
            "day": fields.Date.to_string(d), "today": fields.Date.to_string(app_today(self.env)), "branches": self._branches(),
            "employees": self._employees(), "off": {e.id: e._is_off(d) for e in emps},
            "reports": [dict(self._report_dict(r), id=r.id, branch_id=r.branch_id.id, check=self._cash_check(r, sales.get(r.branch_id.id)),
                             events=[{"level": lv, "title": t, "text": x} for lv, t, x in r._events()],
                             exch_labels=[{"reason": sel_label(self.env, "navitta.daily.report.exchange", "reason", x.reason), "detail": x.detail,
                                           "soldBy": x.sold_by, "by": x.exchanged_by} for x in r.exchange_ids]) for r in reps],
            "visits": [self._visit_dict(v) for v in visits],
            "attendance": att, "breaks": brk, "cases": [self._case_dict(c) for c in cases],
            # today's float and tasks per branch (26.3)
            "daily": self._day_floats_tasks(d),
            # figures corrected by management, or changed after the report closed the day
            "marks": {b: {"edited": r.source == "admin", "changed": changed.get(b, 0), "system": r._is_system(),
                          "returns_amount": r.returns_amount} for b, r in sales.items()},
        }

    # ------------------------------------------------------------------ nightly summary
    @api.model
    def _summary_data(self, day):
        """Everything the nightly 'branches summary' PDF shows for one day."""
        S = self.env
        branches = S["navitta.branch"].sudo().search([])
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        reps = S["navitta.daily.report"].sudo().search([("day", "=", day)])
        visits = S["navitta.visit"].sudo().search([("day", "=", day), ("saved", "=", True)])
        cases = S["navitta.late.case"].sudo().search([("day", "=", day)])
        lreq = S["navitta.leave.request"].sudo().search([("state", "=", "pending")])
        branches._system_sync([day])
        Sales = S["navitta.daily.sales"].sudo()
        ds = Sales.search([("day", "=", day), ("has_sales", "=", True)])
        prev_ds = Sales.search([("day", "=", day - timedelta(days=7)), ("has_sales", "=", True)])
        sales = sum(ds.mapped("sales"))
        invoices = sum(ds.mapped("invoices"))
        buys = len(visits.filtered("bought"))
        floats = S["navitta.float"].sudo().search([("day", "=", day)])
        TaskDef = S["navitta.task.def"].sudo()
        rows = []
        events = []
        for b in branches:
            bf = floats.filtered(lambda f: f.branch_id == b)
            tasks = TaskDef._day_list(b, day) if TaskDef.search_count([]) else []
            for f in bf.filtered(lambda f: f.state == "short"):
                events.append({"level": "bad", "title": _("Float short"), "text": "%s: %s" % (b.name, _(
                    "%(k)s · %(a)s SAR of %(x)s SAR%(n)s", k=sel_label(S, "navitta.float", "kind", f.kind), a="{:,.0f}".format(f.amount),
                    x="{:,.0f}".format(f.expected), n=(" · " + f.note) if f.note else ""))})
            rs = reps.filtered(lambda r: r.branch_id == b)
            bs = ds.filtered(lambda r: r.branch_id == b)
            vs = visits.filtered(lambda v: v.branch_id == b)
            bc = cases.filtered(lambda c: c.employee_id.branch_id == b)
            rows.append({
                "name": b.name, "sales": sum(bs.mapped("sales")), "invoices": sum(bs.mapped("invoices")), "visits": len(vs),
                "buy_pct": (100.0 * len(vs.filtered("bought")) / len(vs)) if vs else None, "sat": rs[:1].sat or None,
                "late": len(bc.filtered(lambda c: c.kind == "late")), "over": len(bc.filtered(lambda c: c.kind == "break")),
                "lost": sum(len(r.lost_ids) for r in rs) if rs else len(vs.filtered(lambda v: v.lost_color or v.lost_size)),
                "sent": bool(rs),
                # the float: ok / short / pending / not counted (start and end of the day), and the tasks done
                "float_start": bf.filtered(lambda f: f.kind == "start")[:1].state or False,
                "float_end": bf.filtered(lambda f: f.kind == "end")[:1].state or False,
                "tasks_done": len([x for x in tasks if x["done_at"]]), "tasks_all": len(tasks),
            })
        review = [{"who": "%s · %s" % (c.employee_id.name, c.employee_id.branch_id.name), "state": c.state,
                   "what": (_("☕ Over by %s min", c.minutes) if c.kind == "break" else _("Late %s min", c.minutes)),
                   "reason": c.reason or _("No reason given yet")}
                  for c in S["navitta.late.case"].sudo().search([("state", "in", ("reply", "review"))], limit=8)]
        review += [{"who": r.employee_id.name, "state": "leave", "what": _("Leave request"),
                    "reason": _("%(t)s from %(a)s to %(b)s", t=sel_label(S, "navitta.leave.request", "type", r.type), a=r.from_day, b=r.to_day)} for r in lreq]
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
        lost_rows = sorted(({"model": k[0], "color": k[1], "size": k[2], "n": x["n"], "branches": _(", ").join(sorted(x["branches"]))}
                            for k, x in lost.items()), key=lambda x: -x["n"])[:6]
        c = self.env.company
        return {
            "day": day, "branches": rows, "missing": [b.name for b in branches if b not in reps.mapped("branch_id")],
            "sales": sales, "prev_sales": sum(prev_ds.mapped("sales")), "invoices": invoices, "visits": len(visits),
            "buy_pct": (100.0 * buys / len(visits)) if visits else None,
            "pending": len(cases.filtered(lambda x: x.state == "review")) + len(lreq),
            "late": len(cases.filtered(lambda x: x.kind == "late")), "over": len(cases.filtered(lambda x: x.kind == "break")),
            "float_short": len(floats.filtered(lambda f: f.state == "short")),
            "review": review, "events": events, "lost": lost_rows,
            "shift": "%s – %s" % (self._fmt12(hours_to_min(c.navitta_shift_start), self.env),
                                  self._fmt12(hours_to_min(c.navitta_shift_end), self.env)),
            "am_shift": "%s – %s" % (self._fmt12(hours_to_min(c.navitta_am_start), self.env),
                                     self._fmt12(hours_to_min(c.navitta_am_end), self.env)),
            "emp_count": len(emps),
        }

    @staticmethod
    def _fmt12(m, env=None):
        return fmt_time(m, env)

    @api.model
    def _cron_daily_summary(self):
        """23:45: email the branches summary PDF to the report address (in the company's language: the cron runs as
        OdooBot)."""
        self = self.with_context(lang=self.env.company._navitta_lang())
        to = self.env.company.navitta_report_email
        if not to:
            _logger.info("Navitta daily summary: no report email set, skipping")
            return
        day = self._d(self.env.context.get("navitta_day"))
        report = self.env.ref("navitta_ops.action_report_daily_summary")
        pdf, _fmt = report.sudo()._render_qweb_pdf(report.report_name, data={"day": fields.Date.to_string(day)})
        name = _("Branches-summary-%s.pdf", day)
        att = self.env["ir.attachment"].sudo().create({"name": name, "datas": base64.b64encode(pdf), "mimetype": "application/pdf"})
        self.env["navitta.notification"]._send(to, _("Daily branches summary · %s", day), _("Attached is the branches summary for %s.", day), attachments=att)

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
