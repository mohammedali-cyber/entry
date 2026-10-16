from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .common import CRYPT


def _tz_get(self):
    import pytz
    return [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith("Etc/") else "_")]


class ResCompany(models.Model):
    _inherit = "res.company"

    navitta_tz = fields.Selection(_tz_get, string="Branches time zone", default="Asia/Riyadh", required=True)
    # the evening shift (every branch); the morning shift only in branches that have one (navitta.branch.two_shifts)
    navitta_shift_start = fields.Float("Evening shift start", default=15.5)
    navitta_shift_end = fields.Float("Evening shift end", default=23.5)
    navitta_am_start = fields.Float("Morning shift start", default=9.5)
    navitta_am_end = fields.Float("Morning shift end", default=16.0)
    navitta_no_am_days = fields.Char("Days without a morning shift", default="0,1,5",
                                     help="Weekdays (0 = Sunday … 6 = Saturday) when morning employees work the evening shift")
    navitta_grace = fields.Integer("Check-in grace (minutes)", default=5)
    navitta_break_max = fields.Integer("Daily break (minutes)", default=30)
    navitta_chances = fields.Integer("Grace chances per month", default=3)
    navitta_report_email = fields.Char("Daily report email")
    navitta_min_roll_invoice = fields.Integer("Minimum: receipt rolls", default=12)
    navitta_min_roll_card = fields.Integer("Minimum: card rolls", default=5)
    navitta_min_bags = fields.Integer("Minimum: bags", default=5)
    navitta_vat_rate = fields.Float("VAT rate (%)", default=15.0,
                                    help="Recorded sales include VAT. Used only to compute VAT and net amounts in sales reports.")
    navitta_checkin_photo = fields.Boolean("Photo with punch", default=True,
                                           help="The iPad takes a photo of the employee with the front camera when she enters her PIN for check-in and check-out")
    navitta_cash_tasks = fields.Boolean("Opening cash count and closing tasks", default=True,
                                        help="The first employee counts the cash float after check-in before opening the counter, and no employee "
                                             "can check out before closing her visits, sending the daily report and counting the closing cash")

    @api.constrains("navitta_shift_start", "navitta_shift_end", "navitta_am_start", "navitta_am_end", "navitta_grace",
                    "navitta_break_max", "navitta_chances")
    def _check_navitta_rules(self):
        for c in self:
            if not (0 <= c.navitta_shift_start < c.navitta_shift_end <= 24) or not (0 <= c.navitta_am_start < c.navitta_am_end <= 24):
                raise ValidationError(_("The shift start must be before its end"))
            if min(c.navitta_grace, c.navitta_break_max, c.navitta_chances) < 0:
                raise ValidationError(_("Values cannot be negative"))

    def _navitta_no_am(self):
        """Weekdays without a morning shift (0 = Sunday … 6 = Saturday)."""
        self.ensure_one()
        return {int(x) for x in (self.navitta_no_am_days or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6}

    def _navitta_lang(self):
        """Language for texts generated in the background (crons, fingerprint device, phone page) for staff:
        the company's language, as OdooBot / the public user usually run in English."""
        self.ensure_one()
        return self.partner_id.lang or self.env.lang or "en_US"

    # ------------------------------------------------------------------ management password
    # Used on branch devices to change targets and undo a mistaken check-out. Stored hashed in a
    # system parameter (only system administrators can read parameters).
    def _navitta_pw_key(self):
        return "navitta_ops.admin_pw_hash.%s" % self.id

    def _navitta_set_admin_password(self, pw):
        self.ensure_one()
        if len(pw or "") < 4:
            raise ValidationError(_("The password must be at least 4 characters"))
        self.env["ir.config_parameter"].sudo().set_param(self._navitta_pw_key(), CRYPT.hash(pw))
        self.env["navitta.branch"].sudo().search([]).write({"pw_fail_count": 0, "pw_locked_until": False})

    def _navitta_admin_password_is_set(self):
        self.ensure_one()
        return bool(self.env["ir.config_parameter"].sudo().get_param(self._navitta_pw_key()))

    def _navitta_verify_admin_password(self, pw):
        self.ensure_one()
        h = self.env["ir.config_parameter"].sudo().get_param(self._navitta_pw_key())
        return bool(h) and CRYPT.verify(pw or "", h)
