import base64
import binascii
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import EXPENSE_CATEGORIES, SUPPLIES, label

MAX_PHOTO_BYTES = 1_500_000           # photos are compressed on the device (~1280px JPEG) before upload
PHOTO_MIMES = ("image/jpeg", "image/png", "image/webp")


class NavittaPhoto(models.Model):
    """Receipt or item photo taken on a branch device (or on a phone through a QR code)."""
    _name = "navitta.photo"
    _description = "صورة (إيصال أو قطعة)"
    _order = "id desc"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    image = fields.Binary("الصورة", attachment=True, required=True)
    mimetype = fields.Char(default="image/jpeg")

    @api.model
    def _from_b64(self, branch, data, mime="image/jpeg"):
        """Creates a photo from base64 data (no data: prefix). Returns (photo, error_code)."""
        mime = mime if mime in PHOTO_MIMES else "image/jpeg"
        try:
            raw = base64.b64decode(data or "", validate=True)
        except (binascii.Error, ValueError):
            return self, "bad_file"
        if len(raw) < 100 or len(raw) > MAX_PHOTO_BYTES:
            return self, "bad_file"
        sig = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp"}
        if not any(raw.startswith(k) for k in sig):
            return self, "bad_file"
        return self.sudo().create({"branch_id": branch.id, "image": data, "mimetype": mime}), None


class NavittaUploadToken(models.Model):
    """A 10-minute code shown as a QR on the branch device so an employee's phone can send one photo (no login)."""
    _name = "navitta.upload.token"
    _description = "رمز رفع صورة من الجوال"

    token = fields.Char(required=True, index=True, copy=False)
    branch_id = fields.Many2one("navitta.branch", required=True, ondelete="cascade")
    photo_id = fields.Many2one("navitta.photo", ondelete="set null")
    expires_at = fields.Datetime(required=True)

    _sql_constraints = [("token_uniq", "unique(token)", "duplicate token")]

    @api.model
    def _new(self, branch):
        self.sudo().search([("expires_at", "<", fields.Datetime.now() - timedelta(days=1))]).unlink()
        return self.sudo().create({"token": secrets.token_urlsafe(18), "branch_id": branch.id,
                                   "expires_at": fields.Datetime.now() + timedelta(minutes=10)})

    @api.model
    def _valid(self, token):
        if not token or len(token) > 64:
            return self
        return self.sudo().search([("token", "=", token), ("expires_at", ">", fields.Datetime.now())], limit=1)


class NavittaBranchRequest(models.Model):
    _name = "navitta.branch.request"
    _description = "طلب فرع (نواقص أو مستلزمات)"
    _order = "create_date desc"
    _rec_name = "model"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    kind = fields.Selection([("goods", "نواقص بضاعة"), ("supply", "مستلزمات")], "النوع", required=True)
    supply = fields.Selection(SUPPLIES, "المستلزم")
    employee_id = fields.Many2one("navitta.employee", "طلبته", ondelete="set null")
    model = fields.Char("الموديل / الصنف")
    color = fields.Char("اللون")
    size = fields.Char("المقاس")
    qty = fields.Integer("الكمية", required=True, default=1)
    reason = fields.Char("السبب")
    note = fields.Text("ملاحظة")
    urgent = fields.Boolean("عاجل")
    photo_id = fields.Many2one("navitta.photo", "صورة القطعة", ondelete="set null")
    photo = fields.Binary(related="photo_id.image", string="الصورة")
    state = fields.Selection([("pending", "بانتظار الإدارة"), ("approved", "وافقت الإدارة"), ("shipped", "انشحن"),
                              ("received", "وصل"), ("rejected", "مرفوض")], "الحالة", default="pending", required=True, index=True)
    admin_note = fields.Text("ملاحظة الإدارة")
    decided_at = fields.Datetime("وقت القرار")
    shipped_at = fields.Datetime("وقت الشحن")
    received_at = fields.Datetime("وقت الاستلام")
    received_by = fields.Many2one("navitta.employee", "استلمته", ondelete="set null")

    _sql_constraints = [("qty_pos", "CHECK(qty > 0)", "الكمية لازم أكبر من صفر")]

    def _what(self):
        self.ensure_one()
        if self.kind == "goods":
            s = " · ".join(x for x in (self.model, self.color, self.size) if x)
        else:
            s = self.model if self.supply == "other" else label(SUPPLIES, self.supply)
        return "%s × %s" % (s, self.qty)

    def _set_state(self, state, note=None):
        """Management: approved / shipped / rejected. Returns an error code or None."""
        self.ensure_one()
        r = self.sudo()
        if state not in ("approved", "shipped", "rejected") or r.state in ("received", "rejected"):
            return "not_found"
        now = fields.Datetime.now()
        r.write({"state": state, "admin_note": (note or "").strip() or r.admin_note,
                 "decided_at": r.decided_at or now, "shipped_at": now if state == "shipped" else r.shipped_at})
        if r.employee_id:
            st = {"approved": _("وافقت الإدارة"), "shipped": _("انشحن 🚚"), "rejected": _("مرفوض")}[state]
            r.employee_id._notify(_("تحديث طلبك"), _("طلبك %(w)s: %(s)s", w=r._what(), s=st) + (" · " + note.strip() if (note or "").strip() else ""))
        return None

    def _receive(self, employee):
        self.ensure_one()
        r = self.sudo()
        if r.state not in ("approved", "shipped") or r.branch_id != employee.branch_id:
            return "not_found"
        r.write({"state": "received", "received_at": fields.Datetime.now(), "received_by": employee.id})
        if r.kind == "supply" and r.supply in ("roll_invoice", "roll_card", "bags"):
            from .common import app_today
            self.env["navitta.supply.move"].sudo().create({
                "branch_id": r.branch_id.id, "day": app_today(self.env), "supply": r.supply, "qty": r.qty,
                "kind": "received", "request_id": r.id})
        return None

    def action_approve(self):
        self._button("approved")

    def action_ship(self):
        self._button("shipped")

    def action_reject(self):
        self._button("rejected")

    def _button(self, state):
        for r in self:
            if r._set_state(state, r.admin_note):
                raise UserError(_("لا يمكن تغيير حالة هذا الطلب"))


