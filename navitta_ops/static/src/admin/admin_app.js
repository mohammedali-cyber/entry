/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    DFST, EXPICON, EXST, LOST, LOSTL, MONTHS, OUTOF, RQST, SREASONL, SUP, WD, addDays, ago, api, clock, dTxt, days, dmy, downloadCsv, f0, h12, longDate,
    mmss, now, pc, setClock, today, weekday, jn, DIR, LANG, vlabel,
} from "../common/utils";
import { DailyQuote, EmpBoard, NvModal, PhotoLink, TargetEditor } from "../common/components";
import { CaseDecide, Dots } from "../common/cases";
import { groupOf, phases } from "../common/tasks";
import { DayChanges, DayEdit, TargetFile } from "./sales_tools";
import { ShipBuilder, ShipLog } from "../common/shipments";

const LEAVEADM = { pending: _t("Awaiting your approval"), cover: _t("Awaiting colleague"), cover_no: _t("Colleague declined"), approved: _t("Approved"), rejected: _t("Rejected") };
// the new PIN's email (sent right away, then wiped)
const PINMAIL = { sent: ["good", _t("📧 Sent to her email now ✓")], failed: ["bad", _t("⚠️ The email was not sent (check the outgoing mail server); give her the PIN yourself")],
    queued: ["info", _t("📧 It will reach her email shortly")], no_email: ["warn", _t("⚠️ She has no email on file; give her the PIN yourself")] };
const NOTIF = { sent: ["good", _t("Sent")], no_email: ["bad", _t("No email")], exception: ["bad", _t("Sending failed")], outgoing: ["info", _t("Waiting to send")] };
// list separator and "old → new" arrow follow the writing direction
const SEP = LANG === "ar" ? "\u060c " : ", ";
const ARROW = DIR === "rtl" ? "←" : "→";
// texts used inside template expressions (expressions are not translated by the template engine)
const TX = {
    sar: _t("SAR"),
    min: _t("min"),
    photo: _t("Photo"),
    open: _t("Open"),
    newPins: _t("🔑 New PINs"),
    devAccounts: _t("📱 Branch device accounts"),
    fromPos: _t("From Point of Sale"),
    fromReports: _t("From daily reports"),
    noPendingDecisions: _t("No pending decisions"),
    morning: _t("Morning shift"),
    evening: _t("Evening shift"),
    breakOver: _t("☕ Break overrun"),
    lateIn: _t("Late check-in"),
    leaveApproved: _t("Leave approved and recorded"),
    requestRejected: _t("Request rejected"),
    changeAccepted: _t("Change accepted"),
    changeRejected: _t("Change rejected"),
    unresolved: _t("Unresolved"),
    resolved: _t("Resolved"),
    management: _t("Management"),
    resolveClose: _t("Resolved, close"),
    reopen: _t("Reopen"),
    pos: _t("Point of Sale"),
    dailyReport: _t("Daily report"),
    moreInvoices: _t("More invoices than “Bought” visits in the counter: sales without a recorded visit"),
    moreBought: _t("More “Bought” visits than invoices"),
    empSalesByCashier: _t("Employee sales by cashier"),
    allBranches: _t("All branches"),
    noDataPeriod: _t("No data for this period"),
    noDataMonth: _t("No data for this month"),
    outOfStock: _t("👠 Out of stock"),
    supplies: _t("🧾 Supplies"),
    itemPhoto: _t("Item photo"),
    approved: _t("Approved"),
    shippedDone: _t("Done: shipped"),
    expenseApproved: _t("Expense approved"),
    expenseRejected: _t("Expense rejected"),
    hide: _t("Hide ▴"),
    more: _t("More ▾"),
    view: _t("View"),
    important: _t("Important"),
    alert: _t("Alert"),
    event: _t("Event"),
    negative: _t("negative"),
    settlement: _t("💳 Settlement"),
    invoicesPhoto: _t("🧾 Invoices"),
    positive: _t("positive"),
    nAwaitDecision: (n) => _t("%s awaiting your decision", n),
    daysNoReport: (n) => _t("%s days without a report", n),
    reportGap: (n) => _t("On %s days with a report: the report figures differ from the Point of Sale invoices", n),
    fromTo: (a, b) => _t("From %(from)s to %(to)s", { from: a, to: b }),
    size: (s) => _t("Size %s", s),
    defectsWaiting: (n) => _t("%s awaiting your reply", n),
    defectPhoto: _t("Defect photo"),
    replyPh: _t("💬 Management reply (required to reject)"),
    defectOk: _t("Approved"),
    defectNo: _t("Rejected"),
    rejectReason: _t("Write the rejection reason in the reply box"),
    caseLine: (c) => c.kind === "break"
        ? _t("Break %(used)s min · over by %(over)s min", { used: c.break_used, over: c.minutes })
        : _t("Late arrival %s min", c.minutes) + (c.check_in ? " · " + _t("Checked in %s", clock(c.check_in)) : ""),
};
const CMPCOLS = [["target", _t("Target")], ["sales", _t("Sales")], ["achieved", _t("Achieved")], ["tax", _t("VAT")], ["invoices", _t("Invoices")],
    ["avg_invoice", _t("Average invoice")], ["items", _t("Items")], ["returns", _t("Returns")], ["visits", _t("Counter customers")],
    ["conversion", _t("Conversion")], ["counter_gap", _t("Invoices − Bought (counter)")], ["report_gap", _t("Report − System")]];
