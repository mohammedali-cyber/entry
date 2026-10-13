from odoo import _, fields, models

from .common import TOPICS, label


class NavittaThread(models.Model):
    """Private conversation between an employee and management (branch devices never read it directly)."""
    _name = "navitta.thread"
    _description = "رسالة موظفة"
    _order = "last_at desc"
    _rec_name = "topic"

    employee_id = fields.Many2one("navitta.employee", "الموظفة", required=True, index=True, ondelete="restrict")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, string="الفرع")
    topic = fields.Selection(TOPICS, "الموضوع", required=True)
    state = fields.Selection([("open", "مفتوحة"), ("closed", "تم الحل")], "الحالة", default="open", required=True)
    last_at = fields.Datetime("آخر رسالة", default=fields.Datetime.now)
    admin_read_at = fields.Datetime()
    emp_read_at = fields.Datetime()
    message_ids = fields.One2many("navitta.message", "thread_id", "الرسائل")
    unread = fields.Boolean("جديدة", compute="_compute_unread")

    def _compute_unread(self):
        for t in self:
            t.unread = any(m.sender == "emp" and (not t.admin_read_at or m.create_date > t.admin_read_at) for m in t.message_ids)

    def _admin_reply(self, body):
        self.ensure_one()
        body = (body or "").strip()
        if not body:
            return "body_required"
        t = self.sudo()
        self.env["navitta.message"].sudo().create({"thread_id": t.id, "sender": "admin", "body": body})
        now = fields.Datetime.now()
        t.write({"last_at": now, "admin_read_at": now, "state": "open"})
        t.employee_id._notify(_("رد الإدارة: %s", label(TOPICS, t.topic)),
                              body + "\n\n" + _("افتحي «تنبيهاتي» برمزك السري للرد. إدارة نافيتا"))
        return None


class NavittaMessage(models.Model):
    _name = "navitta.message"
    _description = "رسالة"
    _order = "create_date, id"

    thread_id = fields.Many2one("navitta.thread", required=True, ondelete="cascade", index=True)
    sender = fields.Selection([("emp", "الموظفة"), ("admin", "الإدارة")], required=True)
    body = fields.Text(required=True)
