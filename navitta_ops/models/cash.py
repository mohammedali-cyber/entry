from odoo import _, api, fields, models

from .common import iso

# Saudi riyal notes counted one by one; coins (1 and 2 riyal, halalas) go in one amount
DENOMS = (500, 200, 100, 50, 20, 10, 5, 1)
KINDS = [("open", "عهدة الافتتاح"), ("close", "كاش الإغلاق")]


class NavittaCashCount(models.Model):
    """A count of the branch's cash, note by note.

    «open»: the first employee in the branch counts the custody after her check-in, before the counter opens;
    it is compared with the last closing count of the branch.
    «close»: each employee counts the cash before her check-out; it is compared with the opening custody plus
    the day's cash sales minus the bank deposit."""
    _name = "navitta.cash.count"
    _description = "عدّ العهدة النقدية"
    _order = "day desc, counted_at desc"

    branch_id = fields.Many2one("navitta.branch", "الفرع", required=True, index=True, ondelete="cascade")
    day = fields.Date("اليوم", required=True, index=True)
    kind = fields.Selection(KINDS, "النوع", required=True)
    employee_id = fields.Many2one("navitta.employee", "عدّتها", required=True, ondelete="restrict")
    counted_at = fields.Datetime("وقت العدّ", required=True, default=fields.Datetime.now)
    n500 = fields.Integer("500 ريال")
    n200 = fields.Integer("200 ريال")
    n100 = fields.Integer("100 ريال")
    n50 = fields.Integer("50 ريال")
    n20 = fields.Integer("20 ريال")
    n10 = fields.Integer("10 ريال")
    n5 = fields.Integer("5 ريال")
    n1 = fields.Integer("1 ريال")
    coins = fields.Float("معدن وهللات", digits=(12, 2))
    total = fields.Float("المجموع", digits=(12, 2), compute="_compute_total", store=True)
    expected = fields.Float("المتوقع", digits=(12, 2), help="الافتتاح: آخر عدّ إغلاق للفرع. الإغلاق: عهدة الافتتاح + مبيعات النقدي − الإيداع")
    has_expected = fields.Boolean("فيه مبلغ متوقع")
    difference = fields.Float("الفرق", digits=(12, 2), compute="_compute_total", store=True,
                              help="المعدود − المتوقع: موجب = زيادة، سالب = نقص")
    note = fields.Char("ملاحظة")

    _sql_constraints = [
        ("n_positive", "CHECK(n500 >= 0 AND n200 >= 0 AND n100 >= 0 AND n50 >= 0 AND n20 >= 0 AND n10 >= 0 AND n5 >= 0 "
                       "AND n1 >= 0 AND coins >= 0)", "الأعداد لا يمكن أن تكون سالبة"),
    ]

    @api.depends(*["n%d" % d for d in DENOMS], "coins", "expected", "has_expected")
    def _compute_total(self):
        for c in self:
            c.total = sum(d * c["n%d" % d] for d in DENOMS) + c.coins
            c.difference = round(c.total - c.expected, 2) if c.has_expected else 0.0

    @api.depends("branch_id", "day", "kind", "employee_id")
    def _compute_display_name(self):
        labels = dict(KINDS)
        for c in self:
            c.display_name = "%s – %s – %s" % (labels.get(c.kind, ""), c.branch_id.name or "", c.employee_id.name or "")

    # ------------------------------------------------------------------ expected amounts
    @api.model
    def _expected(self, branch, day, kind):
        """(amount, known) the count is compared with."""
        Count = self.sudo()
        if kind == "open":
            last = Count.search([("branch_id", "=", branch.id), ("kind", "=", "close"), ("day", "<", day)],
                                order="day desc, counted_at desc", limit=1)
            return (last.total, True) if last else (0.0, False)
        opening = Count.search([("branch_id", "=", branch.id), ("kind", "=", "open"), ("day", "=", day)], limit=1)
        if not opening:
            return 0.0, False
        ds = self.env["navitta.daily.sales"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)], limit=1)
        return round(opening.total + (ds.cash or 0.0) - (ds.deposit or 0.0), 2), True

    @api.model
    def _vals_from(self, counts):
        """Validated note counts from the screen: {"500": 2, …, "coins": 3.5} -> (vals, error)."""
        counts = counts or {}
        vals = {}
        try:
            for d in DENOMS:
                n = int(counts.get(str(d)) or 0)
                if n < 0 or n > 100000:
                    return None, "bad_count"
                vals["n%d" % d] = n
            coins = round(float(counts.get("coins") or 0), 2)
        except (TypeError, ValueError):
            return None, "bad_count"
        if coins < 0 or coins > 100000:
            return None, "bad_count"
        vals["coins"] = coins
        return vals, None

    def _dict(self):
        self.ensure_one()
        return {"id": self.id, "kind": self.kind, "employee_id": self.employee_id.id, "employee": self.employee_id.name,
                "counted_at": iso(self.counted_at), "total": self.total, "expected": self.expected if self.has_expected else None,
                "difference": self.difference, "notes": {str(d): self["n%d" % d] for d in DENOMS}, "coins": self.coins}

    def _alert_difference(self):
        """Management gets an email when the counted cash doesn't match (1 riyal or more)."""
        for c in self.filtered(lambda x: x.has_expected and abs(x.difference) >= 1):
            sign = _("زيادة") if c.difference > 0 else _("نقص")
            self.env["navitta.notification"]._notify_admin(
                _("فرق في %(k)s: %(b)s", k=dict(KINDS)[c.kind], b=c.branch_id.name),
                _("%(e)s عدّت %(k)s في %(b)s: المعدود %(t).2f ر.س والمتوقع %(x).2f ر.س، %(s)s %(d).2f ر.س.%(n)s",
                  e=c.employee_id.name, k=dict(KINDS)[c.kind], b=c.branch_id.name, t=c.total, x=c.expected, s=sign,
                  d=abs(c.difference), n=(" " + _("ملاحظتها: %s", c.note)) if c.note else ""))