const REQKINDS = [["all", _t("All")], ["goods", _t("Out-of-stock request")], ["defect", _t("🩹 Defective items")], ["supply", _t("Supplies")], ["expense", _t("Expenses")]];
const H = { jn, f0, mmss, clock, longDate, h12, dTxt, days, ago, pc, dmy, weekday, addDays, LOSTL, SUP, EXPICON, RQST, EXST, DFST, SREASONL, WD, MONTHS, LEAVEADM, NOTIF, PINMAIL,
    TX, CMPCOLS, REQKINDS, SEP, ARROW, vlabel };

// ---------------------------------------------------------------- shared period filter (every tab)
const FKEY = "navitta_admin_filter";
const PRESETS = [["today", _t("Today")], ["yesterday", _t("Yesterday")], ["last7", _t("Last 7 days")], ["month", _t("This month")], ["prevmonth", _t("Last month")],
    ["last30", _t("Last 30 days")], ["year", _t("Year to date")], ["custom", _t("📅 Custom")]];
function presetRange(k) {
    const td = today();
    const m0 = td.slice(0, 8) + "01";
    switch (k) {
        case "today": return [td, td];
        case "yesterday": return [addDays(td, -1), addDays(td, -1)];
        case "last7": return [addDays(td, -6), td];
        case "last30": return [addDays(td, -29), td];
        case "prevmonth": return [addDays(m0, -1).slice(0, 8) + "01", addDays(m0, -1)];
        case "year": return [td.slice(0, 4) + "-01-01", td];
        default: return [m0, td];
    }
}
function loadFilter() {
    let f = null;
    try {
        f = JSON.parse(localStorage.getItem(FKEY) || "null");
    } catch {
        f = null;
    }
    f = Object.assign({ br: 0, preset: "month", from: "", to: "", wd: [] }, f || {});
    if (f.preset !== "custom" || !f.from || !f.to) {
        [f.from, f.to] = presetRange(f.preset === "custom" ? "month" : f.preset);
    }
    return f;
}

class FilterBar extends Component {
    static template = "navitta_ops.FilterBar";
    static props = { f: Object, branches: Array, dates: Boolean };
    setup() {
        this.PRESETS = PRESETS;
        this.WD = WD;
        this.DAYS = [0, 1, 2, 3, 4, 5, 6];
        this.dmy = dmy;
        this.SEP = SEP;
    }
    save() {
        try {
            localStorage.setItem(FKEY, JSON.stringify(this.props.f));
        } catch {
            // private mode: the filter is just not remembered
        }
    }
    preset(k) {
        const f = this.props.f;
        if (k !== "custom") {
            [f.from, f.to] = presetRange(k);
        }
        f.preset = k;
        this.save();
    }
    setDate(k, v) {
        const f = this.props.f;
        if (!v) return;
        f[k] = v;
        if (f.from > f.to) {
            f[k === "from" ? "to" : "from"] = v;
        }
        f.preset = "custom";
        this.save();
    }
    toggleDay(n) {
        const f = this.props.f;
        f.wd = f.wd.includes(n) ? f.wd.filter((x) => x !== n) : [...f.wd, n].sort();
        this.save();
    }
    setBr(v) {
        this.props.f.br = +v || 0;
        this.save();
    }
    clearDays() {
        this.props.f.wd = [];
        this.save();
    }
}

class AdminBase extends Component {
    static props = { f: Object };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        Object.assign(this, H);
        // reload when the shared filter changes (not on the first run: onWillStart already loaded)
        const f = this.props.f;
        let first = true;
        useEffect(() => {
            if (first) {
                first = false;
            } else if (this.watchFilter) {
                this.load();
            }
        }, () => [f.br, f.from, f.to, f.wd.join(",")]);
    }
    get br() {
        return this.props.f.br;
    }
    toast(msg, type = "success") {
        this.notification.add(msg, { type });
    }
    async act(method, args, ok) {
        const r = await api(this.orm, method, ...args);
        if (r.error) {
            this.toast(r.message, "danger");
            return false;
        }
        this.toast(ok);
        await this.load();
        return true;
    }
    bName(id) {
        return ((this.state.d && this.state.d.branches || []).find((b) => b.id === id) || {}).name || "";
    }
}

