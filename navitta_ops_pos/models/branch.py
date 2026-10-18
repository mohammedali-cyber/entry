"""A Navitta branch's sales from its Odoo points of sale.

Counted orders: paid, posted or invoiced POS orders of the branch's points of sale, by the local day (branch time zone)
of the order. For each day:

- sales = total of the orders including tax (refunds are negative orders, so they come off), tax = their tax;
- invoices = orders with a positive total; returns = pieces returned (negative quantities) and their value;
- items = pieces sold (positive quantities, discount / negative-price lines left out);
- cash = payments with a cash method, card = all the other methods;
- each employee's sales: every order of an employee's own device (point of sale with an «Owner employee») counts for
  her; on the other points of sale by the cashier who made the sale (pos_hr employee on the order), mapped to the
  Navitta employee linked to that cashier account. Orders made without a cashier (the shared POS account) stay on a
  line without employee.

The same figures are kept per point of sale and day (navitta.pos.day) for the «Device» filter of the management
screens. The whole history is synced on demand: the screens call _system_backfill() for the period they show, which
syncs the days that have orders and were not synced yet or changed since (one grouped query to find them).

The Point of Sale itself is never changed here: the cashier lists and settings stay as the owner configures them.
"""
import logging
import time
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import SQL
from odoo.addons.navitta_ops.models.common import CASHIER_NONE, CASHIER_SHARED, app_today, company_tz, day_bounds_utc, local_dt

_logger = logging.getLogger(__name__)

COUNTED_STATES = ("paid", "done", "invoiced")
_LAST_SYNC = {}          # (db, branch id, day) -> time of the last sync in this worker
SYNC_EVERY = 45          # seconds: screens refresh often, the figures don't need to be recomputed each time
DEVICE_FIELDS = ("orders", "sales", "tax", "invoices", "items", "returns", "returns_amount", "cash", "card")


def _blank():
    return {"sales": 0.0, "tax": 0.0, "invoices": 0, "items": 0, "returns": 0, "returns_amount": 0.0,
            "cash": 0.0, "card": 0.0, "orders": 0, "last": None}


def split_shares(pairs):
    """[(device, share %)] -> {device: effective share %} of the branch target.

    A share > 0 is kept as it is. When no device has a share, the target is split equally between all of them;
    otherwise the devices without a share split what remains of 100 % equally (never below 0)."""
    pairs = list(pairs)
    if not pairs:
        return {}
    fixed = {k: float(v) for k, v in pairs if v and v > 0}
    if not fixed:
        return {k: 100.0 / len(pairs) for k, _v in pairs}
    rest = [k for k, v in pairs if k not in fixed]
    left = max(0.0, 100.0 - sum(fixed.values()))
    fixed.update({k: left / len(rest) for k in rest})
    return fixed


def _local_day(tz):
    """SQL: the local day of pos_order o.date_order (stored in UTC)."""
    return SQL("(o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date", tz)


