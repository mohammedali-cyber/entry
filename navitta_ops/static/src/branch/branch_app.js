/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    CASEST, COLORS, EXPICON, LEAVEST, LOST, LOSTL, NOBUY, LOSTFULL, OUTOF, QBREAK, QLATE, RQST, EXST, SAT, SIZES, SUP, TRIED, TYPES,
    CTX, addDays, api, clock, dTxt, days, f0, h12, jn, longDate, minuteNow, mmss, now, photoUrl, setClock, sizesOf, swatch, today, ago, icon, dcsText,
} from "../common/utils";
import {
    DailyQuote, ModelPicker, ModelSearch, NvModal, PhotoLink, PhotoPicker, PinPad, SignModal, TargetBar,
} from "../common/components";

const MFG = "manufacture", OTHER = "__other";
const newDraft = (buy = false) => ({
    buy, exch: false, no: false, reason: "", other: "", note: "", m: null, manual: "", color: "", colorOther: "", size: "", sizeOther: "",
    mfgNote: "", call: false, phone: "", cname: "", req: false, err: "",
});
const H = { jn, f0, minuteNow, mmss, clock, longDate, h12, dTxt, days, ago, photoUrl, swatch, icon, dcsText, LOSTL, LOST, LOSTFULL, SUP, EXPICON, RQST, EXST, CASEST, LEAVEST };

// ---------------------------------------------------------------- visit outcome (bought / exchange / didn't buy + lost sale)
class Outcome extends Component {
    static template = "navitta_ops.Outcome";
    static components = { ModelPicker };
    static props = { x: Object };
    setup() {
        this.x = useState(this.props.x);
        Object.assign(this, H);
        this.OUTOF = OUTOF;
        this.MFG = MFG;
        this.OTHER = OTHER;
    }
    set(patch) {
        Object.assign(this.x, { err: "" }, patch);
    }
    get m() {
        return this.x.m;
    }
    get cols() {
        return this.m ? this.m.colors : [];
    }
    get sizes() {
        const m = this.m, x = this.x;
        if (!m) {
            return [];
        }
        if (x.color && x.color !== OTHER) {
            const c = m.colors.find((c) => c.color === x.color);
            return c ? c.sizes : [];
        }
        return sizesOf(m);
    }
    get bag() {
        return !!this.m && !sizesOf(this.m).length;
    }
    custom(k) {
        const x = this.x;
        this.set({ [k]: x[k] === OTHER ? "" : OTHER, reason: x[k] === OTHER ? x.reason : MFG });
    }
    pick(mm, it) {
        this.set({ m: mm, color: (it && it.color) || "", size: (it && it.size) || "" });
    }
}

class EditReq extends Component {
    static template = "navitta_ops.EditReq";
    static props = { v: Object, done: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const s = this.props.v.duration_sec || 0;
        this.state = useState({ m: Math.floor(s / 60), s: s % 60, why: "", err: "" });
    }
    async send() {
        const st = this.state;
        const ns = +st.m * 60 + Math.min(59, +st.s);
        if (!st.why.trim()) {
            st.err = "اذكري سبب التعديل";
            return;
        }
        if (ns <= 0 || ns === this.props.v.duration_sec) {
            st.err = "اكتبي المدة الصحيحة، ولازم تختلف عن الحالية";
            return;
        }
        const r = await api(this.orm, "visit_edit_request", this.props.v.id, ns, st.why.trim());
        if (r.error) {
            st.err = r.message;
            return;
        }
        this.notification.add("تم إرسال طلب تعديل المدة للإدارة", { type: "success" });
        this.props.done();
    }
}

class UndoOut extends Component {
    static template = "navitta_ops.UndoOut";
    static components = { NvModal };
    static props = { emp: Object, onClose: Function, onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ pw: "", err: "" });
    }
    async go() {
        const r = await api(this.orm, "undo_checkout", this.props.emp.id, this.state.pw);
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.props.onDone();
    }
}