// ---------------------------------------------------------------- dashboard
class Dashboard extends AdminBase {
    static template = "navitta_ops.AdminDashboard";
    static components = { DailyQuote, EmpBoard, NvModal, PhotoLink, CaseDecide };
    setup() {
        super.setup();
        this.state = useState({ d: null, notes: {}, openT: null, reply: "", setup: null, once: null, busy: false });
        this.watchFilter = true;
        this.action = useService("action");
        onWillStart(() => this.load());
        onMounted(() => (this.timer = setInterval(() => this.load(), 60000)));
        onWillUnmount(() => clearInterval(this.timer));
    }
    async load() {
        const f = this.props.f;
        const my = (this.req = (this.req || 0) + 1);
        const [d, setup] = await Promise.all([api(this.orm, "admin_dashboard", f.from, f.to, f.wd), api(this.orm, "admin_setup")]);
        if (my !== this.req) {
            return;
        }
        if (!setup.error) {
            this.state.setup = setup;
        }
        if (d.error) {
            return;
        }
        setClock(d.settings.tz);
        d.E = Object.fromEntries(d.employees.map((e) => [e.id, e]));
        this.state.d = d;
    }
    /** First-time setup buttons. PINs and device passwords are shown once (state.once), then only their hashes stay. */
    async setupDo(key) {
        const reload = { onClose: () => this.load() };
        if (key === "pins" || key === "devices") {
            if (this.state.busy) {
                return;
            }
            this.state.busy = true;
            const r = await api(this.orm, key === "pins" ? "admin_setup_pins" : "admin_setup_devices");
            this.state.busy = false;
            if (r.error) {
                return this.toast(r.message, "danger");
            }
            this.state.once = { kind: key, rows: r.rows };
            return this.load();
        }
        if (key === "admin_pw") {
            return this.action.doAction({ type: "ir.actions.act_window", res_model: "navitta.admin.password.wizard", views: [[false, "form"]], target: "new", name: _t("Management password") }, reload);
        }
        const item = (this.state.setup && this.state.setup.items.find((i) => i.key === key)) || {};
        if (item.action) {
            return this.action.doAction(item.action, reload);
        }
        const xmlid = { email: "navitta_ops.action_navitta_settings", mail_server: "base.action_ir_mail_server_list",
            catalog: "navitta_ops.action_catalog_import", branch: "navitta_ops.action_branch_app", employees: "navitta_ops.action_employees" }[key];
        return this.action.doAction(xmlid, reload);
    }
    saveOnce() {
        const o = this.state.once;
        const rows = o.kind === "pins"
            ? [[_t("Branch"), _t("Employee"), _t("PIN")], ...o.rows.map((r) => [r.branch, r.name, r.pin])]
            : [[_t("Branch"), _t("Login"), _t("Password")], ...o.rows.map((r) => [r.branch, r.login, r.password])];
        downloadCsv(o.kind === "pins" ? "navitta-pins.csv" : "navitta-devices.csv", rows);
    }
    inB(b) {
        return !this.br || b === this.br;
    }
    eB(id) {
        return (this.state.d.E[id] || {}).branch_id;
    }
    eName(id) {
        return (this.state.d.E[id] || {}).name || "—";
    }
    get v() {
        const d = this.state.d;
        const reps = d.reports.filter((r) => this.inB(r.branch_id)), vis = d.visits.filter((v) => this.inB(v.branch_id));
        const sl = d.sales.filter((r) => this.inB(r.branch_id));
        const sales = sl.reduce((t, r) => t + (+r.sales || 0), 0), inv = sl.reduce((t, r) => t + (+r.invoices || 0), 0);
        const tax = sl.reduce((t, r) => t + (+r.tax || 0), 0), sys = sl.some((r) => r.system);
        const buys = vis.filter((v) => v.bought).length;
        const sat = reps.length ? reps.reduce((t, r) => t + (+r.sat || 0), 0) / reps.length : 0;
        return {
            sales, inv, tax, sys, visits: vis.length, buyPct: vis.length ? ((buys / vis.length) * 100).toFixed(1) : 0, sat: sat.toFixed(1),
            review: d.cases.filter((c) => c.state === "review" && this.inB(this.eB(c.employee_id))),
            waiting: d.cases.filter((c) => c.state === "reply" && this.inB(this.eB(c.employee_id))),
            lreq: d.leave_requests.filter((r) => this.inB(this.eB(r.employee_id)) && (["pending", "cover"].includes(r.state) ||
                (r.decided_at && now() - Date.parse(r.decided_at) < 7 * 864e5))),
            ereq: d.edit_requests.filter((r) => this.inB(this.eB(r.employee_id))),
            threads: d.threads.filter((t) => this.inB(this.eB(t.employee_id))),
            emps: d.employees.filter((e) => this.inB(e.branch_id)),
        };
    }
    attRow(e) {
        const d = this.state.d;
        const a = d.attendance.find((x) => x.employee_id === e.id);
        const bm = Math.ceil(d.breaks.filter((b) => b.employee_id === e.id).reduce((t, b) => t + ((b.ended_at ? Date.parse(b.ended_at) : now()) - Date.parse(b.started_at)) / 6e4, 0));
        const onB = d.breaks.some((b) => b.employee_id === e.id && !b.ended_at);
        const lv = d.leaves.some((l) => l.employee_id === e.id);
        const late = d.cases.find((c) => c.employee_id === e.id && c.day === d.day && c.kind === "late");
        let st;
        if (!a) {
            st = d.off[e.id] ? ["neutral", lv ? _t("🏖️ Leave") : _t("🌙 Day off") + (e.weekly_off ? ` (${WD[+e.weekly_off]})` : "")] : ["bad", _t("Not checked in")];
        } else if (a.check_out) {
            st = ["neutral", _t("Checked out")];
        } else if (late) {
            st = ["warn", _t("Late %s min", late.minutes)];
        } else {
            st = ["good", _t("Present")];
        }
        return { a, bm, onB, st };
    }
    branchRows() {
        const d = this.state.d;
        return d.branches.map((b) => {
            const rs = d.reports.filter((r) => r.branch_id === b.id), vs = d.visits.filter((v) => v.branch_id === b.id);
            const ss = d.sales.filter((r) => r.branch_id === b.id);
            const bb = vs.filter((v) => v.bought).length;
            return {
                b, sales: ss.reduce((t, r) => t + (+r.sales || 0), 0), inv: ss.reduce((t, r) => t + (+r.invoices || 0), 0),
                visits: vs.length, buyPct: vs.length ? Math.round((bb / vs.length) * 100) : 0,
                avg: vs.length ? vs.reduce((t, v) => t + (v.duration_sec || 0), 0) / vs.length : 0,
                sent: rs.some((r) => r.day === d.day),
            };
        });
    }
    note(id) {
        return this.state.notes[id] || null;
    }
    async openThread(th) {
        const o = this.state.openT === th.id;
        this.state.openT = o ? null : th.id;
        this.state.reply = "";
        if (!o && th.unread) {
            await api(this.orm, "admin_thread_read", th.id);
            await this.load();
        }
    }
    async sendReply(th) {
        if (!this.state.reply.trim()) {
            this.toast(_t("Write a reply"), "warning");
            return;
        }
        if (await this.act("admin_reply_thread", [th.id, this.state.reply], _t("Your reply was sent"))) {
            this.state.reply = "";
        }
    }
    async toggleThread(th) {
        await api(this.orm, "admin_thread_toggle", th.id);
        await this.load();
    }
}

