import base64
import json
from datetime import date, datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase, freeze_time

# 2026-10-13 is a Tuesday; Riyadh is UTC+3. The shift starts 15:30 (12:30 UTC), grace 5 min.
DAY = date(2026, 10, 13)


def utc(h, m=0, d=DAY):
    """Riyadh local time -> naive UTC string for freeze_time."""
    return datetime(d.year, d.month, d.day, h - 3, m).strftime("%Y-%m-%d %H:%M:%S")


@tagged("post_install", "-at_install", "navitta")
class NavittaCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"navitta_tz": "Asia/Riyadh", "navitta_shift_start": 15.5, "navitta_shift_end": 23.5,
                           "navitta_grace": 5, "navitta_break_max": 30, "navitta_chances": 3,
                           "navitta_report_email": "boss@example.com", "navitta_cash_tasks": False})
        cls.dwd = cls.env.ref("navitta_ops.branch_dwd")
        cls.qsm = cls.env.ref("navitta_ops.branch_qsm")
        cls.reem = cls.env.ref("navitta_ops.emp_dwd_1")       # weekly off: Monday
        cls.shima = cls.env.ref("navitta_ops.emp_dwd_2")      # weekly off: Sunday
        cls.hubour = cls.env.ref("navitta_ops.emp_qsm_1")
        for e in (cls.reem, cls.shima, cls.hubour):
            e._set_pin("2468")
        cls.reem.email = "reem@example.com"
        grp_branch = cls.env.ref("navitta_ops.group_navitta_branch")
        grp_admin = cls.env.ref("navitta_ops.group_navitta_admin")
        user = cls.env.ref("base.group_user")
        cls.dev = cls.env["res.users"].create({
            "name": "جهاز الدوادمي", "login": "dwd-device", "navitta_branch_id": cls.dwd.id,
            "groups_id": [(6, 0, [user.id, grp_branch.id])]})
        cls.dev_qsm = cls.env["res.users"].create({
            "name": "جهاز القصيم", "login": "qsm-device", "navitta_branch_id": cls.qsm.id,
            "groups_id": [(6, 0, [user.id, grp_branch.id])]})
        cls.boss = cls.env["res.users"].create({
            "name": "الإدارة", "login": "boss", "groups_id": [(6, 0, [user.id, grp_admin.id])]})
        cls.plain = cls.env["res.users"].create({"name": "موظف عادي", "login": "plain", "groups_id": [(6, 0, [user.id])]})
        cls.company._navitta_set_admin_password("9999")

    def api(self, user=None):
        return self.env["navitta.api"].with_user(user or self.dev)

    def admin(self):
        return self.api(self.boss)

    # ------------------------------------------------------------------ security
    def test_security_branch_isolation(self):
        """A branch device can't act on another branch's employee and can't change records directly."""
        with freeze_time(utc(15, 20)):
            with self.assertRaises(AccessError):
                self.api().check_in(self.hubour.id, "2468")
            with self.assertRaises(AccessError):
                self.api(self.plain).branch_state()
            with self.assertRaises(AccessError):
                self.api().admin_dashboard()
            self.assertEqual(self.api().check_in(self.reem.id, "2468")["ok"], True)
            v = self.api().visit_start(self.reem.id)["id"]
            visit = self.env["navitta.visit"].with_user(self.dev).browse(v)
            with self.assertRaises(AccessError):
                visit.write({"duration_sec": 1})
            with self.assertRaises(AccessError):
                visit.unlink()
            with self.assertRaises(AccessError):
                self.env["navitta.visit"].with_user(self.dev_qsm).browse(v).read(["duration_sec"])
            with self.assertRaises(AccessError):
                self.api(self.dev_qsm).visit_end(v)
            # own branch reads work, other branches are filtered out
            self.assertEqual(self.env["navitta.visit"].with_user(self.dev_qsm).search_count([]), 0)
            self.assertEqual(self.env["navitta.visit"].with_user(self.dev).search_count([]), 1)
            # private data is not readable from the device
            with self.assertRaises(AccessError):
                self.env["navitta.thread"].with_user(self.dev).search([])
            with self.assertRaises(AccessError):
                self.reem.with_user(self.dev).read(["email"])

    def test_security_matrix(self):
        """Same split as the old app's database policies: a branch device reads its own branch only and writes
        nothing directly (everything goes through the screen API); management reads everything; the private
        models (requests, threads, messages, notifications, devices, upload tokens) are management-only."""
        with freeze_time(utc(15, 20)):
            for dev, emp in ((self.dev, self.reem), (self.dev_qsm, self.hubour)):
                self.assertTrue(self.api(dev).check_in(emp.id, "2468")["ok"])
                self.api(dev).visit_start(emp.id)
        Sales = self.env["navitta.daily.sales"]
        for b in (self.dwd, self.qsm):
            Sales._row(b, DAY).write({"sales": 100})
            self.env["navitta.employee.sales"].create({"branch_id": b.id, "day": DAY, "amount": 100})
        admin_only = {"navitta.leave.request", "navitta.thread", "navitta.message", "navitta.notification",
                      "navitta.device", "navitta.device.punch", "navitta.upload.token"}
        models = [m for m in self.env if m.startswith("navitta.") and not self.env[m]._abstract and not self.env[m]._transient]
        for m in models:
            with self.subTest(model=m):
                everything = self.env[m].sudo().with_context(active_test=False).search([])
                self.assertEqual(self.env[m].with_user(self.boss).with_context(active_test=False).search([]), everything)
                Dev = self.env[m].with_user(self.dev).with_context(active_test=False)
                for op in ("create", "write", "unlink"):
                    self.assertFalse(Dev.has_access(op), "%s: a device may not %s directly" % (m, op))
                if m in admin_only:
                    self.assertFalse(Dev.has_access("read"))
                    continue
                seen = Dev.search([]).sudo()
                if m == "navitta.branch":
                    self.assertEqual(seen, self.dwd)
                elif "branch_id" in seen._fields:
                    self.assertFalse(seen.filtered(lambda r: r.branch_id != self.dwd), m)
                    self.assertEqual(seen, everything.filtered(lambda r: r.branch_id == self.dwd), m)
                elif "report_id" in seen._fields:
                    self.assertFalse(seen.filtered(lambda r: r.report_id.branch_id != self.dwd), m)
        # each branch saw its own rows of the data created above
        for m in ("navitta.attendance", "navitta.visit", "navitta.daily.sales", "navitta.employee.sales"):
            self.assertEqual(self.env[m].with_user(self.dev).search_count([]), 1, m)
            self.assertEqual(self.env[m].with_user(self.dev_qsm).search_count([]), 1, m)
            self.assertEqual(self.env[m].with_user(self.boss).search_count([]), 2, m)
        # a device's screen always works on its own branch, whatever branch the request asks for
        self.assertEqual(self.api(self.dev).with_context(navitta_branch=self.qsm.id)._branch(), self.dwd)
        for f in ("pin_hash", "email", "photo"):
            with self.assertRaises(AccessError):
                self.reem.with_user(self.dev).read([f])

    # ------------------------------------------------------------------ cash custody and closing tasks
    def test_cash_custody_and_closing_tasks(self):
        """The first employee counts the custody before the counter opens; nobody checks out before her open visits
        are closed, the daily report is sent and she counted the closing cash (screen and fingerprint device)."""
        self.company.navitta_cash_tasks = True
        Count = self.env["navitta.cash.count"]
        # yesterday's closing count: today's custody is compared with it
        Count.create({"branch_id": self.dwd.id, "day": DAY - timedelta(days=1), "kind": "close", "employee_id": self.shima.id,
                      "n500": 2, "n100": 3, "coins": 2.5})
        with freeze_time(utc(15, 20)):
            self.assertTrue(self.api().check_in(self.reem.id, "2468")["ok"])
        with freeze_time(utc(15, 25)):
            self.assertTrue(self.api().check_in(self.shima.id, "2468")["ok"])
            st = self.api().branch_state()["cash"]
            self.assertEqual((st["required"], st["open"], st["opener_id"], st["open_expected"]), (True, False, self.reem.id, 1302.5))
            self.assertEqual(st["tasks"][self.reem.id], ["report", "cash_close"])
            self.assertEqual(self.api().visit_start(self.shima.id)["error"], "cash_open_required")   # counter locked for all
            self.assertEqual(self.api().cash_count(self.reem.id, "0000", "open", {"500": 2})["error"], "wrong_pin")
            self.assertEqual(self.api().cash_count(self.reem.id, "2468", "open", {"500": -1})["error"], "bad_count")
            with self.assertRaises(AccessError):                                   # another branch's employee
                self.api().cash_count(self.hubour.id, "2468", "open", {"500": 1})
            r = self.api().cash_count(self.reem.id, "2468", "open", {"500": 2, "100": 3})
            self.assertEqual((r["total"], r["expected"], r["difference"]), (1300, 1302.5, -2.5))      # 2.50 short
            self.assertTrue(self.env["navitta.notification"].search([("subject", "like", "Difference in")]))
            self.assertEqual(self.api().cash_count(self.shima.id, "2468", "open", {"500": 3})["error"], "already_counted")
            vid = self.api().visit_start(self.shima.id)["id"]
        with freeze_time(utc(23, 30)):
            self.api().visit_end(vid)
            # the check-out is refused while tasks are left, and the screen lists them
            r = self.api().check_out(self.shima.id, "2468")
            self.assertEqual((r["error"], r["tasks"]), ("tasks_pending", ["visits", "report", "cash_close"]))
            self.assertTrue(self.api().visit_save(vid, {"buy": True})["ok"])
            self.assertTrue(self.api().report_save({"employee_name": "شيمة", "sales": 900, "cash": 500, "card": 400, "deposit": 300})["ok"])
            self.assertEqual(self.api().check_out(self.shima.id, "2468")["tasks"], ["cash_close"])
            r = self.api().cash_count(self.shima.id, "2468", "close", {"500": 3, "1": 2}, "ok")
            self.assertEqual((r["total"], r["expected"], r["difference"]), (1502, 1500, 2))           # 1300 + 500 cash - 300 deposit
            r = self.api().cash_count(self.shima.id, "2468", "close", {"500": 3})                      # she may count again
            self.assertEqual(r["difference"], 0)
            self.assertEqual(Count.search_count([("employee_id", "=", self.shima.id), ("kind", "=", "close"), ("day", "=", DAY)]), 1)
            self.assertTrue(self.api().check_out(self.shima.id, "2468")["ok"])
            # the fingerprint device can't check reem out either until her closing count
            dev = self.env["navitta.device"].create({"serial_number": "NVCASH", "branch_id": self.dwd.id, "state": "active"})
            self.reem.device_user_id = "8"
            punch = self.env["navitta.device.punch"].create({"device_id": dev.id, "device_user_id": "8", "punched_at": utc(23, 31)})
            punch._process()
            att = self.env["navitta.attendance"].search([("employee_id", "=", self.reem.id), ("day", "=", DAY)])
            self.assertEqual((punch.state, bool(att.check_out)), ("tasks", False))
            self.assertIn("Closing cash not counted", punch.message)
            self.api().cash_count(self.reem.id, "2468", "close", {"500": 3})
            punch.action_reprocess()
            self.assertEqual((punch.state, bool(att.check_out)), ("out", True))
        # management: every count, and the employee's own buttons
        self.assertEqual(self.env["navitta.cash.count"].with_user(self.boss).search_count([("day", "=", DAY)]), 3)
        self.assertEqual(self.env["navitta.cash.count"].with_user(self.dev_qsm).search_count([]), 0)
        self.assertEqual(self.shima.action_sales()["domain"], [("employee_id", "=", self.shima.id)])
        # switched off: the counter opens right away and only open visits block the check-out
        self.company.navitta_cash_tasks = False
        with freeze_time(utc(15, 20, d=DAY + timedelta(days=1))):
            self.assertTrue(self.api().check_in(self.reem.id, "2468")["ok"])
            self.assertTrue(self.api().visit_start(self.reem.id)["ok"])

    def test_arabic_translation(self):
        """English is the source; i18n/ar.po gives Arabic users the module in Arabic (messages, labels, menus)."""
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"]._load_module_terms(["navitta_ops"], ["ar_001"], overwrite=True)
        ar = self.env["navitta.api"].with_user(self.dev).with_context(lang="ar_001")
        self.assertEqual(ar.check_in(self.reem.id, "0000")["message"], "الرمز غير صحيح")
        self.assertEqual(self.env["navitta.api"].with_user(self.dev).check_in(self.reem.id, "0000")["message"], "Wrong PIN")
        self.assertEqual(self.env.ref("navitta_ops.menu_root").with_context(lang="ar_001").name, "فروع نافيتا")
        self.assertEqual(self.env["navitta.cash.count"].with_context(lang="ar_001")._fields["kind"]._description_string(
            self.env(context={"lang": "ar_001"})), "النوع")

    def test_device_sees_branch_screen_only(self):
        """Like the old app, a branch device has the branch screen only (no Discuss, Employees, Dashboards…)."""
        Menu = self.env["ir.ui.menu"]
        root = self.env.ref("navitta_ops.menu_root")
        dev_menus = Menu.browse(Menu.with_user(self.dev)._visible_menu_ids())
        self.assertTrue(dev_menus)
        self.assertTrue(all(m.parent_path.startswith("%d/" % root.id) for m in dev_menus))
        self.assertIn(self.env.ref("navitta_ops.menu_branch_app"), dev_menus)
        boss_menus = Menu.browse(Menu.with_user(self.boss)._visible_menu_ids())
        self.assertIn(self.env.ref("navitta_ops.menu_admin_dashboard"), boss_menus)

    def test_pin_lockout(self):
        with freeze_time(utc(15, 20)):
            for _i in range(4):
                self.assertEqual(self.api().check_in(self.reem.id, "0000")["error"], "wrong_pin")
            self.assertEqual(self.api().check_in(self.reem.id, "0000")["error"], "wrong_pin")
            self.assertEqual(self.api().check_in(self.reem.id, "2468")["error"], "locked")
        with freeze_time(utc(15, 22)):
            self.assertTrue(self.api().check_in(self.reem.id, "2468")["ok"])
            self.assertEqual(self.reem.sudo().pin_fail_count, 0)

    # ------------------------------------------------------------------ attendance and lateness
    def test_late_check_in(self):
        with freeze_time(utc(16, 0)):
            r = self.api().check_in(self.reem.id, "2468")
        self.assertEqual(r["late"], 30)
        case = self.env["navitta.late.case"].search([("employee_id", "=", self.reem.id)])
        self.assertEqual((case.kind, case.minutes, case.state), ("late", 30, "reply"))
        self.assertTrue(self.env["navitta.notification"].search([("employee_id", "=", self.reem.id)]).mail_id)

    def test_on_time_and_day_off(self):
        with freeze_time(utc(15, 34)):
            self.assertEqual(self.api().check_in(self.reem.id, "2468")["late"], 0)
        monday = date(2026, 10, 12)
        self.assertTrue(self.reem._is_off(monday))
        self.assertFalse(self.reem._is_off(DAY))

    def test_cron_late_check(self):
        with freeze_time(utc(15, 30)):
            self.assertEqual(self.env["navitta.late.case"]._cron_late_check(), 0)   # inside the grace
        with freeze_time(utc(15, 40)):
            self.api().check_in(self.shima.id, "2468")
            n = self.env["navitta.late.case"]._cron_late_check()
        # 10 employees, none off on Tuesday, Shima checked in (her own late case comes from the check-in)
        self.assertEqual(n, 9)
        Case = self.env["navitta.late.case"]
        self.assertFalse(Case.search([("employee_id", "=", self.reem.id)]).check_in)
        self.assertTrue(Case.search([("employee_id", "=", self.shima.id)]).check_in)
        with freeze_time(utc(15, 45)):
            self.assertEqual(Case._cron_late_check(), 0)    # once per day
        with freeze_time(utc(16, 0)):
            self.assertEqual(self.api().check_in(self.reem.id, "2468")["late"], 30)
        self.assertEqual(Case.search([("employee_id", "=", self.reem.id)]).minutes, 30)

    def test_break_over_and_edit(self):
        with freeze_time(utc(15, 20)):
            self.api().check_in(self.reem.id, "2468")
        with freeze_time(utc(17, 0)):
            self.assertTrue(self.api().break_start(self.reem.id)["ok"])
        brk = self.env["navitta.break"].search([("employee_id", "=", self.reem.id)])
        with freeze_time(utc(17, 40)):
            self.assertEqual(self.api().break_end(brk.id)["over"], 10)
            self.assertEqual(self.api().break_start(self.reem.id)["error"], "break_used")
        case = self.env["navitta.late.case"].search([("employee_id", "=", self.reem.id), ("kind", "=", "break")])
        self.assertEqual((case.minutes, case.break_used), (10, 40))
        with freeze_time(utc(17, 45)):
            self.assertEqual(self.api().check_out(self.reem.id, "2468")["error"], "reply_required")
            self.assertEqual(self.api().reply_case(self.reem.id, "2468", case.id, "صلاة", "edit", 25)["ok"], True)
            self.assertEqual(self.admin().admin_decide_case(case.id, "edit")["ok"], True)
        self.assertEqual(case.state, "edited")
        self.assertEqual(round(brk.duration_min), 25)
        with freeze_time(utc(23, 0)):
            self.assertTrue(self.api().check_out(self.reem.id, "2468")["ok"])

    def test_forgive_chances_and_penalties(self):
        Case = self.env["navitta.late.case"]
        cases = Case
        for i in range(5):
            cases |= Case.create({"employee_id": self.reem.id, "day": date(2026, 10, 1 + i), "kind": "late", "minutes": 10,
                                  "state": "review", "reason": "x"})
        for c in cases[:3]:
            self.assertTrue(self.admin().admin_decide_case(c.id, "forgive")["ok"])
        self.assertEqual(self.admin().admin_decide_case(cases[3].id, "forgive")["error"], "no_chances")
        self.admin().admin_decide_case(cases[3].id, "reject")
        self.admin().admin_decide_case(cases[4].id, "reject")
        self.assertEqual((cases[3].penalty, cases[3].penalty_occ), ("إنذار كتابي", 1))
        self.assertEqual((cases[4].penalty, cases[4].penalty_occ), ("حسم 5% من أجر اليوم", 2))

    # ------------------------------------------------------------------ visits
    def test_visit_flow_and_lost_sale(self):
        with freeze_time(utc(15, 20)):
            self.api().check_in(self.reem.id, "2468")
            vid = self.api().visit_start(self.reem.id)["id"]
            self.assertEqual(self.api().visit_save(vid, {"buy": True})["error"], "visit_running")
            self.assertEqual(self.api().check_out(self.reem.id, "2468")["error"], "open_visits")
        with freeze_time(utc(15, 27)):
            self.api().visit_end(vid)
            bad = self.api().visit_save(vid, {"no": True, "reason": "size_na", "lost": {"model": "AB12"}})
            self.assertEqual(bad["error"], "size_required")
            bad = self.api().visit_save(vid, {"no": True, "reason": "size_na", "lost": {"model": "AB12", "size": "38", "contact_phone": "123"}})
            self.assertEqual(bad["error"], "bad_phone")
            ok = self.api().visit_save(vid, {"no": True, "reason": "size_na", "request": True,
                                             "lost": {"model": "AB12", "item_no": "12345678", "size": "38", "price": 299,
                                                      "contact_phone": "0501234567", "contact_name": "سارة"}})
            self.assertTrue(ok["ok"])
        v = self.env["navitta.visit"].browse(vid)
        self.assertEqual((v.duration_sec, v.saved, v.lost_reason), (420, True, "size_na"))
        lost = self.env["navitta.lost.sale"].search([("visit_id", "=", vid)])
        self.assertTrue(lost.waiting)
        self.assertEqual(lost.request_id.kind, "goods")
        self.assertEqual(self.api().visit_save(vid, {"buy": True})["error"], "not_found")   # saved visits are final
        self.assertTrue(self.api().visit_edit_request(vid, 300, "نسيت أوقف العداد")["ok"])
        req = self.env["navitta.visit.edit.request"].search([("visit_id", "=", vid)])
        self.admin().admin_decide_visit_edit(req.id, True)
        self.assertEqual(v.duration_sec, 300)
        self.assertTrue(self.api().lost_contacted(lost.id, "ريم")["ok"])
        self.assertFalse(lost.waiting)

    # ------------------------------------------------------------------ leaves
    def test_leave_with_cover(self):
        r = self.api().leave_request(self.reem.id, "2468", "annual", "2026-10-17", "2026-10-20", "سفر", True)
        req = self.env["navitta.leave.request"].browse(r["id"])
        self.assertEqual((req.state, req.cover_employee_id), ("cover", self.shima))
        self.assertEqual(self.api().leave_request(self.reem.id, "2468", "annual", "2026-10-19", "2026-10-21", "x", False)["error"], "overlap")
        self.assertEqual(self.admin().admin_decide_leave(req.id, True)["error"], "not_found")   # waiting for the colleague
        self.assertTrue(self.api().cover_respond(self.shima.id, "2468", req.id, True)["ok"])
        self.assertTrue(self.admin().admin_decide_leave(req.id, True)["ok"])
        self.assertTrue(self.reem._is_off(date(2026, 10, 18)))
        sunday = date(2026, 10, 18)
        self.assertFalse(self.shima._is_off(sunday))               # she covers on her weekly day off
        dfo = self.env["navitta.deferred.off"].search([("employee_id", "=", self.shima.id)])
        self.assertEqual(dfo.original_day, sunday)
        dfo.write({"new_day": date(2026, 10, 22)})
        self.assertTrue(self.shima._is_off(date(2026, 10, 22)))

    # ------------------------------------------------------------------ inbox and messages
    def test_inbox_and_messages(self):
        self.assertEqual(self.api().inbox(self.reem.id, "1111")["error"], "wrong_pin")
        self.assertTrue(self.api().send_message(self.reem.id, "2468", "salary", "سلفة لو سمحتوا")["ok"])
        t = self.env["navitta.thread"].search([("employee_id", "=", self.reem.id)])
        self.assertTrue(t.unread)
        self.assertTrue(self.admin().admin_reply_thread(t.id, "تمام")["ok"])
        box = self.api().inbox(self.reem.id, "2468")
        self.assertEqual([m["sender"] for m in box["threads"][0]["messages"]], ["emp", "admin"])

    # ------------------------------------------------------------------ targets, report, cash
    def test_targets_and_password(self):
        with freeze_time(utc(12, 0, date(2026, 10, 21))):
            self.assertEqual(self.api().target_set_days("1234", "2026-10-21", 1, 5000)["error"], "wrong_pw")
            r = self.api().target_set_month("9999", "2026-10-01", 33000)
            self.assertEqual((r["days"], r["per_day"]), (11, 3000))
            rows = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id)])
            self.assertEqual(sum(rows.mapped("target")), 33000)
            self.assertTrue(self.admin().admin_target_set_days(self.dwd.id, "2026-10-21", 7, 4000)["ok"])
            for _i in range(4):            # the right password above reset the counter
                self.api().target_check("bad")
            self.assertEqual(self.api().target_check("bad")["error"], "wrong_pw")
            self.assertEqual(self.api().target_check("9999")["error"], "locked")

    def test_undo_checkout(self):
        with freeze_time(utc(15, 20)):
            self.api().check_in(self.reem.id, "2468")
            self.api().check_out(self.reem.id, "2468")
            self.assertEqual(self.api().undo_checkout(self.reem.id, "nope")["error"], "wrong_pw")
            self.assertTrue(self.api().undo_checkout(self.reem.id, "9999")["ok"])
        att = self.env["navitta.attendance"].search([("employee_id", "=", self.reem.id)])
        self.assertFalse(att.check_out)

    def test_daily_report_sales_cash_and_supplies(self):
        with freeze_time(utc(23, 30)):
            self.dwd.write({"cash_opening_balance": 1000, "cash_opening_date": date(2026, 10, 1)})
            self.dwd._supply_adjust("bags", 20)
            bad = self.api().report_save({"employee_name": "ريم", "exch": [{"detail": "", "soldBy": "ريم", "by": "شيمة"}]})
            self.assertEqual(bad["error"], "bad_exchange")
            ok = self.api().report_save({
                "employee_name": "ريم", "sales": 5000, "invoices": 10, "items": 14, "cash": 1200, "card": 3800, "deposit": 500,
                "exch": [{"reason": "size", "detail": "مقاس أكبر", "soldBy": "ريم", "by": "شيمة"}],
                "emp": [{"employee_id": self.reem.id, "amount": 3000, "invoices": 6}, {"employee_id": self.shima.id, "amount": 2000, "invoices": 4}],
                "used": {"bags": 12}, "sat": 5, "complaint": True, "complaint_text": "تأخير"})
            self.assertTrue(ok["ok"])
            self.assertTrue(self.api().create_expense(self.reem.id, "2468", 200, "cleaning", "منظفات")["ok"])
            x = self.env["navitta.expense"].search([("branch_id", "=", self.dwd.id)])
            self.assertTrue(self.admin().admin_expense_decide(x.id, True)["ok"])
            ds = self.env["navitta.daily.sales"].search([("branch_id", "=", self.dwd.id), ("day", "=", DAY)])
            self.assertEqual((ds.sales, ds.returns, ds.source), (5000, 1, "report"))
            self.assertAlmostEqual(ds.vat_amount, 5000 * 15 / 115, 2)
            cash = self.dwd._cash_summary()[self.dwd.id]
            self.assertEqual(cash["balance"], 1000 + 1200 - 500 - 200)
            self.assertEqual(self.dwd._stock_summary()[self.dwd.id]["bags"]["balance"], 8)
            # saving again replaces the day (no double counting)
            self.api().report_save({"employee_name": "ريم", "sales": 5100, "used": {"bags": 12}})
            self.assertEqual(self.dwd._stock_summary()[self.dwd.id]["bags"]["balance"], 8)
            summ = self.admin().employee_summary("2026-10-01", "2026-10-31")
            self.assertEqual(summ["rows"], [])          # the second save had no employee lines
            data = self.env["navitta.api"]._summary_data(DAY)
            self.assertEqual(data["sales"], 5100)
            self.assertIn("فرع القصيم", data["missing"])

    def test_requests_and_receive(self):
        r = self.api().create_request(self.reem.id, "2468", {"kind": "supply", "supply": "bags", "qty": 50})
        req = self.env["navitta.branch.request"].browse(r["id"])
        self.assertEqual(self.api().request_received(self.reem.id, "2468", req.id)["error"], "not_found")
        self.assertTrue(self.admin().admin_request_set(req.id, "shipped")["ok"])
        self.assertTrue(self.api().request_received(self.shima.id, "2468", req.id)["ok"])
        self.assertEqual(self.dwd._stock_summary()[self.dwd.id]["bags"]["balance"], 50)
        self.assertEqual(self.api(self.dev_qsm).request_received(self.hubour.id, "2468", req.id)["error"], "not_found")
        with self.assertRaises(AccessError):
            self.env["navitta.branch.request"].with_user(self.dev_qsm).browse(req.id).read(["state"])

    # ------------------------------------------------------------------ photos and the models file
    def test_photo_token(self):
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"0" * 200).decode()
        self.assertEqual(self.api().photo_upload("not-base64!")["error"], "bad_file")
        self.assertEqual(self.api().photo_upload(base64.b64encode(b"<svg>" * 50).decode())["error"], "bad_file")
        pid = self.api().photo_upload(jpeg)["id"]
        self.assertEqual(self.env["navitta.photo"].with_user(self.dev).browse(pid).branch_id, self.dwd)
        with self.assertRaises(AccessError):
            self.env["navitta.photo"].with_user(self.dev_qsm).browse(pid).read(["image"])
        tok = self.api().token_new()
        t = self.env["navitta.upload.token"]._valid(tok["token"])
        self.assertEqual(t.branch_id, self.dwd)
        self.assertFalse(self.env["navitta.upload.token"]._valid("guess"))
        with self.assertRaises(AccessError):
            self.api(self.dev_qsm).create_expense(self.hubour.id, "2468", 10, "other", "x", pid)

    def test_catalog(self):
        Item = self.env["navitta.catalog.item"]
        Item.create([
            {"alu": "100001", "item_no": "12345678", "model": "SANDY", "color": "BLACK", "size": "38", "price": 299, "sequence": 1},
            {"alu": "100002", "item_no": "12345678", "model": "SANDY", "color": "BLACK", "size": "37", "price": 299, "sequence": 2},
            {"alu": "100003", "item_no": "12345678", "model": "SANDY", "color": "BEIGE", "size": "38", "price": 299, "sequence": 3},
            {"alu": "200001", "item_no": "87654321", "model": "BAGGY", "color": "BROWN", "price": 199, "sequence": 4},
        ])
        r = self.api().catalog_find("00000100002")
        self.assertEqual((r["item"]["color"], r["item"]["size"]), ("BLACK", "37"))
        self.assertEqual(r["models"][0]["colors"][0]["sizes"], ["37", "38"])
        self.assertEqual(len(self.api().catalog_find("12345678")["models"]), 1)
        self.assertEqual(self.api().catalog_find("bag")["models"][0]["item_no"], "87654321")
        self.assertEqual(self.api().catalog_find("999")["models"], [])

    # ------------------------------------------------------------------ management screens
    def test_admin_screens_load(self):
        with freeze_time(utc(18, 0)):
            self.api().check_in(self.reem.id, "2468")
            a = self.admin()
            d = a.admin_dashboard(7)
            self.assertEqual(len(d["employees"]), 10)
            a.admin_sales(False, "2026-01-01", "2026-10-13", 2026, "2026-10-01", "2026-10-31")
            a.admin_requests()
            a.admin_lost(False, "2026-10-01", "2026-10-31")
            a.admin_day("2026-10-13")
            self.assertTrue(self.api().branch_state()["attendance"])
            self.assertTrue(self.api().requests_state())
            self.assertTrue(self.api().report_get())
            # same weekday last week had sales: the PDF shows the change in percent
            self.env["navitta.daily.sales"]._row(self.dwd, date(2026, 10, 6)).write({"sales": 1000})
            html = self.env["ir.actions.report"].with_user(self.boss)._render_qweb_html(
                "navitta_ops.report_daily_summary", [], data={"day": "2026-10-13"})[0]
            self.assertIn("Daily branches summary", html.decode())
            self.assertRegex(html.decode(), r"[▲▼] \d+\.\d%")
            with self.assertRaises(AccessError):
                self.env["ir.actions.report"].with_user(self.dev)._render_qweb_html(
                    "navitta_ops.report_daily_summary", [], data={"day": "2026-10-13"})

    def test_admin_uses_branch_screen(self):
        """Management can run any branch's screen by picking it; a device always stays on its own branch."""
        with freeze_time(utc(15, 20)):
            self.assertEqual(self.admin().branch_access()["device"], False)
            self.assertEqual(len(self.admin().branch_access()["branches"]), 5)
            self.assertEqual(self.api().branch_access()["branch"]["id"], self.dwd.id)
            self.assertEqual(self.api(self.plain).branch_access()["error"], "forbidden")
            with self.assertRaises(AccessError):  # no branch picked
                self.admin().branch_state()
            boss_dwd = self.admin().with_context(navitta_branch=self.dwd.id)
            self.assertEqual(boss_dwd.branch_state()["branch"]["id"], self.dwd.id)
            self.assertTrue(boss_dwd.check_in(self.reem.id, "2468")["ok"])
            with self.assertRaises(AccessError):  # employee of another branch than the picked one
                boss_dwd.check_in(self.hubour.id, "2468")
            # a device can't switch branch through the context
            with self.assertRaises(AccessError):
                self.api().with_context(navitta_branch=self.qsm.id).check_in(self.hubour.id, "2468")
            with self.assertRaises(AccessError):
                self.api(self.plain).with_context(navitta_branch=self.dwd.id).branch_state()

    def test_first_setup(self):
        a = self.admin()
        steps = {i["key"]: i["done"] for i in a.admin_setup()["items"]}
        self.assertEqual((steps["pins"], steps["devices"], steps["admin_pw"], steps["email"]), (False, False, True, True))
        rows = a.admin_setup_pins()["rows"]
        self.assertEqual(len(rows), 7)  # 10 employees, 3 already had a PIN
        emp = self.env["navitta.employee"].search([("name", "=", rows[0]["name"])], limit=1)
        self.assertIsNone(emp._check_pin(rows[0]["pin"]))
        self.assertEqual(a.admin_setup_pins()["rows"], [])
        devs = a.admin_setup_devices()["rows"]
        self.assertEqual(sorted(r["branch"] for r in devs), sorted(self.env["navitta.branch"].search([("id", "not in", (self.dwd | self.qsm).ids)]).mapped("name")))
        u = self.env["res.users"].search([("login", "=", devs[0]["login"])])
        self.assertTrue(u.has_group("navitta_ops.group_navitta_branch"))
        self.assertFalse(u.has_group("navitta_ops.group_navitta_admin"))
        self.assertEqual(u.action_id.id, self.env.ref("navitta_ops.action_branch_app").id)
        self.assertEqual(self.env["navitta.api"].with_user(u).branch_access()["branch"]["name"], devs[0]["branch"])
        self.assertEqual(a.admin_setup_devices()["rows"], [])
        self.assertTrue(all(i["done"] for i in a.admin_setup()["items"] if i["key"] in ("pins", "devices")))
        with self.assertRaises(AccessError):
            self.api().admin_setup_pins()
        with self.assertRaises(AccessError):
            self.api().admin_setup_devices()

    def test_settings_wizards(self):
        """The admin password window saves the new password (and doesn't keep it in clear), and the
        device accounts menu opens Odoo's full user form (permissions + the Navitta tab)."""
        w = self.env["navitta.admin.password.wizard"].with_user(self.boss).create({"password": "5555", "confirm": "5555"})
        w.sudo().action_save()
        self.assertTrue(self.company._navitta_verify_admin_password("5555"))
        self.assertFalse(w.sudo().password)
        with self.assertRaises(UserError):
            self.env["navitta.admin.password.wizard"].create({"password": "5555", "confirm": "6666"}).action_save()
        act = self.env.ref("navitta_ops.action_branch_users")
        self.assertEqual(act.view_ids.filtered(lambda v: v.view_mode == "form").view_id, self.env.ref("base.view_users_form"))

    # ------------------------------------------------------------------ employees: any number per branch, the form
    def test_employees_any_number(self):
        Emp = self.env["navitta.employee"]
        third = Emp.create({"name": "نورة", "branch_id": self.dwd.id})
        self.assertEqual(third.slot, 3)                      # next place on the screen
        third._set_pin("2468")
        with freeze_time(utc(15, 20)):
            self.assertEqual([e["name"] for e in self.api().branch_state()["employees"]], ["ريم", "شيمة", "نورة"])
            self.assertTrue(self.api().check_in(third.id, "2468")["ok"])
            with self.assertRaises(UserError):              # checked in: move her after she leaves
                third.write({"branch_id": self.qsm.id})
            # a leave with 2 colleagues: she must say who covers
            self.assertEqual(self.api().leave_request(self.reem.id, "2468", "annual", "2026-11-01", "2026-11-02", "سفر", True)["error"], "pick_cover")
            self.assertEqual(self.api().leave_request(self.reem.id, "2468", "annual", "2026-11-01", "2026-11-02", "سفر", self.hubour.id)["error"], "pick_cover")
            r = self.api().leave_request(self.reem.id, "2468", "annual", "2026-11-01", "2026-11-02", "سفر", third.id)
            self.assertEqual(self.env["navitta.leave.request"].browse(r["id"]).cover_employee_id, third)
            self.assertEqual({m["name"] for m in self.api().inbox(self.reem.id, "2468")["mates"]}, {"شيمة", "نورة"})
        with self.assertRaises(UserError):                  # history: archive instead of delete
            third.unlink()
        Emp.create({"name": "مؤقتة", "branch_id": self.qsm.id}).unlink()    # no history: can be deleted
        self.shima.write({"branch_id": self.qsm.id})        # not checked in today: can move
        self.assertEqual(self.shima.branch_id, self.qsm)

    def test_fingerprint_number(self):
        self.reem.device_user_id = " 5 "
        self.assertEqual(self.reem.device_user_id, "5")
        with self.assertRaises(ValidationError):
            self.shima.device_user_id = "5"                 # same branch
        self.hubour.device_user_id = "5"                    # other branch: fine
        with self.assertRaises(ValidationError):
            self.shima.device_user_id = "A5"

    # ------------------------------------------------------------------ check-in with a photo (iPad)
    def test_checkin_photo(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (180, 120, 90)).save(buf, "JPEG")
        jpeg = base64.b64encode(buf.getvalue()).decode()
        Photo = self.env["navitta.photo"]
        n0 = Photo.search_count([])
        with freeze_time(utc(15, 20)):
            self.assertEqual(self.api().check_in(self.reem.id, "0000", jpeg)["error"], "wrong_pin")
            self.assertEqual(Photo.search_count([]), n0)    # wrong PIN: the picture is not kept
            self.assertTrue(self.api().check_in(self.reem.id, "2468", jpeg)["ok"])
            self.assertTrue(self.api().check_in(self.shima.id, "2468")["ok"])          # camera refused: still checked in
            self.assertEqual(self.api().check_in(self.shima.id, "2468")["error"], "already_in")
        att = self.env["navitta.attendance"].search([("employee_id", "=", self.reem.id)])
        self.assertEqual((att.in_source, bool(att.in_photo_id), att.no_photo), ("ipad", True, False))
        self.assertEqual(att.in_photo_id.branch_id, self.dwd)
        self.assertTrue(self.env["navitta.attendance"].search([("employee_id", "=", self.shima.id)]).no_photo)
        with freeze_time(utc(23, 31)):
            self.assertTrue(self.api().check_out(self.reem.id, "2468", "not-base64!")["ok"])   # broken picture
        att.invalidate_recordset()
        self.assertTrue(att.no_photo)
        att.with_user(self.boss).action_use_as_reference()
        self.assertTrue(self.reem.photo)
        with freeze_time(utc(23, 40)):
            d = self.admin().admin_dashboard(1)
        reem_row = [a for a in d["attendance"] if a["employee_id"] == self.reem.id][0]
        self.assertTrue(reem_row["in_photo_id"])
        self.assertFalse(reem_row["out_photo_id"])

    # ------------------------------------------------------------------ models file import and nightly summary
    def test_catalog_import_wizard(self):
        import io
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Navitta models"])                      # a title row before the header
        ws.append(["ALU", "Desc4", "Desc1", "Attr", "Size", "Price WT", "DCS", "heel", "material"])
        ws.append(["000100001", "SANDY", 12345678, "BLACK", 38, 299.0, "LADSANCHU", "5cm", "PU"])
        ws.append(["000100002", "SANDY", 12345678, "BLACK", 39, 299.0, "LADSANCHU", "5cm", "PU"])
        ws.append([None, "EMPTY", None, None, None, None, None, None, None])   # skipped
        buf = io.BytesIO()
        wb.save(buf)
        Item = self.env["navitta.catalog.item"]
        Item.create({"alu": "999", "item_no": "1", "model": "OLD", "color": "X"})
        wiz = self.env["navitta.catalog.import.wizard"].with_user(self.boss).create({"file": base64.b64encode(buf.getvalue())})
        wiz.action_import()
        self.assertEqual(Item.search([]).mapped("alu"), ["100001", "100002"])     # replaced, zeros stripped
        self.assertEqual(Item.search([], limit=1).item_no, "12345678")
        self.assertEqual(Item.search([], limit=1).size, "38")

    def test_daily_summary_email(self):
        with freeze_time(utc(23, 45)):
            self.env["navitta.api"]._cron_daily_summary()
        n = self.env["navitta.notification"].search([("to_email", "=", "boss@example.com")], limit=1)
        self.assertIn("Daily branches summary", n.subject)
        self.assertEqual(len(n.mail_id.attachment_ids), 1)

    def test_notification_without_email(self):
        n = self.env["navitta.notification"]._notify_employee(self.shima, "x", "y")
        self.assertEqual((n.state, n.mail_id.id), ("no_email", False))

    # ------------------------------------------------------------------ 26.3: morning and evening shifts
    def test_shifts(self):
        """Lateness counts from her own shift: morning (9:30) in a branch with a morning shift, evening otherwise;
        on Sun/Mon/Fri morning employees work the evening shift. The late cron follows each one's shift window."""
        ahs = self.env.ref("navitta_ops.branch_ahs")
        buth, marwa = self.env.ref("navitta_ops.emp_ahs_1"), self.env.ref("navitta_ops.emp_ahs_2")
        self.assertTrue(ahs.two_shifts)                       # Al-Ahsa has two shifts (site default)
        self.assertEqual(self.company._navitta_no_am(), {0, 1, 5})
        buth.write({"shift": "am"})
        buth._set_pin("2468")
        with self.assertRaises(ValidationError):              # no morning shift in Dawadmi
            self.reem.shift = "am"
        self.assertEqual(buth._shift(DAY), {"kind": "am", "start": 570, "end": 960, "moved": False})
        sunday = date(2026, 10, 11)
        self.assertEqual(buth._shift(sunday), {"kind": "pm", "start": 930, "end": 1410, "moved": True})
        self.assertEqual(marwa._shift(DAY)["kind"], "pm")
        Case = self.env["navitta.late.case"]
        with freeze_time(utc(9, 33)):
            self.assertEqual(Case._cron_late_check(), 0)      # inside her grace
        with freeze_time(utc(9, 50)):
            self.assertEqual(Case._cron_late_check(), 1)      # only the morning employee; evening hasn't started
        late = Case.search([("employee_id", "=", buth.id)])
        self.assertEqual((late.minutes, late.check_in), (20, False))
        self.assertIn("9:30 AM", self.env["navitta.notification"].search([("employee_id", "=", buth.id)], limit=1).body)
        boss_ahs = self.admin().with_context(navitta_branch=ahs.id)
        with freeze_time(utc(10, 0)):
            self.assertEqual(boss_ahs.check_in(buth.id, "2468")["late"], 30)
            st = boss_ahs.branch_state()
            sh = {e["id"]: e["shift"] for e in st["employees"]}
            self.assertEqual((sh[buth.id]["kind"], sh[buth.id]["start"], sh[marwa.id]["kind"]), ("am", 570, "pm"))
            self.assertEqual((st["settings"]["am_start"], st["settings"]["am_end"], st["settings"]["no_am_days"]), (570, 960, [0, 1, 5]))
            self.assertEqual(boss_ahs.target_bar()["shift_end"], 1410)       # the branch closes with the evening shift
        msg = self.env["navitta.notification"].search([("employee_id", "=", buth.id), ("subject", "=", "Alert: late arrival")])
        self.assertIn("after your shift start (9:30 AM)", msg.body)
        self.assertEqual(Case.search([("employee_id", "=", buth.id)]).minutes, 30)
        with freeze_time(utc(15, 40)):
            self.assertEqual(Case._cron_late_check(), 9)      # the evening employees (she has her case already)
        with freeze_time(utc(16, 30)):
            self.assertEqual(Case._cron_late_check(), 0)
        # fingerprint device: the same rule (her morning shift), and on Sunday her evening shift
        Att = self.env["navitta.attendance"]
        friday = date(2026, 10, 16)                           # no morning shift on Fridays
        _a, lt = Att._register_in(buth, fields.Datetime.to_datetime(utc(15, 45, friday)), "device")
        self.assertEqual(lt, 15)
        # the admin dashboard and the nightly PDF show both shifts and each employee's shift
        with freeze_time(utc(18, 0)):
            emp = {e["id"]: e for e in self.admin().admin_dashboard(1)["employees"]}
            self.assertEqual(emp[buth.id]["shift"]["kind"], "am")
            data = self.env["navitta.api"]._summary_data(DAY)
            self.assertEqual((data["am_shift"], data["shift"]), ("9:30 AM – 4:00 PM", "3:30 PM – 11:30 PM"))
        # evening only again: her shift is forced back to evening
        ahs.two_shifts = False
        self.assertEqual(buth.shift, "pm")
        # settings: days without a morning shift and the branches with one
        cfg = self.env["res.config.settings"].create({"navitta_no_am_5": False, "navitta_no_am_6": True,
                                                       "navitta_two_shift_branch_ids": [(6, 0, [self.qsm.id])]})
        cfg.execute()
        self.assertEqual(self.company._navitta_no_am(), {0, 1, 6})
        self.assertEqual(self.env["navitta.branch"].search([("two_shifts", "=", True)]), self.qsm)

    # ------------------------------------------------------------------ 26.3: violations (3-way decision, sign, deduct)
    def test_violation_decisions(self):
        Case = self.env["navitta.late.case"]
        mk = lambda d, m=10, kind="late": Case.create({"employee_id": self.reem.id, "day": d, "kind": kind, "minutes": m,
                                                         "state": "review", "reason": "زحمة"})
        a = self.admin()
        old = mk(date(2026, 9, 20))                           # a penalty last month doesn't count in October
        self.assertTrue(a.admin_decide_case(old.id, "penalty")["ok"])
        cases = [mk(date(2026, 10, i)) for i in range(1, 8)]
        pv = a.admin_case_preview(cases[0].id)
        self.assertEqual((pv["used"], pv["max"], pv["penalty"]["occ"], pv["penalty"]["text"]), (0, 3, 1, "إنذار كتابي"))
        # an excuse needs a reason and counts as neither a chance nor a penalty
        self.assertEqual(a.admin_decide_case(cases[0].id, "excuse")["error"], "reason_required")
        self.assertTrue(a.admin_decide_case(cases[0].id, "excuse", "موعد مستشفى بورقة")["ok"])
        self.assertEqual(cases[0].state, "excused")
        for c in cases[1:4]:
            self.assertTrue(a.admin_decide_case(c.id, "chance")["ok"])
        self.assertEqual(a.admin_decide_case(cases[4].id, "chance")["error"], "no_chances")
        self.assertEqual(a.admin_case_preview(cases[4].id)["used"], 3)
        self.assertTrue(a.admin_decide_case(cases[4].id, "penalty", "بدون عذر")["ok"])
        cases[5].minutes = 25
        self.assertTrue(a.admin_decide_case(cases[5].id, "penalty")["ok"])
        self.assertEqual((cases[4].state, cases[4].penalty, cases[4].penalty_occ), ("rejected", "إنذار كتابي", 1))
        self.assertEqual((cases[5].penalty, cases[5].penalty_occ), ("حسم 15% من أجر اليوم", 2))   # 16–30 min, occurrence 2
        self.assertEqual(a.admin_case_preview(cases[6].id)["penalty"]["occ"], 3)
        n = self.env["navitta.notification"].search([("employee_id", "=", self.reem.id)], limit=1)
        self.assertIn("a penalty was issued", n.subject)
        self.assertIn("sign that you read it", n.body)
        # the review list of the dashboard and the rules screen carries the preview
        with freeze_time(utc(18, 0)):
            d = a.admin_dashboard(30)
            rv = [c for c in d["cases"] if c["id"] == cases[6].id][0]
            self.assertEqual(rv["preview"]["penalty"]["occ"], 3)
            r = a.admin_rules()
            self.assertEqual([c["id"] for c in r["review"]], [cases[6].id])
            self.assertEqual(len([c for c in r["cases"] if c["state"] == "rejected"]), 2)    # October only
            self.assertEqual(len(r["tiers"]), 4)
            # she signs her penalty with her PIN (only her own, once)
            box = self.api().inbox(self.reem.id, "2468")
            self.assertEqual({c["id"] for c in box["cases"] if c["state"] == "rejected" and c["day"] >= "2026-10-01" and not c["ack_at"]},
                             {cases[4].id, cases[5].id})
            self.assertEqual(self.api().case_ack(self.shima.id, "2468", cases[4].id)["error"], "not_found")
            self.assertEqual(self.api().case_ack(self.reem.id, "0000", cases[4].id)["error"], "wrong_pin")
            self.assertTrue(self.api().case_ack(self.reem.id, "2468", cases[4].id)["ok"])
            self.assertEqual(self.api().case_ack(self.reem.id, "2468", cases[4].id)["error"], "not_found")
            self.assertEqual(self.api().case_ack(self.reem.id, "2468", cases[1].id)["error"], "not_found")   # not a penalty
        self.assertTrue(cases[4].ack_at)
        # management marks it deducted for payroll (and can undo)
        self.assertTrue(a.admin_case_settle(cases[4].id, True)["ok"])
        self.assertTrue(cases[4].settled_at)
        self.assertTrue(a.admin_case_settle(cases[4].id, False)["ok"])
        self.assertFalse(cases[4].settled_at)
        self.assertEqual(a.admin_case_settle(cases[1].id, True)["error"], "not_found")
        # penalty table edited on the screen
        t = r["tiers"][0]
        self.assertEqual(a.admin_tiers_save([dict(t, p1="")])["error"], "tier_required")
        self.assertTrue(a.admin_tiers_save([dict(t, p1="تنبيه شفهي")])["ok"])
        self.assertEqual(self.env["navitta.penalty.tier"]._tier_for(5).p1, "تنبيه شفهي")
        with self.assertRaises(AccessError):
            self.api().admin_case_settle(cases[4].id, True)
        with self.assertRaises(AccessError):
            self.api().admin_rules()

    # ------------------------------------------------------------------ 26.3: branch rules signed in «تنبيهاتي»
    def test_branch_rules(self):
        a = self.admin()
        with freeze_time(utc(12, 0)):
            box = self.api().inbox(self.reem.id, "2468")
            self.assertEqual((box["rules"], box["rules_signed"]), ([], True))
            self.assertEqual(a.admin_rule_save(False, "  ")["error"], "rule_required")
            rid = a.admin_rule_save(False, "الجوال ممنوع وقت خدمة العميلة")["id"]
            a.admin_rule_save(False, "الالتزام بالزي")
            box = self.api().inbox(self.reem.id, "2468")
            self.assertEqual([r["body"] for r in box["rules"]], ["الجوال ممنوع وقت خدمة العميلة", "الالتزام بالزي"])
            self.assertFalse(box["rules_signed"])
        with freeze_time(utc(12, 5)):
            self.assertEqual(self.api().rules_ack(self.reem.id, "0000")["error"], "wrong_pin")
            self.assertTrue(self.api().rules_ack(self.reem.id, "2468")["ok"])
            self.assertTrue(self.api(self.dev_qsm).rules_ack(self.hubour.id, "2468")["ok"])
            self.assertTrue(self.api().inbox(self.reem.id, "2468")["rules_signed"])
            acks = a.admin_rules()["acks"]
            self.assertEqual((acks[self.reem.id]["current"], acks[self.shima.id]["current"]), (True, False))
        # a branch device sees only its own branch's signatures
        self.assertEqual(self.env["navitta.rule.ack"].with_user(self.dev).search([]).employee_id, self.reem)
        with freeze_time(utc(13, 0)):                         # a rule changed: everyone signs again
            self.assertTrue(a.admin_rule_save(rid, "الجوال ممنوع نهائيًا وقت خدمة العميلة")["ok"])
            self.assertFalse(self.api().inbox(self.reem.id, "2468")["rules_signed"])
            self.assertFalse(a.admin_rules()["acks"][self.reem.id]["current"])
            self.assertTrue(a.admin_rule_delete(rid)["ok"])
            self.assertEqual(len(a.admin_rules()["rules"]), 1)

    # ------------------------------------------------------------------ 26.3: «تنبيهاتي» feed and unread badges
    def test_inbox_notes_and_unread(self):
        with freeze_time(utc(16, 0)):
            self.api().check_in(self.reem.id, "2468")          # a late-arrival alert
        self.reem._notify("تحديث طلبك", "✅ وافقت الإدارة")
        old = self.env["navitta.notification"]._notify_employee(self.reem, "قديم", "x")
        old.flush_recordset()
        self.env.cr.execute("UPDATE navitta_notification SET create_date = create_date - interval '40 days' WHERE id = %s", [old.id])
        old.invalidate_recordset()
        self.reem._new_pin()                                    # the PIN email is secret: never in the feed or the counts
        self.reem._set_pin("2468")
        self.env["navitta.notification"].flush_model()
        with freeze_time(utc(16, 5)):
            un = self.api().branch_unread()["unread"]
            self.assertEqual((un.get(self.reem.id), un.get(self.shima.id)), (2, None))
            self.assertEqual(self.api().branch_state()["unread"][self.reem.id], 2)
            self.assertEqual(self.api(self.dev_qsm).branch_unread()["unread"], {})        # other branch: nothing
            box = self.api().inbox(self.reem.id, "2468")
            self.assertEqual(len(box["notes"]), 3)
            self.assertTrue(all(n["new"] for n in box["notes"]))
            self.assertNotIn("🔐 Your new PIN", [n["subject"] for n in box["notes"]])
            self.assertEqual(self.api().branch_unread()["unread"].get(self.reem.id), None)   # opening the box read them
            self.assertFalse(any(n["new"] for n in self.api().inbox(self.reem.id, "2468")["notes"]))
            # «راتب أو سلفة» is no longer a topic
            self.assertNotIn("salary", [t[0] for t in box["topics"]])
            self.assertTrue(self.api().send_message(self.reem.id, "2468", "salary", "سؤال")["ok"])
        self.assertEqual(self.env["navitta.thread"].search([("employee_id", "=", self.reem.id)], limit=1).topic, "other")

    # ------------------------------------------------------------------ 26.3: the daily break in several parts
    def test_split_break(self):
        Brk = self.env["navitta.break"]
        with freeze_time(utc(15, 20)):
            self.api().check_in(self.reem.id, "2468")
        with freeze_time(utc(17, 0)):
            self.assertTrue(self.api().break_start(self.reem.id)["ok"])
        with freeze_time(utc(17, 10)):
            r = self.api().break_end(Brk.search([("employee_id", "=", self.reem.id), ("ended_at", "=", False)]).id)
            self.assertEqual((r["over"], r["left_sec"]), (0, 20 * 60))
        with freeze_time(utc(19, 0)):
            self.assertTrue(self.api().break_start(self.reem.id)["ok"])      # continue the break later
        with freeze_time(utc(19, 15)):
            r = self.api().break_end(Brk.search([("employee_id", "=", self.reem.id), ("ended_at", "=", False)]).id)
            self.assertEqual((r["over"], r["left_sec"]), (0, 5 * 60))
            self.assertFalse(self.env["navitta.late.case"].search([("employee_id", "=", self.reem.id)]))
        with freeze_time(utc(21, 0)):
            self.assertTrue(self.api().break_start(self.reem.id)["ok"])
        with freeze_time(utc(21, 10)):
            r = self.api().break_end(Brk.search([("employee_id", "=", self.reem.id), ("ended_at", "=", False)]).id)
            self.assertEqual((r["over"], r["left_sec"]), (5, 0))
            self.assertEqual(self.api().break_start(self.reem.id)["error"], "break_used")
        case = self.env["navitta.late.case"].search([("employee_id", "=", self.reem.id), ("kind", "=", "break")])
        self.assertEqual((case.minutes, case.break_used), (5, 35))          # the overrun on the day's total

    # ------------------------------------------------------------------ 26.3: a new PIN is emailed, then wiped
    def test_pin_email(self):
        Notif = self.env["navitta.notification"]
        self.company.email = False                              # no sender address: sending fails, she gets it from management
        self.assertEqual(self.reem._new_pin()[1], "failed")
        self.company.email = "info@navitta.example"
        pin, mail = self.reem._new_pin()
        self.assertEqual(mail, "sent")
        self.assertIsNone(self.reem._check_pin(pin))
        n = Notif.search([("employee_id", "=", self.reem.id), ("secret", "=", True)], limit=1)
        self.assertEqual(n.subject, "🔐 Your new PIN")
        self.assertEqual(n.body, Notif.WIPED)                  # the PIN is not kept after sending
        self.assertNotIn(pin, n.body_text)
        self.assertEqual(n.body_text, "🔐 (The PIN was wiped after sending)")
        self.assertFalse(n.mail_id)                             # the email itself was removed from the queue
        self.assertEqual(n.state, "sent")
        # without an email: only the screen shows it
        pin2, mail2 = self.shima._new_pin()
        self.assertEqual(mail2, "no_email")
        self.assertFalse(Notif.search([("employee_id", "=", self.shima.id)]))
        # the backend button shows it once with the mail status
        act = self.reem.action_new_pin()
        wiz = self.env["navitta.pin.wizard"].browse(act["res_id"])
        self.assertEqual((len(wiz.pin), wiz.mail), (4, "sent"))
        # a PIN email that waits in the queue is wiped when the queue sends it
        q = Notif._send("x@example.com", "🔐 Your new PIN", "1234", employee=self.reem, secret=True)
        self.assertEqual(q.body, "1234")
        q.mail_id.send()
        self.assertEqual(q.body, Notif.WIPED)
        # first-time setup: the generated PINs are emailed too
        self.env.ref("navitta_ops.emp_qsm_2").sudo().email = "shadia@example.com"
        rows = {r["name"]: r["mail"] for r in self.admin().admin_setup_pins()["rows"]}
        self.assertEqual((rows["شادية"], rows["بثينة"]), ("sent", "no_email"))


