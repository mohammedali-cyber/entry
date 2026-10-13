"""Fingerprint terminals (ZKTeco and compatible) that push attendance to Odoo over the internet.

The device is set to "Cloud server / ADMS" with this Odoo's address. It then calls /iclock/... (see controllers/iclock.py)
and uploads every punch as a line "user id <TAB> local time <TAB> status <TAB> verify mode <TAB> work code".
Each punch is kept as it came (navitta.device.punch, unique per device + user id + time, so a re-sent batch is not
counted twice) and turned into check-in / check-out:

- the first punch of the employee's day is her check-in (late rules as on the iPad);
- an earlier punch that arrives later (device was offline) moves the check-in back;
- any punch more than 2 minutes after the check-in is her check-out (the last one wins);
- the status key pressed on the device (in / out) is ignored: staff rarely press it, the order of punches is reliable.
"""
import logging
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import company_tz

_logger = logging.getLogger(__name__)

DUPLICATE_SECONDS = 120
VERIFY_MODES = {0: "رمز", 1: "بصمة إصبع", 2: "بطاقة", 3: "رمز", 4: "بطاقة", 5: "بصمة + بطاقة", 6: "بصمة + رمز",
                7: "بطاقة + رمز", 8: "بطاقة + بصمة + رمز", 15: "وجه", 25: "كف اليد"}


