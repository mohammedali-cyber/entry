/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    EXPICON, EXST, LOST, LOSTL, MONTHS, OUTOF, RQST, SUP, WD, addDays, ago, api, clock, dTxt, days, dmy, downloadCsv, f0, h12, longDate,
    mmss, now, pc, setClock, today, weekday, jn,
} from "../common/utils";
import { DailyQuote, EmpBoard, NvModal, PhotoLink, TargetEditor } from "../common/components";

const LEAVEADM = { pending: "بانتظار موافقتك", cover: "بانتظار الزميلة", cover_no: "اعتذرت الزميلة", approved: "تمت الموافقة", rejected: "مرفوضة" };
const NOTIF = { sent: ["good", "أُرسل"], no_email: ["bad", "لا يوجد إيميل"], exception: ["bad", "فشل الإرسال"], outgoing: ["info", "بانتظار الإرسال"] };
const H = { jn, f0, mmss, clock, longDate, h12, dTxt, days, ago, pc, dmy, weekday, addDays, LOSTL, SUP, EXPICON, RQST, EXST, WD, MONTHS, LEAVEADM, NOTIF };

// ---------------------------------------------------------------- shared period filter (every tab)
const FKEY = "navitta_admin_filter";
const PRESETS = [["today", "اليوم"], ["yesterday", "أمس"], ["last7", "آخر 7 أيام"], ["month", "هذا الشهر"], ["prevmonth", "الشهر الماضي"],
    ["last30", "آخر 30 يوم"], ["year", "من بداية السنة"], ["custom", "📅 مخصص"]];
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
    static components = { DailyQuote, EmpBoard, NvModal, PhotoLink };
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
            return this.action.doAction({ type: "ir.actions.act_window", res_model: "navitta.admin.password.wizard", views: [[false, "form"]], target: "new", name: "كلمة سر الإدارة" }, reload);
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
            ? [["الفرع", "الموظفة", "الرمز السري"], ...o.rows.map((r) => [r.branch, r.name, r.pin])]
            : [["الفرع", "اسم الدخول", "كلمة المرور"], ...o.rows.map((r) => [r.branch, r.login, r.password])];
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
    used(emp) {
        return this.state.d.month_cases.filter((c) => c.employee_id === emp && c.state === "forgiven").length;
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
            st = d.off[e.id] ? ["neutral", lv ? "🏖️ إجازة" : "🌙 إجازة" + (e.weekly_off ? ` (${WD[+e.weekly_off]})` : "")] : ["bad", "لم تسجل حضور"];
        } else if (a.check_out) {
            st = ["neutral", "انصرفت"];
        } else if (late) {
            st = ["warn", `متأخرة ${late.minutes} د`];
        } else {
            st = ["good", "حاضرة"];
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
            this.toast("اكتب الرد", "warning");
            return;
        }
        if (await this.act("admin_reply_thread", [th.id, this.state.reply], "تم إرسال ردك")) {
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
    static components = { EmpBoard, TargetEditor };
    setup() {
        super.setup();
        this.state = useState({ d: null, edit: null, editRows: null });
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
            const x = (byDay[r.day] ||= { day: r.day, target: 0, sales: 0, invoices: 0, items: 0, returns: 0, cash: 0, card: 0, tax: 0, has: false, closed_by: r.closed_by });
            for (const k of ["target", "sales", "invoices", "items", "returns", "cash", "card", "tax"]) {
                x[k] += +(r[k] || 0);
            }
            if (r.sales !== null && r.sales !== false) {
                x.has = true;
            }
        }
        return Object.values(byDay).sort((a, b) => (a.day < b.day ? -1 : 1));
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
    static components = { PhotoLink };
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
            showReq: ["all", "goods", "supply"].includes(s.kind), showExp: ["all", "expense"].includes(s.kind),
        };
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
        downloadCsv(`expenses-${b.code}-${this.state.d.month_start.slice(0, 7)}.csv`, [["التاريخ", "النوع", "التفاصيل", "الموظفة", "المبلغ", "الحالة"],
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
        return Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, n]) => `${k} (${n})`).join(" · ");
    }
    brs(set) {
        return [...set].map((id) => this.bName(id)).join("، ");
    }
    async contacted(w) {
        await this.act("admin_lost_contacted", [w.id], "تم");
    }
    dlNext(v) {
        downloadCsv(`next-order-${this.props.f.from}-${this.props.f.to}.csv`, [["رقم المؤسسة", "الموديل", "اللون", "المقاس", "كم مرة", "الفروع", "الشرح"],
            ...v.next.map((x) => [x.item_no, x.model, x.color, x.size, x.n, this.brs(x.br), x.notes.join(" | ")])]);
    }
    dlAll(v) {
        downloadCsv(`lost-sales-${this.props.f.from}-${this.props.f.to}.csv`, [["التاريخ", "الفرع", "الموظفة", "السبب", "رقم المؤسسة", "الموديل", "اللون", "المقاس", "السعر", "ملاحظة"],
            ...v.rows.map((r) => [r.day, this.bName(r.branch_id), r.employee, LOSTL[r.reason] || r.reason, r.item_no, r.model, r.color, r.size, r.price, r.note])]);
    }
}