@tagged("post_install", "-at_install", "navitta")
class NavittaHttp(HttpCase):

    def test_phone_upload(self):
        dwd = self.env.ref("navitta_ops.branch_dwd")
        tok = self.env["navitta.upload.token"]._new(dwd)
        page = self.url_open("/navitta/r?t=%s" % tok.token)
        self.assertEqual(page.status_code, 200)
        self.assertIn("فرع الدوادمي", page.text)
        self.assertIn("The code has expired", self.url_open("/navitta/r?t=wrong").text)
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"0" * 200).decode()
        r = self.url_open("/navitta/r/upload", data=json.dumps({"jsonrpc": "2.0", "method": "call",
                          "params": {"token": "wrong", "data": jpeg}}), headers={"Content-Type": "application/json"})
        self.assertEqual(r.json()["result"]["error"], "expired")
        r = self.url_open("/navitta/r/upload", data=json.dumps({"jsonrpc": "2.0", "method": "call",
                          "params": {"token": tok.token, "data": jpeg}}), headers={"Content-Type": "application/json"})
        self.assertTrue(r.json()["result"]["ok"])
        tok.invalidate_recordset()
        self.assertEqual(tok.photo_id.branch_id, dwd)

    def test_fingerprint_device_push(self):
        """A ZKTeco terminal in cloud (ADMS) mode: unknown -> pending, approved -> punches become attendance."""
        env = self.env
        env.company.write({"navitta_tz": "Asia/Riyadh", "navitta_shift_start": 15.5, "navitta_shift_end": 23.5, "navitta_grace": 5,
                           "navitta_cash_tasks": False})
        dwd = env.ref("navitta_ops.branch_dwd")
        emp = env["navitta.employee"].create({"name": "تجربة البصمة", "branch_id": dwd.id, "device_user_id": "5"})
        y = (fields.Datetime.now() + timedelta(hours=3)).date() - timedelta(days=1)   # yesterday, Riyadh
        t = lambda h, m, s=0: "%s %02d:%02d:%02d" % (y, h, m, s)
        sn = "NVTEST01"

        def post(lines, sn=sn):
            return self.url_open("/iclock/cdata?SN=%s&table=ATTLOG&Stamp=1" % sn, data="".join(lines),
                                 headers={"Content-Type": "text/plain"})
        hello = self.url_open("/iclock/cdata?SN=%s&options=all&pushver=2.4.1" % sn)
        self.assertEqual(hello.status_code, 200)
        self.assertIn("GET OPTION FROM: %s" % sn, hello.text)
        self.assertIn("Realtime=1", hello.text)
        dev = env["navitta.device"].search([("serial_number", "=", sn)])
        self.assertEqual(dev.state, "pending")
        line = "5\t%s\t0\t1\t0\t0\t0\n"
        self.assertEqual(post([line % t(15, 40)]).status_code, 403)          # not approved: device keeps the punch
        dev.write({"branch_id": dwd.id})
        dev.action_approve()
        r = post([line % t(15, 40), line % t(15, 41)])
        self.assertEqual((r.status_code, r.text), (200, "OK: 2"))
        att = env["navitta.attendance"].search([("employee_id", "=", emp.id), ("day", "=", y)])
        self.assertEqual((att.in_source, att.in_device_id), ("device", dev))
        case = env["navitta.late.case"].search([("employee_id", "=", emp.id), ("day", "=", y), ("kind", "=", "late")])
        self.assertEqual(case.minutes, 10)
        punches = env["navitta.device.punch"].search([("device_id", "=", dev.id)], order="punched_at")
        self.assertEqual(punches.mapped("state"), ["in", "duplicate"])
        self.assertEqual(post([line % t(15, 40)]).text, "OK: 1")               # same batch again: not counted twice
        self.assertEqual(env["navitta.device.punch"].search_count([("device_id", "=", dev.id)]), 2)
        post([line % t(23, 35)])
        att.invalidate_recordset()
        self.assertEqual(att.out_source, "device")
        self.assertEqual(att.check_out.strftime("%H:%M"), "20:35")             # 23:35 Riyadh = 20:35 UTC
        post([line % t(15, 30)])                                               # an earlier punch arrives late
        att.invalidate_recordset()
        self.assertEqual(att.check_in.strftime("%H:%M"), "12:30")
        self.assertFalse(case.exists())                                        # on time after all
        post(["77\t%s\t0\t1\t0\n" % t(16, 0)])                             # nobody has fingerprint number 77 yet
        p77 = env["navitta.device.punch"].search([("device_user_id", "=", "77")])
        self.assertEqual(p77.state, "unmatched")
        other = env["navitta.employee"].create({"name": "رقم 77", "branch_id": dwd.id})
        other.device_user_id = "77"                                            # linking her processes the waiting punch
        self.assertEqual(p77.state, "in")
        self.assertEqual(self.url_open("/iclock/getrequest?SN=%s&INFO=Ver 8.0,3,2" % sn).text, "OK")
        dev.invalidate_recordset()
        self.assertIn("Ver 8.0", dev.info)
        dev.allowed_ip = "10.9.9.9"
        self.assertEqual(post([line % t(23, 50)]).status_code, 403)
        self.assertEqual(self.url_open("/iclock/cdata?SN=bad%20sn!&options=all").status_code, 400)