class NavittaBranch(models.Model):
    _inherit = "navitta.branch"

    pos_config_ids = fields.One2many("pos.config", "navitta_branch_id", string="Points of Sale")
    pos_last_sync = fields.Datetime("Last update from the Point of Sale", readonly=True)
    pos_share_warning = fields.Char("Target shares warning", compute="_compute_pos_share_warning")

    @api.depends("pos_config_ids.navitta_target_share")
    def _compute_pos_share_warning(self):
        for b in self:
            total = sum(b.pos_config_ids.mapped("navitta_target_share"))
            b.pos_share_warning = _(
                "The devices' shares add up to %s%%, more than 100%%: their targets together are more than the branch target",
                ("%.2f" % total).rstrip("0").rstrip(".")) if total > 100.005 else False

    def _pos_target_shares(self):
        """{pos.config id: effective share % of the branch target} for the branch's (active) points of sale."""
        self.ensure_one()
        return split_shares((c.id, c.navitta_target_share) for c in self.pos_config_ids if c.active)

    def _has_system(self):
        self.ensure_one()
        return bool(self.sudo().pos_config_ids)

    # ------------------------------------------------------------------ sales
    def _system_sync(self, days, force=False):
        db = self.env.cr.dbname
        now = time.time()
        for b in self.sudo().filtered("pos_config_ids"):
            todo = [d for d in days if force or now - _LAST_SYNC.get((db, b.id, d), 0) >= SYNC_EVERY]
            if todo:
                b._pos_sync_days(todo)
                for d in todo:
                    _LAST_SYNC[(db, b.id, d)] = now
        return True

    def _system_backfill(self, d0=None, d1=None):
        """The screens: every day of [d0, d1] with Point of Sale orders is synced (the history too), once."""
        return self._pos_backfill(d0, d1)

    def _pos_backfill(self, d0=None, d1=None, configs=None, hr_ids=None, force=False, old_branches=None):
        """Syncs the local days of [d0, d1] (all the history when not given) that have counted orders of these branches'
        points of sale and were never synced, or whose orders changed since (their number, total or latest change
        differ from the stored device figures), or whose day row is missing or not from the Point of Sale.

        configs: only the days of these points of sale's orders; hr_ids: only the days of the orders of these cashiers;
        force: sync all those days again; old_branches: branches that had these points of sale before (synced too).
        Returns the number of branch-days synced."""
        branches = self.sudo()
        if configs is None and not branches.filtered("pos_config_ids") and not old_branches:
            return 0
        Branch = self.env["navitta.branch"].sudo()
        cr = self.env.cr
        for model in ("pos.order", "pos.config", "navitta.pos.day", "navitta.daily.sales"):
            self.env[model].flush_model()
        tz = company_tz(self.env).zone
        conds = [SQL("o.state IN %s", COUNTED_STATES)]
        if configs is not None:
            conds.append(SQL("o.config_id = ANY(%s)", configs.ids or [0]))     # also one just unlinked from its branch
        else:
            conds.append(SQL("c.navitta_branch_id = ANY(%s)", branches.ids or [0]))
        if d0:
            conds.append(SQL("o.date_order >= %s", day_bounds_utc(self.env, d0)[0]))
        if d1:
            conds.append(SQL("o.date_order < %s", day_bounds_utc(self.env, d1)[1]))
        if hr_ids is not None:
            conds.append(SQL("o.employee_id = ANY(%s)", list(hr_ids) or [0]))
            force = True        # only some of the day's orders are seen: the counts can't be compared
        cr.execute(SQL("""
            SELECT o.config_id, c.navitta_branch_id, %s, COUNT(*), COALESCE(SUM(o.amount_total), 0),
                   date_trunc('second', MAX(o.write_date))
              FROM pos_order o JOIN pos_config c ON c.id = o.config_id
             WHERE %s
             GROUP BY 1, 2, 3""", _local_day(tz), SQL(" AND ").join(conds)))
        found = {(cfg, day): (br, n, round(float(total or 0.0), 2), last) for cfg, br, day, n, total, last in cr.fetchall()}
        stale = defaultdict(set)
        if force:
            for (_cfg, day), (br, *_x) in found.items():
                stale[br].add(day)
            if configs is None and hr_ids is None and not d0 and not d1:
                # «sync all»: also the days still counted from the Point of Sale that have no order any more
                cr.execute("SELECT branch_id, day FROM navitta_daily_sales WHERE source = 'pos' AND branch_id = ANY(%s)",
                           [branches.ids or [0]])
                for br, day in cr.fetchall():
                    stale[br].add(day)
        else:
            dconds = [SQL("TRUE")]
            if d0:
                dconds.append(SQL("day >= %s", d0))
            if d1:
                dconds.append(SQL("day <= %s", d1))
            dom = SQL(" AND ").join(dconds)
            cfg_ids = configs.ids if configs is not None else branches.pos_config_ids.ids
            cr.execute(SQL("""
                SELECT config_id, branch_id, day, orders, sales, last_order_write FROM navitta_pos_day
                 WHERE %s AND (config_id = ANY(%s) OR branch_id = ANY(%s))""", dom, cfg_ids or [0], branches.ids or [0]))
            stored = {(cfg, day): (br, n, round(float(s or 0.0), 2), last) for cfg, br, day, n, s, last in cr.fetchall()}
            cr.execute(SQL("SELECT branch_id, day, source FROM navitta_daily_sales WHERE %s AND branch_id = ANY(%s)",
                           dom, list({v[0] for v in found.values()}) or [0]))
            source = {(br, day): src for br, day, src in cr.fetchall()}
            for key, (br, n, total, last) in found.items():
                old = stored.pop(key, None)
                if old != (br, n, total, last) or source.get((br, key[1])) not in ("pos", "admin"):
                    stale[br].add(key[1])
                    if old and old[0] != br:
                        stale[old[0]].add(key[1])     # the point of sale moved to another branch
            for (_cfg, day), (br, n, *_x) in stored.items():
                if n:
                    stale[br].add(day)               # its orders were cancelled or moved: count the day again
        if old_branches:
            days = {day for (_cfg, day) in found}
            for ob in old_branches:
                stale[ob.id] |= days
        db, now, n = cr.dbname, time.time(), 0
        for br, days in stale.items():
            b = Branch.browse(br).exists() if br else Branch
            if not b or not days:
                continue
            b._pos_sync_days(sorted(days), quiet=True)
            for d in days:
                _LAST_SYNC[(db, b.id, d)] = now
            n += len(days)
        if n:
            _logger.info("Navitta: %s branch-days synced from the Point of Sale", n)
        return n

    def _pos_owner_of(self):
        """{pos.config id: navitta.employee} for the branch's points of sale that belong to one employee (her own device)."""
        self.ensure_one()
        return {c.id: c.navitta_employee_id for c in self.sudo().pos_config_ids if c.navitta_employee_id}

    def _pos_figures(self, day):
        """(day totals, {who: totals}, {0: shared account user ids}, [order info]) of one day; see _pos_figures_days."""
        self.ensure_one()
        return (self._pos_figures_days([day]).get(day) or (_blank(), defaultdict(_blank), defaultdict(set), [], {}))[:4]

    def _pos_figures_days(self, days):
        """{day: (day totals, {who: totals}, {0: shared account user ids}, [order info], {pos.config id: totals})} for the
        days with counted orders, in three queries for all the days.

        who = ("emp", navitta employee id) for every order of an employee's own device (whoever was picked as cashier)
        and for a cashier linked to an employee; ("hr", hr employee id) for a cashier not linked to any employee; 0 for
        an order without a cashier (the shared account)."""
        self.ensure_one()
        cfgs = self.sudo().pos_config_ids.ids
        days = sorted(set(days))
        if not cfgs or not days:
            return {}
        cr = self.env.cr
        for model in ("pos.order", "pos.order.line", "pos.payment", "pos.payment.method"):
            self.env[model].flush_model()
        tz = company_tz(self.env).zone
        start, end = day_bounds_utc(self.env, days[0])[0], day_bounds_utc(self.env, days[-1])[1]
        base = SQL("""
            SELECT o.id, %s AS day, o.amount_total, o.amount_tax, o.employee_id, o.user_id, o.config_id, o.write_date
              FROM pos_order o
             WHERE o.config_id = ANY(%s) AND o.state IN %s AND o.date_order >= %s AND o.date_order < %s""",
                   _local_day(tz), cfgs, COUNTED_STATES, start, end)
        cr.execute(SQL("SELECT * FROM (%s) x WHERE x.day = ANY(%s) ORDER BY x.id", base, days))
        orders = cr.fetchall()
        lines, pays = {}, defaultdict(lambda: [0.0, 0.0])
        if orders:
            cr.execute(SQL("""
                WITH o AS (%s)
                SELECT l.order_id,
                       COALESCE(SUM(l.qty) FILTER (WHERE l.qty > 0 AND l.price_subtotal_incl >= 0), 0),
                       COALESCE(SUM(-l.qty) FILTER (WHERE l.qty < 0), 0)
                  FROM pos_order_line l JOIN o ON o.id = l.order_id
                 WHERE o.day = ANY(%s) GROUP BY l.order_id""", base, days))
            lines = {o: (sold, ret) for o, sold, ret in cr.fetchall()}
            cr.execute(SQL("""
                WITH o AS (%s)
                SELECT p.pos_order_id, pm.is_cash_count, SUM(p.amount)
                  FROM pos_payment p JOIN o ON o.id = p.pos_order_id
                  JOIN pos_payment_method pm ON pm.id = p.payment_method_id
                 WHERE o.day = ANY(%s) GROUP BY 1, 2""", base, days))
            for o, is_cash, amount in cr.fetchall():
                pays[o][0 if is_cash else 1] += amount or 0.0
        owners = self._pos_owner_of()
        hr_ids = list({o[4] for o in orders if o[4]})
        nav_of = {e.hr_employee_id.id: e.id for e in self.env["navitta.employee"].sudo().with_context(active_test=False).search(
            [("hr_employee_id", "in", hr_ids)])} if hr_ids else {}

        def who(cfg, hr):
            if cfg in owners:
                return ("emp", owners[cfg].id)
            if hr:
                return ("emp", nav_of[hr]) if hr in nav_of else ("hr", hr)
            return 0

        out = {}
        for oid, day, total, tax, emp, user, cfg, wdate in orders:
            wdate = wdate.replace(microsecond=0)
            if day not in out:
                out[day] = (_blank(), defaultdict(_blank), defaultdict(set), [], defaultdict(_blank))
            day_t, by_emp, users, info, by_cfg = out[day]
            sold, ret = lines.get(oid, (0, 0))
            key = who(cfg, emp)
            for t in (day_t, by_emp[key], by_cfg[cfg]):
                t["orders"] += 1
                t["sales"] += total or 0.0
                t["tax"] += tax or 0.0
                t["invoices"] += 1 if (total or 0) > 0 else 0
                t["returns_amount"] += -total if (total or 0) < 0 else 0.0
                t["items"] += int(sold or 0)
                t["returns"] += int(ret or 0)
                t["cash"] += pays[oid][0]
                t["card"] += pays[oid][1]
                t["last"] = max(t["last"], wdate) if t["last"] else wdate
            if not key and user:
                users[0].add(user)
            info.append({"id": oid, "total": total or 0.0, "returned": int(ret or 0),
                         "employee_id": key[1] if key and key[0] == "emp" else False})
        return out

    def _pos_sync_day(self, day):
        self.ensure_one()
        return self._pos_sync_days([day])

    def _pos_sync_days(self, days, quiet=False):
        """Writes the days' POS figures into the branch's daily sales, the employees' daily sales and the device figures.

        quiet (the history being synced): a day whose figures did not come from the Point of Sale yet takes them without
        an entry in «changes after the day was closed» (that log is for later refunds and late orders)."""
        self.ensure_one()
        days = sorted(set(days))
        if not days:
            return
        figs = self._pos_figures_days(days)
        today = app_today(self.env)
        Sales = self.env["navitta.daily.sales"].sudo().with_context(navitta_change_source="pos")
        rows = {r.day: r for r in Sales.search([("branch_id", "=", self.id), ("day", "in", days)])}
        EmpSales = self.env["navitta.employee.sales"].sudo()
        old_lines = defaultdict(list)
        for l in EmpSales.search([("branch_id", "=", self.id), ("day", "in", days), ("source", "in", ("pos", "report"))]):
            old_lines[l.day].append(l)
        PosDay = self.env["navitta.pos.day"].sudo()
        cfg_ids = self.sudo().pos_config_ids.ids
        pdays = {(p.config_id.id, p.day): p for p in PosDay.search(
            [("day", "in", days), "|", ("branch_id", "=", self.id), ("config_id", "in", cfg_ids or [0])])}
        Hr = self.env["hr.employee"].sudo()
        Emp = self.env["navitta.employee"].sudo().with_context(active_test=False)
        now = fields.Datetime.now()
        pd_new, pd_gone, line_new, line_gone = [], PosDay, [], EmpSales
        touched = False
        keys = ("employee_id", "cashier", "amount", "tax", "invoices", "items", "returns", "returns_amount")
        for day in days:
            day_t, by_emp, users, info, by_cfg = figs.get(day) or (_blank(), {}, {}, [], {})
            # the device figures
            for cfg, t in by_cfg.items():
                vals = {"branch_id": self.id, "last_order_write": t["last"]}
                vals.update({k: round(t[k], 2) if isinstance(t[k], float) else t[k] for k in DEVICE_FIELDS})
                p = pdays.pop((cfg, day), None)
                if p is None:
                    pd_new.append(dict(vals, config_id=cfg, day=day, synced_at=now))
                elif any((p[k].id if k == "branch_id" else p[k]) != v for k, v in vals.items()):
                    p.write(dict(vals, synced_at=now))
            for key in [k for k in pdays if k[1] == day]:
                if pdays[key].branch_id.id == self.id:
                    pd_gone |= pdays.pop(key)        # no order on this device that day any more
            # the branch's day
            row = rows.get(day)
            if not day_t["orders"] and (not row or row.source != "pos"):
                continue    # nothing sold in POS that day: keep what the daily report / management entered
            vals = {"sales": round(day_t["sales"], 2), "tax_amount": round(day_t["tax"], 2), "invoices": day_t["invoices"],
                    "items": day_t["items"], "returns": day_t["returns"], "returns_amount": round(day_t["returns_amount"], 2),
                    "cash": round(day_t["cash"], 2), "card": round(day_t["card"], 2), "source": "pos"}
            if row and row.source == "admin":
                # a day management corrected by hand keeps its figures; the refunds and the VAT keep following the orders
                vals = {k: vals[k] for k in ("tax_amount", "returns_amount")}
            first = not row or row.source != "pos"
            row = row or Sales._row(self, day)
            if any(row[k] != v for k, v in vals.items()):
                (row.with_context(navitta_from_report=True) if quiet and first else row).write(vals)
                touched = True
            # the employees' lines
            user_names = ", ".join(self.env["res.users"].sudo().browse(sorted(users.get(0, ()))).mapped("name"))
            new = []
            for key, t in by_emp.items():
                emp = Emp.browse(key[1]) if key and key[0] == "emp" else Emp
                # stored markers (kept as they are, compared on the next sync), shown translated by cashier_text()
                cashier = ((emp.hr_employee_id.name or emp.name) if emp else Hr.browse(key[1]).name if key
                           else CASHIER_SHARED % user_names if user_names else CASHIER_NONE)
                new.append({"branch_id": self.id, "day": day, "employee_id": emp.id or False, "cashier": cashier,
                            "amount": round(t["sales"], 2), "tax": round(t["tax"], 2), "invoices": t["invoices"], "items": t["items"],
                            "returns": t["returns"], "returns_amount": round(t["returns_amount"], 2), "source": "pos"})
            mine = old_lines.get(day, [])
            # the daily report's lines for the same day would count every sale twice
            report = [l for l in mine if l.source == "report"]
            old = [l for l in mine if l.source == "pos"]
            same = sorted((tuple((l[k].id if k == "employee_id" else l[k]) or False for k in keys) for l in old)) == \
                sorted((tuple(v[k] or False for k in keys) for v in new))
            for l in report + ([] if same else old):
                line_gone |= l
            if not same:
                line_new += new
                touched = True
            if day >= today - timedelta(days=1):
                self._pos_link_visits(day, info)
        pd_gone.unlink()
        if pd_new:
            PosDay.create(pd_new)
        line_gone.unlink()
        if line_new:
            EmpSales.create(line_new)
        if touched:
            self.pos_last_sync = now       # last time the figures changed

    # ------------------------------------------------------------------ sales made without the counter
    def _pos_link_visits(self, day, info):
        """Each new order belongs to the counter visit running around its time (same employee when known): the visit
        gets the invoice amount (and «bought» / «exchange» if it was saved without it). An order of an employee with no
        such visit becomes an automatic visit «sold without the counter» that she confirms before checking out.
        Only today's and yesterday's orders (not the history), each order once."""
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

    def action_pos_sync_all(self):
        """Button: count every day again from the Point of Sale orders (the whole history of these branches)."""
        n = self.sudo()._pos_backfill(force=True)
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": _("%s days were synced from the Point of Sale", n), "type": "success"}}

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