// ---------------------------------------------------------------- day log
class Day extends AdminBase {
    static template = "navitta_ops.AdminDay";
    setup() {
        super.setup();
        this.state = useState({ d: null, day: today() });
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
            r.exch_labels.forEach((e) => ev.push(["neutral", `استبدال (${e.reason}): ${e.detail} · باعت ${e.soldBy} · استبدلت ${e.by}`]));
        }
        vs.filter((v) => v.lost_color || v.lost_size).forEach((v) => ev.push(["neutral", "مبيعات ضائعة: " + [v.lost_model, v.lost_color, v.lost_size && "مقاس " + v.lost_size].filter(Boolean).join(" · ")]));
        d.cases.filter((c) => (E[c.employee_id] || {}).branch_id === b.id).forEach((c) => ev.push([c.state === "rejected" ? "bad" : "warn",
            `${c.kind === "break" ? "تجاوز استراحة" : "تأخير"} ${(E[c.employee_id] || {}).name} ${c.minutes} د${c.reason ? " · السبب: " + c.reason : ""}${c.penalty ? " · " + c.penalty : ""}`]));
        const rows = emps.map((e) => {
            const a = d.attendance.find((y) => y.employee_id === e.id);
            const bm = Math.ceil(d.breaks.filter((y) => y.employee_id === e.id && y.ended_at).reduce((t, y) => t + (Date.parse(y.ended_at) - Date.parse(y.started_at)) / 6e4, 0));
            const st = a ? ["good", "حضرت"] : d.off[e.id] ? ["neutral", "🌙 إجازة"] : ["bad", "لم تسجل حضور"];
            return { e, a, bm, st };
        });
        return {
            r, vs, buyPct: vs.length ? Math.round((buys / vs.length) * 100) + "%" : "—",
            avg: vs.length ? mmss(vs.reduce((t, v) => t + (v.duration_sec || 0), 0) / vs.length) : "—", ev, rows,
        };
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
        const head = ["الفرع", "التارقت", "المبيعات", "التحقق %", "الضريبة", "الصافي", "الفواتير", "متوسط الفاتورة", "القطع", "قطع/فاتورة",
            "المرتجع (قطع)", "قيمة المرتجع", "نقدي", "شبكة", "عميلات العدّاد", "اشترت (العدّاد)", "التحويل %", "فرق الفواتير عن العدّاد", "مبيعات التقارير", "فرق التقرير عن النظام"];
        const line = (r) => [r.name, r.target, r.sales, r.achieved ?? "", r.tax, r.net, r.invoices, r.avg_invoice, r.items, r.items_per_invoice,
            r.returns, r.returns_amount, r.cash, r.card, r.visits, r.bought, r.conversion ?? "", r.counter_gap, r.report_sales, r.report_gap ?? ""];
        downloadCsv(`branches-${this.props.f.from}-${this.props.f.to}.csv`, [head, ...this.rows.map(line), line(Object.assign({ name: "الإجمالي" }, this.total))]);
    }
}

// ---------------------------------------------------------------- root
export class AdminApp extends Component {
    static template = "navitta_ops.AdminApp";
    static components = { Dashboard, Sales, Compare, Requests, Lost, Day, FilterBar };
    static props = { ...standardActionServiceProps };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ tab: (this.props.action.params && this.props.action.params.tab) || "dashboard", branches: [] });
        this.f = useState(loadFilter());
        this.tabs = [["dashboard", "لوحة الإدارة"], ["compare", "📊 مقارنة الفروع"], ["sales", "المبيعات والتارقت"], ["requests", "📦 طلبات الفروع"],
            ["lost", "💸 المبيعات الضائعة"], ["day", "سجل الأيام"]];
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
        return !["requests", "day"].includes(this.state.tab);
    }
}

registry.category("actions").add("navitta_admin_app", AdminApp);
