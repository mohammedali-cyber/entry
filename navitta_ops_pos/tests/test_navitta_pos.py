from odoo.addons.navitta_ops.models.common import app_today, dow
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
        self.assertTrue(api.report_save({"employee_name": "ريم", "sales": 99999, "cash": 1, "deposit": 300})["ok"])
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
                         (True, 920, 120, 4, 230, 5, 1, 1, 1, 3, 0))
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
