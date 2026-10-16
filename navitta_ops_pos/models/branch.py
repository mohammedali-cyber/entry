"""A Navitta branch's sales from its Odoo points of sale.

Counted orders: paid, posted or invoiced POS orders of the branch's points of sale, by the local day (branch time zone)
of the order. For each day:

- sales = total of the orders including tax (refunds are negative orders, so they come off), tax = their tax;
- invoices = orders with a positive total; returns = pieces returned (negative quantities) and their value;
- items = pieces sold (positive quantities, discount / negative-price lines left out);
- cash = payments with a cash method, card = all the other methods;
- each cashier's sales by the cashier who made the sale (pos_hr employee on the order), mapped to the Navitta employee
  linked to that cashier. Orders made without a cashier (the shared POS account) stay on a line without employee.
"""
import logging
import time
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.addons.navitta_ops.models.common import CASHIER_NONE, CASHIER_SHARED, app_today, day_bounds_utc, local_dt

_logger = logging.getLogger(__name__)

COUNTED_STATES = ("paid", "done", "invoiced")
_LAST_SYNC = {}          # (db, branch id, day) -> time of the last sync in this worker
SYNC_EVERY = 45          # seconds: screens refresh often, the figures don't need to be recomputed each time


class NavittaBranch(models.Model):
    _inherit = "navitta.branch"

    pos_config_ids = fields.One2many("pos.config", "navitta_branch_id", string="Points of Sale")
    pos_last_sync = fields.Datetime("Last update from the Point of Sale", readonly=True)

    def _has_system(self):
        self.ensure_one()
        return bool(self.sudo().pos_config_ids)

    # ------------------------------------------------------------------ sales
    def _system_sync(self, days, force=False):
        db = self.env.cr.dbname
        now = time.time()
        for b in self.sudo().filtered("pos_config_ids"):
            for day in days:
                key = (db, b.id, day)
                if not force and now - _LAST_SYNC.get(key, 0) < SYNC_EVERY:
                    continue
                b._pos_sync_day(day)
                _LAST_SYNC[key] = now
        return True

    def _pos_owner_of(self):
        """{pos.config id: navitta.employee} for the branch's points of sale that belong to one employee (her own device)."""
        self.ensure_one()
        return {c.id: c.navitta_employee_id for c in self.sudo().pos_config_ids if c.navitta_employee_id}

    def _pos_figures(self, day):
        """(day totals, {who: totals}, {0: shared account user ids}, [order info])

        who = ("emp", navitta employee id) for every order of an employee's own device (whoever was picked as cashier)
        and for a cashier linked to an employee; ("hr", hr employee id) for a cashier not linked to any employee; 0 for
        an order without a cashier (the shared account)."""
        self.ensure_one()
        start, end = day_bounds_utc(self.env, day)
        cr = self.env.cr
        for model in ("pos.order", "pos.order.line", "pos.payment", "pos.payment.method"):
            self.env[model].flush_model()
        cr.execute("""
            SELECT o.id, o.amount_total, o.amount_tax, o.employee_id, o.user_id, o.config_id
              FROM pos_order o
             WHERE o.config_id = ANY(%s) AND o.state IN %s AND o.date_order >= %s AND o.date_order < %s""",
                   [self.pos_config_ids.ids, COUNTED_STATES, start, end])
        orders = cr.fetchall()
        ids = [o[0] for o in orders]
        lines, pays = {}, defaultdict(lambda: [0.0, 0.0])
        if ids:
            cr.execute("""
                SELECT order_id,
                       COALESCE(SUM(qty) FILTER (WHERE qty > 0 AND price_subtotal_incl >= 0), 0),
                       COALESCE(SUM(-qty) FILTER (WHERE qty < 0), 0)
                  FROM pos_order_line WHERE order_id = ANY(%s) GROUP BY order_id""", [ids])
            lines = {o: (sold, ret) for o, sold, ret in cr.fetchall()}
            cr.execute("""
                SELECT p.pos_order_id, pm.is_cash_count, SUM(p.amount)
                  FROM pos_payment p JOIN pos_payment_method pm ON pm.id = p.payment_method_id
                 WHERE p.pos_order_id = ANY(%s) GROUP BY 1, 2""", [ids])
            for o, is_cash, amount in cr.fetchall():
                pays[o][0 if is_cash else 1] += amount or 0.0
        owners = self._pos_owner_of()
        hr_ids = list({o[3] for o in orders if o[3]})
        nav_of = {e.hr_employee_id.id: e.id for e in self.env["navitta.employee"].sudo().with_context(active_test=False).search(
            [("hr_employee_id", "in", hr_ids)])} if hr_ids else {}

        def who(cfg, hr):
            if cfg in owners:
                return ("emp", owners[cfg].id)
            if hr:
                return ("emp", nav_of[hr]) if hr in nav_of else ("hr", hr)
            return 0

        def blank():
            return {"sales": 0.0, "tax": 0.0, "invoices": 0, "items": 0, "returns": 0, "returns_amount": 0.0,
                    "cash": 0.0, "card": 0.0, "orders": 0}
        day_t, by_emp, users, info = blank(), defaultdict(blank), defaultdict(set), []
        for oid, total, tax, emp, user, cfg in orders:
            sold, ret = lines.get(oid, (0, 0))
            key = who(cfg, emp)
            for t in (day_t, by_emp[key]):
                t["orders"] += 1
                t["sales"] += total or 0.0
                t["tax"] += tax or 0.0
                t["invoices"] += 1 if (total or 0) > 0 else 0
                t["returns_amount"] += -total if (total or 0) < 0 else 0.0
                t["items"] += int(sold or 0)
                t["returns"] += int(ret or 0)
                t["cash"] += pays[oid][0]
                t["card"] += pays[oid][1]
            if not key and user:
                users[0].add(user)
            info.append({"id": oid, "total": total or 0.0, "returned": int(ret or 0),
                         "employee_id": key[1] if key and key[0] == "emp" else False})
        return day_t, by_emp, users, info

    def _pos_sync_day(self, day):
        """Writes the day's POS figures into the branch's daily sales and the employees' daily sales."""
        self.ensure_one()
        day_t, by_emp, users, info = self._pos_figures(day)
        Sales = self.env["navitta.daily.sales"].sudo().with_context(navitta_change_source="pos")
        row = Sales.search([("branch_id", "=", self.id), ("day", "=", day)], limit=1)
        if not day_t["orders"] and (not row or row.source != "pos"):
            return          # nothing sold in POS that day: keep what the daily report / management entered
        vals = {"sales": round(day_t["sales"], 2), "tax_amount": round(day_t["tax"], 2), "invoices": day_t["invoices"],
                "items": day_t["items"], "returns": day_t["returns"], "returns_amount": round(day_t["returns_amount"], 2),
                "cash": round(day_t["cash"], 2), "card": round(day_t["card"], 2), "source": "pos"}
        if row.source == "admin":
            # a day management corrected by hand keeps its figures; the refunds and the VAT keep following the orders
            vals = {k: vals[k] for k in ("tax_amount", "returns_amount")}
        row = row or Sales._row(self, day)
        changed = any(row[k] != v for k, v in vals.items())
        if changed:
            row.write(vals)
        Hr = self.env["hr.employee"].sudo()
        Emp = self.env["navitta.employee"].sudo().with_context(active_test=False)
        user_names = ", ".join(self.env["res.users"].sudo().browse(sorted(users[0])).mapped("name"))
        new = []
        for key, t in by_emp.items():
            emp = Emp.browse(key[1]) if key and key[0] == "emp" else Emp
            # stored markers (kept as they are, compared on the next sync), shown translated by cashier_text()
            cashier = ((emp.hr_employee_id.name or emp.name) if emp else Hr.browse(key[1]).name if key
                       else CASHIER_SHARED % user_names if user_names else CASHIER_NONE)
            new.append({"branch_id": self.id, "day": day, "employee_id": emp.id or False, "cashier": cashier,
                        "amount": round(t["sales"], 2), "tax": round(t["tax"], 2), "invoices": t["invoices"], "items": t["items"],
                        "returns": t["returns"], "returns_amount": round(t["returns_amount"], 2), "source": "pos"})
        EmpSales = self.env["navitta.employee.sales"].sudo()
        # the daily report's lines for the same day would count every sale twice
        EmpSales.search([("branch_id", "=", self.id), ("day", "=", day), ("source", "=", "report")]).unlink()
        old = EmpSales.search([("branch_id", "=", self.id), ("day", "=", day), ("source", "=", "pos")])
        keys = ("employee_id", "cashier", "amount", "tax", "invoices", "items", "returns", "returns_amount")
        same = sorted((tuple((l[k].id if k == "employee_id" else l[k]) or False for k in keys) for l in old)) == \
            sorted((tuple(v[k] or False for k in keys) for v in new))
        if not same:
            old.unlink()
            EmpSales.create(new)
        if changed or not same:
            self.pos_last_sync = fields.Datetime.now()       # last time the figures changed
        if day >= app_today(self.env) - timedelta(days=1):
            self._pos_link_visits(day, info)

    # ------------------------------------------------------------------ sales made without the counter
    def _pos_link_visits(self, day, info):
        """Each new order belongs to the counter visit running around its time (same employee when known): the visit
        gets the invoice amount (and «bought» / «exchange» if it was saved without it). An order of an employee with no
        such visit becomes an automatic visit «sold without the counter» that she confirms before checking out.
        Only today's and yesterday's orders (not the 60-day backfill), each order once."""
        self.ensure_one()
        Order = self.env["pos.order"].sudo()
        todo = Order.browse([x["id"] for x in info]).filtered(lambda o: not o.navitta_checked)
        if not todo:
            return
        by_id = {x["id"]: x for x in info}
        Visit = self.env["navitta.visit"].sudo()
        mine = set(self.sudo().employee_ids.filtered("active").ids)
        for o in todo.sorted(lambda o: (o.date_order, o.id)):
            x, at = by_id[o.id], o.date_order
            emp = x["employee_id"]
            exchange = x["returned"] > 0
            dom = [("branch_id", "=", self.id), ("day", "=", day), ("auto", "=", False), ("started_at", "<=", at + timedelta(minutes=2))]
            if emp:
                dom.append(("employee_id", "=", emp))
            visits = Visit.search(dom, order="started_at desc", limit=5)
            v = next((v for v in visits if not v.ended_at or v.ended_at >= at - timedelta(minutes=10)), None)
            vals = {"navitta_checked": True}
            if v:
                upd = {"amount": round(v.amount + x["total"], 2)}
                if v.saved:
                    upd["exchanged" if exchange else "bought"] = True
                v.write(upd)
                vals["navitta_visit_id"] = v.id
            elif emp in mine:
                nv = Visit.create({"branch_id": self.id, "employee_id": emp, "day": day, "started_at": at, "ended_at": at,
                                   "duration_sec": 0, "saved": True, "bought": not exchange, "exchanged": exchange,
                                   "amount": round(x["total"], 2), "auto": True})
                vals["navitta_visit_id"] = nv.id
            o.write(vals)

    @api.model
    def _cron_pos_sync(self):
        """Every 10 minutes: today and yesterday (a session closed after midnight)."""
        day = app_today(self.env)
        branches = self.sudo().search([("pos_config_ids", "!=", False)])
        branches._system_sync([day - timedelta(days=1), day], force=True)
        # older orders edited or refunded lately (their day is counted again, and logged if it was closed already)
        since = fields.Datetime.now() - timedelta(hours=1)
        for b in branches:
            orders = self.env["pos.order"].sudo().search([("config_id", "in", b.pos_config_ids.ids), ("write_date", ">=", since),
                                                          ("date_order", "<", day_bounds_utc(self.env, day - timedelta(days=1))[0])])
            days = sorted({local_dt(self.env, o.date_order).date() for o in orders})
            if days:
                b._system_sync(days, force=True)

    def action_pos_sync_month(self):
        """Button: recompute the last 60 days from the POS orders (after linking a point of sale or an employee)."""
        day = app_today(self.env)
        self._system_sync([day - timedelta(days=i) for i in range(60)], force=True)
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("The last 60 days were synced from the Point of Sale"), "type": "success"}}

    # ------------------------------------------------------------------ registers of the day
    def _system_status(self, day):
        res = {}
        start, end = day_bounds_utc(self.env, day)
        Session = self.env["pos.session"].sudo()
        for b in self.sudo().filtered("pos_config_ids"):
            sessions = Session.search([("config_id", "in", b.pos_config_ids.ids), "|", ("state", "in", ("opened", "closing_control")),
                                       "&", ("start_at", ">=", start), ("start_at", "<", end)], order="start_at")
            res[b.id] = [s._navitta_status() for s in sessions]
        return res

    # ------------------------------------------------------------------ cashiers
    def _navitta_sync_cashiers(self):
        """The cashier list ("Change Cashier") of each point of sale of the branch = the branch's employees.
        Managers (advanced rights) and other cashiers not linked to a Navitta employee are kept.
        An employee's own device (point of sale with an owner) lists only her."""
        Emp = self.env["navitta.employee"].sudo().with_context(active_test=False)
        linked = Emp.search([("hr_employee_id", "!=", False)])
        for b in self.sudo():
            own = linked.filtered(lambda e: e.active and e.branch_id == b).mapped("hr_employee_id")
            others = linked.mapped("hr_employee_id") - own
            for cfg in b.pos_config_ids:
                vals = {}
                if not cfg.module_pos_hr and not cfg.has_active_session:
                    vals["module_pos_hr"] = True
                owner = cfg.navitta_employee_id
                if owner:
                    # her own device: she is its only cashier (managers keep their access)
                    basic = owner.hr_employee_id if owner.active else self.env["hr.employee"]
                else:
                    basic = (cfg.basic_employee_ids - others) | own
                basic -= cfg.advanced_employee_ids
                if basic != cfg.basic_employee_ids:
                    vals["basic_employee_ids"] = [(6, 0, basic.ids)]
                if vals:
                    cfg.write(vals)
        return True

    def action_link_cashiers(self):
        """Button: give every employee of the branch a cashier (an existing HR employee with the same name, or a new one)
        and put them in the branch's cashier list."""
        Hr = self.env["hr.employee"].sudo()
        n = 0
        for b in self.sudo():
            for e in b.employee_ids.filtered(lambda x: x.active and not x.hr_employee_id):
                hr = Hr.search([("name", "=", e.name), ("company_id", "in", (b.company_id.id, False))], limit=1)
                if hr and self.env["navitta.employee"].sudo().with_context(active_test=False).search_count([("hr_employee_id", "=", hr.id)]):
                    hr = Hr
                e.hr_employee_id = hr or Hr.create({"name": e.name, "company_id": b.company_id.id})
                n += 1
        self._navitta_sync_cashiers()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("%s employees linked to a cashier account, and the cashier lists of the points of sale were updated", n), "type": "success"}}