class NavittaDevice(models.Model):
    _name = "navitta.device"
    _description = "جهاز بصمة"
    _order = "branch_id, name"

    name = fields.Char("اسم الجهاز", required=True, default=lambda self: _("جهاز بصمة"))
    serial_number = fields.Char("الرقم التسلسلي (SN)", required=True, copy=False, index=True,
                                help="مكتوب على ملصق الجهاز وفي قائمته: معلومات النظام ← معلومات الجهاز")
    branch_id = fields.Many2one("navitta.branch", "الفرع", index=True, ondelete="restrict",
                                help="بصمات هذا الجهاز تنحسب لموظفات هذا الفرع")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    state = fields.Selection([("pending", "بانتظار الاعتماد"), ("active", "معتمد"), ("blocked", "موقوف")],
                             "الحالة", default="active", required=True,
                             help="جهاز جديد يتصل لأول مرة ينضاف «بانتظار الاعتماد»، وما تنقبل بصماته لين تعتمده وتحدد فرعه")
    allowed_ip = fields.Char("يقبل فقط من IP", help="اختياري: عنوان IP الثابت لإنترنت الفرع. فاضي = أي عنوان")
    last_seen = fields.Datetime("آخر اتصال", readonly=True)
    last_ip = fields.Char("آخر IP", readonly=True)
    info = fields.Text("معلومات الجهاز", readonly=True, help="الإصدار وعدد المستخدمين والبصمات كما يرسلها الجهاز")
    attlog_stamp = fields.Char("آخر علامة استلام", readonly=True, copy=False)
    online = fields.Boolean("متصل", compute="_compute_online")
    punch_ids = fields.One2many("navitta.device.punch", "device_id", "البصمات")
    punch_count = fields.Integer("عدد البصمات", compute="_compute_punch_count")
    unmatched_count = fields.Integer("بصمات بدون موظفة", compute="_compute_punch_count")

    _sql_constraints = [("sn_uniq", "unique(serial_number)", "هذا الرقم التسلسلي مسجل لجهاز ثاني")]

    def _compute_online(self):
        limit = fields.Datetime.now() - timedelta(minutes=10)
        for d in self:
            d.online = bool(d.last_seen and d.last_seen >= limit)

    def _compute_punch_count(self):
        Punch = self.env["navitta.device.punch"].sudo()
        for d in self:
            d.punch_count = Punch.search_count([("device_id", "=", d.id)])
            d.unmatched_count = Punch.search_count([("device_id", "=", d.id), ("state", "=", "unmatched")])

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("serial_number"):
                vals["serial_number"] = vals["serial_number"].strip()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("serial_number"):
            vals["serial_number"] = vals["serial_number"].strip()
        return super().write(vals)

    @api.constrains("state", "branch_id")
    def _check_branch(self):
        for d in self:
            if d.state == "active" and not d.branch_id:
                raise UserError(_("حدد فرع الجهاز قبل اعتماده"))

    def action_approve(self):
        self.write({"state": "active"})

    def action_block(self):
        self.write({"state": "blocked"})

    def action_reprocess(self):
        self.env["navitta.device.punch"].sudo().search(
            [("device_id", "in", self.ids), ("state", "in", ("unmatched", "error"))])._process()

    def action_punches(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("بصمات %s", self.name), "res_model": "navitta.device.punch",
                "view_mode": "list,form", "domain": [("device_id", "=", self.id)]}

    # ------------------------------------------------------------------ protocol side (called by controllers/iclock.py)
    @api.model
    def _from_request(self, sn, ip):
        """The device record for a request. An unknown serial number is added as pending (at most 20 pending)."""
        sn = (sn or "").strip()
        if not sn or len(sn) > 40 or not all(ch.isalnum() or ch in "-_" for ch in sn):
            return self
        dev = self.sudo().search([("serial_number", "=", sn)], limit=1)
        if not dev:
            if self.sudo().search_count([("state", "=", "pending")]) >= 20:
                return self
            dev = self.sudo().create({"name": _("جهاز جديد %s", sn), "serial_number": sn, "state": "pending"})
            _logger.info("Navitta: new fingerprint device %s from %s waits for approval", sn, ip)
        dev.sudo().write({"last_seen": fields.Datetime.now(), "last_ip": ip})
        return dev

    def _accepts(self, ip):
        self.ensure_one()
        if self.state != "active" or not self.branch_id:
            return False
        return not self.allowed_ip or self.allowed_ip.strip() == ip

    def _tz(self):
        return pytz.timezone(self.branch_id.company_id.navitta_tz or "Asia/Riyadh") if self.branch_id \
            else company_tz(self.env)

    def _receive_attlog(self, body):
        """Stores the punch lines of an ATTLOG upload and processes them. Returns the number of lines read."""
        self.ensure_one()
        Punch = self.env["navitta.device.punch"].sudo()
        tz, n, new = self._tz(), 0, Punch
        now = fields.Datetime.now()
        for line in (body or "").splitlines():
            parts = line.rstrip("\r").split("\t")
            if len(parts) < 2 or not parts[0].strip():
                continue
            n += 1
            uid = parts[0].strip()
            try:
                local = datetime.strptime(parts[1].strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                _logger.warning("Navitta: device %s sent a bad time: %r", self.serial_number, line[:80])
                continue
            when = tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)
            if when > now + timedelta(minutes=10) or when < now - timedelta(days=60):
                state, msg = "error", _("وقت البصمة بعيد عن وقت السيرفر (ساعة الجهاز غلط؟)")
            else:
                state, msg = "new", False

            def num(i):
                try:
                    return int(parts[i])
                except (IndexError, ValueError):
                    return 0
            if Punch.search_count([("device_id", "=", self.id), ("device_user_id", "=", uid), ("punched_at", "=", when)], limit=1):
                continue          # the device sent this batch before
            new |= Punch.create({"device_id": self.id, "device_user_id": uid, "punched_at": when, "status": num(2),
                                 "verify_mode": num(3), "state": state, "message": msg, "raw": line[:200]})
        new.filtered(lambda p: p.state == "new").sorted("punched_at")._process()
        return n