// ---------------------------------------------------------------- counter
class Counter extends Component {
    static template = "navitta_ops.Counter";
    static components = { DailyQuote, TargetBar, ModelSearch, NvModal, PinPad, Outcome, EditReq, UndoOut };
    static props = { goto: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, tick: 0, pin: null, drafts: {}, editFor: null, search: false, block: null, undo: null });
        Object.assign(this, H);
        this.OTHER = OTHER;
        onWillStart(() => this.load());
        onMounted(() => {
            this.t1 = setInterval(() => this.load(), 30000);
            this.t2 = setInterval(() => this.state.tick++, 1000);
        });
        onWillUnmount(() => {
            clearInterval(this.t1);
            clearInterval(this.t2);
        });
        useExternalListener(window, "keydown", (e) => {
            const d = this.state.d;
            if (!d || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || this.state.pin || this.state.search || document.querySelector(".nv-modal-bg")) {
                return;
            }
            const n = { 1: 1, "١": 1, 2: 2, "٢": 2 }[e.key];
            const emp = n && d.employees.find((x) => x.slot === n);
            if (emp && this.isIn(emp) && !this.onBreak(emp)) {
                this.startVisit(emp);
            }
        });
    }
    toast(msg, type = "info") {
        this.notification.add(msg, { type });
    }
    async load() {
        const d = await api(this.orm, "branch_state");
        if (d.error) {
            this.toast(d.message, "danger");
            return;
        }
        setClock(d.settings.tz, d.server_now);
        const open = new Set();
        for (const v of d.visits) {
            if (!v.saved) {
                open.add(String(v.id));
                if (!this.state.drafts[v.id]) {
                    this.state.drafts[v.id] = newDraft();
                }
            }
        }
        this.state.d = d;
        for (const id of Object.keys(this.state.drafts)) {
            if (!open.has(id)) {
                delete this.state.drafts[id];
            }
        }
    }
    async editDone() {
        this.state.editFor = null;
        await this.load();
    }
    // ----- per-employee helpers
    att(emp) {
        return this.state.d.attendance.find((a) => a.employee_id === emp.id);
    }
    isIn(emp) {
        const a = this.att(emp);
        return !!(a && !a.check_out);
    }
    onBreak(emp) {
        return this.state.d.breaks.find((b) => b.employee_id === emp.id && !b.ended_at);
    }
    breakLeft(emp) {
        const used = this.state.d.breaks.filter((b) => b.employee_id === emp.id)
            .reduce((t, b) => t + ((b.ended_at ? Date.parse(b.ended_at) : now()) - Date.parse(b.started_at)) / 1000, 0);
        return this.state.d.settings.break_max * 60 - used;
    }
    mine(emp) {
        return this.state.d.visits.filter((v) => v.employee_id === emp.id);
    }
    pane(emp) {
        const d = this.state.d, mine = this.mine(emp);
        const saved = mine.filter((v) => v.saved), open = mine.filter((v) => !v.saved);
        const buys = saved.filter((v) => v.outcomes.includes("buy")).length;
        const avg = saved.length ? saved.reduce((t, v) => t + (v.duration_sec || 0), 0) / saved.length : 0;
        return {
            att: this.att(emp), isIn: this.isIn(emp), onBreak: this.onBreak(emp), left: this.breakLeft(emp), open, saved,
            buys, avg, buyPct: saved.length ? Math.round((buys / saved.length) * 100) : 0,
            mySales: +(d.emp_sales[emp.id] || 0),
            replyCases: d.cases.filter((c) => c.employee_id === emp.id && c.state === "reply"),
            covers: d.covers.filter((c) => c.cover_employee_id === emp.id),
            lateBy: minuteNow() - d.settings.shift_start,
        };
    }
    secs(v) {
        return ((v.ended_at ? Date.parse(v.ended_at) : now()) - Date.parse(v.started_at)) / 1000;
    }
    dr(v) {
        return this.state.drafts[v.id];
    }
    editReq(v) {
        return this.state.d.edit_requests.find((r) => r.visit_id === v.id);
    }
    outcomeChips(v) {
        return v.outcomes.map((o) => ({
            k: o, cls: o === "buy" ? "good" : "neutral",
            text: o === "buy" ? "شراء" : o === "exchange" ? "استبدال" : o === "other" && v.other_text ? "أخرى: " + v.other_text : LOSTL[o] || o,
        }));
    }
    lostChip(v) {
        return [v.lost_model, v.lost_color, v.lost_size && "مقاس " + v.lost_size].filter(Boolean).join(" · ");
    }
    // ----- actions
    async call(method, ...args) {
        const r = await api(this.orm, method, ...args);
        if (r.error) {
            this.toast(r.message, "danger");
            return null;
        }
        return r;
    }
    async startVisit(emp) {
        if (await this.call("visit_start", emp.id)) {
            await this.load();
        }
    }
    async endVisit(v) {
        await this.call("visit_end", v.id);
        await this.load();
    }
    async delVisit(v) {
        await this.call("visit_delete", v.id);
        await this.load();
    }
    async breakStart(emp, open) {
        if (open.length) {
            this.toast("أوقفي واحفظي الزيارات المفتوحة قبل الاستراحة", "warning");
            return;
        }
        await this.call("break_start", emp.id);
        await this.load();
    }
    async breakEnd(b) {
        const r = await this.call("break_end", b.id);
        if (r) {
            this.toast(r.over ? `تجاوزتِ الاستراحة ${r.over} دقيقة، اذكري السبب من «تنبيهاتي»` : "انتهت الاستراحة", r.over ? "warning" : "success");
        }
        await this.load();
    }
    async saveVisit(v) {
        const x = this.dr(v);
        const colorCustom = x.color === OTHER, sizeCustom = x.size === OTHER;
        // a model typed by hand (not in the models file) only has the free-text color / size boxes
        const color = (colorCustom || !x.m ? x.colorOther : x.color).trim(), size = (sizeCustom || !x.m ? x.sizeOther : x.size).trim();
        const model = (x.m && x.m.model) || x.manual.trim(), r = x.reason, phone = x.phone.replace(/\D/g, "");
        let err = "";
        if (!x.buy && !x.exch && !x.no) err = "اختاري نتيجة الزيارة";
        else if (x.no && !r) err = "اختاري سبب إنها ما اشترت";
        else if (x.no && r === "other" && !x.other.trim()) err = "اكتبي السبب في خانة «أخرى»";
        else if (x.no && (OUTOF.includes(r) || r === MFG) && !model) err = "اختاري الموديل اللي طلبته العميلة";
        else if (x.no && r === "color_na" && !color) err = "اختاري اللون المطلوب";
        else if (x.no && r === "size_na" && !size) err = "اختاري المقاس المطلوب";
        else if (x.no && r === MFG && !colorCustom && !sizeCustom && !x.manual) err = "اختاري «أخرى» في اللون أو المقاس واكتبي المطلوب";
        else if (x.no && r === MFG && !x.mfgNote.trim()) err = "اشرحي وش تبي العميلة بالضبط";
        else if (x.no && OUTOF.includes(r) && x.call && !/^(05\d{8}|9665\d{8})$/.test(phone)) err = "رقم الجوال غير صحيح، اكتبيه مثل 05XXXXXXXX";
        if (err) {
            x.err = err;
            return;
        }
        const data = {
            buy: x.buy, exch: x.exch, no: x.no, reason: r, other: x.other, note: x.note, request: OUTOF.includes(r) && x.req,
            lost: x.no ? {
                item_no: (x.m && x.m.item_no) || null, model: model || null, color: color || null, size: size || null,
                price: (x.m && x.m.price) || null, custom: colorCustom || sizeCustom || (!!x.manual && r === MFG),
                note: r === MFG ? x.mfgNote.trim() : r === "other" ? x.other.trim() : null,
                contact_phone: OUTOF.includes(r) && x.call ? phone : null, contact_name: OUTOF.includes(r) && x.call ? x.cname.trim() : null,
            } : {},
        };
        const res = await api(this.orm, "visit_save", v.id, data);
        if (res.error) {
            x.err = res.message;
            return;
        }
        this.toast(x.no && x.req && OUTOF.includes(r) ? "تم حفظ الزيارة وإرسال طلب النواقص" : "تم حفظ الزيارة", "success");
        await this.load();
    }
    out(emp) {
        const open = this.mine(emp).filter((v) => !v.saved);
        if (open.length) {
            this.state.block = { emp, open };
            return;
        }
        this.state.pin = { emp, mode: "out" };
    }
    goTo(id) {
        this.state.block = null;
        setTimeout(() => {
            const el = document.getElementById(id);
            if (!el) {
                return;
            }
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.classList.remove("flash");
            void el.offsetWidth;
            el.classList.add("flash");
        }, 50);
    }
    async pinDone(code) {
        const p = this.state.pin;
        const r = await api(this.orm, p.mode === "in" ? "check_in" : "check_out", p.emp.id, code);
        if (r.error) {
            return r.message;
        }
        this.state.pin = null;
        if (p.mode === "in") {
            this.toast(r.late ? `تم تسجيل حضورك بتأخير ${r.late} دقيقة، اذكري السبب من «تنبيهاتي»` : `أهلًا ${p.emp.name}، تم تسجيل حضورك`, r.late ? "warning" : "success");
        } else {
            this.toast(`تم تسجيل انصراف ${p.emp.name}`, "success");
        }
        await this.load();
        return null;
    }
    async undone() {
        this.state.undo = null;
        this.toast("رجعتِ للدوام، العدّاد مفتوح", "success");
        await this.load();
    }
}