// ---------------------------------------------------------------- sales and targets
class Sales extends AdminBase {
    static template = "navitta_ops.AdminSales";
    static components = { EmpBoard, TargetEditor, TargetFile, DayEdit, DayChanges };
    setup() {
        super.setup();
        this.state = useState({ d: null, edit: null, editRows: null, dayEdit: null, changes: null });
        this.watchFilter = true;
        onWillStart(() => this.load());
    }
    get year() {
        return +this.props.f.to.slice(0, 4);
    }
    get range() {
        const f = this.props.f;
        return { custom: true, m: +f.from.slice(5, 7), from: f.from, to: f.to, mFrom: f.from, mTo: f.to, curM: +today().slice(5, 7) };
    }
    async load() {
        const r = this.range;
        const my = (this.req = (this.req || 0) + 1);
        const d = await api(this.orm, "admin_sales", this.br || false, r.from, r.to, this.year, r.mFrom, r.mTo, this.props.f.wd);
        if (d.error || my !== this.req) {
            return;
        }
        setClock(d.settings.tz);
        this.state.d = d;
    }
    vat(x) {
        const rate = this.state.d.settings.vat_rate || 0;
        return (x * rate) / (100 + rate);
    }
    get rows() {
        return this.state.d.summary.filter((r) => !this.br || r.branch_id === this.br);
    }
    tot(k) {
        return this.rows.reduce((s, r) => s + +(r[k] || 0), 0);
    }
    get mx() {
        return Math.max(1, ...this.state.d.monthly.map((x) => Math.max(+x.target, +x.sales)));
    }
    month(i) {
        return this.state.d.monthly.find((y) => +y.month === i + 1) || { target: 0, sales: 0, target_to_date: 0 };
    }
    get dayRows() {
        const byDay = {};
        for (const r of this.state.d.daily) {
            const x = (byDay[r.day] ||= { day: r.day, target: 0, sales: 0, invoices: 0, items: 0, returns: 0, returns_amount: 0, cash: 0, card: 0,
                tax: 0, has: false, closed_by: r.closed_by, edited: false, changed: 0, checked: false, diff: 0, notes: [] });
            for (const k of ["target", "sales", "invoices", "items", "returns", "returns_amount", "cash", "card", "tax"]) {
                x[k] += +(r[k] || 0);
            }
            if (r.sales !== null && r.sales !== false) {
                x.has = true;
            }
            x.edited = x.edited || r.edited;
            x.changed += r.changed || 0;
            // counted cash + card (daily report) minus the Point of Sale's, once the report is in
            if (r.check) {
                x.checked = true;
                x.diff += (r.check.cash + r.check.card) - (r.check.sys_cash + r.check.sys_card);
                const n = [r.check.cash_note && "💵 " + r.check.cash_note, r.check.card_note && "💳 " + r.check.card_note].filter(Boolean).join(" · ");
                if (n) {
                    x.notes.push(this.br ? n : this.bName(r.branch_id) + ": " + n);
                }
            }
        }
        return Object.values(byDay).sort((a, b) => (a.day < b.day ? -1 : 1));
    }
    diffCell(x) {
        if (!x.checked) {
            return { cls: "", text: "—", title: "" };
        }
        if (Math.round(x.diff) === 0) {
            return { cls: "good-t", text: "✓", title: "" };
        }
        return { cls: "bad-t", text: (x.diff > 0 ? "+" : "−") + f0(Math.abs(x.diff)) + (x.notes.length ? " 💬" : ""),
            title: x.notes.join("\n") || _t("No reason") };
    }
    async daySaved() {
        this.state.dayEdit = null;
        await this.load();
    }
    async openEdit(id) {
        const rows = await api(this.orm, "admin_target_rows", id);
        this.state.editRows = rows.error ? [] : rows.rows;
        this.state.edit = { id, name: this.bName(id) };
    }
    async saved() {
        this.state.edit = null;
        await this.load();
    }
}