class NavittaDevicePunch(models.Model):
    _name = "navitta.device.punch"
    _description = "بصمة من جهاز البصمة"
    _order = "punched_at desc, id desc"
    _rec_name = "device_user_id"

    device_id = fields.Many2one("navitta.device", "الجهاز", required=True, index=True, ondelete="cascade")
    branch_id = fields.Many2one(related="device_id.branch_id", store=True, string="الفرع")
    device_user_id = fields.Char("رقم البصمة", required=True, index=True)
    punched_at = fields.Datetime("وقت البصمة", required=True, index=True)
    status = fields.Integer("المفتاح المضغوط", help="0 حضور، 1 انصراف، 2-5 استراحة/إضافي (حسب إعداد الجهاز). لا يُعتمد عليه")
    verify_mode = fields.Integer("طريقة التحقق")
    verify_label = fields.Char("طريقة التحقق ", compute="_compute_verify_label")
    employee_id = fields.Many2one("navitta.employee", "الموظفة", index=True, ondelete="set null")
    attendance_id = fields.Many2one("navitta.attendance", "سجل الحضور", ondelete="set null")
    state = fields.Selection([("new", "جديدة"), ("in", "حضور"), ("out", "انصراف"), ("duplicate", "مكررة"),
                              ("unmatched", "بدون موظفة"), ("error", "خطأ")], "النتيجة", default="new", required=True, index=True)
    message = fields.Char("ملاحظة")
    raw = fields.Char("السطر كما وصل")

    _sql_constraints = [("punch_uniq", "unique(device_id, device_user_id, punched_at)", "البصمة مسجلة مسبقًا")]

    @api.depends("verify_mode")
    def _compute_verify_label(self):
        for p in self:
            p.verify_label = VERIFY_MODES.get(p.verify_mode, str(p.verify_mode))

    def _process(self):
        """Turns punches into check-in / check-out (oldest first)."""
        Att = self.env["navitta.attendance"].sudo()
        Emp = self.env["navitta.employee"].sudo()
        for p in self.sorted("punched_at"):
            dev = p.device_id
            emp = Emp.search([("branch_id", "=", dev.branch_id.id), ("device_user_id", "=", p.device_user_id),
                              ("active", "=", True)], limit=1) if dev.branch_id else Emp
            if not emp:
                p.write({"state": "unmatched", "employee_id": False,
                         "message": _("ما فيه موظفة في %(b)s رقم بصمتها %(u)s", b=dev.branch_id.name or "—", u=p.device_user_id)})
                continue
            day = pytz.utc.localize(p.punched_at).astimezone(dev._tz()).date()
            att = Att.search([("employee_id", "=", emp.id), ("day", "=", day)], limit=1)
            vals = {"employee_id": emp.id, "message": False}
            if not att:
                att, late = Att._register_in(emp, p.punched_at, "device", device=dev)
                vals.update(state="in", attendance_id=att.id, message=_("متأخرة %s دقيقة", late) if late else False)
            elif p.punched_at < att.check_in - timedelta(seconds=DUPLICATE_SECONDS):
                # an earlier punch delivered late: it is the real check-in
                att.write({"check_in": p.punched_at, "in_source": "device", "in_device_id": dev.id, "in_photo_id": False})
                late = att._apply_late()
                vals.update(state="in", attendance_id=att.id, message=_("عدّلت وقت الحضور (وصلت متأخرة من الجهاز)"))
            elif p.punched_at <= att.check_in + timedelta(seconds=DUPLICATE_SECONDS):
                vals.update(state="duplicate", attendance_id=att.id, message=_("بصمة مكررة خلال دقيقتين"))
            elif att.check_out and p.punched_at <= att.check_out:
                vals.update(state="duplicate", attendance_id=att.id, message=_("قبل آخر انصراف مسجل"))
            else:
                att._register_out(p.punched_at, "device", device=dev)
                vals.update(state="out", attendance_id=att.id)
            p.write(vals)

    @api.model
    def _process_unmatched(self, employees):
        """After an employee gets her fingerprint number (or moves branch): her waiting punches."""
        for e in employees.filtered(lambda x: x.device_user_id and x.active):
            self.sudo().search([("state", "=", "unmatched"), ("device_user_id", "=", e.device_user_id),
                                ("device_id.branch_id", "=", e.branch_id.id)])._process()

    def action_reprocess(self):
        self.filtered(lambda p: p.state in ("unmatched", "error", "new"))._process()
