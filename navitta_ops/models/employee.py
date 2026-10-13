from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import sql

from .common import CRYPT, WEEKDAYS, dow, new_pin


class NavittaEmployee(models.Model):
    _name = "navitta.employee"
    _description = "موظفة فرع"
    _order = "branch_id, slot"

    name = fields.Char("الاسم", required=True)
    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    slot = fields.Integer("رقمها في الفرع", required=True, default=1, help="1 أو 2: مكانها في شاشة العدّاد")
    email = fields.Char("الإيميل", groups="navitta_ops.group_navitta_admin", help="تُرسل إليه التنبيهات والقرارات")
    weekly_off = fields.Selection(WEEKDAYS, "الإجازة الأسبوعية")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True)

    pin_hash = fields.Char(groups="base.group_system", copy=False)
    pin_fail_count = fields.Integer(groups="base.group_system", copy=False)
    pin_locked_until = fields.Datetime(groups="base.group_system", copy=False)
    has_pin = fields.Boolean("لها رمز سري", compute="_compute_has_pin")

    @api.constrains("slot")
    def _check_slot(self):
        for e in self:
            if e.slot not in (1, 2):
                raise ValidationError(_("رقم الموظفة في الفرع لازم يكون 1 أو 2"))

    def init(self):
        # one active employee per slot in a branch (replaced employees are archived)
        if not sql.index_exists(self.env.cr, "navitta_employee_branch_slot_active"):
            self.env.cr.execute("CREATE UNIQUE INDEX navitta_employee_branch_slot_active "
                                "ON navitta_employee (branch_id, slot) WHERE active")

    def _compute_has_pin(self):
        for e in self:
            e.has_pin = bool(e.sudo().pin_hash)

    # ------------------------------------------------------------------ PIN
    def _set_pin(self, pin):
        self.ensure_one()
        if not (pin and len(pin) == 4 and pin.isdigit()):
            raise ValidationError(_("الرمز لازم 4 أرقام"))
        self.sudo().write({"pin_hash": CRYPT.hash(pin), "pin_fail_count": 0, "pin_locked_until": False})

    def action_new_pin(self):
        """Admin: generate a new random PIN and show it once."""
        self.ensure_one()
        pin = new_pin()
        self._set_pin(pin)
        wiz = self.env["navitta.pin.wizard"].create({"employee_id": self.id, "pin": pin})
        return {
            "type": "ir.actions.act_window", "res_model": "navitta.pin.wizard", "res_id": wiz.id,
            "view_mode": "form", "target": "new", "name": _("الرمز السري الجديد"),
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
                    _("محاولات خاطئة لرمز %s", e.name),
                    _("الرمز السري لـ %(n)s في %(b)s أُدخل خطأ %(c)s مرات متتالية. انقفل مؤقتًا.", n=e.name, b=e.branch_id.name, c=fails))
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

    def _mate(self):
        """The other active employee of the same branch."""
        self.ensure_one()
        return self.sudo().search([("branch_id", "=", self.branch_id.id), ("slot", "!=", self.slot), ("active", "=", True)], limit=1)

    def _notify(self, subject, body):
        self.env["navitta.notification"]._notify_employee(self, subject, body)


class NavittaNotification(models.Model):
    """Emails to employees and management (late alerts, decisions, replies…), sent through Odoo's mail queue."""
    _name = "navitta.notification"
    _description = "إشعار بالإيميل"
    _order = "id desc"

    employee_id = fields.Many2one("navitta.employee", "الموظفة", ondelete="set null")
    to_email = fields.Char("إلى")
    subject = fields.Char("الموضوع", required=True)
    body = fields.Text("النص", required=True)
    mail_id = fields.Many2one("mail.mail", ondelete="set null", copy=False)
    state = fields.Selection([("no_email", "لا يوجد إيميل"), ("outgoing", "بانتظار الإرسال"), ("sent", "أُرسل"),
                              ("exception", "فشل الإرسال")], compute="_compute_state", string="الحالة")
    failure_reason = fields.Text(related="mail_id.failure_reason", string="سبب الفشل")

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
        return Markup(
            '<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;font-size:15px;line-height:1.8;color:#0B0B0C;max-width:560px">'
            '<div style="letter-spacing:.4em;color:#5C5F65;font-size:12px;font-family:Georgia,serif">NAVITTA</div>'
            '<div style="height:2px;background:#C7CACF;margin:6px 0 16px"></div>%s</div>'
        ) % Markup("<br/>").join(escape(body).split("\n"))

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