// ---------------------------------------------------------------- requests, expenses, supplies
class Requests extends AdminBase {
    static template = "navitta_ops.AdminRequests";
    static components = { PhotoLink, ShipBuilder, ShipLog };
    setup() {
        super.setup();
        this.state = useState({ d: null, kind: "all", notes: {}, open: null });
        onWillStart(() => this.load());
        onMounted(() => (this.timer = setInterval(() => this.load(), 60000)));
        onWillUnmount(() => clearInterval(this.timer));
    }
    async load() {
        const d = await api(this.orm, "admin_requests");
        if (!d.error) {
            setClock(d.settings.tz);
            this.state.d = d;
        }
    }
    inB(b) {
        return !this.br || b === this.br;
    }
    nm(id) {
        return (this.state.d.employees.find((e) => e.id === id) || {}).name || "—";
    }
    get mins() {
        const st = this.state.d.settings;
        return { roll_invoice: st.min_roll_invoice, roll_card: st.min_roll_card, bags: st.min_bags };
    }
    bal(b, k) {
        return ((this.state.d.stock[String(b)] || {})[k] || {}).balance || 0;
    }
    get v() {
        const d = this.state.d, s = this.state;
        const reqs = d.requests.filter((r) => this.inB(r.branch_id) && (s.kind === "all" || s.kind === r.kind));
        return {
            reqs, live: reqs.filter((r) => ["pending", "approved", "shipped"].includes(r.state)),
            done: reqs.filter((r) => ["received", "rejected"].includes(r.state)).slice(0, 15),
            pendX: d.expenses.filter((x) => x.state === "pending" && this.inB(x.branch_id)),
            pendTotal: d.expenses.filter((x) => x.state === "pending" && this.inB(x.branch_id)).reduce((s, x) => s + Number(x.amount), 0),
            lowB: d.branches.filter((b) => Object.keys(this.mins).some((k) => this.bal(b.id, k) < this.mins[k])),
            showReq: ["all", "goods", "supply"].includes(s.kind), showExp: ["all", "expense"].includes(s.kind), showDef: ["all", "defect"].includes(s.kind),
            ships: d.shipments.filter((x) => this.inB(x.branch_id)).slice(0, 8),
            defOpen: d.defects.filter((x) => x.state === "pending" && this.inB(x.branch_id)),
            defDone: d.defects.filter((x) => x.state !== "pending" && this.inB(x.branch_id)).slice(0, 10),
        };
    }
    get bNameFn() {
        return (id) => this.bName(id);
    }
    defWhat(x) {
        return jn([x.item_no, x.model !== x.item_no && x.model, x.color, x.size && TX.size(x.size)]) + (x.qty > 1 ? " × " + x.qty : "");
    }
    decideDefect(x, ok) {
        const note = (this.state.notes[x.id] || "").trim();
        if (!ok && !note) {
            this.toast(TX.rejectReason, "warning");
            return;
        }
        this.act("admin_defect_decide", [x.id, ok, note || null], ok ? TX.defectOk : TX.defectNo);
    }
    branchExp(b) {
        const d = this.state.d, m0 = d.month_start;
        const mine = d.expenses.filter((x) => x.branch_id === b.id && x.day >= m0);
        const live = mine.filter((x) => x.state !== "rejected");
        const total = live.reduce((s, x) => s + +x.amount, 0);
        const prevT = d.expenses.filter((x) => x.branch_id === b.id && x.day < m0 && x.state !== "rejected").reduce((s, x) => s + +x.amount, 0);
        const cats = {};
        live.forEach((x) => (cats[x.category] = (cats[x.category] || { label: x.category_label, v: 0 })) && (cats[x.category].v += +x.amount));
        return { mine, total, prevT, cats: Object.entries(cats).sort((a, c) => c[1].v - a[1].v), cash: d.cash[String(b.id)] || {} };
    }
    csv(b) {
        const rows = this.branchExp(b).mine;
        downloadCsv(`expenses-${b.code}-${this.state.d.month_start.slice(0, 7)}.csv`, [[_t("Date"), _t("Type"), _t("Details"), _t("Employee"), _t("Amount"), _t("Status")],
            ...rows.map((x) => [x.day, x.category_label, x.details, x.employee, x.amount, EXST[x.state][1]])]);
    }
    note(id) {
        return this.state.notes[id] || null;
    }
}

// ---------------------------------------------------------------- lost sales
class Lost extends AdminBase {
    static template = "navitta_ops.AdminLost";
    setup() {
        super.setup();
        this.state = useState({ d: null });
        this.LOST = LOST;
        this.watchFilter = true;
        onWillStart(() => this.load());
    }
    async load() {
        const f = this.props.f;
        const my = (this.req = (this.req || 0) + 1);
        const d = await api(this.orm, "admin_lost", this.br || false, f.from, f.to, f.wd);
        if (!d.error && my === this.req) {
            this.state.d = d;
        }
    }
    get v() {
        const rows = this.state.d.rows, lost = rows.filter((r) => r.reason !== "browsing");
        const mfg = rows.filter((r) => r.reason === "manufacture");
        const top = Object.values(rows.filter((r) => OUTOF.includes(r.reason) && r.model).reduce((a, r) => {
            const k = r.item_no || r.model;
            const x = (a[k] ||= { k, item_no: r.item_no, model: r.model, n: 0, value: 0, sizes: {}, colors: {}, br: new Set() });
            x.n++;
            x.value += +(r.price || 0);
            x.br.add(r.branch_id);
            if (r.size) x.sizes[r.size] = (x.sizes[r.size] || 0) + 1;
            if (r.color) x.colors[r.color] = (x.colors[r.color] || 0) + 1;
            return a;
        }, {})).sort((a, b) => b.n - a.n).slice(0, 15);
        const next = Object.values(mfg.reduce((a, r) => {
            const k = [r.item_no || r.model, r.color, r.size].join("|");
            const x = (a[k] ||= { k, item_no: r.item_no, model: r.model, color: r.color, size: r.size, n: 0, br: new Set(), notes: [] });
            x.n++;
            x.br.add(r.branch_id);
            if (r.note && x.notes.length < 3) x.notes.push(r.note);
            return a;
        }, {})).sort((a, b) => b.n - a.n);
        return {
            rows, lost, value: lost.reduce((s, r) => s + +(r.price || 0), 0), mfg, top, next,
            oos: rows.filter((r) => OUTOF.includes(r.reason)).length,
            reasons: LOST.map((r) => ({ r, n: rows.filter((x) => x.reason === r).length })).filter((x) => x.n),
        };
    }
    tops(o) {
        return Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, n]) => `${vlabel(k)} (${n})`).join(" · ");
    }
    brs(set) {
        return [...set].map((id) => this.bName(id)).join(SEP);
    }
    async contacted(w) {
        await this.act("admin_lost_contacted", [w.id], _t("Done"));
    }
    dlNext(v) {
        downloadCsv(`next-order-${this.props.f.from}-${this.props.f.to}.csv`, [[_t("Item no."), _t("Model"), _t("Color"), _t("Size"), _t("Times"), _t("Branches"), _t("Notes")],
            ...v.next.map((x) => [x.item_no, x.model, vlabel(x.color), x.size, x.n, this.brs(x.br), x.notes.join(" | ")])]);
    }
    dlAll(v) {
        downloadCsv(`lost-sales-${this.props.f.from}-${this.props.f.to}.csv`, [[_t("Date"), _t("Branch"), _t("Employee"), _t("Reason"), _t("Item no."), _t("Model"), _t("Color"), _t("Size"), _t("Price"), _t("Note")],
            ...v.rows.map((r) => [r.day, this.bName(r.branch_id), r.employee, LOSTL[r.reason] || r.reason, r.item_no, r.model, vlabel(r.color), r.size, r.price, r.note])]);
    }
}

