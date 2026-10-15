from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import CRYPT, WEEKDAYS, dow, new_pin


class NavittaEmployee(models.Model):
    _name = "navitta.employee"
    _description = "Branch employee"
    _order = "branch_id, slot, id"

    name = fields.Char("Name", required=True)
    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    slot = fields.Integer("Order on screen", required=True, default=0,
                          help="Position of her card on the counter screen and her keyboard shortcut number. Assigned automatically when added")
    device_user_id = fields.Char("Fingerprint device ID", copy=False, index=True,
                                 help="The employee's number registered in her branch's fingerprint device (ZKTeco). Her punches are linked to it automatically")
    photo = fields.Image("Reference photo", max_width=512, max_height=512, groups="navitta_ops.group_navitta_admin",
                         help="A clear face photo that management compares punch photos with")
    email = fields.Char("Email", groups="navitta_ops.group_navitta_admin", help="Alerts and decisions are sent to this address")
    weekly_off = fields.Selection(WEEKDAYS, "Weekly day off")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True)

    pin_hash = fields.Char(groups="base.group_system", copy=False)
    pin_fail_count = fields.Integer(groups="base.group_system", copy=False)
    pin_locked_until = fields.Datetime(groups="base.group_system", copy=False)
    has_pin = fields.Boolean("Has a PIN", compute="_compute_has_pin")

    @api.constrains("slot")
    def _check_slot(self):
        for e in self:
            if e.slot < 1:
                raise ValidationError(_("The order on screen must be 1 or more"))

    @api.constrains("device_user_id", "branch_id", "active")
    def _check_device_user_id(self):
        for e in self.filtered(lambda x: x.device_user_id and x.active):
            if not e.device_user_id.strip().isdigit():
                raise ValidationError(_("The fingerprint device ID must contain digits only (like the employee's number on the device)"))
            other = self.sudo().search([("id", "!=", e.id), ("branch_id", "=", e.branch_id.id), ("active", "=", True),
                                        ("device_user_id", "=", e.device_user_id.strip())], limit=1)
            if other:
                raise ValidationError(_("Fingerprint device ID %(n)s is already used by another employee in the same branch: %(o)s", n=e.device_user_id, o=other.name))

    def init(self):
        # versions before 18.0.1.2 allowed only 2 employees per branch (slot 1/2, unique)
        self.env.cr.execute("DROP INDEX IF EXISTS navitta_employee_branch_slot_active")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("device_user_id"):
                vals["device_user_id"] = str(vals["device_user_id"]).strip()
            if not vals.get("slot") and vals.get("branch_id"):
                last = self.sudo().search([("branch_id", "=", vals["branch_id"])], order="slot desc", limit=1)
                vals["slot"] = (last.slot or 0) + 1
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("device_user_id"):
            vals["device_user_id"] = str(vals["device_user_id"]).strip()
        if "branch_id" in vals:
            from .common import app_today
            moving = self.filtered(lambda e: e.branch_id.id != vals["branch_id"])
            if moving and self.env["navitta.attendance"].sudo().search_count(
                    [("employee_id", "in", moving.ids), ("day", "=", app_today(self.env)), ("check_out", "=", False)]):
                raise UserError(_("The employee is checked in at her branch right now. Move her after she checks out"))
        res = super().write(vals)
        if vals.get("device_user_id") or vals.get("branch_id"):
            # punches that came in before she was linked to her fingerprint number
            self.env["navitta.device.punch"].sudo()._process_unmatched(self)
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_without_history(self):
        for e in self:
            if self.env["navitta.attendance"].sudo().search_count([("employee_id", "=", e.id)], limit=1) or \
                    self.env["navitta.visit"].sudo().search_count([("employee_id", "=", e.id)], limit=1):
                raise UserError(_("%s has attendance or visit records, so she cannot be deleted. Use “Archive” instead of deleting "
                                  "(she disappears from the branch screen and her records are kept).", e.name))

    def action_attendance(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Attendance: %s", self.name), "res_model": "navitta.attendance",
                "view_mode": "kanban,list,form", "domain": [("employee_id", "=", self.id)]}

    def action_sales(self):
        """Her sales day by day: from the cash register (her cashier account) when the branch is linked to one,
        otherwise from the daily report."""
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Sales: %s", self.name), "res_model": "navitta.employee.sales",
                "view_mode": "list,pivot", "domain": [("employee_id", "=", self.id)]}

    def action_cash_counts(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Opening cash count: %s", self.name), "res_model": "navitta.cash.count",
                "view_mode": "list,form", "domain": [("employee_id", "=", self.id)]}

    @api.depends("pin_hash")
    def _compute_has_pin(self):
        for e in self:
            e.has_pin = bool(e.sudo().pin_hash)

    # ------------------------------------------------------------------ PIN
    def _set_pin(self, pin):
        self.ensure_one()
        if not (pin and len(pin) == 4 and pin.isdigit()):
            raise ValidationError(_("The PIN must be 4 digits"))
        self.sudo().write({"pin_hash": CRYPT.hash(pin), "pin_fail_count": 0, "pin_locked_until": False})

    def action_new_pin(self):
        """Admin: generate a new random PIN and show it once."""
        self.ensure_one()
        pin = new_pin()
        self._set_pin(pin)
        wiz = self.env["navitta.pin.wizard"].create({"employee_id": self.id, "pin": pin})
        return {
            "type": "ir.actions.act_window", "res_model": "navitta.pin.wizard", "res_id": wiz.id,
            "view_mode": "form", "target": "new", "name": _("New PIN"),
        }

    def _check_pin(self, pin):
        """None when the PIN is right, else an error code. Failures are saved (no exception is raised).

        Every 5 wrong PINs in a row lock the employee: 1 minute, then 15 minutes, then 1 hour.
        Management gets an email from the 10th wrong PIN in a row.
        """
        self.ensure_one()
        e = self.sudo()
        if not e.active:
            return "no_employee"
        if not e.pin_hash:
            return "no_pin"
        now = fields.Datetime.now()
        if e.pin_locked_until and e.pin_locked_until > now:
            return "locked"
        if CRYPT.verify(pin or "", e.pin_hash):
            if e.pin_fail_count or e.pin_locked_until:
                e.write({"pin_fail_count": 0, "pin_locked_until": False})
            return None
        fails = e.pin_fail_count + 1
        vals = {"pin_fail_count": fails}
        if fails % 5 == 0:
            vals["pin_locked_until"] = now + timedelta(minutes=1 if fails < 10 else 15 if fails < 15 else 60)
            if fails >= 10:
                self.env["navitta.notification"]._notify_admin(
                    _("Wrong PIN attempts: %s", e.name),
                    _("The PIN of %(n)s at %(b)s was entered wrong %(c)s times in a row. It is temporarily locked.", n=e.name, b=e.branch_id.name, c=fails))
        e.write(vals)
        return "wrong_pin"

    # ------------------------------------------------------------------ days off
    def _is_off(self, day):
        """On leave, on a postponed day off, or her weekly day off (unless she covers a colleague that day)."""
        self.ensure_one()
        Leave = self.env["navitta.leave"].sudo()
        if Leave.search_count([("employee_id", "=", self.id), ("from_day", "<=", day), ("to_day", ">=", day)], limit=1):
            return True
        if self.env["navitta.deferred.off"].sudo().search_count([("employee_id", "=", self.id), ("new_day", "=", day)], limit=1):
            return True
        if self.weekly_off and int(self.weekly_off) == dow(day):
            covering = Leave.search_count([("cover_employee_id", "=", self.id), ("from_day", "<=", day), ("to_day", ">=", day)], limit=1)
            return not covering
        return False

    def _mates(self):
        """The other active employees of the same branch."""
        self.ensure_one()
        return self.sudo().search([("branch_id", "=", self.branch_id.id), ("id", "!=", self.id), ("active", "=", True)])

    def _notify(self, subject, body):
        self.env["navitta.notification"]._notify_employee(self, subject, body)


