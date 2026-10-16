from datetime import timedelta

from odoo import _, api, fields, models

from .common import STOCK_SUPPLIES, app_today


class NavittaBranch(models.Model):
    _name = "navitta.branch"
    _description = "Navitta branch"
    _order = "sequence, id"

    name = fields.Char("Branch", required=True, translate=False)
    code = fields.Char("Code", required=True, help="Short code such as dwd")
    sequence = fields.Integer("Sequence", default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    employee_ids = fields.One2many("navitta.employee", "branch_id", string="Employees")
    user_ids = fields.One2many("res.users", "navitta_branch_id", string="Branch device accounts")
    device_ids = fields.One2many("navitta.device", "branch_id", string="Fingerprint devices")

    two_shifts = fields.Boolean("Has a morning shift",
                                help="The branch works a morning and an evening shift. Each employee then has her own shift "
                                     "(morning or evening); otherwise everyone works the evening shift.")

    # today's float («عهدة اليوم»): counted at the start of the day on the counter and at the end in the daily report
    float_amount = fields.Float("Float amount (SAR)", digits=(12, 2), default=1000.0,
                                help="The fixed amount of cash the employees count at the start and at the end of the shift")
    float_place = fields.Char("Float location", default=lambda self: self.with_context(lang=self.env.company._navitta_lang()).env._("First cashier drawer"),
                              help="Where the float is kept in the branch (shown to the employee who counts it)")

    cash_opening_balance = fields.Float("Cash box opening balance", digits=(12, 2))
    cash_opening_date = fields.Date("As of", help="The balance after this date is computed automatically from cash sales, bank deposits and approved expenses")

    # management-password attempts typed on this branch's device
    pw_fail_count = fields.Integer(groups="base.group_system", copy=False)
    pw_locked_until = fields.Datetime(groups="base.group_system", copy=False)

    cash_balance = fields.Float("Current cash box balance", compute="_compute_cash", digits=(12, 2))
    month_expenses = fields.Float("This month's expenses", compute="_compute_cash", digits=(12, 2))
    pending_expenses = fields.Float("Expenses awaiting approval", compute="_compute_cash", digits=(12, 2))
    stock_roll_invoice = fields.Integer("Receipt rolls in stock", compute="_compute_stock")
    stock_roll_card = fields.Integer("Card rolls in stock", compute="_compute_stock")
    stock_bags = fields.Integer("Bags in stock", compute="_compute_stock")

    _sql_constraints = [("code_uniq", "unique(code)", "The branch code is already used")]

    def write(self, vals):
        res = super().write(vals)
        if "two_shifts" in vals and not vals["two_shifts"]:
            # evening only: every employee of the branch is on the evening shift
            self.env["navitta.employee"].sudo().with_context(active_test=False).search(
                [("branch_id", "in", self.ids), ("shift", "=", "am")]).write({"shift": "pm"})
        return res

    def _shift_end_today(self, day=None):
        """Latest shift end (minutes) of the branch's active employees on `day` (for "time left in the shift")."""
        self.ensure_one()
        from .common import app_today, hours_to_min
        day = day or app_today(self.env)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", self.id), ("active", "=", True)])
        return max([e._shift(day)["end"] for e in emps] or [hours_to_min(self.company_id.navitta_shift_end)])

    # ------------------------------------------------------------------ cash register system (overridden by navitta_ops_pos)
    def _has_system(self):
        """True when the branch's sales come from a linked cash register system (e.g. Odoo POS)."""
        return False

    def _system_sync(self, days):
        """Refreshes the sales of these days from the cash register system. Nothing without one."""
        return True

    def _system_status(self, day):
        """{branch_id: [{"name", "opened_at", "opened_by", "cashier", "state"}]}: the registers of the day."""
        return {}

    def _cash_summary(self):
        """{branch_id: {'balance', 'month_expenses', 'pending_expenses'}} computed in SQL (one query per figure)."""
        res = {b.id: {"balance": b.cash_opening_balance or 0.0, "month_expenses": 0.0, "pending_expenses": 0.0} for b in self}
        if not self:
            return res
        today = app_today(self.env)
        m0 = today.replace(day=1)
        for model in ("navitta.branch", "navitta.daily.sales", "navitta.expense"):
            self.env[model].flush_model()
        cr = self.env.cr
        cr.execute("""
            SELECT b.id, COALESCE(SUM(COALESCE(s.cash, 0) - COALESCE(s.deposit, 0)), 0)
              FROM navitta_branch b
              JOIN navitta_daily_sales s ON s.branch_id = b.id AND s.day > COALESCE(b.cash_opening_date, '1900-01-01')
             WHERE b.id IN %s GROUP BY b.id""", [tuple(self.ids)])
        for bid, v in cr.fetchall():
            res[bid]["balance"] += v
        cr.execute("""
            SELECT b.id,
                   COALESCE(SUM(e.amount) FILTER (WHERE e.state = 'approved' AND e.day > COALESCE(b.cash_opening_date, '1900-01-01')), 0),
                   COALESCE(SUM(e.amount) FILTER (WHERE e.state <> 'rejected' AND e.day >= %s), 0),
                   COALESCE(SUM(e.amount) FILTER (WHERE e.state = 'pending'), 0)
              FROM navitta_branch b JOIN navitta_expense e ON e.branch_id = b.id
             WHERE b.id IN %s GROUP BY b.id""", [m0, tuple(self.ids)])
        for bid, approved, month, pending in cr.fetchall():
            res[bid]["balance"] -= approved
            res[bid]["month_expenses"] = month
            res[bid]["pending_expenses"] = pending
        return res

    def _stock_summary(self):
        """{branch_id: {supply: {'balance', 'last_received'}}}"""
        res = {b.id: {k: {"balance": 0, "last_received": False} for k in STOCK_SUPPLIES} for b in self}
        if not self:
            return res
        self.env["navitta.supply.move"].flush_model()
        self.env.cr.execute("""
            SELECT branch_id, supply, SUM(qty), MAX(day) FILTER (WHERE kind = 'received')
              FROM navitta_supply_move WHERE branch_id IN %s GROUP BY 1, 2""", [tuple(self.ids)])
        for bid, supply, bal, last in self.env.cr.fetchall():
            res[bid][supply] = {"balance": int(bal or 0), "last_received": last and fields.Date.to_string(last)}
        return res

    @api.depends("cash_opening_balance", "cash_opening_date")
    def _compute_cash(self):
        data = self.filtered("id")._cash_summary()
        for b in self:
            x = data.get(b.id, {"balance": 0.0, "month_expenses": 0.0, "pending_expenses": 0.0})
            b.cash_balance, b.month_expenses, b.pending_expenses = x["balance"], x["month_expenses"], x["pending_expenses"]

    def _compute_stock(self):
        data = self.filtered("id")._stock_summary()
        for b in self:
            x = data.get(b.id)
            b.stock_roll_invoice = x["roll_invoice"]["balance"] if x else 0
            b.stock_roll_card = x["roll_card"]["balance"] if x else 0
            b.stock_bags = x["bags"]["balance"] if x else 0

    def _supply_adjust(self, supply, balance):
        """Sets the current balance of a supply by adding an adjustment move."""
        self.ensure_one()
        cur = self._stock_summary()[self.id][supply]["balance"]
        if balance != cur:
            self.env["navitta.supply.move"].sudo().create({
                "branch_id": self.id, "day": app_today(self.env), "supply": supply, "qty": balance - cur, "kind": "adjust"})

    def _check_admin_password(self, pw):
        """None when the management password is right, else an error code. Failures are counted per branch."""
        self.ensure_one()
        b = self.sudo()
        now = fields.Datetime.now()
        if not self.company_id._navitta_admin_password_is_set():
            return "no_target_pw"
        if b.pw_locked_until and b.pw_locked_until > now:
            return "locked"
        if self.company_id._navitta_verify_admin_password(pw):
            b.write({"pw_fail_count": 0, "pw_locked_until": False})
            return None
        fails = b.pw_fail_count + 1
        vals = {"pw_fail_count": fails}
        if fails % 5 == 0:
            vals["pw_locked_until"] = now + timedelta(minutes=5 if fails < 15 else 60)
            if fails >= 10:
                self.env["navitta.notification"]._notify_admin(
                    _("Wrong management password attempts"),
                    _("The %(b)s device entered a wrong management password %(n)s times in a row.", b=self.name, n=fails))
        b.write(vals)
        return "wrong_pw"