// ---------------------------------------------------------------- employee inbox (PIN)
class InboxCase extends Component {
    static template = "navitta_ops.InboxCase";
    static props = { c: Object, call: Function };
    setup() {
        this.state = useState({ why: "", req: "forgive", mins: 30 });
        Object.assign(this, H);
        this.Q = this.props.c.kind === "break" ? QBREAK : QLATE;
    }
    send() {
        const s = this.state;
        this.props.call("reply_case", [this.props.c.id, s.why, s.req, s.req === "edit" ? +s.mins : null],
            s.req === "edit" ? "تم رفع طلب تعديل المدة للإدارة" : "تم إرسال السبب للإدارة");
    }
}

class Inbox extends Component {
    static template = "navitta_ops.Inbox";
    static components = { PinPad, InboxCase };
    static props = { open: { optional: true }, empId: { optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            emps: [], emp: null, pin: null, box: null, leaveForm: this.props.open === "leave", msgForm: this.props.open === "msg",
            lv: { type: "annual", from: "", to: "", why: "", cover: true }, msg: { topic: "personal", body: "" }, rep: {}, coverNote: {},
        });
        Object.assign(this, H);
        onWillStart(async () => {
            const d = await api(this.orm, "branch_state");
            if (!d.error) {
                setClock(d.settings.tz, d.server_now);
                this.state.emps = d.employees;
                this.state.emp = d.employees.find((e) => e.id === this.props.empId) || null;
                const t = addDays(today(), 1);
                this.state.lv.from = t;
                this.state.lv.to = t;
            }
        });
    }
    pick(e) {
        if (!this.state.emp || this.state.emp.id !== e.id) {
            this.lock();
        }
        this.state.emp = e;
    }
    lock() {
        this.state.pin = null;
        this.state.box = null;
    }
    async load(code) {
        const r = await api(this.orm, "inbox", this.state.emp.id, code || this.state.pin);
        if (r.error) {
            return r.message;
        }
        this.state.box = r;
        return null;
    }
    async unlock(code) {
        const e = await this.load(code);
        if (!e) {
            this.state.pin = code;
        }
        return e;
    }
    async call(method, args, ok) {
        const r = await api(this.orm, method, this.state.emp.id, this.state.pin, ...args);
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
            return false;
        }
        this.notification.add(ok, { type: "success" });
        await this.load();
        return true;
    }
    get chanceDots() {
        return Array.from({ length: this.state.box.chances.max }, (_, i) => i);
    }
    get monthCases() {
        const month = today().slice(0, 7);
        return this.state.box.cases.filter((c) => c.day.startsWith(month) && c.state !== "edited");
    }
    async sendLeave() {
        const lv = this.state.lv;
        if (await this.call("leave_request", [lv.type, lv.from, lv.to, lv.why, lv.cover],
            lv.cover ? "تم إرسال الطلب لزميلتك لتوافق على التغطية، وبعدها يوصل للإدارة" : "تم إرسال طلب الإجازة للإدارة")) {
            this.state.leaveForm = false;
            lv.why = "";
        }
    }
    async sendMsg() {
        const m = this.state.msg;
        if (await this.call("send_message", [m.topic, m.body], "تم إرسال رسالتك للإدارة")) {
            m.body = "";
            this.state.msgForm = false;
        }
    }
    async replyThread(th) {
        if (await this.call("reply_thread", [th.id, this.state.rep[th.id] || ""], "تم إرسال ردك")) {
            this.state.rep[th.id] = "";
        }
    }
    cover(r, ok) {
        this.call("cover_respond", [r.id, ok, this.state.coverNote[r.id] || ""],
            ok ? "تم: وافقتِ على التغطية، وانرسل الطلب للإدارة" : "تم إبلاغ زميلتك باعتذارك");
    }
}

