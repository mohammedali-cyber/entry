"""Deleting records from the management screens, as on the site (Navitta Pro 26.3, admin_delete / delete_log).

Every 🗑️ in the management app asks for a reason and the separate DELETE password (not the management password that
is typed on the branch devices). The record is deleted on the server and a copy of it goes to «سجل الحذف» (the delete
log: who, when, what kind of record, a short summary, the reason and a copy of the data).

Only Navitta records can be deleted this way (the kinds below); Point of Sale and accounting records never are.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .common import CRYPT

# what the screens may delete: site table name -> Navitta model (the site's admin_delete list, 0008 + 0010, and ideas)
DELETE_KINDS = [
    ("daily_sales", "Sales day"), ("daily_reports", "Daily report"), ("branch_requests", "Branch request"),
    ("expenses", "Expense"), ("defects", "Defective item"), ("shipments", "Shipment"), ("leaves", "Leave"),
    ("ideas", "Idea"), ("lost_sales", "Lost sale"), ("late_cases", "Violation"), ("floats", "Float"), ("rules", "Rule"),
    ("visits", "Visit"), ("attendance", "Attendance"), ("breaks", "Break"), ("leave_requests", "Leave request"),
    ("visit_edit_requests", "Visit duration change request"), ("threads", "Conversation"), ("messages", "Message"),
    ("notifications", "Notification"), ("task_done", "Task done"),
]
DELETE_MODELS = {
    "daily_sales": "navitta.daily.sales", "daily_reports": "navitta.daily.report", "branch_requests": "navitta.branch.request",
    "expenses": "navitta.expense", "defects": "navitta.defect", "shipments": "navitta.shipment", "leaves": "navitta.leave",
    "ideas": "navitta.idea", "lost_sales": "navitta.lost.sale", "late_cases": "navitta.late.case", "floats": "navitta.float",
    "rules": "navitta.rule", "visits": "navitta.visit", "attendance": "navitta.attendance", "breaks": "navitta.break",
    "leave_requests": "navitta.leave.request", "visit_edit_requests": "navitta.visit.edit.request", "threads": "navitta.thread",
    "messages": "navitta.message", "notifications": "navitta.notification", "task_done": "navitta.task.done",
}


class NavittaDeleteLog(models.Model):
    """A copy of every record deleted from the management screens (management-only, like the site's delete_log)."""
    _name = "navitta.delete.log"
    _description = "Delete log"
    _order = "id desc"
    _rec_name = "summary"

    kind = fields.Selection(DELETE_KINDS, "Record", required=True, readonly=True)
    res_model = fields.Char("Model", readonly=True)
    res_id = fields.Integer("Record ID", readonly=True)
    branch_id = fields.Many2one("navitta.branch", "Branch", ondelete="set null", readonly=True, index=True)
    summary = fields.Char("What was deleted", readonly=True)
    reason = fields.Text("Reason", required=True, readonly=True)
    data = fields.Json("Copy of the data", readonly=True)
    data_text = fields.Text("Data copy (text)", compute="_compute_data_text")
    user_id = fields.Many2one("res.users", "Deleted by", default=lambda self: self.env.user, ondelete="set null", readonly=True)

    def _compute_data_text(self):
        import json
        for r in self:
            r.data_text = json.dumps(r.data or {}, ensure_ascii=False, indent=1, default=str)

    @api.model
    def _copy_of(self, rec):
        """The record's stored values (and its lines) as JSON-ready data; binary fields (photos) are left out."""
        def plain(r):
            out = {"id": r.id}
            for name, f in r._fields.items():
                if not f.store or f.type in ("binary", "one2many", "many2many") or name in ("create_uid", "write_uid", "write_date"):
                    continue
                v = r[name]
                if f.type == "many2one":
                    v = [v.id, v.display_name] if v else False
                elif f.type in ("date", "datetime"):
                    v = fields.Datetime.to_string(v) if f.type == "datetime" else fields.Date.to_string(v)
                out[name] = v
            return out
        data = plain(rec)
        for name, f in rec._fields.items():
            if f.store and f.type == "one2many" and rec[name]:
                data[name] = [plain(x) for x in rec[name]]
            elif f.store and f.type == "many2many" and rec[name] and name.endswith("_ids"):
                data[name] = rec[name].ids
        return data


class ResCompany(models.Model):
    _inherit = "res.company"

    # the delete password («كلمة سر الحذف»): separate from the management password; wrong attempts lock it
    navitta_del_pw_fail_count = fields.Integer(groups="base.group_system", copy=False)
    navitta_del_pw_locked_until = fields.Datetime(groups="base.group_system", copy=False)

    def _navitta_del_pw_key(self):
        return "navitta_ops.delete_pw_hash.%s" % self.id

    def _navitta_delete_password_is_set(self):
        self.ensure_one()
        return bool(self.env["ir.config_parameter"].sudo().get_param(self._navitta_del_pw_key()))

    def _navitta_set_delete_password(self, pw):
        self.ensure_one()
        if len(pw or "") < 4:
            raise ValidationError(_("The password must be at least 4 characters"))
        self.env["ir.config_parameter"].sudo().set_param(self._navitta_del_pw_key(), CRYPT.hash(pw))
        self.sudo().write({"navitta_del_pw_fail_count": 0, "navitta_del_pw_locked_until": False})

    def _navitta_check_delete_password(self, pw):
        """None when the delete password is right, else an error code (no_delete_pw / locked / wrong_pw). Same rule as
        the management password: every 5 wrong attempts in a row lock it 5 minutes (an hour from the 15th), and
        management is emailed from the 10th."""
        self.ensure_one()
        c = self.sudo()
        h = self.env["ir.config_parameter"].sudo().get_param(self._navitta_del_pw_key())
        if not h:
            return "no_delete_pw"
        now = fields.Datetime.now()
        if c.navitta_del_pw_locked_until and c.navitta_del_pw_locked_until > now:
            return "locked"
        if CRYPT.verify(pw or "", h):
            if c.navitta_del_pw_fail_count or c.navitta_del_pw_locked_until:
                c.write({"navitta_del_pw_fail_count": 0, "navitta_del_pw_locked_until": False})
            return None
        fails = c.navitta_del_pw_fail_count + 1
        vals = {"navitta_del_pw_fail_count": fails}
        if fails % 5 == 0:
            vals["navitta_del_pw_locked_until"] = now + timedelta(minutes=5 if fails < 15 else 60)
            if fails >= 10:
                self.env["navitta.notification"]._notify_admin(
                    _("Wrong delete password attempts"),
                    _("The delete password was entered wrong %(n)s times in a row (by %(u)s). Deleting is locked for a while.",
                      n=fails, u=self.env.user.name))
        c.write(vals)
        return "wrong_pw"
