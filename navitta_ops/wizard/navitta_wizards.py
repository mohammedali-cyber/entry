import base64
import io

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# column titles in the models file -> catalog fields
HEADERS = {"alu": "alu", "desc4": "model", "desc1": "item_no", "attr": "color", "size": "size",
           "price wt": "price", "price": "price", "dcs": "dcs", "heel": "heel", "material": "material"}


class NavittaPinWizard(models.TransientModel):
    _name = "navitta.pin.wizard"
    _description = "عرض الرمز السري الجديد"

    employee_id = fields.Many2one("navitta.employee", readonly=True)
    pin = fields.Char("الرمز", readonly=True)


class NavittaAdminPasswordWizard(models.TransientModel):
    _name = "navitta.admin.password.wizard"
    _description = "كلمة سر الإدارة لأجهزة الفروع"

    # not required at database level: the typed password is wiped from the wizard right after saving
    password = fields.Char("كلمة السر الجديدة")
    confirm = fields.Char("تأكيد كلمة السر")

    def action_save(self):
        self.ensure_one()
        if not self.password:
            raise UserError(_("اكتب كلمة السر الجديدة"))
        if self.password != self.confirm:
            raise UserError(_("كلمتا السر غير متطابقتين"))
        self.env.company._navitta_set_admin_password(self.password)
        self.write({"password": False, "confirm": False})
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("تم حفظ كلمة سر الإدارة"), "type": "success", "next": {"type": "ir.actions.act_window_close"}}}


class NavittaCatalogImportWizard(models.TransientModel):
    _name = "navitta.catalog.import.wizard"
    _description = "رفع ملف الموديلات"

    file = fields.Binary("ملف Excel (.xlsx)", required=True)
    filename = fields.Char()
    info = fields.Char(compute="_compute_info")

    def _compute_info(self):
        x = self.env["navitta.catalog.item"]._info()
        for w in self:
            w.info = _("الحالي: %(m)s موديل · %(n)s قطعة", m=x["models"], n=x["items"]) if x["items"] else _("ما فيه ملف موديلات بعد")

    @staticmethod
    def _cell(v):
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    def _rows(self):
        """Reads the sheet: finds the header row (with Desc1) in the first 10 rows, then one item per row."""
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(self.file)), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - any unreadable file gets the same message
            msg = str(e).lower()
            if any(k in msg for k in ("password", "encrypt", "zip")):
                raise UserError(_("الملف محمي بكلمة سر. افتحه في Excel واحفظه بدون كلمة سر وارفعه مرة ثانية")) from e
            raise UserError(_("تعذر قراءة الملف. تأكد أنه ملف Excel بصيغة xlsx")) from e
        rows = list(wb.active.iter_rows(values_only=True))
        head_i = next((i for i, r in enumerate(rows[:10]) if any(self._cell(c).lower() == "desc1" for c in r)), None)
        if head_i is None:
            raise UserError(_("ما لقينا الأعمدة (ALU, Desc4, Desc1, Attr, Size …) في أول الملف"))
        cols = [HEADERS.get(self._cell(c).lower()) for c in rows[head_i]]
        out = []
        for n, r in enumerate(rows[head_i + 1:], start=1):
            o = {"sequence": n}
            for k, v in zip(cols, r):
                if k and v is not None and self._cell(v) != "":
                    o[k] = self._cell(v)
            o["alu"] = (o.get("alu") or "").lstrip("0")
            if o["alu"] and o.get("item_no") and o.get("model"):
                o["color"] = o.get("color") or "-"
                try:
                    o["price"] = float(o["price"]) if o.get("price") else 0.0
                except ValueError:
                    o["price"] = 0.0
                out.append(o)
        if not out:
            raise UserError(_("الملف فاضي"))
        return out

    def action_import(self):
        """Replaces the whole models file (the new file is the full list)."""
        self.ensure_one()
        rows = self._rows()
        Item = self.env["navitta.catalog.item"].sudo()
        by_alu = {}
        for o in rows:            # last line wins for a repeated barcode
            by_alu[o["alu"]] = o
        vals = list(by_alu.values())
        Item.search([]).unlink()
        for i in range(0, len(vals), 1000):
            Item.create(vals[i:i + 1000])
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("تم تحديث ملف الموديلات: %s قطعة", len(by_alu)), "type": "success",
                           "next": {"type": "ir.actions.act_window_close"}}}


class NavittaSummaryWizard(models.TransientModel):
    _name = "navitta.summary.wizard"
    _description = "طباعة ملخص الفروع اليومي"

    day = fields.Date("اليوم", required=True, default=lambda self: self._default_day())

    @api.model
    def _default_day(self):
        from ..models.common import app_today
        return app_today(self.env)

    def action_print(self):
        self.ensure_one()
        return self.env.ref("navitta_ops.action_report_daily_summary").report_action(self, data={"day": fields.Date.to_string(self.day)})

    def action_send(self):
        self.ensure_one()
        if not self.env.company.navitta_report_email:
            raise UserError(_("حدد إيميل التقرير اليومي من الإعدادات أولًا"))
        self.env["navitta.api"].with_context(navitta_day=fields.Date.to_string(self.day))._cron_daily_summary()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("انضاف التقرير لقائمة الإرسال"), "type": "success"}}
