import base64
from datetime import timedelta

from odoo import fields
from odoo.addons.navitta_ops.models.common import app_today, dow
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "navitta")
class NavittaPosCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        if not company.chart_template:
            env["account.chart.template"].try_loading("generic_coa", company=company, install_demo=False)
        company.write({"navitta_tz": "Asia/Riyadh", "navitta_vat_rate": 15.0})
        cls.dwd, cls.qsm = env.ref("navitta_ops.branch_dwd"), env.ref("navitta_ops.branch_qsm")
        cls.reem, cls.shima = env.ref("navitta_ops.emp_dwd_1"), env.ref("navitta_ops.emp_dwd_2")
        cls.hubour = env.ref("navitta_ops.emp_qsm_1")
        for e in (cls.reem, cls.shima, cls.hubour):
            e._set_pin("2468")
        J = env["account.journal"]
        PM = env["pos.payment.method"]
        cls.cash = PM.create({"name": "نقدي", "journal_id": J.search([("type", "=", "cash"), ("company_id", "=", company.id)], limit=1).id})
        cls.card = PM.create({"name": "شبكة", "journal_id": J.search([("type", "=", "bank"), ("company_id", "=", company.id)], limit=1).id})
        Cfg = env["pos.config"]
        cls.cfg_dwd = Cfg.create({"name": "كاشير الدوادمي", "payment_method_ids": [(6, 0, [cls.cash.id, cls.card.id])],
                                  "navitta_branch_id": cls.dwd.id})
        cash_j2 = J.create({"name": "صندوق القصيم", "code": "CSHQ", "type": "cash", "company_id": company.id})
        cls.cash_qsm = PM.create({"name": "نقدي القصيم", "journal_id": cash_j2.id})      # Odoo: one cash method per point of sale
        cls.cfg_qsm = Cfg.create({"name": "كاشير القصيم", "payment_method_ids": [(6, 0, [cls.cash_qsm.id, cls.card.id])],
                                  "navitta_branch_id": cls.qsm.id})
        cls.tax = env["account.tax"].create({"name": "ضريبة 15", "amount": 15, "price_include_override": "tax_included",
                                             "type_tax_use": "sale", "company_id": company.id})
        cls.prod = env["product.product"].create({"name": "حذاء", "list_price": 230, "available_in_pos": True,
                                                  "taxes_id": [(6, 0, cls.tax.ids)]})
        grp_b, user = env.ref("navitta_ops.group_navitta_branch"), env.ref("base.group_user")
        cls.dev = env["res.users"].create({"name": "جهاز الدوادمي", "login": "dwd-pos-device", "navitta_branch_id": cls.dwd.id,
                                           "groups_id": [(6, 0, [user.id, grp_b.id])]})
        cls.boss = env["res.users"].create({"name": "الإدارة", "login": "boss-pos",
                                            "groups_id": [(6, 0, [user.id, env.ref("navitta_ops.group_navitta_admin").id])]})
        cls.general = env["hr.employee"].create({"name": "Administrator", "company_id": company.id})   # not a Navitta employee
        cls.day = app_today(env)
        env["navitta.task.def"].search([]).write({"active": False})     # end-of-shift tasks are tested in navitta_ops

    def photo(self):
        return self.env["navitta.photo"]._from_b64(self.dwd, base64.b64encode(b"\xff\xd8\xff" + b"0" * 200).decode())[0].id

    def rep(self, **vals):
        return dict({"employee_name": "ريم", "photo_card": self.photo(), "photo_inv": self.photo()}, **vals)

    def end_float(self):
        self.env["navitta.float"].create({"branch_id": self.dwd.id, "day": self.day, "kind": "end", "expected": 1000, "amount": 1000, "state": "ok"})

    def own_config(self, name, owner=None):
        """A second point of sale of the dwd branch (an employee's own device)."""
        J = self.env["account.journal"]
        code = "CW%s" % J.search_count([])
        cash_j = J.create({"name": "صندوق %s" % name, "code": code, "type": "cash", "company_id": self.env.company.id})
        pm = self.env["pos.payment.method"].create({"name": "نقدي %s" % name, "journal_id": cash_j.id})
        cfg = self.env["pos.config"].create({"name": name, "payment_method_ids": [(6, 0, [pm.id, self.card.id])],
                                             "navitta_branch_id": self.dwd.id, "navitta_employee_id": owner.id if owner else False})
        return cfg, pm

    def checked_in(self, *emps):
        now = fields.Datetime.now()
        for e in emps:
            self.env["navitta.attendance"].create({"employee_id": e.id, "day": self.day, "check_in": now - timedelta(hours=2)})

    def _order(self, session, qty, pm, hr=None):
        sub, incl = 200.0 * qty, 230.0 * qty
        o = self.env["pos.order"].create({
            "session_id": session.id, "company_id": self.env.company.id, "employee_id": hr.id if hr else False,
            "lines": [(0, 0, {"product_id": self.prod.id, "qty": qty, "price_unit": 230, "price_subtotal": sub,
                              "price_subtotal_incl": incl, "tax_ids": [(6, 0, self.tax.ids)]})],
            "amount_tax": incl - sub, "amount_total": incl, "amount_paid": 0, "amount_return": 0})
        o.add_payment({"pos_order_id": o.id, "amount": incl, "payment_method_id": pm.id})
        o.action_pos_order_paid()
        return o

    def _open(self, cfg, hr):
        s = self.env["pos.session"].create({"config_id": cfg.id, "user_id": self.env.uid})
        s.employee_id = hr
        s.set_opening_control(0, "")
        return s

    def test_cashiers_follow_the_branch(self):
        self.dwd.action_link_cashiers()
        self.qsm.action_link_cashiers()
        self.assertTrue(self.reem.hr_employee_id and self.shima.hr_employee_id)
        self.assertTrue(self.cfg_dwd.module_pos_hr)                               # "Log in with employees"
        self.assertEqual(self.cfg_dwd.basic_employee_ids, self.reem.hr_employee_id | self.shima.hr_employee_id)
        self.assertEqual(self.cfg_qsm.basic_employee_ids, self.qsm.employee_ids.mapped("hr_employee_id"))
        self.assertNotIn(self.reem.hr_employee_id, self.cfg_qsm.basic_employee_ids)
        # Odoo's Employees app: a branch device sees its own branch's cashiers only, management sees all
        mine = self.reem.hr_employee_id | self.shima.hr_employee_id
        self.assertEqual(self.env["hr.employee"].with_user(self.dev).search([]), mine)
        self.assertEqual(self.env["hr.employee.public"].with_user(self.dev).search([]).ids, mine.ids)
        self.assertIn(self.hubour.hr_employee_id, self.env["hr.employee"].with_user(self.boss).search([]))
        self.shima.branch_id = self.qsm                                          # moves: her cashier moves too
        self.assertEqual(self.cfg_dwd.basic_employee_ids, self.reem.hr_employee_id)
        self.assertIn(self.shima.hr_employee_id, self.cfg_qsm.basic_employee_ids)
        self.reem.active = False                                                 # left: out of the cashier list
        self.assertFalse(self.cfg_dwd.basic_employee_ids)
        with self.assertRaises(Exception):
            self.hubour.hr_employee_id = self.shima.hr_employee_id               # one cashier per employee

    def test_sales_from_pos_orders(self):
        self.dwd.action_link_cashiers()
        reem_hr, shima_hr = self.reem.hr_employee_id, self.shima.hr_employee_id
        s = self._open(self.cfg_dwd, reem_hr)
        self._order(s, 2, self.cash, reem_hr)          # 460, tax 60
        self._order(s, 1, self.card, shima_hr)         # 230
        self._order(s, 1, self.cash)                   # 230 without a cashier: the shared account
        self._order(s, -1, self.cash, reem_hr)         # refund -230
        self._order(s, 1, self.card, self.general)     # 230 by a cashier that is no Navitta employee
        self.dwd._system_sync([self.day], force=True)
        ds = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual((ds.source, ds.sales, ds.tax_amount, ds.vat_amount, ds.invoices, ds.items, ds.returns, ds.returns_amount,
                          ds.cash, ds.card), ("pos", 920, 120, 120, 4, 5, 1, 230, 460, 460))
        lines = self.env["navitta.employee.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        by = {(l.employee_id.name or l.cashier): (l.amount, l.invoices, l.items, l.returns) for l in lines}
        self.assertEqual(by["ريم"], (230, 1, 2, 1))
        self.assertEqual(by["شيمة"], (230, 1, 1, 0))
        self.assertEqual(by["Administrator"], (230, 1, 1, 0))
        self.assertTrue(any(k.startswith("الحساب العام") for k in by))
        # registers: who opened it
        st = self.dwd._system_status(self.day)[self.dwd.id]
        self.assertEqual((st[0]["opened_by"], st[0]["state"], st[0]["general"]), ("ريم", "opened", False))
        # daily report: figures come from the POS, typed sales are ignored
        api = self.env["navitta.api"].with_user(self.dev)
        got = api.report_get()["system"]
        self.assertEqual((got["sales"], got["invoices"], got["cash"]), (920, 4, 460))
        self.assertEqual(api.report_save(self.rep(sales=99999, cash=460, card=460, deposit=300))["error"], "float_end_required")
        self.end_float()
        self.assertTrue(api.report_save(self.rep(sales=99999, cash=460, card=460, deposit=300))["ok"])
        ds.invalidate_recordset()
        self.assertEqual((ds.sales, ds.cash, ds.deposit, ds.source), (920, 460, 300, "pos"))
        rep = self.env["navitta.daily.report"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual(rep.sales, 920)
        self.assertEqual(self.env["navitta.employee.sales"].search_count(          # not counted twice
            [("branch_id", "=", self.dwd.id), ("day", "=", self.day), ("source", "=", "report")]), 0)
        self.assertEqual(self.cfg_dwd.navitta_session_label, "Open")
        # management: employees by cashier, branches compared, weekday filter
        boss = self.env["navitta.api"].with_user(self.boss)
        names = {r["name"] for r in boss.employee_summary(self.day, self.day, self.dwd.id)["rows"]}
        self.assertIn("ريم", names)
        self.assertTrue(any("cashier not linked" in n for n in names))
        self.assertTrue(any(n.startswith("Shared account") for n in names))
        self.env["navitta.visit"].create({"branch_id": self.dwd.id, "employee_id": self.reem.id, "day": self.day,
                                          "started_at": "2026-01-01 12:00:00", "saved": True, "bought": True})
        row = [r for r in boss.admin_compare(self.day, self.day)["rows"] if r["branch_id"] == self.dwd.id][0]
        self.assertEqual((row["system"], row["sales"], row["tax"], row["invoices"], row["avg_invoice"], row["items"], row["returns"],
                          row["visits"], row["bought"], row["counter_gap"], row["report_gap"]),
                         (True, 920, 120, 4, 230, 5, 1, 4, 3, 1, 0))     # + 3 automatic visits: sales made without the counter
        other_day = [(dow(self.day) + 1) % 7]
        row = [r for r in boss.admin_compare(self.day, self.day, other_day)["rows"] if r["branch_id"] == self.dwd.id][0]
        self.assertEqual(row["sales"], 0)
        self.assertEqual(boss.admin_dashboard(str(self.day), str(self.day))["sales"][0]["sales"], 920)
        # nightly summary
        self.assertEqual(self.env["navitta.api"]._summary_data(self.day)["sales"], 920)

    def test_cron_and_no_pos_branch(self):
        self.dwd.action_link_cashiers()
        s = self._open(self.cfg_dwd, self.reem.hr_employee_id)
        self._order(s, 1, self.cash, self.reem.hr_employee_id)
        self.env["navitta.branch"]._cron_pos_sync()
        ds = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual((ds.sales, ds.source), (230, "pos"))
        # a branch without a point of sale keeps the daily report
        self.cfg_qsm.navitta_branch_id = False
        self.assertFalse(self.qsm._has_system())
        self.assertFalse(self.env["navitta.api"].with_user(self.boss)._system_day(self.qsm, self.day))
        setup = {i["key"]: i["done"] for i in self.env["navitta.api"].with_user(self.boss).admin_setup()["items"]}
        self.assertFalse(setup["pos_branch"])

    # ------------------------------------------------------------------ 26.3
    def test_report_cash_card_against_pos(self):
        """The daily report: sales / invoices / items locked to the POS; the counted cash and the card-terminal settlement
        are compared with the POS payments, a difference needs a reason and alerts management."""
        self.dwd.action_link_cashiers()
        reem_hr = self.reem.hr_employee_id
        s = self._open(self.cfg_dwd, reem_hr)
        self._order(s, 2, self.cash, reem_hr)          # 460 cash
        self._order(s, 1, self.card, reem_hr)          # 230 card
        self.end_float()
        api = self.env["navitta.api"].with_user(self.dev)
        sys = api.report_get()["system"]
        self.assertEqual((sys["sales"], sys["cash"], sys["card"]), (690, 460, 230))
        self.assertEqual(api.report_save(self.rep(sales=1, cash=440, card=230))["error"], "cash_reason_required")
        self.assertEqual(api.report_save(self.rep(cash=460.2, card=200))["error"], "card_reason_required")
        self.assertTrue(api.report_save(self.rep(sales=1, invoices=50, cash=440, card=230.3, cash_note="20 للفكة"))["ok"])
        rep = self.env["navitta.daily.report"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual((rep.sales, rep.invoices, rep.cash, rep.sys_cash, rep.cash_diff, rep.card_diff), (690, 2, 440, 460, -20, 0))
        alert = self.env["navitta.notification"].search([("subject", "like", "Cash or card difference")])
        self.assertEqual(len(alert), 1)
        self.assertIn("Point of Sale 460", alert.body)
        ds = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual((ds.cash, ds.card, ds.source), (460, 230, "pos"))       # the day keeps the POS figures
        boss = self.env["navitta.api"].with_user(self.boss)
        row = [x for x in boss.admin_sales(self.dwd.id, self.day, self.day, self.day.year, self.day, self.day)["daily"]][0]
        self.assertEqual((row["check"]["cash_diff"], row["check"]["cash_note"]), (-20, "20 للفكة"))

    def test_changes_after_close_and_pinned_day(self):
        """After the report closed the day, a later POS order (e.g. a refund) changes the day: logged with the source. A day
        management corrected keeps its figures; only the refunds and VAT keep following the orders."""
        self.dwd.action_link_cashiers()
        reem_hr = self.reem.hr_employee_id
        s = self._open(self.cfg_dwd, reem_hr)
        self._order(s, 1, self.cash, reem_hr)
        self.end_float()
        api = self.env["navitta.api"].with_user(self.dev)
        self.assertTrue(api.report_save(self.rep(cash=230, card=0))["ok"])
        Log = self.env["navitta.sales.change.log"]
        self.assertFalse(Log.search([]))
        self._order(s, -1, self.cash, reem_hr)         # a refund after closing
        self.dwd._system_sync([self.day], force=True)
        got = {c.field: (c.old_value, c.new_value, c.source) for c in Log.search([])}
        self.assertEqual(got["sales"], (230, 0, "pos"))
        self.assertEqual(got["returns_amount"], (0, 230, "pos"))
        self.assertEqual(got["cash"], (230, 0, "pos"))
        boss = self.env["navitta.api"].with_user(self.boss)
        self.assertTrue(boss.admin_day_edit(self.dwd.id, self.day, {"sales": 230}, "المرتجع انسجل بالغلط")["ok"])
        self.assertTrue(boss.admin_day_get(self.dwd.id, self.day)["log"])
        self._order(s, 1, self.card, reem_hr)
        self.dwd._system_sync([self.day], force=True)
        ds = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual((ds.sales, ds.source, ds.returns_amount), (230, "admin", 230))     # pinned
        self.assertEqual(Log.search([("source", "=", "admin")]).mapped("field"), ["sales"])

    def test_auto_visits(self):
        """A POS order with no counter visit around it becomes an automatic visit for the employee who sold it; she
        confirms it before checking out. An order during her running visit is linked to it. Each order once."""
        self.dwd.action_link_cashiers()
        reem_hr, shima_hr = self.reem.hr_employee_id, self.shima.hr_employee_id
        self.checked_in(self.reem, self.shima)
        api = self.env["navitta.api"].with_user(self.dev)
        vid = api.visit_start(self.reem.id)["id"]
        s = self._open(self.cfg_dwd, reem_hr)
        o1 = self._order(s, 1, self.cash, reem_hr)        # during reem's visit
        o2 = self._order(s, 2, self.card, shima_hr)       # shima, counter off
        o3 = self._order(s, 1, self.cash)                 # no cashier (shared account): the visit running in the branch
        o4 = self._order(s, -1, self.cash, shima_hr)      # an exchange (returned item) by shima
        self.dwd._system_sync([self.day], force=True)    # every 10 minutes, at session close, and on the screens
        st = api.branch_state()
        Visit = self.env["navitta.visit"]
        visit = Visit.browse(vid)
        self.assertEqual((o1.navitta_visit_id, o3.navitta_visit_id, visit.amount, visit.auto), (visit, visit, 460, False))
        autos = Visit.search([("auto", "=", True)], order="id")
        self.assertEqual([(a.employee_id, a.amount, a.bought, a.exchanged, a.saved) for a in autos],
                         [(self.shima, 460, True, False, True), (self.shima, -230, False, True, True)])
        self.assertEqual((o2.navitta_visit_id, o4.navitta_visit_id), (autos[0], autos[1]))
        self.assertTrue(o3.navitta_checked)
        self.assertEqual(len([v for v in st["visits"] if v["auto"]]), 2)
        api.branch_state()
        self.dwd._system_sync([self.day], force=True)
        self.assertEqual(Visit.search_count([("auto", "=", True)]), 2)              # not added twice
        r = api.check_out(self.shima.id, "2468")
        self.assertEqual((r["error"], len(r["autos"])), ("auto_unacked", 2))
        for a in autos:
            self.assertTrue(api.auto_visit_ack(a.id)["ok"])
        self.assertTrue(api.check_out(self.shima.id, "2468")["ok"])
        # a sale rung up after her last check: the check-out syncs first and asks her to confirm it
        api.visit_end(vid)
        api.visit_save(vid, {"buy": True})
        visit.write({"ended_at": fields.Datetime.now() - timedelta(minutes=30)})   # her visit ended long before this sale
        self._order(s, 1, self.card, reem_hr)
        r = api.check_out(self.reem.id, "2468")
        self.assertEqual(r["error"], "auto_unacked")
        # the fingerprint device holds her out punch the same way
        dev = self.env["navitta.device"].create({"serial_number": "NVPOS1", "branch_id": self.dwd.id, "state": "active"})
        self.reem.device_user_id = "4"
        punch = self.env["navitta.device.punch"].create({"device_id": dev.id, "device_user_id": "4", "punched_at": fields.Datetime.now()})
        punch._process()
        self.assertEqual(punch.state, "tasks")
        self.assertIn("without the counter", punch.message)
        api.auto_visit_ack(r["autos"][0]["id"], "عميلة مستعجلة")
        punch.action_reprocess()
        self.assertEqual(punch.state, "out")

    def test_own_device_attribution(self):
        """An employee's own device: every order on it counts for her whoever is picked as cashier (sales, refunds, auto
        visits, no «shared account»), and she is its only cashier. Other points of sale keep the cashier on the order."""
        self.dwd.action_link_cashiers()
        reem_hr, shima_hr = self.reem.hr_employee_id, self.shima.hr_employee_id
        cfg, pm = self.own_config("ريم", self.reem)
        self.assertEqual(cfg.basic_employee_ids, reem_hr)
        cfg.advanced_employee_ids = self.general                       # a manager keeps her access
        self.assertEqual(cfg.basic_employee_ids, reem_hr)
        self.assertIn(self.general, cfg.advanced_employee_ids)
        self.assertEqual(self.cfg_dwd.basic_employee_ids, reem_hr | shima_hr)    # the shared device: unchanged
        s = self._open(cfg, reem_hr)
        self._order(s, 2, pm, shima_hr)                 # shima picked as cashier on reem's device
        self._order(s, 1, pm)                           # no cashier
        self._order(s, -1, pm, self.general)            # a refund by a manager
        s2 = self._open(self.cfg_dwd, shima_hr)
        self._order(s2, 1, self.cash, shima_hr)         # the shared device: by its cashier
        self.dwd._system_sync([self.day], force=True)
        lines = self.env["navitta.employee.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        by = {l.employee_id.name or l.cashier: (l.amount, l.invoices, l.items, l.returns, l.returns_amount) for l in lines}
        self.assertEqual(by, {"ريم": (460, 2, 3, 1, 230), "شيمة": (230, 1, 1, 0, 0)})
        autos = self.env["navitta.visit"].search([("auto", "=", True)])
        self.assertEqual(autos.mapped("employee_id"), self.reem | self.shima)
        self.assertEqual(len(autos.filtered(lambda v: v.employee_id == self.reem)), 3)
        st = self.dwd._system_status(self.day)[self.dwd.id]
        mine = [x for x in st if x["name"] == "ريم"][0]
        self.assertEqual((mine["owner"], mine["general"]), ("ريم", False))
        # one device per employee, from its branch
        with self.assertRaises(ValidationError):
            self.own_config("ريم 2", self.reem)
        with self.assertRaises(ValidationError):
            self.cfg_qsm.navitta_employee_id = self.reem
        # she moves to another branch: the device is no longer hers, its cashier list is the branch's again
        self.reem.branch_id = self.qsm
        self.assertFalse(cfg.navitta_employee_id)
        self.assertEqual(cfg.basic_employee_ids, shima_hr)
        self.assertEqual(self.cfg_qsm.navitta_employee_id.id, False)
