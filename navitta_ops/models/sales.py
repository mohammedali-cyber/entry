import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import format_date

from .common import EMERGENCIES, EXCHANGE_REASONS, SHELF, STOCK_SUPPLIES, app_today


class NavittaDailySales(models.Model):
    """One row per branch and day: the target and the actual sales (from the daily report or entered by management)."""
    _name = "navitta.daily.sales"
    _description = "Daily target and sales"
    _order = "day desc, branch_id"
    _rec_name = "day"

    @api.depends("branch_id", "day")
    def _compute_display_name(self):
        for r in self:
            r.display_name = "%s – %s" % (r.branch_id.name or "", format_date(self.env, r.day) if r.day else "")

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    day = fields.Date("Day", required=True, index=True)
    target = fields.Float("Target", digits=(12, 2), default=0.0)
    sales = fields.Float("Sales (incl. VAT)", digits=(12, 2))
    has_sales = fields.Boolean("Has recorded sales", compute="_compute_has_sales", store=True)
    invoices = fields.Integer("Invoices")
    items = fields.Integer("Items sold")
    returns = fields.Integer("Returned / exchanged (items)")
    returns_amount = fields.Float("Returns amount", digits=(12, 2))
    tax_amount = fields.Float("Actual VAT", digits=(12, 2),
                              help="VAT as recorded by the cashier system. If missing, it is computed from the VAT rate in the settings")
    cash = fields.Float("Cash", digits=(12, 2))
    card = fields.Float("Card", digits=(12, 2))
    deposit = fields.Float("Bank deposit", digits=(12, 2))
    closed_by = fields.Char("Closed by")
    source = fields.Selection([("report", "Daily report"), ("manual", "Management")], "Source")
    vat_amount = fields.Float("VAT", digits=(12, 2), compute="_compute_vat", store=True)
    net_sales = fields.Float("Net after VAT", digits=(12, 2), compute="_compute_vat", store=True)

    _sql_constraints = [("branch_day_uniq", "unique(branch_id, day)", "A record already exists for this branch and day")]

    @api.depends("sales", "tax_amount", "branch_id.company_id.navitta_vat_rate")
    def _compute_vat(self):
        for r in self:
            rate = r.branch_id.company_id.navitta_vat_rate or 0.0
            s = r.sales or 0.0
            r.vat_amount = r.tax_amount if r.tax_amount else (s * rate / (100.0 + rate) if rate else 0.0)
            r.net_sales = s - r.vat_amount

    def _is_system(self):
        """Figures that come from a cash register system (e.g. Odoo POS), not typed by people."""
        self.ensure_one()
        return bool(self.source) and self.source not in ("report", "manual")

    @api.depends("source")
    def _compute_has_sales(self):
        for r in self:
            r.has_sales = bool(r.source)

    def write(self, vals):
        # figures typed by management in the backend
        if not vals.get("source") and set(vals) & {"sales", "invoices", "items", "returns", "cash", "card"}:
            for r in self.filtered(lambda x: not x.source):
                super(NavittaDailySales, r).write(dict(vals, source="manual"))
            self = self.filtered("source")
            if not self:
                return True
        return super().write(vals)

    @api.model
    def _row(self, branch, day):
        rec = self.sudo().search([("branch_id", "=", branch.id), ("day", "=", day)], limit=1)
        return rec or self.sudo().create({"branch_id": branch.id, "day": day})

    @api.model
    def _set_target_days(self, branch, from_day, days, amount, by_admin):
        if amount is None or amount < 0 or not (1 <= days <= 31):
            return "bad_amount"
        Log = self.env["navitta.target.log"].sudo()
        for i in range(days):
            d = from_day + timedelta(days=i)
            row = self._row(branch, d)
            old = row.target
            row.target = amount
            Log.create({"branch_id": branch.id, "by_admin": by_admin, "scope": "day", "ref": fields.Date.to_string(d),
                        "old_amount": old, "new_amount": amount})
        return None

    @api.model
    def _set_target_month(self, branch, month, amount, by_admin):
        """The month total: what is left after the past days is spread evenly over the remaining days."""
        if amount is None or amount < 0:
            return "bad_amount", 0, 0
        m0 = month.replace(day=1)
        m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        start = max(m0, app_today(self.env))
        if start > m1:
            return "month_over", 0, 0
        rows = self.sudo().search([("branch_id", "=", branch.id), ("day", ">=", m0), ("day", "<=", m1)])
        old = sum(rows.mapped("target"))
        fixed = sum(rows.filtered(lambda r: r.day < start).mapped("target"))
        n = (m1 - start).days + 1
        rest = max(amount - fixed, 0)
        per = math.floor(rest / n)
        for i in range(n):
            self._row(branch, start + timedelta(days=i)).target = rest - per * (n - 1) if i == n - 1 else per
        self.env["navitta.target.log"].sudo().create({"branch_id": branch.id, "by_admin": by_admin, "scope": "month",
                                                      "ref": m0.strftime("%Y-%m"), "old_amount": old, "new_amount": amount})
        return None, per, n