class NavittaSupplyMove(models.Model):
    """Supply ledger: + received / − used. The balance is the sum."""
    _name = "navitta.supply.move"
    _description = "حركة مستلزمات"
    _order = "day desc, id desc"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    day = fields.Date("اليوم", required=True, index=True)
    supply = fields.Selection([s for s in SUPPLIES if s[0] != "other"], "المستلزم", required=True)
    qty = fields.Integer("الكمية", required=True)
    kind = fields.Selection([("opening", "افتتاحي"), ("received", "مستلم"), ("used", "مستخدم"), ("adjust", "تعديل")],
                            "الحركة", required=True)
    request_id = fields.Many2one("navitta.branch.request", "الطلب", ondelete="set null")


class NavittaExpense(models.Model):
    _name = "navitta.expense"
    _description = "مصروف فرع"
    _order = "day desc, id desc"
    _rec_name = "details"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="restrict")
    day = fields.Date("اليوم", required=True, index=True)
    employee_id = fields.Many2one("navitta.employee", "سجلته", ondelete="set null")
    amount = fields.Float("المبلغ", digits=(10, 2), required=True)
    category = fields.Selection(EXPENSE_CATEGORIES, "النوع", required=True)
    details = fields.Text("التفاصيل", required=True)
    receipt_id = fields.Many2one("navitta.photo", "الإيصال", ondelete="set null")
    receipt = fields.Binary(related="receipt_id.image", string="صورة الإيصال")
    has_receipt = fields.Boolean("فيه إيصال", compute="_compute_has_receipt", store=True)
    state = fields.Selection([("pending", "بانتظار الاعتماد"), ("approved", "معتمد"), ("rejected", "مرفوض")],
                             "الحالة", default="pending", required=True, index=True)
    admin_note = fields.Text("ملاحظة الإدارة")
    decided_at = fields.Datetime("وقت القرار")

    _sql_constraints = [("amount_pos", "CHECK(amount > 0)", "المبلغ لازم أكبر من صفر")]

    @api.depends("receipt_id")
    def _compute_has_receipt(self):
        for x in self:
            x.has_receipt = bool(x.receipt_id)

    def _decide(self, ok, note=None):
        self.ensure_one()
        x = self.sudo()
        if x.state != "pending":
            return "not_found"
        x.write({"state": "approved" if ok else "rejected", "admin_note": (note or "").strip() or False,
                 "decided_at": fields.Datetime.now()})
        if x.employee_id:
            x.employee_id._notify(_("اعتُمد المصروف") if ok else _("رُفض المصروف"),
                                  "%s ر.س · %s%s" % (x.amount, label(EXPENSE_CATEGORIES, x.category),
                                                     " · " + note.strip() if (note or "").strip() else ""))
        return None

    def action_approve(self):
        for x in self:
            if x._decide(True, x.admin_note):
                raise UserError(_("هذا المصروف تم التعامل معه"))

    def action_reject(self):
        for x in self:
            if x._decide(False, x.admin_note):
                raise UserError(_("هذا المصروف تم التعامل معه"))