// ---------------------------------------------------------------- day log
class Day extends AdminBase {
    static template = "navitta_ops.AdminDay";
    static components = { PhotoLink, DayChanges };
    setup() {
        super.setup();
        this.state = useState({ d: null, day: today(), changes: null });
        onWillStart(() => this.load());
    }
    async load() {
        const d = await api(this.orm, "admin_day", this.state.day);
        if (!d.error) {
            setClock();
            this.state.d = d;
        }
    }
    async setDay(v) {
        if (!v) return;
        this.state.day = v;
        await this.load();
    }
    get shownBranches() {
        return this.state.d.branches.filter((b) => !this.br || b.id === this.br);
    }
    block(b) {
        const d = this.state.d;
        const E = Object.fromEntries(d.employees.map((e) => [e.id, e]));
        const r = d.reports.find((x) => x.branch_id === b.id);
        const vs = d.visits.filter((v) => v.branch_id === b.id), buys = vs.filter((v) => v.outcomes.includes("buy")).length;
        const emps = d.employees.filter((e) => e.branch_id === b.id);
        const ev = r ? r.events.map((e) => [e.level, e.title + ": " + e.text]) : [];
        if (r) {
            r.exch_labels.forEach((e) => ev.push(["neutral", _t("Exchange (%(reason)s): %(detail)s · sold by %(sold)s · exchanged by %(by)s",
                { reason: e.reason, detail: e.detail, sold: e.soldBy, by: e.by })]));
        }
        vs.filter((v) => v.lost_color || v.lost_size).forEach((v) => ev.push(["neutral", _t("Lost sales: %s",
            [v.lost_model, vlabel(v.lost_color), v.lost_size && _t("Size %s", v.lost_size)].filter(Boolean).join(" · "))]));
        d.cases.filter((c) => (E[c.employee_id] || {}).branch_id === b.id).forEach((c) => ev.push([c.state === "rejected" ? "bad" : "warn",
            _t("%(kind)s %(name)s %(min)s min", { kind: c.kind === "break" ? _t("Break overrun") : _t("Late arrival"), name: (E[c.employee_id] || {}).name, min: c.minutes })
            + (c.reason ? " · " + _t("Reason: %s", c.reason) : "") + (c.penalty ? " · " + c.penalty : "")]));
        const rows = emps.map((e) => {
            const a = d.attendance.find((y) => y.employee_id === e.id);
            const bm = Math.ceil(d.breaks.filter((y) => y.employee_id === e.id && y.ended_at).reduce((t, y) => t + (Date.parse(y.ended_at) - Date.parse(y.started_at)) / 6e4, 0));
            const st = a ? ["good", _t("Attended")] : d.off[e.id] ? ["neutral", _t("🌙 Day off")] : ["bad", _t("Not checked in")];
            return { e, a, bm, st };
        });
        // today's float (start and end of the shift) and today's tasks
        const daily = (d.daily || {})[b.id] || {};
        const fl = daily.float || {}, T = daily.tasks || null;
        const floats = [["start", _t("Start of shift")], ["end", _t("End of shift")]].map(([k, label]) => ({ k, label, f: fl[k] || null }));
        floats.filter((x) => x.f && x.f.state === "short").forEach((x) => ev.unshift(["bad", _t("Float short (%(when)s): %(a)s SAR of %(x)s SAR",
            { when: x.label, a: f0(x.f.amount), x: f0(x.f.expected) }) + (x.f.note ? " · " + x.f.note : "")]));
        // end of the shift (26.3): the counted cash / card against the Point of Sale, with the employee's reasons
        const chk = r && r.check;
        if (chk) {
            [["cash", _t("💵 Cash")], ["card", _t("💳 Card")]].filter(([k]) => chk[k + "_diff"]).forEach(([k, label]) => ev.unshift(["bad",
                _t("%(what)s differs from the Point of Sale: counted %(c)s · Point of Sale %(o)s · difference %(d)s", {
                    what: label, c: f0(chk[k]), o: f0(chk["sys_" + k]), d: (chk[k + "_diff"] > 0 ? "+" : "−") + f0(Math.abs(chk[k + "_diff"])) })
                + " · " + (chk[k + "_note"] ? _t("Reason: %s", chk[k + "_note"]) : _t("No reason"))]));
        }
        const autos = vs.filter((v) => v.auto);
        if (autos.length) {
            ev.push(["warn", _t("🧾 %(n)s invoices sold without the counter (%(a)s not confirmed)", { n: autos.length, a: autos.filter((v) => !v.auto_ack).length })]);
        }
        const timed = vs.filter((v) => !v.auto);
        return {
            r, vs, buyPct: vs.length ? Math.round((buys / vs.length) * 100) + "%" : "—",
            avg: timed.length ? mmss(timed.reduce((t, v) => t + (v.duration_sec || 0), 0) / timed.length) : "—", ev, rows, floats, T,
            mk: (d.marks || {})[b.id] || {}, chk,
        };
    }
    get phases() {
        return phases();
    }
    floatState(f) {
        return !f ? ["warn", _t("Not counted")] : f.state === "ok" ? ["good", "✓ " + _t("Complete")] : f.state === "pending"
            ? ["neutral", _t("Waiting for confirmation")] : ["bad", "⚠️ " + _t("Short") + (f.note ? ": " + f.note : "")];
    }
    floatWho(f, k) {
        return f && f[k + "_by"] ? `${f[k + "_name"] || "—"} ${clock(f[k + "_at"])}` : k === "confirmed" && f && f.confirm_needed ? "⏳" : "—";
    }
    tasksDone(T) {
        return _t("%(n)s of %(m)s done", { n: T.tasks.filter((x) => x.done_at).length, m: T.tasks.length });
    }
    past(T, ph) {
        return T.now > T.windows[ph][1];
    }
    taskOf(T, ph) {
        return T.tasks.filter((x) => x.phase === ph);
    }
    groupOf(x) {
        return groupOf(x);
    }
    taskLine(x) {
        return (x.who === "both" ? `${x.for_name}: ` : "") + x.title;
    }
    doneLine(x) {
        return "✓ " + (x.done_name || "") + " " + clock(x.done_at);
    }
    async reopen(x, b) {
        if (!window.confirm(_t("Re-open “%(t)s” at %(b)s? It shows as not done again.", { t: x.title, b: b.name }))) {
            return;
        }
        await this.act("admin_task_reopen", [x.done_id], _t("Task re-opened"));
    }
}