class NavittaTargetLog(models.Model):
    _name = "navitta.target.log"
    _description = "Target change log"
    _order = "create_date desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    by_admin = fields.Boolean("By management")
    scope = fields.Selection([("day", "Day"), ("month", "Month")], "Scope", required=True)
    ref = fields.Char("Date or month", required=True)
    old_amount = fields.Float("Old amount", digits=(12, 2))
    new_amount = fields.Float("New amount", digits=(12, 2), required=True)
    user_id = fields.Many2one("res.users", "User", default=lambda self: self.env.user)


class NavittaDailyReport(models.Model):
    _name = "navitta.daily.report"
    _description = "Branch daily report"
    _order = "day desc, branch_id"
    _rec_name = "day"

    @api.depends("branch_id", "day")
    def _compute_display_name(self):
        for r in self:
            r.display_name = "%s – %s" % (r.branch_id.name or "", format_date(self.env, r.day) if r.day else "")

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    day = fields.Date("Day", required=True, index=True)
    employee_id = fields.Many2one("navitta.employee", "Written by", ondelete="set null")
    employee_name = fields.Char("Employee name", required=True)
    # sales (from the cashier system)
    sales = fields.Float("Total sales", digits=(12, 2))
    invoices = fields.Integer("Number of invoices")
    items = fields.Integer("Number of items sold")
    cash = fields.Float("Cash sales", digits=(12, 2))
    card = fields.Float("Card sales", digits=(12, 2))
    deposit = fields.Float("Bank deposit from the cash box", digits=(12, 2))
    exchange_ids = fields.One2many("navitta.daily.report.exchange", "report_id", "Exchanges")
    employee_line_ids = fields.One2many("navitta.daily.report.employee", "report_id", "Employees' sales")
    used_roll_invoice = fields.Integer("Receipt rolls used")
    used_roll_card = fields.Integer("Card rolls used")
    used_bags = fields.Integer("Bags used")
    # customers
    visitors = fields.Integer("Number of customers")
    non_buy = fields.Integer("Non-buyers")
    nobuy_reasons = fields.Json("Reasons for not buying")
    lost_ids = fields.One2many("navitta.daily.report.lost", "report_id", "Lost sales")
    product_type = fields.Char("Most requested product type")
    top_color = fields.Char("Most requested color")
    top_size = fields.Char("Most requested size")
    tried_model = fields.Char("Item tried on but not sold")
    tried_reason = fields.Char("Reason")
    # customer experience
    sat = fields.Integer("Customer satisfaction (1-5)", aggregator="avg")
    phrase = fields.Char("Most repeated phrase")
    tone = fields.Selection([("positive", "Positive"), ("negative", "Negative")], "Phrase type")
    complaint = fields.Boolean("Complaint")
    complaint_text = fields.Char("Complaint details")
    influ = fields.Boolean("Influencer customer")
    influ_text = fields.Char("Influencer details")
    # branch & operations
    shelf = fields.Selection(SHELF, "Shelf tidiness")
    short = fields.Boolean("Stock shortage")
    short_text = fields.Char("Shortage details")
    damage = fields.Boolean("Damage")
    damage_text = fields.Char("Damage details")
    emergency = fields.Selection(EMERGENCIES, "Emergency incident", default="none")
    improve = fields.Text("To improve sales")
    suggest = fields.Text("Suggestions")

    _sql_constraints = [("branch_day_uniq", "unique(branch_id, day)", "A report already exists for this branch and day")]

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._sync()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self._sync()
        return res

    def _sync(self):
        """Daily report -> branch daily sales and supplies used that day."""
        Sales = self.env["navitta.daily.sales"]
        Move = self.env["navitta.supply.move"].sudo()
        EmpSales = self.env["navitta.employee.sales"].sudo()
        for r in self:
            row = Sales._row(r.branch_id, r.day)
            if row._is_system():
                # sales, cash and the employees' sales come from the cash register system: keep them
                row.write({"deposit": r.deposit, "closed_by": r.employee_name})
            else:
                row.write({"sales": r.sales, "invoices": r.invoices, "items": r.items, "returns": len(r.exchange_ids),
                           "cash": r.cash, "card": r.card, "deposit": r.deposit, "closed_by": r.employee_name, "source": "report"})
            EmpSales.search([("branch_id", "=", r.branch_id.id), ("day", "=", r.day), ("source", "=", "report")]).unlink()
            if row._is_system():
                continue        # the system already has each cashier's line for this day: don't count it twice
            EmpSales.create([{"branch_id": r.branch_id.id, "day": r.day, "employee_id": l.employee_id.id, "cashier": l.employee_id.name,
                              "amount": l.amount, "invoices": l.invoices, "items": l.items, "returns": l.exchanged, "source": "report"}
                             for l in r.employee_line_ids])
            Move.search([("branch_id", "=", r.branch_id.id), ("day", "=", r.day), ("kind", "=", "used")]).unlink()
            for k in STOCK_SUPPLIES:
                q = r["used_" + k]
                if q > 0:
                    Move.create({"branch_id": r.branch_id.id, "day": r.day, "supply": k, "qty": -q, "kind": "used"})

    def _events(self):
        """[(level, title, text)] worth management's attention."""
        self.ensure_one()
        ev = []
        if self.complaint:
            ev.append(("bad", _("Complaint"), self.complaint_text or ""))
        if self.emergency and self.emergency != "none":
            ev.append(("bad", _("Emergency incident"), dict(self._fields["emergency"]._description_selection(self.env)).get(self.emergency, self.emergency)))
        if self.short:
            ev.append(("warn", _("Stock shortage"), self.short_text or ""))
        if self.damage:
            ev.append(("warn", _("Damage"), self.damage_text or ""))
        if self.shelf == "needs":
            ev.append(("warn", _("Shelves"), _("Need tidying")))
        if self.influ:
            ev.append(("info", _("Influencer visit"), self.influ_text or ""))
        return ev


