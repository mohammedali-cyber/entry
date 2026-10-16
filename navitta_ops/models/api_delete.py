"""Screen API of the last 26.3 features: deleting with the delete password and the delete log, the management settings
tab (shift toggles, delete password), the month calendar of the day log and the ideas backlog (Navitta Pro 26.3)."""
import logging
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import app_today, iso
from .delete import DELETE_MODELS
from .idea import APP_VERSION, IDEA_VERSIONS

_logger = logging.getLogger(__name__)


class NavittaApiDelete(models.AbstractModel):
    _inherit = "navitta.api"

    # ------------------------------------------------------------------ delete with the delete password
    @api.model
    def _delete_summary(self, kind, rec):
        """A short line of what is deleted, for the delete log (the record's own name, branch, employee and day)."""
        parts = [rec.display_name or ""]
        br = rec["branch_id"] if "branch_id" in rec._fields else False
        emp = rec["employee_id"] if "employee_id" in rec._fields else False
        if br and br.name not in parts[0]:
            parts.append(br.name)
        if emp and emp.name not in parts[0]:
            parts.append(emp.name)
        day = rec["day"] if "day" in rec._fields else False
        if day and fields.Date.to_string(day) not in parts[0]:
            parts.append(fields.Date.to_string(day))
        return " · ".join(p for p in parts if p)[:300]

    @api.model
    def _delete_prepare(self, kind, rec):
        """Keeps the other Navitta records consistent before `rec` goes (as the site's admin_delete). Point of Sale and
        accounting records are never changed here; the database only clears their link to a deleted visit."""
        S = self.env
        if kind == "daily_reports":
            # the figures the report wrote into the day: its employee sales lines, the supplies used, who closed the day,
            # the bank deposit, and the day's sales when they came from the report (not from the Point of Sale)
            dom = [("branch_id", "=", rec.branch_id.id), ("day", "=", rec.day)]
            S["navitta.employee.sales"].sudo().search(dom + [("source", "=", "report")]).unlink()
            S["navitta.supply.move"].sudo().search(dom + [("kind", "=", "used")]).unlink()
            row = S["navitta.daily.sales"].sudo().with_context(navitta_from_report=True).search(dom, limit=1)
            if row.source == "report":
                row.write({"sales": 0.0, "invoices": 0, "items": 0, "returns": 0, "cash": 0.0, "card": 0.0, "deposit": 0.0,
                           "closed_by": False, "source": False})
            elif row:
                row.write({"closed_by": False} if row.source == "admin" else {"closed_by": False, "deposit": 0.0})
        elif kind == "daily_sales":
            # the employees' sales of that day go with it (the Point of Sale lines are recomputed by its next sync)
            S["navitta.employee.sales"].sudo().search([("branch_id", "=", rec.branch_id.id), ("day", "=", rec.day)]).unlink()

    @api.model
    def admin_delete(self, kind, rec_id, reason, pw):
        """Deletes one record of a known kind after a reason and the delete password; a copy goes to the delete log."""
        self._admin()
        model = DELETE_MODELS.get(kind or "")
        if not model:
            return self._err("forbidden")
        reason = (reason or "").strip()
        if not reason:
            return self._err("reason_required")
        err = self.env.company._navitta_check_delete_password(pw or "")
        if err:
            return self._err(err)
        rec = self.env[model].sudo().with_context(active_test=False).browse(int(rec_id or 0)).exists()
        if not rec:
            return self._err("not_found")
        log = {"kind": kind, "res_model": model, "res_id": rec.id, "summary": self._delete_summary(kind, rec),
               "data": self.env["navitta.delete.log"]._copy_of(rec), "reason": reason[:1000], "user_id": self.env.uid,
               "branch_id": (rec["branch_id"].id if "branch_id" in rec._fields else False)
               or (rec["employee_id"].branch_id.id if "employee_id" in rec._fields else False) or False}
        try:
            with self.env.cr.savepoint():
                self._delete_prepare(kind, rec)
                rec.unlink()
                self.env.flush_all()
        except (psycopg2.IntegrityError, UserError, ValidationError):
            return self._err("in_use")
        self.env["navitta.delete.log"].sudo().create(log)
        _logger.info("Navitta: %s %s deleted by user %s: %s", model, log["res_id"], self.env.uid, reason)
        return {"ok": True}

    @api.model
    def _delete_state(self):
        rows = self.env["navitta.delete.log"].sudo().search([], limit=15)
        labels = dict(self.env["navitta.delete.log"]._fields["kind"]._description_selection(self.env))
        return {"set": self.env.company._navitta_delete_password_is_set(),
                "log": [{"id": x.id, "kind": x.kind, "label": labels.get(x.kind, x.kind), "summary": x.summary or "",
                         "reason": x.reason, "at": iso(x.create_date), "by": x.user_id.name or ""} for x in rows]}

    @api.model
    def admin_set_delete_pw(self, new_pw, current_pw=None):
        """Sets the delete password, or changes it: changing needs the current one (same lockout as deleting)."""
        self._admin()
        c = self.env.company
        if c._navitta_delete_password_is_set():
            err = c._navitta_check_delete_password(current_pw or "")
            if err:
                return self._err(err)
        if len(new_pw or "") < 4:
            return self._err("pw_short")
        c._navitta_set_delete_password(new_pw)
        return {"ok": True}

    # ------------------------------------------------------------------ settings tab: shifts, delete password, leaves
    @api.model
    def admin_settings(self):
        self._admin()
        day = app_today(self.env)
        branches = self.env["navitta.branch"].sudo().search([])
        leaves = self.env["navitta.leave"].sudo().search([("to_day", ">=", day)], order="from_day")
        return {
            "settings": self._settings(), "day": fields.Date.to_string(day),
            "branches": [{"id": b.id, "name": b.name, "code": b.code, "two_shifts": b.two_shifts} for b in branches],
            "employees": self._employees(),
            "leaves": [{"id": l.id, "employee_id": l.employee_id.id, "type": l.type,
                        "type_label": dict(l._fields["type"]._description_selection(self.env)).get(l.type, l.type),
                        "from_day": fields.Date.to_string(l.from_day), "to_day": fields.Date.to_string(l.to_day),
                        "cover_employee_id": l.cover_employee_id.id or False} for l in leaves],
            "delete": self._delete_state(),
        }

    @api.model
    def admin_set_two_shifts(self, branch_id, on):
        """A branch with or without the morning shift; without it every employee of the branch works the evening."""
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(int(branch_id or 0)).exists()
        if not b:
            return self._err("not_found")
        b.two_shifts = bool(on)
        return {"ok": True}

    @api.model
    def admin_set_shift(self, emp_id, shift):
        """An employee's shift: morning only in a branch that has a morning shift (site: admin_set_shift)."""
        self._admin()
        if shift not in ("am", "pm"):
            return self._err("bad_shift")
        e = self.env["navitta.employee"].sudo().browse(int(emp_id or 0)).exists()
        if not e:
            return self._err("not_found")
        if shift == "am" and not e.branch_id.two_shifts:
            return self._err("no_am_branch")
        e.shift = shift
        return {"ok": True}

    # ------------------------------------------------------------------ day log: month calendar
    @api.model
    def admin_day_calendar(self, month, branch_id=False):
        """Per day of the month, for one branch or all: sales and target, the reports sent, lateness / break overruns
        (not excused or edited), the counted cash + card minus the Point of Sale's, floats short, refunds and changes
        after closing (site: DayCalendar)."""
        self._admin()
        d = fields.Date.to_date(month) if month else app_today(self.env)
        m0 = d.replace(day=1)
        m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        S = self.env
        branches = S["navitta.branch"].sudo().search([("id", "=", int(branch_id))] if branch_id else [])
        ids = branches.ids
        dom = [("branch_id", "in", ids), ("day", ">=", m0), ("day", "<=", m1)]
        cells = {}

        def c(day):
            return cells.setdefault(fields.Date.to_string(day), {"sales": 0.0, "target": 0.0, "has": False, "rep": 0, "late": 0,
                                                                  "diff": 0.0, "float": 0, "refund": 0.0, "chg": 0})
        rows = {(r.branch_id.id, r.day): r for r in S["navitta.daily.sales"].sudo().search(dom)}
        for r in rows.values():
            x = c(r.day)
            x["target"] += r.target
            x["refund"] += r.returns_amount
            if r.has_sales:
                x["sales"] += r.sales
                x["has"] = True
        for rep in S["navitta.daily.report"].sudo().search(dom):
            x = c(rep.day)
            x["rep"] += 1
            chk = self._cash_check(rep, rows.get((rep.branch_id.id, rep.day)))
            if chk:
                x["diff"] += (chk["cash"] + chk["card"]) - (chk["sys_cash"] + chk["sys_card"])
        for lc in S["navitta.late.case"].sudo().search([("day", ">=", m0), ("day", "<=", m1), ("employee_id.branch_id", "in", ids),
                                                        ("state", "not in", ("excused", "edited"))]):
            c(lc.day)["late"] += 1
        for f in S["navitta.float"].sudo().search(dom + [("state", "not in", ("ok", "pending"))]):
            c(f.day)["float"] += 1
        for x in S["navitta.sales.change.log"].sudo().search_read(dom, ["day"]):
            c(x["day"])["chg"] += 1
        for x in cells.values():
            x["diff"] = round(x["diff"], 2)
        return {"month": fields.Date.to_string(m0), "today": fields.Date.to_string(app_today(self.env)), "branches": len(ids),
                "days": cells}

    # ------------------------------------------------------------------ ideas
    @api.model
    def _idea_dict(self, x):
        return {"id": x.id, "title": x.title, "details": x.details or "", "state": x.state, "version": x.version,
                "photo": bool(x.photo), "created_at": iso(x.create_date), "updated_at": iso(x.updated_at)}

    @api.model
    def admin_ideas(self):
        self._admin()
        return {"version": APP_VERSION, "versions": IDEA_VERSIONS,
                "ideas": [self._idea_dict(x) for x in self.env["navitta.idea"].sudo().search([])]}

    @api.model
    def admin_idea_save(self, idea_id, vals):
        """Adds an idea (title required, details, release, optional photo) or updates its status / release."""
        self._admin()
        vals = vals or {}
        Idea = self.env["navitta.idea"].sudo()
        out = {"updated_at": fields.Datetime.now()}
        if "title" in vals:
            title = (vals.get("title") or "").strip()
            if not title:
                return self._err("idea_required")
            out["title"] = title[:300]
        if "details" in vals:
            out["details"] = (vals.get("details") or "").strip()[:4000] or False
        if "version" in vals:
            v = (vals.get("version") or "").strip()
            if not v:
                return self._err("not_found")
            out["version"] = v[:20]
        if "state" in vals:
            if vals["state"] not in ("todo", "done", "no"):
                return self._err("not_found")
            out["state"] = vals["state"]
        if vals.get("photo"):
            mime, err = Idea._check_photo(vals["photo"])
            if err:
                return self._err(err)
            out.update(photo=vals["photo"], photo_mime=mime)
        if idea_id:
            x = Idea.browse(int(idea_id)).exists()
            if not x:
                return self._err("not_found")
            x.write(out)
        else:
            if "title" not in out:
                return self._err("idea_required")
            out.setdefault("version", APP_VERSION)
            x = Idea.create(out)
        return {"ok": True, "id": x.id}

