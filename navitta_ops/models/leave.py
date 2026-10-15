from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import LEAVE_TYPES, dow, sel_label


class NavittaLeave(models.Model):
    _name = "navitta.leave"
    _description = "Leave"
    _order = "from_day desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="Branch")
    from_day = fields.Date("From", required=True)
    to_day = fields.Date("Last day of leave", required=True)
    type = fields.Selection(LEAVE_TYPES, "Type", default="annual", required=True)
    cover_employee_id = fields.Many2one("navitta.employee", "Covers the branch", ondelete="restrict")
    deferred_ids = fields.One2many("navitta.deferred.off", "leave_id", "Postponed days off")

    @api.constrains("from_day", "to_day")
    def _check_days(self):
        for l in self:
            if l.to_day < l.from_day:
                raise ValidationError(_("Check the dates"))

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        leaves._defer_cover_offs()
        return leaves

    def _defer_cover_offs(self):
        """The covering colleague works her weekly day off inside the leave; management sets a new date."""
        Def = self.env["navitta.deferred.off"].sudo()
        for l in self.filtered("cover_employee_id"):
            off = l.cover_employee_id.weekly_off
            if not off:
                continue
            d = l.from_day
            while d <= l.to_day:
                if dow(d) == int(off) and not Def.search_count([("employee_id", "=", l.cover_employee_id.id), ("original_day", "=", d)]):
                    Def.create({"employee_id": l.cover_employee_id.id, "leave_id": l.id, "original_day": d})
                d += timedelta(days=1)

    @api.model
    def _admin_add(self, employee, from_day, to_day, type_, cover=None):
        """cover: the colleague (navitta.employee) who covers the branch, if any."""
        mate = cover or self.env["navitta.employee"]
        return self.sudo().create({"employee_id": employee.id, "from_day": from_day, "to_day": to_day,
                                   "type": type_, "cover_employee_id": mate.id or False})


class NavittaDeferredOff(models.Model):
    _name = "navitta.deferred.off"
    _description = "Postponed day off due to cover"
    _order = "original_day"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="Branch")
    leave_id = fields.Many2one("navitta.leave", "Because of leave", ondelete="cascade")
    original_day = fields.Date("Original day off", required=True)
    new_day = fields.Date("New date")

    _sql_constraints = [("emp_day_uniq", "unique(employee_id, original_day)", "This day off is already postponed")]

    def _set_new_day(self, day):
        self.ensure_one()
        x = self.sudo()
        if x.employee_id._is_off(day):
            return "already_off"
        x.with_context(navitta_skip=True).write({"new_day": day})
        x.employee_id._notify(_("Replacement day off date"),
                              _("Your day off on %(a)s was postponed because you covered the branch. The new date is %(b)s. Navitta Management", a=x.original_day, b=day))
        return None

    def write(self, vals):
        if "new_day" in vals and vals["new_day"] and not self.env.context.get("navitta_skip"):
            day = fields.Date.to_date(vals.pop("new_day"))
            for x in self:
                err = x._set_new_day(day)
                if err:
                    raise UserError(self.env["navitta.api"]._err_text(err))
            if not vals:
                return True
        return super().write(vals)


class NavittaLeaveRequest(models.Model):
    _name = "navitta.leave.request"
    _description = "Leave request"
    _order = "create_date desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="Branch")
    type = fields.Selection(LEAVE_TYPES, "Type", required=True)
    from_day = fields.Date("From", required=True)
    to_day = fields.Date("Last day of leave", required=True)
    reason = fields.Text("Reason", required=True)
    cover_employee_id = fields.Many2one("navitta.employee", "Covers the branch", ondelete="restrict")
    state = fields.Selection([
        ("cover", "Waiting for colleague's approval"), ("cover_no", "Colleague declined"), ("pending", "Waiting for management"),
        ("approved", "Approved"), ("rejected", "Rejected"),
    ], "Status", required=True, index=True)
    cover_note = fields.Text("Colleague's note")
    note = fields.Text("Management note")
    decided_at = fields.Datetime("Decision time")

    def _decide(self, approve, note=None):
        self.ensure_one()
        r = self.sudo()
        if r.state != "pending":
            return "not_found"
        r.write({"state": "approved" if approve else "rejected", "note": note, "decided_at": fields.Datetime.now()})
        if approve:
            self.env["navitta.leave"].sudo().create({
                "employee_id": r.employee_id.id, "from_day": r.from_day, "to_day": r.to_day,
                "type": r.type, "cover_employee_id": r.cover_employee_id.id})
        r.employee_id._notify(
            _("Your leave was approved") if approve else _("Reply to your leave request"),
            _("Hello %(n)s, your %(t)s leave request from %(a)s to %(b)s was %(s)s.%(x)s Navitta Management",
              n=r.employee_id.name, s=_("approved") if approve else _("rejected"), t=sel_label(self.env, "navitta.leave.request", "type", r.type),
              a=r.from_day, b=r.to_day, x=_(" Management note: %s", note) if note else ""))
        return None

    def action_approve(self):
        for r in self:
            err = r._decide(True, r.note)
            if err:
                raise UserError(self.env["navitta.api"]._err_text(err))

    def action_reject(self):
        for r in self:
            err = r._decide(False, r.note)
            if err:
                raise UserError(self.env["navitta.api"]._err_text(err))