class NavittaDailyReportExchange(models.Model):
    _name = "navitta.daily.report.exchange"
    _description = "Daily report exchange"

    report_id = fields.Many2one("navitta.daily.report", required=True, ondelete="cascade", index=True)
    reason = fields.Selection(EXCHANGE_REASONS, "Exchange reason", required=True)
    detail = fields.Char("Details", required=True)
    sold_by = fields.Char("Sold by", required=True)
    exchanged_by = fields.Char("Exchanged by", required=True)


class NavittaDailyReportEmployee(models.Model):
    """Each employee's sales of the day (from the cashier report at the end of the shift)."""
    _name = "navitta.daily.report.employee"
    _description = "Employee's daily sales line"
    _order = "day desc, branch_id"

    report_id = fields.Many2one("navitta.daily.report", required=True, ondelete="cascade", index=True)
    branch_id = fields.Many2one(related="report_id.branch_id", store=True, index=True, string="Branch")
    day = fields.Date(related="report_id.day", store=True, index=True, string="Day")
    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, ondelete="restrict")
    amount = fields.Float("Her sales", digits=(12, 2))
    invoices = fields.Integer("Her invoices")
    items = fields.Integer("Items sold")
    exchanged = fields.Integer("Items exchanged")


class NavittaEmployeeSales(models.Model):
    """One employee's (cashier's) sales in a branch on a day.

    From the daily report (source «report»), or from the cash register system when it is linked (e.g. Odoo POS,
    module navitta_ops_pos): then every sale counts for the cashier who made it, and sales made under the shared
    account without a cashier stay as a line without employee («بدون كاشير»)."""
    _name = "navitta.employee.sales"
    _description = "Employee daily sales"
    _order = "day desc, branch_id, amount desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    employee_id = fields.Many2one("navitta.employee", "Employee", index=True, ondelete="set null")
    cashier = fields.Char("Cashier name")
    amount = fields.Float("Sales (incl. VAT)", digits=(12, 2))
    tax = fields.Float("VAT", digits=(12, 2))
    invoices = fields.Integer("Invoices")
    items = fields.Integer("Items sold")
    returns = fields.Integer("Returns (items)")
    returns_amount = fields.Float("Returns amount", digits=(12, 2))
    source = fields.Selection([("report", "Daily report")], "Source", required=True, default="report")

    @api.model
    def _preferred_domain(self, d0, d1, branch_id=None):
        """Lines of the period, the cash register system's lines replacing the daily report's for the same branch and day."""
        dom = [("day", ">=", d0), ("day", "<=", d1)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        lines = self.sudo().search(dom)
        sys_days = {(l.branch_id.id, l.day) for l in lines if l.source != "report"}
        return lines.filtered(lambda l: l.source != "report" or (l.branch_id.id, l.day) not in sys_days)


class NavittaDailyReportLost(models.Model):
    _name = "navitta.daily.report.lost"
    _description = "Unavailable request (daily report)"

    report_id = fields.Many2one("navitta.daily.report", required=True, ondelete="cascade", index=True)
    model = fields.Char("Model number")
    color = fields.Char("Color")
    size = fields.Char("Size")