// ---------------------------------------------------------------- branches compared
class Compare extends AdminBase {
    static template = "navitta_ops.AdminCompare";
    static components = { EmpBoard };
    setup() {
        super.setup();
        this.state = useState({ d: null, sort: "sales" });
        this.watchFilter = true;
        onWillStart(() => this.load());
    }
    async load() {
        const f = this.props.f;
        const my = (this.req = (this.req || 0) + 1);
        const d = await api(this.orm, "admin_compare", f.from, f.to, f.wd);
        if (!d.error && my === this.req) {
            setClock(d.settings.tz);
            this.state.d = d;
        }
    }
    get rows() {
        const k = this.state.sort;
        return this.state.d.rows.filter((r) => !this.br || r.branch_id === this.br)
            .sort((a, b) => (+(b[k] ?? -1e9)) - (+(a[k] ?? -1e9)));
    }
    get total() {
        const rows = this.rows, t = {};
        for (const k of ["target", "sales", "tax", "net", "invoices", "items", "returns", "returns_amount", "cash", "card", "visits", "bought", "report_sales"]) {
            t[k] = rows.reduce((s, r) => s + (+r[k] || 0), 0);
        }
        t.achieved = t.target ? +((t.sales * 100) / t.target).toFixed(1) : null;
        t.avg_invoice = t.invoices ? t.sales / t.invoices : 0;
        t.items_per_invoice = t.invoices ? t.items / t.invoices : 0;
        t.conversion = t.visits ? +((t.invoices * 100) / t.visits).toFixed(1) : null;
        t.counter_gap = t.invoices - t.bought;
        return t;
    }
    get best() {
        return Math.max(1, ...this.rows.map((r) => +r.sales || 0));
    }
    exportCsv() {
        const head = [_t("Branch"), _t("Target"), _t("Sales"), _t("Achieved %"), _t("VAT"), _t("Net"), _t("Invoices"), _t("Average invoice"), _t("Items"), _t("Items/invoice"),
            _t("Returns (items)"), _t("Returns value"), _t("Cash"), _t("Card"), _t("Counter customers"), _t("Bought (counter)"), _t("Conversion %"),
            _t("Invoices vs counter gap"), _t("Report sales"), _t("Report vs system gap")];
        const line = (r) => [r.name, r.target, r.sales, r.achieved ?? "", r.tax, r.net, r.invoices, r.avg_invoice, r.items, r.items_per_invoice,
            r.returns, r.returns_amount, r.cash, r.card, r.visits, r.bought, r.conversion ?? "", r.counter_gap, r.report_sales, r.report_gap ?? ""];
        downloadCsv(`branches-${this.props.f.from}-${this.props.f.to}.csv`, [head, ...this.rows.map(line), line(Object.assign({ name: _t("Total") }, this.total))]);
    }
}

// ---------------------------------------------------------------- rules and violations
const KIND = { late: () => _t("Late arrival"), break: () => _t("Break overrun") };