// ---------------------------------------------------------------- branch requests, supplies, expenses, waiting customers
class Requests extends Component {
    static template = "navitta_ops.Requests";
    static components = { SignModal, PhotoPicker, PhotoLink };
    static props = { tab: { optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            tab: this.props.tab || "goods", d: null, sign: null,
            g: this.blankGoods(), s: { supply: "bags", qty: 1, what: "", note: "" }, x: { amount: "", category: "delivery", details: "", receipt: null },
            err: "",
        });
        Object.assign(this, H);
        this.COLORS = COLORS;
        this.SIZES = SIZES;
        this.STEPS = ["pending", "approved", "shipped", "received"];
        this.STEPL = { pending: "انطلب", approved: "وافقت الإدارة", shipped: "انشحن", received: "وصل" };
        onWillStart(() => this.load());
    }
    blankGoods() {
        return { model: "", color: "", size: "", qty: 1, reason: "asked", note: "", urgent: false, photo: null };
    }
    async load() {
        const d = await api(this.orm, "requests_state");
        if (!d.error) {
            setClock(d.settings.tz);
            this.state.d = d;
        }
    }
    get mins() {
        const st = this.state.d.settings;
        return { roll_invoice: st.min_roll_invoice, roll_card: st.min_roll_card, bags: st.min_bags };
    }
    bal(k) {
        return this.state.d.stock[k].balance;
    }
    get low() {
        return Object.keys(this.mins).filter((k) => this.bal(k) < this.mins[k]);
    }
    get waiting() {
        return this.state.d.waiting.filter((w) => !w.contacted_at);
    }
    reqs(kind) {
        return this.state.d.requests.filter((r) => r.kind === kind);
    }
    stepCls(r, s, i) {
        const at = this.STEPS.indexOf(r.state);
        return "s" + (i < at || (i === at && s === "received") ? " done" : i === at ? " cur" : "");
    }
    sign(title, method, argsFn, ok, reset) {
        this.state.sign = {
            title,
            run: async (emp, pin) => {
                const r = await api(this.orm, method, emp.id, pin, ...argsFn());
                if (r.error) {
                    return r.message;
                }
                this.state.sign = null;
                this.notification.add(ok, { type: "success" });
                if (reset) {
                    reset();
                }
                await this.load();
                return null;
            },
        };
    }
    submitGoods() {
        const g = this.state.g;
        if (!g.model.trim()) return (this.state.err = "اكتبي رقم الموديل");
        if (!(+g.qty > 0)) return (this.state.err = "الكمية غير صحيحة");
        this.state.err = "";
        this.sign("طلب نواقص", "create_request", () => [{ kind: "goods", model: g.model, color: g.color, size: g.size, qty: +g.qty,
            reason: g.reason, note: g.note, urgent: g.urgent, photo_id: g.photo }], "تم إرسال الطلب للإدارة", () => (this.state.g = this.blankGoods()));
    }
    submitSupply() {
        const s = this.state.s;
        if (!(+s.qty > 0)) return (this.state.err = "الكمية غير صحيحة");
        if (s.supply === "other" && !s.what.trim()) return (this.state.err = "اكتبي وش تحتاجين");
        this.state.err = "";
        this.sign("طلب مستلزمات", "create_request", () => [{ kind: "supply", supply: s.supply, model: s.supply === "other" ? s.what : null,
            qty: +s.qty, note: s.note }], "تم إرسال الطلب للإدارة", () => Object.assign(s, { qty: 1, what: "", note: "" }));
    }
    submitExpense() {
        const x = this.state.x;
        if (!(+x.amount > 0)) return (this.state.err = "المبلغ غير صحيح");
        if (!x.details.trim()) return (this.state.err = "اكتبي التفاصيل");
        this.state.err = "";
        this.sign("تسجيل مصروف", "create_expense", () => [+x.amount, x.category, x.details, x.receipt], "تم تسجيل المصروف وانرسل للإدارة",
            () => Object.assign(x, { amount: "", details: "", receipt: null }));
    }
    receive(r) {
        this.sign("استلام الطلب", "request_received", () => [r.id], "تم تسجيل الاستلام");
    }
    async contacted(w, e) {
        const r = await api(this.orm, "lost_contacted", w.id, e.name);
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
        } else {
            this.notification.add("تم", { type: "success" });
            await this.load();
        }
    }
}

