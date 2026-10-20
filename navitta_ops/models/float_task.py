"""«عهدة اليوم» (the branch float) and «مهام اليوم» (today's tasks), as on the site (Navitta Pro 26.3).

Float: a fixed amount per branch (navitta.branch.float_amount, default 1000 SAR), counted note by note and coin by coin
(200/100/50/20/10/5/2/1/0.50/0.25, as on the site 26.6).
At the start of the day a checked-in employee counts it with a photo of the drawer and signs with her PIN; a colleague on
shift then confirms it with her own PIN, or reports a shortage with a note (management is alerted). At the end of the
day it is counted again inside the daily report, which can't be sent before. A total that differs from the amount needs
a note and alerts management.

Tasks: definitions per period (start / mid / end of the shift); each period is open in its own window of the day.
«any» = one of the employees signs it, «both» = each employee on shift signs her own, «side» = one per section (the
employee's number on the screen), done by its owner or, when she is off, by the first employee on shift. Auto tasks
mark themselves (the float counts and the daily report). A period that ends with tasks not done alerts management once.
"""
from datetime import timedelta

from odoo import _, api, fields, models

from .common import app_minute, app_today, dow, hours_to_min, iso

# the site's denominations (Navitta Pro 26.6): notes and coins in SAR. Each has its own count column «n<key>» (0.50 -> n050);
# the columns of the old list (100/50/10/5/1) are unchanged, so the counts saved before stay readable.
DENOMS = (200, 100, 50, 20, 10, 5, 2, 1, 0.5, 0.25)


def denom_key(d):
    """The screen's key of a denomination: 200 -> "200", 0.5 -> "0.50", 0.25 -> "0.25"."""
    return "%.2f" % d if d < 1 else str(int(d))


def denom_field(d):
    """The count column of a denomination: 200 -> "n200", 0.5 -> "n050", 0.25 -> "n025"."""
    return "n" + denom_key(d).replace(".", "")
FLOAT_KINDS = [("start", "Start of shift"), ("end", "End of shift")]
FLOAT_STATES = [("pending", "Waiting for confirmation"), ("ok", "Complete"), ("short", "Short")]
PHASES = [("start", "🌅 Start of shift"), ("mid", "☀️ Mid-shift"), ("end", "🌙 End of shift")]
PHASE_SEQ = {"start": 1, "mid": 2, "end": 3}
WHO = [("any", "Either one"), ("both", "Both"), ("side", "Each her section")]
AUTO = [("float", "Start-of-shift float count"), ("float_end", "End-of-shift float count"), ("report", "Daily report")]