class Rules extends AdminBase {
    static template = "navitta_ops.AdminRules";
    static components = { CaseDecide, Dots };
    setup() {
        super.setup();
        this.state = useState({ d: null, editT: null, nr: "", edR: {} });
        onWillStart(() => this.load());
    }
    async load() {
        const d = await api(this.orm, "admin_rules");
        if (d.error) {
            return;
        }
        setClock(d.settings.tz);
        d.E = Object.fromEntries(d.employees.map((e) => [e.id, e]));
        this.state.d = d;
    }
    inB(empId) {
        const e = this.state.d.E[empId];
        return !this.br || (e && e.branch_id === this.br);
    }
    eName(id) {
        return (this.state.d.E[id] || {}).name || "—";
    }
    eBranch(id) {
        return this.bName((this.state.d.E[id] || {}).branch_id);
    }
    kind(c) {
        return KIND[c.kind] ? KIND[c.kind]() : c.kind;
    }
    get v() {
        const d = this.state.d;
        const month = d.cases.filter((c) => this.inB(c.employee_id));
        const emps = d.employees.filter((e) => !this.br || e.branch_id === this.br);
        return {
            month, emps, pens: month.filter((c) => c.state === "rejected"),
            review: d.review.filter((c) => this.inB(c.employee_id)),
            max: d.settings.chances,
        };
    }
    used(empId) {
        return this.v.month.filter((c) => c.employee_id === empId && c.state === "forgiven").length;
    }
    row(e) {
        const mine = this.v.month.filter((c) => c.employee_id === e.id && c.state !== "excused" && c.state !== "edited");
        return {
            late: mine.filter((c) => c.kind === "late").length, brk: mine.filter((c) => c.kind === "break").length,
            pens: mine.filter((c) => c.state === "rejected").length,
        };
    }
    ack(e) {
        return this.state.d.acks[e.id] || {};
    }
    // texts used in expressions
    get resetNote() {
        return _t("Chances reset to %s at the start of each month", this.state.d.settings.chances);
    }
    min(n) {
        return _t("%s min", n);
    }
    perMonth(n) {
        return _t("%s per month", n);
    }
    chancesStep(n) {
        return _t("② Chances (%s per month)", n);
    }
    chancesText(n) {
        return _t("The first %s violations in a month count as a “chance”, with no penalty.", n);
    }
    nAwait(n) {
        return _t("%s awaiting your decision", n);
    }
    occ(n) {
        return _t("(occurrence %s)", n);
    }
    pensSub(c) {
        return this.kind(c) + " " + this.min(c.minutes);
    }
    get noPending() {
        return _t("No pending decisions");
    }
    get rulePh() {
        return _t("New rule, e.g. No mobile phone while serving a customer");
    }
    // penalty table
    editTiers() {
        this.state.editT = this.state.d.tiers.map((x) => Object.assign({}, x));
    }
    async saveTiers() {
        if (await this.act("admin_tiers_save", [this.state.editT], _t("Penalty table saved"))) {
            this.state.editT = null;
        }
    }
    // branch rules
    async ruleSave(id, body) {
        if (!(body || "").trim()) {
            return this.toast(_t("Write the rule"), "warning");
        }
        if (await this.act("admin_rule_save", [id || false, body], _t("Saved; the employees sign again"))) {
            if (id) {
                delete this.state.edR[id];
            } else {
                this.state.nr = "";
            }
        }
    }
    async ruleDel(r) {
        if (window.confirm(_t("Delete this rule?") + "\n\n" + r.body)) {
            await this.act("admin_rule_delete", [r.id], _t("Rule deleted"));
        }
    }
    async settle(c) {
        await this.act("admin_case_settle", [c.id, !c.settled_at], c.settled_at ? _t("Deduction undone") : _t("Marked as deducted"));
    }
    csv() {
        const v = this.v;
        downloadCsv(`penalties-${this.state.d.month.slice(0, 7)}.csv`, [
            [_t("Employee"), _t("Branch"), _t("Date"), _t("Violation"), _t("Minutes"), _t("Penalty"), _t("Occurrence"), _t("Signed"), _t("Deducted")],
            ...v.pens.map((c) => [this.eName(c.employee_id), this.eBranch(c.employee_id), c.day, this.kind(c), c.minutes, c.penalty,
                c.penalty_occ, c.ack_at ? "✓" : "", c.settled_at ? "✓" : ""]),
        ]);
    }
}

// ---------------------------------------------------------------- root
export class AdminApp extends Component {
    static template = "navitta_ops.AdminApp";
    static components = { Dashboard, Sales, Compare, Requests, Lost, Day, Rules, FilterBar };
    static props = { ...standardActionServiceProps };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ tab: (this.props.action.params && this.props.action.params.tab) || "dashboard", branches: [] });
        this.f = useState(loadFilter());
        this.DIR = DIR;
        this.LANG = LANG;
        this.tabs = [["dashboard", _t("Dashboard")], ["compare", _t("📊 Branch comparison")], ["sales", _t("Sales and targets")], ["requests", _t("📦 Branch requests")],
            ["lost", _t("💸 Lost sales")], ["rules", _t("📜 Rules and violations")], ["day", _t("Daily log")]];
        onWillStart(async () => {
            const d = await api(this.orm, "admin_filters");
            if (!d.error) {
                setClock(d.settings.tz);
                this.state.branches = d.branches;
                Object.assign(this.f, loadFilter());       // dates of the presets in the branches' time zone
            }
        });
    }
    get withDates() {
        return !["requests", "day", "rules"].includes(this.state.tab);
    }
}

registry.category("actions").add("navitta_admin_app", AdminApp);