// ---------------------------------------------------------------- daily report
class Report extends Component {
    static template = "navitta_ops.Report";
    static props = {};
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, f: null, saved: false, err: "" });
        Object.assign(this, H);
        Object.assign(this, { COLORS, SIZES, TYPES, TRIED, SAT, NOBUY, LOSTFULL });
        onWillStart(() => this.load());
    }
    async load() {
        const d = await api(this.orm, "report_get");
        if (d.error) {
            return;
        }
        this.state.d = d;
        const empRows = d.employees.map((m) => ({ employee_id: m.id, amount: "", invoices: "", items: "", exchanged: "" }));
        const base = {
            employee_name: "", sales: 0, invoices: 0, items: 0, exch: [], visitors: 0, nonBuy: 0, noBuy: {}, lost: [],
            type: TYPES[0], color: COLORS[0], size: "38", tried_model: "", tried_reason: TRIED[0], sat: 4, phrase: "", tone: "positive",
            complaint: false, complaint_text: "", influ: false, influ_text: "", short: false, short_text: "", shelf: "good", damage: false,
            damage_text: "", emergency: "none", improve: "", suggest: "", cash: 0, card: 0, deposit: 0, emp: empRows,
            used: { roll_invoice: 0, roll_card: 0, bags: 0 },
        };
        if (d.report) {
            const rep = d.report;
            const emp = empRows.map((x) => Object.assign(x, rep.emp.find((y) => y.employee_id === x.employee_id) || {}));
            this.state.f = Object.assign(base, rep, { emp });
            this.state.saved = true;
        } else {
            this.state.f = Object.assign(base, d.from_visits, { invoices: d.from_visits.invoices });
        }
    }
    empName(id) {
        return (this.state.d.employees.find((e) => e.id === id) || {}).name || "";
    }
    setExch(c) {
        const f = this.state.f;
        c = Math.max(0, Math.min(20, +c || 0));
        while (f.exch.length < c) {
            f.exch.push({ reason: "size", detail: "", soldBy: "", by: "" });
        }
        f.exch.splice(c);
    }
    step(obj, key, d) {
        obj[key] = Math.max(0, (+obj[key] || 0) + d);
    }
    get nobuySum() {
        return Object.values(this.state.f.noBuy).reduce((t, x) => t + (+x || 0), 0);
    }
    get empSum() {
        const f = this.state.f;
        const s = (k) => f.emp.reduce((t, x) => t + (+x[k] || 0), 0);
        return { amount: s("amount"), invoices: s("invoices"), items: s("items"), exchanged: s("exchanged"),
            match: Math.round(s("amount")) === Math.round(+f.sales || 0) };
    }
    async submit(ev) {
        ev.preventDefault();
        const f = this.state.f;
        if (!f.employee_name) return (this.state.err = "اختاري اسم الموظفة اللي تكتب التقرير");
        const bad = f.exch.findIndex((x) => !String(x.detail || "").trim() || !x.soldBy || !x.by);
        if (bad > -1) return (this.state.err = `استبدال رقم ${bad + 1}: اكتبي السبب بالتفصيل ومن باعت ومن استبدلت`);
        const lb = f.lost.findIndex((x) => !x.color && !x.size);
        if (lb > -1) return (this.state.err = `المبيعات الضائعة رقم ${lb + 1}: اكتبي اللون أو المقاس`);
        this.state.err = "";
        const r = await api(this.orm, "report_save", JSON.parse(JSON.stringify(f)));
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.state.saved = true;
        this.notification.add("تم إرسال التقرير اليومي للإدارة", { type: "success" });
    }
}