class NavittaFloat(models.Model):
    _name = "navitta.float"
    _description = "Branch float count"
    _order = "day desc, branch_id, kind desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    kind = fields.Selection(FLOAT_KINDS, "Period", required=True)
    expected = fields.Float("Float amount", digits=(12, 2), help="The branch's float amount when it was counted")
    amount = fields.Float("Counted", digits=(12, 2))
    difference = fields.Float("Difference", digits=(12, 2), compute="_compute_difference", store=True,
                              help="Counted − float amount: negative = short, positive = over")
    n200 = fields.Integer("200 SAR")
    n100 = fields.Integer("100 SAR")
    n50 = fields.Integer("50 SAR")
    n20 = fields.Integer("20 SAR")
    n10 = fields.Integer("10 SAR")
    n5 = fields.Integer("5 SAR")
    n2 = fields.Integer("2 SAR")
    n1 = fields.Integer("1 SAR")
    n050 = fields.Integer("0.50 SAR")
    n025 = fields.Integer("0.25 SAR")
    photo_id = fields.Many2one("navitta.photo", "Photo", ondelete="set null")
    counted_by = fields.Many2one("navitta.employee", "Counted by", ondelete="restrict")
    counted_at = fields.Datetime("Count time")
    confirm_needed = fields.Boolean("Needs a colleague's confirmation")
    confirmed_by = fields.Many2one("navitta.employee", "Confirmed by", ondelete="restrict")
    confirmed_at = fields.Datetime("Confirmation time")
    state = fields.Selection(FLOAT_STATES, "Status", required=True, default="ok")
    note = fields.Char("Note")

    _sql_constraints = [
        ("float_uniq", "unique(branch_id, day, kind)", "The float is counted once a day at the start and once at the end"),
        ("n_positive", "CHECK(n200 >= 0 AND n100 >= 0 AND n50 >= 0 AND n20 >= 0 AND n10 >= 0 AND n5 >= 0 AND n2 >= 0 AND n1 >= 0"
                       " AND n050 >= 0 AND n025 >= 0)", "Counts cannot be negative"),
    ]

    @api.depends("amount", "expected")
    def _compute_difference(self):
        for f in self:
            f.difference = round(f.amount - f.expected, 2)

    @api.depends("branch_id", "day", "kind")
    def _compute_display_name(self):
        labels = dict(self._fields["kind"]._description_selection(self.env))
        for f in self:
            f.display_name = "%s – %s – %s" % (labels.get(f.kind, ""), f.branch_id.name or "", f.day or "")

    @api.model
    def _counts(self, counts):
        """Counts from the screen {"200": 1, "100": 8, "0.50": 3, …} -> ({"n200": 1, "n100": 8, "n050": 3, …}, total rounded to
        the halala), or (None, None) when one is invalid. "0.5" is accepted for "0.50"."""
        counts = counts or {}
        vals = {}
        try:
            for d in DENOMS:
                k = denom_key(d)
                raw = counts.get(k)
                if raw in (None, ""):
                    raw = counts.get(str(d)) or counts.get(d) or 0
                n = int(raw)
                if n < 0 or n > 100000 or n != float(raw):
                    return None, None
                vals[denom_field(d)] = n
        except (TypeError, ValueError, AttributeError):
            return None, None
        return vals, round(sum(d * vals[denom_field(d)] for d in DENOMS), 2)

    def _dict(self):
        self.ensure_one()
        return {"id": self.id, "kind": self.kind, "expected": self.expected, "amount": self.amount, "difference": self.difference,
                "counts": {denom_key(d): self[denom_field(d)] or 0 for d in DENOMS}, "photo_id": self.photo_id.id or False,
                "counted_by": self.counted_by.id or False, "counted_name": self.counted_by.name or "", "counted_at": iso(self.counted_at),
                "confirm_needed": self.confirm_needed, "confirmed_by": self.confirmed_by.id or False,
                "confirmed_name": self.confirmed_by.name or "", "confirmed_at": iso(self.confirmed_at),
                "state": self.state, "note": self.note or "", "day": fields.Date.to_string(self.day)}


