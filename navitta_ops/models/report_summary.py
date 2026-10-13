from odoo import api, fields, models

from .common import app_today


class ReportDailySummary(models.AbstractModel):
    _name = "report.navitta_ops.report_daily_summary"
    _description = "ملخص الفروع اليومي (PDF)"

    @api.model
    def _get_report_values(self, docids, data=None):
        if not (self.env.su or self.env.user.has_group("navitta_ops.group_navitta_admin")):
            from odoo.exceptions import AccessError
            raise AccessError("Navitta management only")
        day = fields.Date.to_date((data or {}).get("day")) or app_today(self.env)
        return {
            "doc_ids": docids, "s": self.env["navitta.api"]._summary_data(day), "company": self.env.company,
            "f0": lambda n: "{:,.0f}".format(n or 0),
            "pct": lambda v: "%.1f%%" % v if v is not None else "—",
        }