// ---------------------------------------------------------------- root
export class BranchApp extends Component {
    static template = "navitta_ops.BranchApp";
    static components = { Counter, Inbox, Requests, Report, ModelSearch };
    static props = { ...standardActionServiceProps };
    setup() {
        this.state = useState({ tab: "counter", inboxOpen: "", inboxEmp: null, reqTab: "", branch: "", key: 1, branches: null, bid: 0 });
        this.orm = useService("orm");
        onWillStart(async () => {
            const a = await api(this.orm, "branch_access");
            if (a.error) {
                this.error = a.message;
                return;
            }
            if (a.device) {
                CTX.branch = 0;
                this.state.branch = a.branch.name;
                return;
            }
            // management: works on the branch picked here (remembered on this browser)
            this.state.branches = a.branches;
            let saved = 0;
            try {
                saved = +localStorage.getItem("navitta_admin_branch") || 0;
            } catch {
                saved = 0;
            }
            if (a.branches.some((b) => b.id === saved)) {
                this.pick(saved);
            }
        });
        onWillUnmount(() => (CTX.branch = 0));
    }
    pick(id) {
        id = +id || 0;
        const b = (this.state.branches || []).find((x) => x.id === id);
        CTX.branch = b ? b.id : 0;
        this.state.bid = CTX.branch;
        this.state.branch = b ? b.name : "";
        try {
            localStorage.setItem("navitta_admin_branch", String(CTX.branch));
        } catch {
            // private mode: the pick is just not remembered
        }
        this.state.key++;
    }
    go(tab, opts = {}) {
        Object.assign(this.state, { tab, inboxOpen: opts.open || "", inboxEmp: opts.emp || null, reqTab: opts.reqTab || "" });
        this.state.key++;
    }
}

registry.category("actions").add("navitta_branch_app", BranchApp);