class NavittaTaskDef(models.Model):
    """A task of the day, set up by management. Never deleted: deactivated instead (its history stays)."""
    _name = "navitta.task.def"
    _description = "Daily task"
    _order = "phase_seq, sequence, id"
    _rec_name = "title"

    title = fields.Char("Task", required=True, translate=True)
    phase = fields.Selection(PHASES, "Period", required=True, default="start")
    phase_seq = fields.Integer(compute="_compute_phase_seq", store=True)
    who = fields.Selection(WHO, "Who", required=True, default="any",
                           help="Either one: one of the employees signs it. Both: every employee on shift signs her own. "
                                "Each her section: employee 1 the right side, employee 2 the left side…; if only one is on shift, all sides are hers.")
    photo = fields.Boolean("📷 Photo", help="The employee photographs it before signing with her PIN")
    auto = fields.Selection(AUTO, "Marked automatically by", readonly=True,
                            help="Marks itself when this is done; its assignee and photo can't be changed")
    sequence = fields.Integer("Sequence", default=99)
    active = fields.Boolean("Active", default=True)

    @api.depends("phase")
    def _compute_phase_seq(self):
        for t in self:
            t.phase_seq = PHASE_SEQ.get(t.phase, 9)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("auto"):
                vals.update(who="any", photo=False)
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        # an auto task keeps «either one» and no photo (the float count / the report marks it)
        fix = self.filtered(lambda t: t.auto and (t.who != "any" or t.photo))
        if fix:
            super(NavittaTaskDef, fix).write({"who": "any", "photo": False})
        return res

    # ------------------------------------------------------------------ windows and the day's list
    @api.model
    def _windows(self, branch, day):
        """{phase: [from, to]} in minutes from local midnight. The start opens an hour before the branch's earliest shift
        that day (the morning shift when the branch has one and that weekday has it) and the end closes an hour after the
        evening shift (at most 23:59)."""
        c = self.env.company
        pm_start, pm_end = hours_to_min(c.navitta_shift_start), hours_to_min(c.navitta_shift_end)
        first = pm_start
        if branch.two_shifts and dow(day) not in c._navitta_no_am() and self.env["navitta.employee"].sudo().search_count(
                [("branch_id", "=", branch.id), ("active", "=", True), ("shift", "=", "am")]):
            first = min(hours_to_min(c.navitta_am_start), pm_start)
        return {"start": [max(0, first - 60), hours_to_min(c.navitta_task_start_to)],
                "mid": [hours_to_min(c.navitta_task_mid_from), hours_to_min(c.navitta_task_mid_to)],
                "end": [hours_to_min(c.navitta_task_end_from), min(1439, pm_end + 60)]}

    @api.model
    def _day_list(self, branch, day):
        """Every task of the day for the branch: who it is for, and who did it and when (dicts for the screens)."""
        S = self.env
        emps = S["navitta.employee"].sudo().search([("branch_id", "=", branch.id), ("active", "=", True)], order="slot, id")
        shift = emps.filtered(lambda e: not e._is_off(day)) or emps
        floats = S["navitta.float"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)])
        fl, fe = floats.filtered(lambda f: f.kind == "start")[:1], floats.filtered(lambda f: f.kind == "end")[:1]
        rep = S["navitta.daily.report"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)], limit=1)
        done = {(d.task_id.id, d.part): d for d in S["navitta.task.done"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)])}
        slots = sorted(set(emps.mapped("slot")))
        rows = []

        def row(t, part, emp, d=None):
            r = {"task_id": t.id, "phase": t.phase, "title": t.title, "who": t.who, "photo": t.photo, "auto": t.auto or False,
                 "part": part, "for_emp": emp.id or False, "for_name": emp.name or "",
                 "done_id": False, "done_by": False, "done_name": "", "done_at": False, "photo_id": False}
            if d:
                r.update(done_id=d.id, done_by=d.employee_id.id or False, done_name=d.employee_id.name or "",
                         done_at=iso(d.done_at), photo_id=d.photo_id.id or False)
            rows.append(r)
            return r

        none = S["navitta.employee"]
        for t in self.sudo().search([]):
            if t.who == "any":
                r = row(t, 0, none, None if t.auto else done.get((t.id, 0)))
                if t.auto == "float" and fl and fl.state != "pending":
                    r.update(done_by=fl.counted_by.id, done_name=fl.counted_by.name or "", done_at=iso(fl.confirmed_at or fl.counted_at))
                elif t.auto == "float_end" and fe:
                    r.update(done_by=fe.counted_by.id, done_name=fe.counted_by.name or "", done_at=iso(fe.counted_at))
                elif t.auto == "report" and rep:
                    r.update(done_name=rep.employee_name or "", done_at=iso(rep.create_date))
            elif t.who == "both":
                for e in shift:
                    row(t, e.slot, e, done.get((t.id, e.slot)))
            else:
                # each section is its owner's; when she is off, the first employee on shift covers it
                for part in slots:
                    cover = shift.filtered(lambda e: e.slot == part)[:1] or shift[:1]
                    row(t, part, cover, done.get((t.id, part)))
        return rows

    @api.model
    def _end_left(self, employee, day, minute):
        """The end-of-shift tasks that still block her check-out at `minute`: her own («both», her section) once the end
        period has opened, and the shared «either one» tasks only when she is the last one still in the branch."""
        branch = employee.branch_id
        if minute < self._windows(branch, day)["end"][0] or not self.sudo().search_count([("phase", "=", "end")]):
            return []
        alone = not self.env["navitta.attendance"].sudo().search_count(
            [("branch_id", "=", branch.id), ("day", "=", day), ("check_out", "=", False), ("employee_id", "!=", employee.id)])
        return [x for x in self._day_list(branch, day) if x["phase"] == "end" and not x["done_at"]
                and (x["for_emp"] == employee.id or (x["who"] == "any" and alone))]


