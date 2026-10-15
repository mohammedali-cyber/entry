from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .common import CRYPT


def _tz_get(self):
    import pytz
    return [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith("Etc/") else "_")]


class ResCompany(models.Model):
    _inherit = "res.company"

    navitta_tz = fields.Selection(_tz_get, string="المنطقة الزمنية للفروع", default="Asia/Riyadh", required=True)
    navitta_shift_start = fields.Float("بداية الدوام", default=15.5)
    navitta_shift_end = fields.Float("نهاية الدوام", default=23.5)
    navitta_grace = fields.Integer("مهلة الحضور (دقائق)", default=5)
    navitta_break_max = fields.Integer("الاستراحة اليومية (دقائق)", default=30)
    navitta_chances = fields.Integer("فرص المسامحة شهريًا", default=3)
    navitta_report_email = fields.Char("إيميل التقرير اليومي")
    navitta_min_roll_invoice = fields.Integer("الحد الأدنى: رول فاتورة", default=12)
    navitta_min_roll_card = fields.Integer("الحد الأدنى: رول شبكة", default=5)
    navitta_min_bags = fields.Integer("الحد الأدنى: أكياس", default=5)
    navitta_vat_rate = fields.Float("نسبة ضريبة القيمة المضافة (%)", default=15.0,
                                    help="المبيعات المسجلة شاملة الضريبة. تُستخدم لحساب الضريبة والصافي في تقارير المبيعات فقط.")
    navitta_checkin_photo = fields.Boolean("صورة مع البصمة", default=True,
                                           help="الآيباد يصوّر الموظفة بالكاميرا الأمامية لحظة كتابة رمزها للحضور والانصراف")
    navitta_cash_tasks = fields.Boolean("عدّ العهدة ومهام الإغلاق", default=True,
                                        help="أول موظفة تعدّ العهدة بعد حضورها قبل فتح العدّاد، وكل موظفة ما تقدر تسجل انصراف "
                                             "قبل ما تقفل زياراتها ويُرسل التقرير اليومي وتعدّ كاش الإغلاق")

    @api.constrains("navitta_shift_start", "navitta_shift_end", "navitta_grace", "navitta_break_max", "navitta_chances")
    def _check_navitta_rules(self):
        for c in self:
            if not (0 <= c.navitta_shift_start < c.navitta_shift_end <= 24):
                raise ValidationError(_("بداية الدوام لازم تكون قبل نهايته"))
            if min(c.navitta_grace, c.navitta_break_max, c.navitta_chances) < 0:
                raise ValidationError(_("القيم لا يمكن أن تكون سالبة"))

    # ------------------------------------------------------------------ management password
    # Used on branch devices to change targets and undo a mistaken check-out. Stored hashed in a
    # system parameter (only system administrators can read parameters).
    def _navitta_pw_key(self):
        return "navitta_ops.admin_pw_hash.%s" % self.id

    def _navitta_set_admin_password(self, pw):
        self.ensure_one()
        if len(pw or "") < 4:
            raise ValidationError(_("كلمة السر لازم 4 خانات أو أكثر"))
        self.env["ir.config_parameter"].sudo().set_param(self._navitta_pw_key(), CRYPT.hash(pw))
        self.env["navitta.branch"].sudo().search([]).write({"pw_fail_count": 0, "pw_locked_until": False})

    def _navitta_admin_password_is_set(self):
        self.ensure_one()
        return bool(self.env["ir.config_parameter"].sudo().get_param(self._navitta_pw_key()))

    def _navitta_verify_admin_password(self, pw):
        self.ensure_one()
        h = self.env["ir.config_parameter"].sudo().get_param(self._navitta_pw_key())
        return bool(h) and CRYPT.verify(pw or "", h)