class NavittaNotification(models.Model):
    """Emails to employees and management (late alerts, decisions, replies…), sent through Odoo's mail queue."""
    _name = "navitta.notification"
    _description = "Email notification"
    _order = "id desc"

    employee_id = fields.Many2one("navitta.employee", "Employee", ondelete="set null")
    to_email = fields.Char("To")
    subject = fields.Char("Subject", required=True)
    body = fields.Text("Text", required=True)
    mail_id = fields.Many2one("mail.mail", ondelete="set null", copy=False)
    state = fields.Selection([("no_email", "No email"), ("outgoing", "Waiting to be sent"), ("sent", "Sent"),
                              ("exception", "Sending failed")], compute="_compute_state", string="Status")
    failure_reason = fields.Text(related="mail_id.failure_reason", string="Failure reason")

    @api.depends("mail_id.state", "to_email")
    def _compute_state(self):
        for n in self:
            if not n.to_email:
                n.state = "no_email"
            elif not n.mail_id:
                n.state = "sent"            # the mail was sent and auto-deleted
            elif n.mail_id.state in ("sent", "received"):
                n.state = "sent"
            elif n.mail_id.state in ("exception", "cancel"):
                n.state = "exception"
            else:
                n.state = "outgoing"

    @api.model
    def _html(self, body):
        from markupsafe import Markup, escape
        direction = self.env["res.lang"]._get_data(code=self.env.lang or "en_US").direction or "ltr"
        return Markup(
            '<div dir="%s" style="font-family:Tahoma,Arial,sans-serif;font-size:15px;line-height:1.8;color:#0B0B0C;max-width:560px">'
            '<div style="letter-spacing:.4em;color:#5C5F65;font-size:12px;font-family:Georgia,serif">NAVITTA</div>'
            '<div style="height:2px;background:#C7CACF;margin:6px 0 16px"></div>%s</div>'
        ) % (direction, Markup("<br/>").join(escape(body).split("\n")))

    @api.model
    def _send(self, to_email, subject, body, employee=None, attachments=None):
        vals = {"employee_id": employee and employee.id, "to_email": to_email, "subject": subject, "body": body}
        if to_email:
            company = self.env.company
            mail = self.env["mail.mail"].sudo().create({
                "subject": subject,
                "body_html": self._html(body),
                "email_to": to_email,
                "email_from": company.email_formatted or company.partner_id.email_formatted,
                "auto_delete": False,
                "attachment_ids": [(6, 0, attachments.ids)] if attachments else False,
            })
            vals["mail_id"] = mail.id
        return self.sudo().create(vals)

    @api.model
    def _notify_employee(self, employee, subject, body):
        employee = employee.sudo()
        return self._send(employee.email, subject, body, employee=employee)

    @api.model
    def _notify_admin(self, subject, body):
        return self._send(self.env.company.navitta_report_email, subject, body)