class NavittaTaskDone(models.Model):
    _name = "navitta.task.done"
    _description = "Done daily task"
    _order = "day desc, done_at desc, id desc"
    _rec_name = "task_id"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    task_id = fields.Many2one("navitta.task.def", "Task", required=True, index=True, ondelete="cascade")
    phase = fields.Selection(related="task_id.phase", string="Period")
    part = fields.Integer("Section", default=0, help="0 = either one; otherwise the employee's number on the screen (1 right side, 2 left side…)")
    employee_id = fields.Many2one("navitta.employee", "Done by", ondelete="restrict")
    done_at = fields.Datetime("Done at", default=fields.Datetime.now)
    photo_id = fields.Many2one("navitta.photo", "Photo", ondelete="set null")

    _sql_constraints = [("task_done_uniq", "unique(branch_id, day, task_id, part)", "This task is already done today")]


class NavittaTaskAlert(models.Model):
    """One check per branch, day and period once the period is over (management is emailed when tasks were missed)."""
    _name = "navitta.task.alert"
    _description = "Missed tasks check"
    _order = "day desc, branch_id, id desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    phase = fields.Selection(PHASES, "Period", required=True)
    missed = fields.Text("Missed tasks")

    _sql_constraints = [("task_alert_uniq", "unique(branch_id, day, phase)", "This period is already checked")]

    @api.model
    def _cron_check(self):
        """Every 5 minutes: a period that is over with tasks not done -> one alert to management (once per branch, day
        and period). Yesterday is checked too, so the end of the shift (open until 23:59) is checked after midnight."""
        self = self.with_context(lang=self.env.company._navitta_lang())
        S = self.env
        TaskDef = S["navitta.task.def"].sudo()
        if not TaskDef.search_count([]):
            return 0
        today, m = app_today(S), app_minute(S)
        heads = {"start": _("Start-of-shift tasks that were not done:"), "mid": _("Mid-shift tasks that were not done:"),
                 "end": _("End-of-shift tasks that were not done:")}
        n = 0
        for day in (today - timedelta(days=1), today):
            branches = S["navitta.attendance"].sudo().search([("day", "=", day)]).mapped("branch_id")
            for b in branches:
                w, rows = TaskDef._windows(b, day), None
                for ph in ("start", "mid", "end"):
                    if (day == today and m <= w[ph][1]) or self.sudo().search_count(
                            [("branch_id", "=", b.id), ("day", "=", day), ("phase", "=", ph)]):
                        continue
                    if rows is None:
                        rows = TaskDef._day_list(b, day)
                    miss = "\n".join("• %s%s" % (x["title"], " (%s)" % x["for_name"] if x["for_name"] else "")
                                     for x in rows if x["phase"] == ph and not x["done_at"])
                    self.sudo().create({"branch_id": b.id, "day": day, "phase": ph, "missed": miss or False})
                    if miss:
                        S["navitta.notification"]._notify_admin(_("⏱️ Missed tasks: %s", b.name), "%s\n%s" % (heads[ph], miss))
                        n += 1
        return n

