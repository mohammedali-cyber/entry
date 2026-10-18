from odoo import _, api, fields, models
from odoo.tools import SQL
from odoo.addons.navitta_ops.models.common import CASHIER_SHARED, company_tz, day_bounds_utc

from .branch import COUNTED_STATES, _local_day


class NavittaDailySales(models.Model):
    _inherit = "navitta.daily.sales"

    source = fields.Selection(selection_add=[("pos", "Point of Sale")], ondelete={"pos": "set null"})


class NavittaEmployeeSales(models.Model):
    _inherit = "navitta.employee.sales"

    source = fields.Selection(selection_add=[("pos", "Point of Sale")], ondelete={"pos": "cascade"})


class NavittaApi(models.AbstractModel):
    _inherit = "navitta.api"

    @api.model
    def admin_setup(self):
        """First-time setup: the points of sale and the employees' devices, too."""
        res = super().admin_setup()
        S = self.env
        branches = S["navitta.branch"].sudo().search([])
        no_pos = branches.filtered(lambda b: not b.pos_config_ids)
        unlinked = S["navitta.employee"].sudo().search([("active", "=", True), ("hr_employee_id", "=", False), ("pos_config_owned_ids", "=", False),
                                                        ("branch_id.pos_config_ids", "!=", False)])
        res["items"] += [
            {"key": "pos_branch", "done": not no_pos, "title": _("Link branches to the Point of Sale"),
             "text": _("Branches without a point of sale: %s", _(", ").join(no_pos.mapped("name"))) if no_pos else _("Every branch is linked to its point of sale"),
             "action": "point_of_sale.action_pos_config_kanban", "button": _("Points of Sale")},
            {"key": "pos_cashier", "done": not unlinked, "title": _("Employees' devices"),
             "text": _("%s employees without their own device or a cashier account (their sales go to the shared account)", len(unlinked))
             if unlinked else _("Every employee has her own device or a cashier account"),
             "action": "navitta_ops.action_branches", "button": _("Branches → Point of Sale")},
        ]
        res["done"] = all(i["done"] for i in res["items"])
        return res

    # ------------------------------------------------------------------ the «Device» filter
    @api.model
    def _devices(self):
        cfgs = self.env["pos.config"].sudo().search([("navitta_branch_id", "!=", False)])
        cfgs = cfgs.sorted(lambda c: (c.navitta_branch_id.name or "", c.name or "", c.id))
        shares = {}
        for b in cfgs.navitta_branch_id:
            shares.update(b._pos_target_shares())
        return [{"id": c.id, "name": c.name, "branch_id": c.navitta_branch_id.id, "owner_id": c.navitta_employee_id.id or False,
                 "owner": c.navitta_employee_id.name or "", "share": round(shares.get(c.id, 0.0), 4)} for c in cfgs]

    @api.model
    def _devices_totals(self, d0, d1, weekdays=None):
        """{pos.config id: its figures summed over the days of [d0, d1] (the chosen weekdays)}: one grouped query."""
        self.env["navitta.pos.day"].flush_model()
        wd = self._wd(weekdays) or None
        self.env.cr.execute("""
            SELECT config_id, COALESCE(SUM(sales), 0), COALESCE(SUM(tax), 0), COALESCE(SUM(invoices), 0), COALESCE(SUM(items), 0),
                   COALESCE(SUM(returns), 0), COALESCE(SUM(returns_amount), 0), COALESCE(SUM(cash), 0), COALESCE(SUM(card), 0),
                   COUNT(*) FILTER (WHERE orders > 0)
              FROM navitta_pos_day
             WHERE day BETWEEN %s AND %s AND (%s::int[] IS NULL OR EXTRACT(dow FROM day)::int = ANY(%s::int[]))
             GROUP BY config_id""", [d0, d1, wd, wd])
        keys = ("sales", "tax", "invoices", "items", "returns", "returns_amount", "cash", "card", "sales_days")
        return {r[0]: dict(zip(keys, r[1:])) for r in self.env.cr.fetchall()}

    @api.model
    def _device_days(self, device, d0, d1, weekdays=None):
        dom = self._day_filter(d0, d1, weekdays) + [("config_id", "=", device["id"])]
        return [{"branch_id": r.branch_id.id, "day": r.day, "sales": r.sales, "tax": r.tax, "invoices": r.invoices, "items": r.items,
                 "returns": r.returns, "returns_amount": r.returns_amount, "cash": r.cash, "card": r.card}
                for r in self.env["navitta.pos.day"].sudo().search(dom, order="day")]

    @api.model
    def _device_months(self, device, year):
        self.env["navitta.pos.day"].flush_model()
        self.env.cr.execute("""SELECT EXTRACT(month FROM day)::int, COALESCE(SUM(sales), 0) FROM navitta_pos_day
                                WHERE config_id = %s AND EXTRACT(year FROM day) = %s GROUP BY 1""", [device["id"], int(year)])
        return dict(self.env.cr.fetchall())

    @api.model
    def _device_emp_rows(self, device, d0, d1, weekdays=None):
        """An employee's own device: one row, hers. Another point of sale: by the cashier on its orders (as the sync)."""
        Emp = self.env["navitta.employee"].sudo().with_context(active_test=False)

        def row(emp, name):
            return {"branch_id": device["branch_id"], "employee_id": emp.id or False, "name": name, "slot": emp.slot or 99,
                    "amount": 0.0, "tax": 0.0, "invoices": 0, "items": 0, "exchanged": 0, "returns_amount": 0.0, "amount_inv": 0.0,
                    "source": "pos", "visits": 0, "bought": 0}
        if device.get("owner_id"):
            emp = Emp.browse(device["owner_id"])
            r = row(emp, emp.name)
            for x in self._device_days(device, d0, d1, weekdays):
                r["amount"] += x["sales"]
                r["tax"] += x["tax"]
                r["invoices"] += x["invoices"]
                r["items"] += x["items"]
                r["exchanged"] += x["returns"]
                r["returns_amount"] += x["returns_amount"]
                r["amount_inv"] += x["sales"] if x["invoices"] else 0.0
            return [r] if r["invoices"] or r["amount"] or r["exchanged"] else []
        for model in ("pos.order", "pos.order.line"):
            self.env[model].flush_model()
        wd = self._wd(weekdays) or None
        tz = company_tz(self.env).zone
        self.env.cr.execute(SQL("""
            WITH o AS (SELECT o.id, o.amount_total, o.amount_tax, o.employee_id, o.user_id, %s AS day
                         FROM pos_order o
                        WHERE o.config_id = %s AND o.state IN %s AND o.date_order >= %s AND o.date_order < %s),
                 l AS (SELECT l.order_id, SUM(l.qty) FILTER (WHERE l.qty > 0 AND l.price_subtotal_incl >= 0) AS sold,
                              SUM(-l.qty) FILTER (WHERE l.qty < 0) AS ret
                         FROM pos_order_line l JOIN o ON o.id = l.order_id GROUP BY 1)
            SELECT o.employee_id, COALESCE(SUM(o.amount_total), 0), COALESCE(SUM(o.amount_tax), 0),
                   COUNT(*) FILTER (WHERE o.amount_total > 0), COALESCE(SUM(-o.amount_total) FILTER (WHERE o.amount_total < 0), 0),
                   COALESCE(SUM(l.sold), 0), COALESCE(SUM(l.ret), 0), ARRAY_AGG(DISTINCT o.user_id)
              FROM o LEFT JOIN l ON l.order_id = o.id
             WHERE %s::int[] IS NULL OR EXTRACT(dow FROM o.day)::int = ANY(%s::int[])
             GROUP BY 1""", _local_day(tz), device["id"], COUNTED_STATES, day_bounds_utc(self.env, d0)[0],
                                day_bounds_utc(self.env, d1)[1], wd, wd))
        res = self.env.cr.fetchall()
        hr_ids = [x[0] for x in res if x[0]]
        nav_of = {e.hr_employee_id.id: e for e in Emp.search([("hr_employee_id", "in", hr_ids)])} if hr_ids else {}
        Hr, Users = self.env["hr.employee"].sudo(), self.env["res.users"].sudo()
        out = []
        for hr, amount, tax, inv, ret_amount, sold, ret, users in res:
            if hr in nav_of:
                r = row(nav_of[hr], nav_of[hr].name)
            elif hr:
                r = row(Emp, self._cashier_label(Hr.browse(hr).name))
            else:
                names = ", ".join(Users.browse(sorted(u for u in users if u)).mapped("name"))
                r = row(Emp, self._cashier_label(CASHIER_SHARED % names if names else ""))
            r.update(amount=round(amount, 2), tax=round(tax, 2), invoices=inv, items=int(sold), exchanged=int(ret),
                     returns_amount=round(ret_amount, 2), amount_inv=round(amount, 2) if inv else 0.0)
            out.append(r)
        return out
