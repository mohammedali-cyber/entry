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

    def link(self):
        """As the owner does it: each employee picks her existing cashier account (Odoo employee) by hand."""
        Hr = self.env["hr.employee"]
        for e in (self.reem, self.shima, self.hubour):
            if not e.hr_employee_id:
                e.hr_employee_id = Hr.create({"name": e.name, "company_id": self.env.company.id})
        return self.reem.hr_employee_id, self.shima.hr_employee_id

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

    def test_sales_from_pos_orders(self):
        reem_hr, shima_hr = self.link()
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
        self.link()
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
        reem_hr = self.link()[0]
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
        reem_hr = self.link()[0]
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
        reem_hr, shima_hr = self.link()
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
        reem_hr, shima_hr = self.link()
        cfg, pm = self.own_config("ريم", self.reem)
        self.assertFalse(cfg.basic_employee_ids)                       # Odoo's cashier lists stay as configured
        self.assertFalse(self.cfg_dwd.basic_employee_ids)
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
        # she moves to another branch: the device is no longer hers
        self.reem.branch_id = self.qsm
        self.assertFalse(cfg.navitta_employee_id)
        self.assertFalse(cfg.basic_employee_ids)
        self.assertEqual(self.cfg_qsm.navitta_employee_id.id, False)

    def test_delete_never_touches_pos(self):
        """26.3 delete with the delete password: deleting a counter visit, a sales day or a daily report removes only
        Navitta records; the Point of Sale orders, payments and their accounting stay (an order only loses its link to
        the deleted visit), and the next sync counts the day again from the orders."""
        reem_hr = self.link()[0]
        self.checked_in(self.reem)
        api = self.env["navitta.api"].with_user(self.dev)
        adm = self.env["navitta.api"].with_user(self.boss)
        self.env.company._navitta_set_delete_password("del-1234")
        vid = api.visit_start(self.reem.id)["id"]
        s = self._open(self.cfg_dwd, reem_hr)
        o = self._order(s, 2, self.cash, reem_hr)
        self.dwd._system_sync([self.day], force=True)
        self.assertEqual(o.navitta_visit_id.id, vid)
        orders, payments = self.env["pos.order"].search_count([]), self.env["pos.payment"].search_count([])
        moves = self.env["account.move"].search_count([])
        self.assertTrue(adm.admin_delete("visits", vid, "test visit", "del-1234")["ok"])
        o.invalidate_recordset()
        self.assertTrue(o.exists())
        self.assertFalse(o.navitta_visit_id)
        row = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual(row.sales, 460)
        self.assertTrue(adm.admin_delete("daily_sales", row.id, "recount", "del-1234")["ok"])
        self.assertFalse(row.exists())
        self.assertEqual((self.env["pos.order"].search_count([]), self.env["pos.payment"].search_count([]),
                          self.env["account.move"].search_count([])), (orders, payments, moves))
        self.assertEqual(o.state, "paid")
        self.dwd._system_sync([self.day], force=True)          # the POS figures come back from the orders
        row = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", self.day)])
        self.assertEqual(row.sales, 460)
        self.assertEqual(adm.admin_delete("pos_orders", o.id, "x", "del-1234")["error"], "forbidden")

    # ------------------------------------------------------------------ owner's fixes (26.3 follow-up)
    def test_linking_creates_no_employee_and_keeps_cashier_lists(self):
        """Linking a point of sale to a branch or an owner, linking an employee to a cashier account and syncing never
        create Odoo employees and never change Odoo's own cashier lists / «Log in with Employees»."""
        Hr = self.env["hr.employee"].with_context(active_test=False)
        akaria, akaria2 = Hr.create({"name": "POS_AKARIA"}), Hr.create({"name": "POS_AKARIA2"})
        qassim = Hr.create({"name": "POS_QASSIM"})
        self.cfg_dwd.basic_employee_ids = akaria | akaria2
        self.cfg_qsm.basic_employee_ids = qassim
        pos_hr = (self.cfg_dwd.module_pos_hr, self.cfg_qsm.module_pos_hr)
        n = Hr.search_count([])
        cfg, pm = self.own_config("جهاز ريم", self.reem)                 # created with its owner
        cfg.navitta_employee_id = False
        cfg.navitta_employee_id = self.reem                              # owner changed
        self.cfg_dwd.navitta_branch_id = False
        self.cfg_dwd.navitta_branch_id = self.dwd                        # linked again
        self.shima.hr_employee_id = akaria2                              # optional: an existing cashier account
        s = self._open(self.cfg_dwd, akaria)
        self._order(s, 1, self.cash, akaria)
        self.dwd.action_pos_sync_all()
        self.env["navitta.api"].with_user(self.boss).admin_dashboard()
        self.assertEqual(Hr.search_count([]), n)
        self.assertEqual(self.cfg_dwd.basic_employee_ids, akaria | akaria2)
        self.assertEqual(self.cfg_qsm.basic_employee_ids, qassim)
        self.assertFalse(cfg.basic_employee_ids)
        self.assertEqual((self.cfg_dwd.module_pos_hr, self.cfg_qsm.module_pos_hr), pos_hr)
        self.assertFalse(hasattr(self.dwd, "action_link_cashiers"))
        # the cashier account is chosen from the existing Odoo employees only
        view = self.env.ref("navitta_ops_pos.view_employee_form_pos").arch
        self.assertIn("no_create", view)
        # Point of Sale → Settings: choose the device, then its branch and its owner
        from odoo.tests import Form
        form = Form(self.env["res.config.settings"])
        form.pos_config_id = self.cfg_qsm
        form.pos_navitta_branch_id = self.dwd
        form.pos_navitta_employee_id = self.shima
        form.save().execute()
        self.assertEqual((self.cfg_qsm.navitta_branch_id, self.cfg_qsm.navitta_employee_id), (self.dwd, self.shima))
        self.assertEqual(self.cfg_qsm.basic_employee_ids, qassim)
        self.assertEqual(Hr.search_count([]), n)

    def test_device_access_and_system_administrators(self):
        """A branch device reads its own branch's cashier accounts only (its employees' and its points of sale's cashiers);
        a system administrator who also has the device group by mistake reads every Odoo employee and opens Settings."""
        from odoo.exceptions import AccessError
        from odoo.tests import Form
        Hr = self.env["hr.employee"]
        akaria, qassim = Hr.create({"name": "POS_AKARIA"}), Hr.create({"name": "POS_QASSIM"})
        manager = Hr.create({"name": "POS manager"})
        self.cfg_dwd.write({"basic_employee_ids": [(6, 0, akaria.ids)], "advanced_employee_ids": [(6, 0, manager.ids)]})
        self.cfg_qsm.basic_employee_ids = qassim
        reem_hr = self.link()[0]
        mine = akaria | self.cfg_dwd.advanced_employee_ids | reem_hr | self.shima.hr_employee_id     # (Odoo adds its POS managers)
        self.assertIn(manager, mine)
        self.assertEqual(Hr.with_user(self.dev).search([]), mine)
        self.assertEqual(sorted(self.env["hr.employee.public"].with_user(self.dev).search([]).ids), sorted(mine.ids))
        with self.assertRaises(AccessError):
            Hr.with_user(self.dev).browse(qassim.id).read(["name"])
        self.assertIn(qassim, Hr.with_user(self.boss).search([]))
        # the owner's Administrator: Settings access + «Branch device» ticked, «Branch management» not
        grp = [self.env.ref(x).id for x in ("base.group_user", "base.group_system", "point_of_sale.group_pos_manager",
                                            "navitta_ops.group_navitta_branch")]
        for branch in (False, self.qsm.id):
            sysdev = self.env["res.users"].create({"name": "Admin %s" % branch, "login": "sysdev-%s" % branch,
                                                   "navitta_branch_id": branch, "groups_id": [(6, 0, grp)]})
            everything = Hr.sudo().search([])
            self.assertEqual(Hr.with_user(sysdev).search([]), everything)
            self.assertTrue(Hr.with_user(sysdev).browse(akaria.id).read(["name"]))
            self.assertEqual(self.env["hr.employee.public"].with_user(sysdev).search_count([]), len(everything))
            Settings = self.env["res.config.settings"].with_user(sysdev)
            Settings.default_get(list(Settings._fields))
            form = Form(Settings)                       # default_get + onchange of the whole Settings form
            form.pos_config_id = self.cfg_dwd           # the Point of Sale settings of a device with its cashiers
            self.assertEqual(form.pos_basic_employee_ids[:], akaria)
            form.save()
            # and the menus are not reduced to the Navitta app
            menus = self.env["ir.ui.menu"].with_user(sysdev)._visible_menu_ids()
            self.assertIn(self.env.ref("base.menu_administration").id, menus)

    def test_owned_device_cashier_account_counts_for_owner(self):
        """The cashier accounts (POS_AKARIA…) stay as they are; the device is linked to its owner: every order on it,
        with the cashier POS_AKARIA, counts for the owner — and nothing for «POS_AKARIA (cashier not linked)»."""
        akaria = self.env["hr.employee"].create({"name": "POS_AKARIA"})
        cfg, pm = self.own_config("جهاز شيمة", self.shima)
        cfg.basic_employee_ids = akaria
        s = self._open(cfg, akaria)
        self._order(s, 1, pm, akaria)
        self._order(s, 2, pm, akaria)
        self._order(s, -1, pm, akaria)
        boss = self.env["navitta.api"].with_user(self.boss)
        rows = boss.employee_summary(str(self.day), str(self.day), self.dwd.id)["rows"]
        self.assertEqual([(r["name"], r["amount"], r["invoices"], r["exchanged"]) for r in rows], [("شيمة", 460, 2, 1)])
        dev = boss.employee_summary(str(self.day), str(self.day), self.dwd.id, [], cfg.id)["rows"]
        self.assertEqual([(r["name"], r["amount"], r["employee_id"]) for r in dev], [("شيمة", 460, self.shima.id)])
        st = [x for x in self.dwd._system_status(self.day)[self.dwd.id] if x["name"] == "جهاز شيمة"][0]
        self.assertEqual((st["owner"], st["general"]), ("شيمة", False))
        self.assertEqual(cfg.basic_employee_ids, akaria)

    def _at(self, o, day, hour=12.0):
        """Moves an order to a local day / hour (the branches' time zone)."""
        from odoo.addons.navitta_ops.models.common import day_bounds_utc
        o.write({"date_order": day_bounds_utc(self.env, day)[0] + timedelta(hours=hour)})
        return o

    def _sql(self, d0, d1, branch=None, cfg=None, wd=None):
        """The truth: a direct SQL sum over pos_order (counted orders of the linked points of sale, local days)."""
        self.env["pos.order"].flush_model()
        q = """SELECT COALESCE(SUM(o.amount_total), 0), COUNT(*) FILTER (WHERE o.amount_total > 0)
                 FROM pos_order o JOIN pos_config c ON c.id = o.config_id
                WHERE c.navitta_branch_id IS NOT NULL AND o.state IN ('paid', 'done', 'invoiced')
                  AND (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Riyadh')::date BETWEEN %s AND %s
                  AND (%s::int IS NULL OR c.navitta_branch_id = %s) AND (%s::int IS NULL OR c.id = %s)
                  AND (%s::int[] IS NULL OR EXTRACT(dow FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Riyadh'))::int = ANY(%s::int[]))"""
        self.env.cr.execute(q, [d0, d1, branch, branch, cfg, cfg, wd or None, wd or None])
        s, n = self.env.cr.fetchone()
        return round(s, 2), n

    def test_history_by_period_branch_and_device(self):
        """The dashboard, Sales and targets and the employee board show the whole history from the Point of Sale for any
        period (not only the days the 10-minute sync covers), per branch and per device, equal to the orders."""
        Hr = self.env["hr.employee"]
        akaria = Hr.create({"name": "POS_AKARIA"})                          # a cashier account not linked to anyone
        reem_hr, shima_hr = self.link()
        n_hr = Hr.with_context(active_test=False).search_count([])
        cfg_reem, pm_reem = self.own_config("جهاز ريم", self.reem)
        s_dwd, s_reem, s_qsm = self._open(self.cfg_dwd, shima_hr), self._open(cfg_reem, reem_hr), self._open(self.cfg_qsm, akaria)
        day = self.day
        plan = [  # (days ago, local hour, session, qty, payment, cashier)
            (0, 12, s_dwd, 2, self.cash, shima_hr), (0, 13, s_reem, 1, pm_reem, akaria), (0, 9, s_qsm, 1, self.cash_qsm, akaria),
            (1, 18, s_dwd, 1, self.card, akaria), (1, 0.5, s_reem, 3, self.card, None),        # 00:30 local = the day before in UTC
            (3, 23.9, s_qsm, 2, self.card, None), (6, 11, s_dwd, -1, self.cash, shima_hr),     # a refund
            (10, 15, s_reem, 1, pm_reem, reem_hr), (10, 16, s_dwd, 4, self.card, shima_hr), (29, 20, s_qsm, 1, self.cash_qsm, akaria),
            (35, 12, s_dwd, 2, self.cash, None), (45, 21, s_reem, 2, self.card, reem_hr), (80, 10, s_qsm, 3, self.card, akaria),
            (150, 14, s_dwd, 1, self.cash, shima_hr), (150, 15, s_reem, -1, pm_reem, reem_hr), (300, 12, s_qsm, 5, self.cash_qsm, None),
        ]
        orders = [self._at(self._order(s, q, pm, hr), day - timedelta(days=ago), h) for ago, h, s, q, pm, hr in plan]
        boss = self.env["navitta.api"].with_user(self.boss)
        m0 = day.replace(day=1)
        pm1 = m0 - timedelta(days=1)
        presets = {"today": (day, day), "yesterday": (day - timedelta(days=1),) * 2, "last7": (day - timedelta(days=6), day),
                   "month": (m0, day), "prevmonth": (pm1.replace(day=1), pm1), "last30": (day - timedelta(days=29), day),
                   "year": (day.replace(month=1, day=1), day), "custom": (day - timedelta(days=50), day - timedelta(days=5))}
        scopes = [(None, None), (self.dwd.id, None), (self.qsm.id, None), (self.dwd.id, self.cfg_dwd.id),
                  (self.dwd.id, cfg_reem.id), (self.qsm.id, self.cfg_qsm.id)]

        def check(label):
            for name, (d0, d1) in presets.items():
                for wd in ([], sorted({dow(day), dow(day - timedelta(days=3)), dow(day - timedelta(days=10))})):
                    for br, cfg in scopes:
                        want = self._sql(d0, d1, br, cfg, wd)
                        k = boss.admin_dashboard(str(d0), str(d1), wd, br or False, cfg or False)["kpi"]
                        self.assertEqual((round(k["sales"], 2), k["invoices"]), want, (label, name, wd, br, cfg))
                        if not (label == "moved" and br == self.qsm.id):        # its only point of sale moved away
                            self.assertEqual(k["source"], "pos")
                        if wd:
                            continue
                        sal = boss.admin_sales(br or False, str(d0), str(d1), d1.year, str(d0), str(d1), wd, cfg or False)
                        rows = [r for r in sal["summary"] if not br or r["branch_id"] == br]
                        self.assertEqual(round(sum(r["sales"] for r in rows), 2), want[0], ("sales", label, name, br, cfg))
                        self.assertEqual(round(sum(r["sales"] or 0 for r in sal["daily"]), 2), want[0], ("daily", label, name, br, cfg))
                        emp = boss.employee_summary(str(d0), str(d1), br or False, wd, cfg or False)["rows"]
                        self.assertEqual(round(sum(r["amount"] for r in emp), 2), want[0], ("board", label, name, br, cfg))
            cmp = {r["branch_id"]: r for r in boss.admin_compare(str(day.replace(month=1, day=1)), str(day))["rows"]}
            for b in (self.dwd, self.qsm):
                self.assertEqual((round(cmp[b.id]["sales"], 2), cmp[b.id]["invoices"]), self._sql(day.replace(month=1, day=1), day, b.id))

        check("first")
        # the device owner gets every order of her device (whoever was the cashier), the shared device by its cashier
        rows = boss.employee_summary(str(day - timedelta(days=10)), str(day), self.dwd.id, [], cfg_reem.id)["rows"]
        self.assertEqual([r["employee_id"] for r in rows], [self.reem.id])
        rows = boss.employee_summary(str(day - timedelta(days=1)), str(day - timedelta(days=1)), self.dwd.id, [], self.cfg_dwd.id)["rows"]
        self.assertEqual([r["name"] for r in rows], ["POS_AKARIA (cashier not linked to an employee)"])
        # later: an old order edited, a refund of an old day, an order moved to another day, a point of sale moved to another branch
        orders[8].write({"amount_total": orders[8].amount_total + 115})
        self._at(self._order(s_dwd, -2, self.cash, shima_hr), day - timedelta(days=35))
        self._at(self._order(s_qsm, 1, self.card, akaria), day - timedelta(days=80), 9)
        self._at(orders[13], day - timedelta(days=151))                    # its date corrected: the old day loses it
        check("changed")
        self.cfg_qsm.navitta_branch_id = self.dwd
        scopes[:] = [(None, None), (self.dwd.id, None), (self.qsm.id, None), (self.dwd.id, self.cfg_qsm.id)]
        check("moved")
        # the button counts everything again; nothing was created in Odoo's employees, the cashier lists are untouched
        self.env["navitta.pos.day"].search([]).unlink()
        self.dwd.action_pos_sync_all()
        self.assertTrue(self.env["navitta.pos.day"].search_count([("config_id", "=", cfg_reem.id)]))
        self.assertEqual(Hr.with_context(active_test=False).search_count([]), n_hr)
        self.assertFalse(self.cfg_dwd.basic_employee_ids)
        # the device filter
        devs = {d["id"]: d for d in boss.admin_filters()["devices"]}
        self.assertEqual((devs[cfg_reem.id]["owner_id"], devs[cfg_reem.id]["branch_id"]), (self.reem.id, self.dwd.id))
        self.assertEqual(boss.admin_dashboard(str(day), str(day), [], False, cfg_reem.id)["kpi"]["device"]["owner"], "ريم")

    def test_model_search_in_products(self):
        """🔎 Model search (and the shortage list's item chooser): the Odoo products named «21160003- …» with color and
        size variants and a barcode per variant come first, in the screen's structure; the models file stays the fallback."""
        PA = self.env["product.attribute"]
        color = PA.create({"name": "اللون", "value_ids": [(0, 0, {"name": "أسود", "sequence": 1}), (0, 0, {"name": "بيج", "sequence": 2})]})
        size = PA.create({"name": "المقاس", "value_ids": [(0, 0, {"name": s, "sequence": i}) for i, s in enumerate(["36", "37", "38"])]})
        heel = PA.create({"name": "Heel", "value_ids": [(0, 0, {"name": "7 cm"})]})
        tax = self.env["account.tax"].create({"name": "VAT 15 excl", "amount": 15, "type_tax_use": "sale", "company_id": self.env.company.id})
        lines = [(0, 0, {"attribute_id": a.id, "value_ids": [(6, 0, a.value_ids.ids)]}) for a in (color, size, heel)]
        T = self.env["product.template"]
        shoe = T.create({"name": "21160003- حذاء نسائي", "list_price": 200, "taxes_id": [(6, 0, tax.ids)], "available_in_pos": True,
                         "attribute_line_ids": lines})
        self.assertEqual(len(shoe.product_variant_ids), 6)
        for i, v in enumerate(shoe.product_variant_ids):
            names = v.product_template_attribute_value_ids.mapped("name")
            v.barcode = "000123456789" if ("أسود" in names and "37" in names) else "62810000%04d" % i
        T.create({"name": "21169999 - شنطة يد", "list_price": 230, "taxes_id": [(6, 0, self.tax.ids)], "sale_ok": True, "barcode": "5550001"})
        for i in range(1, 8):
            T.create({"name": "2116100%s- صندل %s" % (i, i), "list_price": 100, "sale_ok": True})
        T.create({"name": "21162222- غير معروض", "sale_ok": False, "available_in_pos": False})
        api = self.env["navitta.api"].with_user(self.dev)         # the branch device (no access to products itself)

        r = api.catalog_find("21160003")
        self.assertNotIn("item", r)
        m = r["models"][0]
        self.assertEqual((len(r["models"]), m["item_no"], m["model"], m["price"]), (1, "21160003", "حذاء نسائي", 230.0))
        self.assertEqual(m["colors"], [{"color": "أسود", "sizes": ["36", "37", "38"]}, {"color": "بيج", "sizes": ["36", "37", "38"]}])
        self.assertEqual(len(m["items"]), 6)
        it = [i for i in m["items"] if i["alu"] == "000123456789"][0]
        self.assertEqual((it["item_no"], it["model"], it["color"], it["size"], it["price"]), ("21160003", "حذاء نسائي", "أسود", "37", 230.0))
        for k in ("alu", "item_no", "model", "color", "size", "price", "dcs", "heel", "material"):
            self.assertIn(k, it)
        # a scanned barcode, with the leading zeros stripped or added: that piece (color and size) and its whole model
        for scan in ("000123456789", "123456789", "0000123456789"):
            r = api.catalog_find(scan)
            self.assertEqual((r["item"]["color"], r["item"]["size"], r["item"]["alu"]), ("أسود", "37", "000123456789"), scan)
            self.assertEqual((r["models"][0]["item_no"], len(r["models"][0]["items"])), ("21160003", 6))
        # a product without variants: one item; a prefix of digits: up to 6 models; text: the name
        r = api.catalog_find("21169999")
        self.assertEqual((r["models"][0]["model"], r["models"][0]["colors"], len(r["models"][0]["items"]), r["models"][0]["price"]),
                         ("شنطة يد", [], 1, 230.0))
        self.assertEqual(api.catalog_find("5550001")["item"]["item_no"], "21169999")
        self.assertEqual(len(api.catalog_find("2116")["models"]), 6)
        self.assertEqual([x["item_no"] for x in api.catalog_find("21161")["models"]], ["2116100%s" % i for i in range(1, 7)])
        self.assertEqual([x["item_no"] for x in api.catalog_find("نسائي")["models"]], ["21160003"])
        self.assertFalse(api.catalog_find("21162222")["models"])               # not for sale
        # nothing in the products: the models file as before
        self.env["navitta.catalog.item"].create({"alu": "777", "item_no": "30010001", "model": "SANDY", "color": "BLACK", "size": "38"})
        r = api.catalog_find("30010001")
        self.assertEqual((r["models"][0]["model"], r.get("source")), ("SANDY", None))
        self.assertEqual(api.catalog_find("777")["item"]["model"], "SANDY")
        # management uses the same search
        self.assertEqual(self.env["navitta.api"].with_user(self.boss).catalog_find("21160003")["models"][0]["item_no"], "21160003")

    # ------------------------------------------------------------------ device targets, comparison per device, stock
    def _targets(self, branch, day, amount, days=1):
        self.env["navitta.daily.sales"]._set_target_days(branch, day, days, amount, True)

    def test_device_target_shares(self):
        """Device day target = branch day target × share / 100: equal split when no device has a share, the devices
        without a share split the rest of 100 %, a warning when the shares add up to more than 100 %; the dashboard,
        Sales and targets and the employee board show the device target with the Device filter."""
        cfg_reem, pm_reem = self.own_config("جهاز ريم", self.reem)
        cfg_x, _pm = self.own_config("جهاز ثالث")
        eff = lambda: [round(c.navitta_target_share_eff, 2) for c in (self.cfg_dwd, cfg_reem, cfg_x)]  # noqa: E731
        shares = lambda: {d["id"]: round(d["share"], 2) for d in self.env["navitta.api"]._devices() if d["branch_id"] == self.dwd.id}  # noqa: E731
        self.assertEqual(eff(), [33.33, 33.33, 33.33])                                 # nothing set: equal split
        self.assertEqual(shares()[self.cfg_dwd.id], 33.33)
        self.assertFalse(self.dwd.pos_share_warning)
        self.assertEqual([d["share"] for d in self.env["navitta.api"]._devices() if d["id"] == self.cfg_qsm.id], [100.0])  # alone
        self.cfg_dwd.navitta_target_share, cfg_reem.navitta_target_share, cfg_x.navitta_target_share = 50, 30, 20
        self.assertEqual(eff(), [50, 30, 20])                                          # explicit shares
        cfg_x.navitta_target_share = 0
        cfg_reem.navitta_target_share = 0
        self.assertEqual(eff(), [50, 25, 25])                                          # mixed: the rest split equally
        self.assertEqual(shares(), {self.cfg_dwd.id: 50, cfg_reem.id: 25, cfg_x.id: 25})
        self.cfg_dwd.navitta_target_share, cfg_reem.navitta_target_share = 80, 40
        self.dwd.invalidate_recordset(["pos_share_warning"])
        self.assertEqual(eff(), [80, 40, 0])                                           # never negative
        self.assertIn("120%", self.dwd.pos_share_warning)
        with self.assertRaises(ValidationError):
            cfg_x.navitta_target_share = 120
        # the branch form: the warning follows the shares typed in
        from odoo.tests import Form
        with Form(self.dwd) as f:
            self.assertTrue(f.pos_share_warning)
            for i in range(3):
                with f.pos_config_ids.edit(i) as line:
                    line.navitta_target_share = 10
            self.assertFalse(f.pos_share_warning)
        self.assertEqual(eff(), [10, 10, 10])
        # achievement with the Device filter: 1000 a day for the branch, her device 10 % → 100
        self.cfg_dwd.navitta_target_share = cfg_x.navitta_target_share = 0
        self.assertEqual(eff(), [45, 10, 45])
        day = self.day
        self._targets(self.dwd, day - timedelta(days=1), 1000, 2)
        reem_hr, shima_hr = self.link()
        s_reem = self._open(cfg_reem, reem_hr)
        self._order(s_reem, 1, pm_reem, shima_hr)                                     # 230, counts for her (her device)
        boss = self.env["navitta.api"].with_user(self.boss)
        d0 = day - timedelta(days=1)
        k = boss.admin_dashboard(str(d0), str(day), [], self.dwd.id, cfg_reem.id)["kpi"]
        self.assertEqual((k["target"], k["target_full"], k["sales"], k["device"]["share"]), (200, 200, 230, 10))
        k = boss.admin_dashboard(str(d0), str(day), [], self.dwd.id, False)["kpi"]
        self.assertEqual((k["target"], k["sales"]), (2000, 230))
        sal = boss.admin_sales(self.dwd.id, str(d0), str(day), day.year, str(d0), str(day), [], cfg_reem.id)
        self.assertEqual((sal["summary"][0]["target_to_date"], sal["summary"][0]["sales"]), (200, 230))
        self.assertEqual({r["day"]: (r["target"], r["sales"]) for r in sal["daily"]},
                         {str(d0): (100, None), str(day): (100, 230)})
        month = [m for m in sal["monthly"] if m["month"] == day.month][0]
        self.assertEqual((month["sales"], month["target"] >= 100), (230, True))
        emp = boss.employee_summary(str(d0), str(day), self.dwd.id, [], cfg_reem.id)
        self.assertEqual((emp["targets"][self.dwd.id], emp["own_targets"]), (2000, {self.reem.id: 200}))
        emp = boss.employee_summary(str(d0), str(day), self.dwd.id, [])
        self.assertEqual(emp["own_targets"], {self.reem.id: 200})

    def test_compare_per_device(self):
        """Branch comparison in «Devices» mode: one row per point of sale equal to the SQL sums of its orders for the
        period, its target (share of its branch's), its owner's counter visits, the ranks and the totals."""
        Hr = self.env["hr.employee"]
        akaria = Hr.create({"name": "POS_AKARIA"})
        reem_hr, shima_hr = self.link()
        cfg_reem, pm_reem = self.own_config("جهاز ريم", self.reem)
        s_dwd, s_reem, s_qsm = self._open(self.cfg_dwd, shima_hr), self._open(cfg_reem, reem_hr), self._open(self.cfg_qsm, akaria)
        day = self.day
        plan = [(0, s_dwd, 2, self.cash), (0, s_reem, 1, pm_reem), (1, s_reem, 3, self.card), (2, s_qsm, 1, self.cash_qsm),
                (3, s_dwd, -1, self.cash), (5, s_qsm, 3, self.card), (9, s_reem, 1, pm_reem), (40, s_dwd, 5, self.card)]
        for ago, s, q, pm in plan:
            self._at(self._order(s, q, pm), day - timedelta(days=ago))
        cfg_reem.navitta_target_share = 40
        self._targets(self.dwd, day - timedelta(days=9), 1000, 10)
        self._targets(self.qsm, day - timedelta(days=9), 500, 10)
        Visit = self.env["navitta.visit"]
        for i, bought in enumerate((True, False, True, False)):
            Visit.create({"branch_id": self.dwd.id, "employee_id": self.reem.id, "day": day - timedelta(days=i), "saved": True,
                          "started_at": fields.Datetime.now() - timedelta(days=i), "bought": bought})
        boss = self.env["navitta.api"].with_user(self.boss)
        d0, d1 = day - timedelta(days=9), day
        cmp = boss.admin_compare(str(d0), str(d1), [], "device")
        self.assertEqual(cmp["level"], "device")
        rows = {r["device_id"]: r for r in cmp["rows"]}
        self.assertEqual(set(rows), {self.cfg_dwd.id, cfg_reem.id, self.cfg_qsm.id})
        self.env["pos.order.line"].flush_model()
        for cfg in (self.cfg_dwd, cfg_reem, self.cfg_qsm):
            r = rows[cfg.id]
            self.assertEqual((round(r["sales"], 2), r["invoices"]), self._sql(d0, d1, None, cfg.id), cfg.name)
            self.env.cr.execute("""
                SELECT COALESCE(SUM(l.qty) FILTER (WHERE l.qty > 0), 0), COALESCE(SUM(-l.qty) FILTER (WHERE l.qty < 0), 0),
                       COALESCE(SUM(o.amount_tax), 0)
                  FROM pos_order_line l JOIN pos_order o ON o.id = l.order_id
                 WHERE o.config_id = %s AND (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Riyadh')::date BETWEEN %s AND %s""",
                                [cfg.id, d0, d1])
            items, ret, tax = self.env.cr.fetchone()
            self.assertEqual((r["items"], r["returns"], round(r["tax"], 2)), (items, ret, round(tax, 2)), cfg.name)
            self.assertEqual(r["avg_invoice"], round(r["sales"] / r["invoices"], 2) if r["invoices"] else 0.0)
        # targets: her device 40 %, the shared device of dwd the remaining 60 %, qsm's only device 100 % (days up to today)
        self.assertEqual((rows[cfg_reem.id]["target"], rows[self.cfg_dwd.id]["target"], rows[self.cfg_qsm.id]["target"]), (4000, 6000, 5000))
        self.assertEqual(rows[cfg_reem.id]["achieved"], round(rows[cfg_reem.id]["sales"] * 100 / 4000, 1))
        # her counter visits and purchase rate; the shared device has none
        r = rows[cfg_reem.id]
        vdom = [("employee_id", "=", self.reem.id), ("branch_id", "=", self.dwd.id), ("saved", "=", True), ("day", ">=", d0), ("day", "<=", d1)]
        n, b = Visit.search_count(vdom), Visit.search_count(vdom + [("bought", "=", True)])      # with the automatic visits
        self.assertGreaterEqual(n, 4)
        self.assertEqual((r["visits"], r["bought"], r["buy_pct"], r["owner"]), (n, b, round(b * 100.0 / n, 1), "ريم"))
        self.assertEqual(r["label"], "جهاز ريم · %s · ريم" % self.dwd.name)
        self.assertEqual((rows[self.cfg_dwd.id]["visits"], rows[self.cfg_dwd.id]["buy_pct"]), (None, None))
        # ranks by sales, and the devices add up to the branches
        by_sales = sorted(cmp["rows"], key=lambda x: -x["sales"])
        self.assertEqual([x["rank"] for x in cmp["rows"]], [1, 2, 3])
        self.assertEqual([x["device_id"] for x in cmp["rows"]], [x["device_id"] for x in by_sales])
        br = {r["branch_id"]: r for r in boss.admin_compare(str(d0), str(d1))["rows"]}
        for b in (self.dwd, self.qsm):
            mine = [x for x in cmp["rows"] if x["branch_id"] == b.id]
            self.assertEqual(round(sum(x["sales"] for x in mine), 2), round(br[b.id]["sales"], 2))
            self.assertEqual(sum(x["invoices"] for x in mine), br[b.id]["invoices"])
            self.assertAlmostEqual(sum(x["target"] for x in mine), br[b.id]["target"], 2)
        self.assertEqual(br[self.dwd.id]["rank"] in (1, 2), True)
        # a weekday filter applies to the devices too
        wd = [dow(day)]
        rows = {r["device_id"]: r for r in boss.admin_compare(str(d0), str(d1), wd, "device")["rows"]}
        self.assertEqual((round(rows[cfg_reem.id]["sales"], 2), rows[cfg_reem.id]["invoices"]), self._sql(d0, d1, None, cfg_reem.id, wd))

    def _shoe(self):
        PA = self.env["product.attribute"]
        color = PA.create({"name": "اللون", "value_ids": [(0, 0, {"name": "أسود", "sequence": 1}), (0, 0, {"name": "بيج", "sequence": 2})]})
        size = PA.create({"name": "المقاس", "value_ids": [(0, 0, {"name": s, "sequence": i}) for i, s in enumerate(["36", "37"])]})
        lines = [(0, 0, {"attribute_id": a.id, "value_ids": [(6, 0, a.value_ids.ids)]}) for a in (color, size)]
        shoe = self.env["product.template"].create({"name": "21170001- حذاء", "list_price": 200, "available_in_pos": True, "is_storable": True,
                                                     "attribute_line_ids": lines})
        return {tuple(v.product_template_attribute_value_ids.mapped("name")): v for v in shoe.product_variant_ids}

    def test_model_search_stock(self):
        """🔎 Model search in the products shows the quantity on hand per variant at the branch's own warehouse (of its
        points of sale, with the sub-locations): a branch device its own branch and the other branches' total,
        management one row per branch. One grouped read; the models file has no stock."""
        Q = self.env["stock.quant"]
        v = self._shoe()
        wh1 = self.cfg_dwd.picking_type_id.warehouse_id
        wh2 = self.env["stock.warehouse"].create({"name": "مستودع القصيم", "code": "QSM"})
        self.cfg_qsm.picking_type_id = wh2.pos_type_id
        shelf = self.env["stock.location"].create({"name": "رف", "location_id": wh1.lot_stock_id.id, "usage": "internal"})
        black36, black37, beige36, beige37 = v[("أسود", "36")], v[("أسود", "37")], v[("بيج", "36")], v[("بيج", "37")]
        black37.barcode = "6281999000377"
        Q._update_available_quantity(black37, wh1.lot_stock_id, 3)
        Q._update_available_quantity(black37, shelf, 2)            # a sub-location of the branch's stock
        Q._update_available_quantity(beige36, wh1.lot_stock_id, -1)   # negative: shown as it is, out of stock
        Q._update_available_quantity(black37, wh2.lot_stock_id, 4)
        Q._update_available_quantity(black36, wh2.lot_stock_id, 6)
        other = self.env["stock.warehouse"].create({"name": "مستودع آخر", "code": "OTH"})   # no branch: not counted
        Q._update_available_quantity(black36, other.lot_stock_id, 50)
        api = self.env["navitta.api"].with_user(self.dev)
        m = api.catalog_find("21170001")["models"][0]
        qty = {(i["color"], i["size"]): (i["qty"], i["qty_other"]) for i in m["items"]}
        self.assertEqual(qty, {("أسود", "36"): (0, 6), ("أسود", "37"): (5, 4), ("بيج", "36"): (-1, 0), ("بيج", "37"): (0, 0)})
        st = m["stock"]
        self.assertEqual((st["branch_id"], st["here"], st["other"], st["branches"]), (self.dwd.id, 4, 10, []))
        self.assertEqual(st["by"], {"أسود": {"36": 0, "37": 5}, "بيج": {"36": -1, "37": 0}})
        self.assertEqual(st["colors"], {"أسود": 5, "بيج": -1})
        # a scanned piece: its own quantities too
        r = api.catalog_find("6281999000377")
        self.assertEqual((r["item"]["qty"], r["item"]["qty_other"]), (5, 4))
        # the other branch's device: the other way round
        dev_qsm = self.env["res.users"].create({"name": "جهاز القصيم", "login": "qsm-pos-device", "navitta_branch_id": self.qsm.id,
                                                "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("navitta_ops.group_navitta_branch").id])]})
        st = self.env["navitta.api"].with_user(dev_qsm).catalog_find("21170001")["models"][0]["stock"]
        self.assertEqual((st["here"], st["other"], st["branches"]), (10, 4, []))
        # management on the branch screen of dwd: one row per branch (its own first), and the same «here»
        boss = self.env["navitta.api"].with_user(self.boss)
        st = boss.with_context(navitta_branch=self.dwd.id).catalog_find("21170001")["models"][0]["stock"]
        self.assertEqual((st["here"], [(b["id"], b["qty"]) for b in st["branches"]]), (4, [(self.dwd.id, 4), (self.qsm.id, 10)]))
        st = boss.catalog_find("21170001")["models"][0]["stock"]          # no branch picked: per branch only
        self.assertEqual((st["here"], {b["id"]: b["qty"] for b in st["branches"]}), (None, {self.dwd.id: 4, self.qsm.id: 10}))
        # the models file: no stock
        self.env["navitta.catalog.item"].create({"alu": "778", "item_no": "30010002", "model": "SANDY", "color": "BLACK", "size": "38"})
        self.assertNotIn("stock", api.catalog_find("30010002")["models"][0])
