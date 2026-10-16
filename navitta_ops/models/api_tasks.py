"""Screen API for today's float («عهدة اليوم») and today's tasks («مهام اليوم»), as on the site (Navitta Pro 26.3)."""
from odoo import _, api, fields, models

from .common import app_minute, app_today


class NavittaApiTasks(models.AbstractModel):
    _inherit = "navitta.api"

    # ------------------------------------------------------------------ state for the screens
    @api.model
    def _float_state(self, branch, day):
        floats = self.env["navitta.float"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)])
        start, end = floats.filtered(lambda f: f.kind == "start")[:1], floats.filtered(lambda f: f.kind == "end")[:1]
        return {"amount": branch.float_amount, "place": branch.float_place or "",
                "start": start._dict() if start else False, "end": end._dict() if end else False}

    @api.model
    def _tasks_state(self, branch, day):
        """{now, windows: {start|mid|end: [from, to]}, tasks: […]}: `now` is the local minute (1440 for a past day)."""
        TaskDef = self.env["navitta.task.def"]
        today = app_today(self.env)
        return {"day": fields.Date.to_string(day), "now": app_minute(self.env) if day == today else (1440 if day < today else -1),
                "windows": TaskDef._windows(branch, day), "tasks": TaskDef._day_list(branch, day)}

    @api.model
    def float_get(self):
        b = self._branch()
        return self._float_state(b, app_today(self.env))

    @api.model
    def tasks_state(self):
        b = self._branch()
        return self._tasks_state(b, app_today(self.env))

    # ------------------------------------------------------------------ float
    @api.model
    def float_count(self, emp_id, pin, kind, counts, photo_id=None, note=None):
        """The float counted note by note. kind «start»: on the counter, by a checked-in employee, with a photo of the
        drawer; then a colleague on shift confirms it. kind «end»: inside the daily report. A total that differs from the
        branch's float amount needs a note and alerts management. Can be counted again (not once the start is confirmed)."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        if kind not in ("start", "end"):
            return self._err("not_found")
        b, day = e.branch_id, app_today(self.env)
        photo = self._photo(b, photo_id)
        if kind == "start":
            if not photo:
                return self._err("photo_required")
            if not self._working(e, day):
                return self._err("not_checked_in")
        Float = self.env["navitta.float"].sudo()
        old = Float.search([("branch_id", "=", b.id), ("day", "=", day), ("kind", "=", kind)], limit=1)
        if old and kind == "start" and old.state == "ok":
            return self._err("already_done")
        vals, total = Float._counts(counts)
        if vals is None:
            return self._err("bad_count")
        expected = b.float_amount
        note = (note or "").strip()[:500]
        if round(total, 2) != round(expected, 2) and not note:
            return self._err("float_mismatch")
        # a colleague on shift today (not on a day off) confirms the start count with her own eyes
        need = kind == "start" and any(not m._is_off(day) for m in e._mates())
        state = "short" if round(total, 2) != round(expected, 2) else "pending" if need else "ok"
        vals.update({"branch_id": b.id, "day": day, "kind": kind, "expected": expected, "amount": total,
                     "counted_by": e.id, "counted_at": fields.Datetime.now(), "confirm_needed": need,
                     "confirmed_by": False, "confirmed_at": False, "state": state, "note": note or False})
        if photo:
            vals["photo_id"] = photo.id
        if old:
            old.write(vals)
        else:
            Float.create(vals)
        if state == "short":
            when = _("at the start of the shift") if kind == "start" else _("at the end of the shift")
            self.env["navitta.notification"]._notify_admin(
                _("⚠️ Float short: %s", b.name),
                _("%(e)s counted the float %(w)s: %(t)s SAR of %(x)s SAR. Reason: %(n)s", e=e.name, w=when,
                  t=_fmt(total), x=_fmt(expected), n=note))
        return {"ok": True, "state": state, "total": total}

    @api.model
    def float_confirm(self, emp_id, pin, float_id, ok, note=None):
        """Her colleague confirms the start count with her PIN, or reports a shortage (a note is required; management
        is alerted)."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        f = self.env["navitta.float"].sudo().browse(int(float_id or 0)).exists()
        if not f or f.branch_id != e.branch_id or f.state != "pending":
            return self._err("not_found")
        if f.counted_by == e:
            return self._err("same_emp")
        note = (note or "").strip()[:500]
        if not ok and not note:
            return self._err("reason_required")
        f.write({"confirmed_by": e.id, "confirmed_at": fields.Datetime.now(), "state": "ok" if ok else "short",
                 "note": f.note if ok else note})
        if not ok:
            self.env["navitta.notification"]._notify_admin(
                _("⚠️ Float short: %s", f.branch_id.name),
                _("%(e)s did not confirm the float %(c)s counted (%(a)s SAR): %(n)s", e=e.name, c=f.counted_by.name or "—",
                  a=_fmt(f.amount), n=note))
        return {"ok": True}

    # ------------------------------------------------------------------ tasks
    @api.model
    def task_done(self, emp_id, pin, task_id, part=0, photo_id=None):
        """She signs a task of an open period with her PIN (with a photo when the task needs one)."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        b, day = e.branch_id, app_today(self.env)
        t = self.env["navitta.task.def"].sudo().browse(int(task_id or 0)).exists()
        if not t or not t.active or t.auto:
            return self._err("not_found")
        w = t._windows(b, day)[t.phase]
        m = app_minute(self.env)
        if m < w[0]:
            return self._err("task_locked")
        if m > w[1]:
            return self._err("task_missed")
        photo = self._photo(b, photo_id)
        if t.photo and not photo:
            return self._err("photo_required")
        part = int(part or 0)
        if t.who == "both" and part != e.slot:
            return self._err("not_yours")
        if t.who == "any" and part != 0:
            return self._err("not_found")
        if t.who == "side" and not self.env["navitta.employee"].sudo().search_count(
                [("branch_id", "=", b.id), ("active", "=", True), ("slot", "=", part)]):
            return self._err("not_found")
        Done = self.env["navitta.task.done"].sudo()
        if Done.search_count([("branch_id", "=", b.id), ("day", "=", day), ("task_id", "=", t.id), ("part", "=", part)]):
            return self._err("already_done")
        Done.create({"branch_id": b.id, "day": day, "task_id": t.id, "part": part, "employee_id": e.id,
                     "done_at": fields.Datetime.now(), "photo_id": photo.id or False})
        return {"ok": True}

    @api.model
    def _checkout_blockers(self, employee, day, minute):
        """Why a check-out punch can't count yet (the fingerprint device): [text, …], empty when she may leave."""
        out = []
        if self.env["navitta.visit"].sudo().search_count([("employee_id", "=", employee.id), ("day", "=", day), ("saved", "=", False)]):
            out.append(_("Open visits on the counter"))
        # sales the cash register recorded without a counter visit, not confirmed yet (navitta_ops_pos)
        employee.branch_id._system_sync([day], force=True)
        autos = self.env["navitta.visit"]._unacked_autos(employee, day)
        if autos:
            out.append(_("Invoices sold without the counter not confirmed yet: %s", len(autos)))
        left = self.env["navitta.task.def"]._end_left(employee, day, minute)
        if left:
            out.append(_("End-of-shift tasks not done: %s", _(", ").join(
                x["title"] + (" (%s)" % x["for_name"] if x["for_name"] and x["who"] != "any" else "") for x in left)))
        return out

    # ------------------------------------------------------------------ management
    @api.model
    def admin_task_reopen(self, done_id):
        """Management re-opens a task marked done by mistake (it shows as not done again)."""
        self._admin()
        d = self.env["navitta.task.done"].sudo().browse(int(done_id or 0)).exists()
        if not d:
            return self._err("not_found")
        d.unlink()
        return {"ok": True}

    @api.model
    def _day_floats_tasks(self, day):
        """For the day log: {branch_id: {"float": {...}, "tasks": {...}}}."""
        return {b.id: {"float": self._float_state(b, day), "tasks": self._tasks_state(b, day)}
                for b in self.env["navitta.branch"].sudo().search([])}


def _fmt(n):
    """1000.0 -> '1,000', 999.5 -> '999.50' (amounts in alert texts)."""
    return "{:,.0f}".format(n) if float(n).is_integer() else "{:,.2f}".format(n)
