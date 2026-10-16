/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { withNavittaTerms } from "../common/terms";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    CASEST, COLORS, EXPICON, LEAVEST, LOST, LOSTL, NOBUY, LOSTFULL, OUTOF, QBREAK, QLATE, RQST, EXST, SAT, SIZES, SREASONL, SUP, TRIED, TYPES,
    CTX, addDays, api, clock, dTxt, days, f0, h12, jn, longDate, minuteNow, mmss, now, photoUrl, setClock, sizesOf, swatch, today, ago, icon, dcsText,
    DIR, LANG, vlabel, WD,
} from "../common/utils";
import { Bulb, HelpLayer, ThemeToggle, TourBtn, useTheme } from "../common/help";
import { Dots } from "../common/cases";
import { FloatCard, FloatEnd, TasksCard, endLeft } from "../common/tasks";
import { IncomingShipments } from "../common/shipments";
import { Defects, ShortageList } from "./shortage";
import {
    DailyQuote, FaceCam, ModelPicker, ModelSearch, NvModal, PhotoLink, PhotoPicker, PinPad, SignModal, TargetBar,
} from "../common/components";

const MFG = "manufacture", OTHER = "__other";
const newDraft = (buy = false) => ({
    buy, exch: false, no: false, reason: "", other: "", note: "", m: null, manual: "", color: "", colorOther: "", size: "", sizeOther: "",
    mfgNote: "", call: false, phone: "", cname: "", req: false, err: "",
});
const H = { jn, f0, minuteNow, mmss, clock, longDate, h12, dTxt, days, ago, photoUrl, swatch, icon, dcsText, vlabel, LOSTL, LOST, LOSTFULL, SUP, EXPICON, RQST, EXST, CASEST, LEAVEST };

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
            st.err = _t("Give the reason for the edit");
            return;
        }
        if (ns <= 0 || ns === this.props.v.duration_sec) {
            st.err = _t("Enter the correct duration; it must differ from the current one");
            return;
        }
        const r = await api(this.orm, "visit_edit_request", this.props.v.id, ns, st.why.trim());
        if (r.error) {
            st.err = r.message;
            return;
        }
        this.notification.add(_t("Duration edit request sent to Management"), { type: "success" });
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
    static components = { DailyQuote, TargetBar, ModelSearch, NvModal, PinPad, FaceCam, Outcome, EditReq, UndoOut, FloatCard, TasksCard, Bulb };
    static props = { goto: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, tick: 0, pin: null, drafts: {}, editFor: null, search: false, block: null, undo: null, autoNote: {} });
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
            const digits = "١٢٣٤٥٦٧٨٩";
            const n = /^[1-9]$/.test(e.key) ? +e.key : digits.indexOf(e.key) + 1;
            const emp = n > 0 && d.employees[n - 1];
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
        for (const v of d.visits) {
            if (v.auto && !v.auto_ack && !this.state.autoNote[v.id]) {
                this.state.autoNote[v.id] = { open: false, note: "" };
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
        // the daily break may be taken in several parts: what's left of all of today's parts together
        const used = this.state.d.breaks.filter((b) => b.employee_id === emp.id)
            .reduce((t, b) => t + ((b.ended_at ? Date.parse(b.ended_at) : now()) - Date.parse(b.started_at)) / 1000, 0);
        return this.state.d.settings.break_max * 60 - used;
    }
    lastBreak(emp) {
        return this.state.d.breaks.filter((b) => b.employee_id === emp.id && b.ended_at)
            .sort((a, b) => (a.ended_at < b.ended_at ? 1 : -1))[0] || null;
    }
    mine(emp) {
        return this.state.d.visits.filter((v) => v.employee_id === emp.id);
    }
    pane(emp) {
        const d = this.state.d, mine = this.mine(emp);
        const saved = mine.filter((v) => v.saved), open = mine.filter((v) => !v.saved);
        const buys = saved.filter((v) => v.outcomes.includes("buy")).length;
        // sales made without the counter have no duration: the average is of the timed visits
        const timed = saved.filter((v) => !v.auto);
        const avg = timed.length ? timed.reduce((t, v) => t + (v.duration_sec || 0), 0) / timed.length : 0;
        return {
            att: this.att(emp), isIn: this.isIn(emp), onBreak: this.onBreak(emp), left: this.breakLeft(emp), open, saved,
            buys, avg, buyPct: saved.length ? Math.round((buys / saved.length) * 100) : 0,
            mySales: +(d.emp_sales[emp.id] || 0),
            replyCases: d.cases.filter((c) => c.employee_id === emp.id && c.state === "reply"),
            covers: d.covers.filter((c) => c.cover_employee_id === emp.id),
            // her own shift today (morning / evening): lateness and the check-in hint count from it
            sh: emp.shift, lateBy: minuteNow() - emp.shift.start,
            lastBrk: this.lastBreak(emp),
            backRecently: !!this.lastBreak(emp) && now() - Date.parse(this.lastBreak(emp).ended_at) < 15 * 6e4,
            unread: +(d.unread[emp.id] || 0),
            // end-of-shift tasks that block her check-out: hers, and the shared ones when she is the last one in
            tasks: endLeft(d.tasks, emp, d.attendance),
            // sales the Point of Sale recorded without a counter visit, not confirmed yet (they block her check-out)
            autos: saved.filter((v) => v.auto && !v.auto_ack),
        };
    }
    toReport() {
        this.state.block = null;
        this.props.goto("report");
    }
    taskLine(x) {
        return x.who === "any" ? x.title : `${x.title} (${x.for_name})`;
    }
    tasks(emp) {
        const p = this.pane(emp);
        this.state.block = { emp, open: p.open, tasks: p.tasks, autos: p.autos };
    }
    autoText(v) {
        return _t("An invoice of %(a)s SAR was made under your name at %(t)s while the counter was off. It was added automatically as a visit.",
            { a: f0(v.amount || 0), t: clock(v.started_at) });
    }
    autoLine(v) {
        return _t("Invoice %(a)s SAR from Point of Sale · %(t)s", { a: f0(v.amount || 0), t: clock(v.started_at) });
    }
    an(v) {
        return this.state.autoNote[v.id] || { open: false, note: "" };
    }
    async ackAuto(v) {
        const n = this.an(v);
        if (await this.call("auto_visit_ack", v.id, n.note || null)) {
            delete this.state.autoNote[v.id];
            await this.load();
        }
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
            text: o === "buy" ? _t("Purchase") : o === "exchange" ? _t("Exchange") : o === "other" && v.other_text ? _t("Other: %s", v.other_text) : LOSTL[o] || o,
        }));
    }
    lostChip(v) {
        return [v.lost_model, v.lost_color, v.lost_size && _t("Size %s", v.lost_size)].filter(Boolean).join(" · ");
    }
    // ----- texts shown by the template
    visitTitle(v) {
        return v.ended_at ? _t("Visit %s – %s", clock(v.started_at), clock(v.ended_at)) : _t("Visit running since %s", clock(v.started_at));
    }
    visitHint(v) {
        return v.ended_at ? _t("Outcome not chosen yet (Bought / Didn't buy and the reason)") : _t("Tap “Customer out” and choose the outcome");
    }
    caseTitle(c) {
        return c.kind === "break" ? _t("☕ You exceeded the break by %s min", c.minutes) : _t("You were %s min late", c.minutes);
    }
    shiftLabel(emp) {
        const sh = emp.shift;
        if (sh.kind === "am") {
            return _t("☀️ Morning shift");
        }
        // a morning employee on a day without the morning shift
        return sh.moved ? _t("🌙 Evening shift (%s)", WD[new Date(this.state.d.day + "T00:00:00Z").getUTCDay()]) : _t("🌙 Evening shift");
    }
    checkinHint(sh) {
        return _t("Tap and enter your PIN to check in. Shift %(a)s – %(b)s", { a: h12(sh.start), b: h12(sh.end) });
    }
    bellText(n) {
        return _t("🔔 %s new alerts · open", n);
    }
    breakBtn(p) {
        return p.lastBrk ? _t("☕ Continue the break (%s min left)", Math.floor(p.left / 60)) : _t("☕ Break (%s min)", Math.ceil(p.left / 60));
    }
    backText(p) {
        return _t("☕ You came back from the break after %(a)s · %(b)s min of today's break remain; you can continue it later",
            { a: mmss((Date.parse(p.lastBrk.ended_at) - Date.parse(p.lastBrk.started_at)) / 1000), b: mmss(p.left) });
    }
    breakHint(left, max) {
        return left < 0 ? _t("Break time exceeded") : _t("Time left of today's break (%s min)", max);
    }
    editReqLabel(rq) {
        return rq.state === "pending" ? _t("Edit request awaiting reply") : rq.state === "approved" ? _t("Edit approved") : _t("Edit rejected");
    }
    pinTitle(pin) {
        return pin.mode === "in" ? _t("Check-in: %s", pin.emp.name) : _t("Check-out: %s", pin.emp.name);
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
            this.toast(_t("Stop and save your open visits before the break"), "warning");
            return;
        }
        await this.call("break_start", emp.id);
        await this.load();
    }
    async breakEnd(b) {
        const r = await this.call("break_end", b.id);
        if (r) {
            this.toast(r.over ? _t("You exceeded the break by %s min; give the reason in “My alerts”", r.over)
                : _t("Break ended, %s min left", mmss(r.left_sec || 0)), r.over ? "warning" : "success");
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
        if (!x.buy && !x.exch && !x.no) err = _t("Choose the visit outcome");
        else if (x.no && !r) err = _t("Choose why she didn't buy");
        else if (x.no && r === "other" && !x.other.trim()) err = _t("Type the reason in the “Other” box");
        else if (x.no && (OUTOF.includes(r) || r === MFG) && !model) err = _t("Choose the model the customer asked for");
        else if (x.no && r === "color_na" && !color) err = _t("Choose the requested color");
        else if (x.no && r === "size_na" && !size) err = _t("Choose the requested size");
        else if (x.no && r === MFG && !colorCustom && !sizeCustom && !x.manual) err = _t("Choose “Other” for color or size and type what is needed");
        else if (x.no && r === MFG && !x.mfgNote.trim()) err = _t("Explain exactly what the customer wants");
        else if (x.no && OUTOF.includes(r) && x.call && !/^(05\d{8}|9665\d{8})$/.test(phone)) err = _t("Invalid mobile number; enter it like 05XXXXXXXX");
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
        this.toast(x.no && x.req && OUTOF.includes(r) ? _t("Visit saved and Out-of-stock request sent") : _t("Visit saved"), "success");
        await this.load();
    }
    out(emp) {
        const p = this.pane(emp), open = p.open, tasks = p.tasks, autos = p.autos;
        if (open.length || tasks.length || autos.length) {
            this.state.block = { emp, open, tasks, autos };
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
        // the photo goes with the PIN; the server keeps it only when the PIN is right
        const photo = this.cam && this.state.d.settings.checkin_photo ? await this.cam.capture() : null;
        const r = await api(this.orm, p.mode === "in" ? "check_in" : "check_out", p.emp.id, code, photo);
        if (r.error) {
            if (r.tasks || r.autos) {
                // end-of-shift tasks left (e.g. a colleague checked out meanwhile), or sales made without the counter
                // that the Point of Sale just sent: show the list instead of the PIN
                this.state.pin = null;
                await this.load();
                this.state.block = { emp: p.emp, open: this.mine(p.emp).filter((v) => !v.saved), tasks: r.tasks || [], autos: r.autos || [] };
                return null;
            }
            return r.message;
        }
        this.state.pin = null;
        if (p.mode === "in") {
            this.toast(r.late ? _t("Checked in %s min late; give the reason in “My alerts”", r.late) : _t("Welcome %s, you are checked in", p.emp.name), r.late ? "warning" : "success");
        } else {
            this.toast(_t("%s checked out", p.emp.name), "success");
        }
        await this.load();
        return null;
    }
    async undone() {
        this.state.undo = null;
        this.toast(_t("You are back at work; the counter is open"), "success");
        await this.load();
    }
}

// ---------------------------------------------------------------- employee inbox (PIN)
/** «📬 آخر التنبيهات»: every alert and management reply, newest first; new ones are marked until she opens them. */
class InboxNotes extends Component {
    static template = "navitta_ops.InboxNotes";
    static props = { notes: Array };
    setup() {
        this.state = useState({ all: false });
        this.ago = ago;
    }
    get nNew() {
        return this.props.notes.filter((n) => n.new).length;
    }
    get shown() {
        const notes = this.props.notes;
        return this.state.all ? notes : notes.slice(0, Math.max(5, this.nNew));
    }
    get newChip() {
        return _t("%s new", this.nNew);
    }
    tone(n) {
        // approvals in green, rejections in red (by the text, as on the site)
        const ok = /^✅|موافقة|تمت الموافقة|اعتُمد|وافقت|approved|accepted/i.test(n.body) || /✅/.test(n.subject);
        const no = /^✕|مرفوض|رُفض|rejected|declined/i.test(n.body);
        return ok ? "ok" : no ? "no" : "";
    }
}

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
            s.req === "edit" ? _t("Duration edit request submitted to Management") : _t("Reason sent to Management"));
    }
    get kindLabel() {
        return this.props.c.kind === "break" ? _t("☕ Break overrun") : _t("Late arrival");
    }
    get stateLabel() {
        const c = this.props.c;
        return c.state === "review" && c.request === "edit" ? _t("Duration edit awaiting Management") : CASEST[c.state][1];
    }
    get detail() {
        const c = this.props.c;
        if (c.kind === "break") {
            return _t("☕ Break %s min · over by %s min", c.break_used, c.minutes);
        }
        return c.check_in ? _t("Checked in %s · late by %s min", clock(c.check_in), c.minutes) : _t("Not checked in (%s min late at alert time)", c.minutes);
    }
}

class Inbox extends Component {
    static template = "navitta_ops.Inbox";
    static components = { PinPad, InboxCase, InboxNotes, Dots };
    static props = { open: { optional: true }, empId: { optional: true }, onRead: { type: Function, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            emps: [], unread: {}, emp: null, pin: null, box: null, leaveForm: this.props.open === "leave", msgForm: this.props.open === "msg",
            lv: { type: "annual", from: "", to: "", why: "", cover: "" }, msg: { topic: "personal", body: "" }, rep: {}, coverNote: {},
        });
        Object.assign(this, H);
        onWillStart(async () => {
            const d = await api(this.orm, "branch_state");
            if (!d.error) {
                setClock(d.settings.tz, d.server_now);
                this.state.emps = d.employees;
                this.state.unread = d.unread || {};
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
        // opening her alerts marks them read: her badge and the menu badge go away
        this.state.unread[this.state.emp.id] = 0;
        if (this.props.onRead) {
            this.props.onRead();
        }
        // who covers the branch during a leave: preselected when she has a single colleague
        this.state.lv.cover = r.mates.length === 1 ? String(r.mates[0].id) : "";
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
    get pinTitle() {
        return _t("Alerts for %s", this.state.emp.name);
    }
    lockLabel(e) {
        return this.state.emp && this.state.emp.id === e.id && this.state.box ? _t("Unlocked") : _t("Locked 🔒");
    }
    threadState(th) {
        return th.state === "open" ? _t("Open") : _t("Resolved");
    }
    senderName(m) {
        return m.sender === "admin" ? _t("Management") : _t("You");
    }
    get chanceDots() {
        return Array.from({ length: this.state.box.chances.max }, (_, i) => i);
    }
    badge(e) {
        const n = +(this.state.unread[e.id] || 0);
        return n > 0 && !(this.state.emp && this.state.emp.id === e.id && this.state.box) ? n : 0;
    }
    /** «⚖️ فرصك وعقوباتك هذا الشهر»: shown when the chances are used up or she has penalties this month. */
    get pens() {
        const month = today().slice(0, 7);
        return this.state.box.cases.filter((c) => c.state === "rejected" && c.day.startsWith(month));
    }
    get showPens() {
        const ch = this.state.box.chances;
        return ch.used >= ch.max || this.pens.length > 0;
    }
    penLine(c) {
        return (c.kind === "break" ? _t("Break overrun of %s minutes", c.minutes) : _t("Late by %s minutes", c.minutes)) + " · " + _t("Occurrence %s", c.penalty_occ);
    }
    get resetText() {
        return _t("Your chances reset to %s next month.", this.state.box.chances.max);
    }
    signPenalty(c) {
        this.call("case_ack", [c.id], _t("Signature recorded"));
    }
    signRules() {
        this.call("rules_ack", [], _t("Signature recorded"));
    }
    get monthCases() {
        const month = today().slice(0, 7);
        return this.state.box.cases.filter((c) => c.day.startsWith(month) && c.state !== "edited");
    }
    async sendLeave() {
        const lv = this.state.lv;
        if (this.state.box.mates.length && lv.cover === "") {
            this.notification.add(_t("Choose the colleague who will cover the Branch"), { type: "warning" });
            return;
        }
        const cover = +lv.cover || false;
        if (await this.call("leave_request", [lv.type, lv.from, lv.to, lv.why, cover],
            cover ? _t("Request sent to your colleague to approve the cover; then it goes to Management") : _t("Leave request sent to Management"))) {
            this.state.leaveForm = false;
            lv.why = "";
        }
    }
    async sendMsg() {
        const m = this.state.msg;
        if (await this.call("send_message", [m.topic, m.body], _t("Your message was sent to Management"))) {
            m.body = "";
            this.state.msgForm = false;
        }
    }
    async replyThread(th) {
        if (await this.call("reply_thread", [th.id, this.state.rep[th.id] || ""], _t("Your reply was sent"))) {
            this.state.rep[th.id] = "";
        }
    }
    cover(r, ok) {
        this.call("cover_respond", [r.id, ok, this.state.coverNote[r.id] || ""],
            ok ? _t("Done: you accepted the cover and the request was sent to Management") : _t("Your colleague was told you declined"));
    }
}

// ---------------------------------------------------------------- branch requests, supplies, expenses, waiting customers
class Requests extends Component {
    static template = "navitta_ops.Requests";
    static components = { SignModal, PhotoPicker, PhotoLink, IncomingShipments, ShortageList, Defects };
    static props = { tab: { optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            tab: this.props.tab || "goods", d: null, sign: null,
            s: { supply: "bags", qty: 1, what: "", note: "" }, x: { amount: "", category: "delivery", details: "", receipt: null },
            err: "",
        });
        Object.assign(this, H);
        this.SREASONL = SREASONL;
        this.STEPS = ["pending", "approved", "shipped", "received"];
        this.STEPL = { pending: _t("Requested"), approved: _t("Approved by Management"), shipped: _t("Shipped"), received: _t("Arrived") };
        onWillStart(() => this.load());
    }
    get receiptLabel() {
        return _t("🧾 Receipt photo");
    }
    waitTitle(w) {
        return jn([w.item_no && "🏷️ " + w.item_no, w.model, w.color, w.size && _t("Size %s", w.size)], " · ");
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
    get pendingDefects() {
        return this.state.d.defects.filter((x) => x.state === "pending").length;
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
    submitSupply() {
        const s = this.state.s;
        if (!(+s.qty > 0)) return (this.state.err = _t("Invalid quantity"));
        if (s.supply === "other" && !s.what.trim()) return (this.state.err = _t("Type what you need"));
        this.state.err = "";
        this.sign(_t("Supplies request"), "create_request", () => [{ kind: "supply", supply: s.supply, model: s.supply === "other" ? s.what : null,
            qty: +s.qty, note: s.note }], _t("Request sent to Management"), () => Object.assign(s, { qty: 1, what: "", note: "" }));
    }
    submitExpense() {
        const x = this.state.x;
        if (!(+x.amount > 0)) return (this.state.err = _t("Invalid amount"));
        if (!x.details.trim()) return (this.state.err = _t("Enter the details"));
        this.state.err = "";
        this.sign(_t("Record an expense"), "create_expense", () => [+x.amount, x.category, x.details, x.receipt], _t("Expense recorded and sent to Management"),
            () => Object.assign(x, { amount: "", details: "", receipt: null }));
    }
    receive(r) {
        this.sign(_t("Confirm receipt"), "request_received", () => [r.id], _t("Receipt recorded"));
    }
    async contacted(w, e) {
        const r = await api(this.orm, "lost_contacted", w.id, e.name);
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
        } else {
            this.notification.add(_t("Done"), { type: "success" });
            await this.load();
        }
    }
}

// ---------------------------------------------------------------- daily report
class Report extends Component {
    static template = "navitta_ops.Report";
    static components = { FloatEnd, PhotoPicker };
    static props = {};
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, f: null, saved: false, err: "" });
        Object.assign(this, H);
        Object.assign(this, { COLORS, SIZES, TYPES, TRIED, SAT, NOBUY, LOSTFULL });
        // aria-labels of the +/- steppers
        this.L = {
            invoices: _t("Number of invoices"), items: _t("Number of items"), roll_invoice: _t("Receipt roll"), roll_card: _t("Card roll"),
            bags: _t("Bags"), visitors: _t("Customers"), nonBuy: _t("Did not buy"),
        };
        // yes/no questions: [flag field, text field, question, placeholder]
        this.QA = [
            ["complaint", "complaint_text", _t("Did a customer make a complaint?"), _t("Write the complaint")],
            ["influ", "influ_text", _t("Did an influencer customer visit?"), _t("Who is she? What did she buy?")],
        ];
        this.QB = [
            ["short", "short_text", _t("Stock shortage that may cause lost sales tomorrow?"), _t("Model, color and size")],
            ["damage", "damage_text", _t("Damaged, broken or faulty item?"), _t("Describe the problem")],
        ];
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
            used: { roll_invoice: 0, roll_card: 0, bags: 0 }, photo_card: null, photo_inv: null, cash_note: "", card_note: "",
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
    async floatDone() {
        const r = await api(this.orm, "float_get");
        if (!r.error) {
            this.state.d.float = r;
            this.state.err = "";
        }
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
    get salesLegend() {
        return this.state.d.system ? _t("Exchanges") : _t("Sales");
    }
    get photoCardLabel() {
        return _t("💳 Card-terminal settlement photo *");
    }
    get photoInvLabel() {
        return _t("🧾 Sales invoices photo *");
    }
    /** The counted cash / card settlement against the Point of Sale (rounded to the riyal, as on the site). */
    box(k) {
        const sy = this.state.d.system, f = this.state.f;
        const o = sy ? +sy[k] || 0 : null, v = +f[k] || 0;
        const off = o !== null && Math.round(v) !== Math.round(o);
        return { k, o, v, off, diff: o === null ? 0 : Math.round((v - o) * 100) / 100 };
    }
    get boxes() {
        return [
            { ...this.box("cash"), title: _t("💵 Cash"), lab: _t("✋ Cash you counted in the drawer (SAR)") },
            { ...this.box("card"), title: _t("💳 Card"), lab: _t("✋ Card terminal settlement total (SAR)") },
        ];
    }
    diffText(b) {
        return _t("⚠️ Difference: %(w)s %(n)s SAR", { w: b.diff < 0 ? _t("short") : _t("over"), n: f0(Math.abs(b.diff)) });
    }
    get totals() {
        const sy = this.state.d.system, f = this.state.f;
        const ot = (+sy.cash || 0) + (+sy.card || 0), mt = (+f.cash || 0) + (+f.card || 0), df = Math.round(mt - ot);
        return { ot, mt, df, text: df ? _t("Difference: %s · goes to Management with the reason", f0(df)) : _t("✓ No difference") };
    }
    get submitLabel() {
        return this.state.saved ? _t("Update report") : _t("Send report");
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
        if (!f.employee_name) return (this.state.err = _t("Choose the name of the employee writing the report"));
        const bad = f.exch.findIndex((x) => !String(x.detail || "").trim() || !x.soldBy || !x.by);
        if (bad > -1) return (this.state.err = _t("Exchange %s: give the detailed reason, who sold and who exchanged", bad + 1));
        const lb = f.lost.findIndex((x) => !x.color && !x.size);
        if (lb > -1) return (this.state.err = _t("Lost sale %s: enter the color or size", lb + 1));
        if (!f.photo_card) return (this.state.err = _t("Attach the card-terminal settlement photo"));
        if (!f.photo_inv) return (this.state.err = _t("Attach the sales invoices photo"));
        if (this.box("cash").off && !String(f.cash_note || "").trim()) {
            return (this.state.err = _t("The cash doesn't match the Point of Sale: state the reason for the difference"));
        }
        if (this.box("card").off && !String(f.card_note || "").trim()) {
            return (this.state.err = _t("The card total doesn't match the Point of Sale: state the reason for the difference"));
        }
        if (!this.state.d.float.end) return (this.state.err = _t("Count the end-of-shift float and confirm it with your PIN first"));
        this.state.err = "";
        const r = await api(this.orm, "report_save", JSON.parse(JSON.stringify(f)));
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.state.saved = true;
        this.notification.add(_t("Daily report sent to Management"), { type: "success" });
    }
}

// ---------------------------------------------------------------- root
export class BranchApp extends Component {
    static template = "navitta_ops.BranchApp";
    static components = { Counter, Inbox, Requests, Report, ModelSearch, Bulb, HelpLayer, TourBtn, ThemeToggle };
    static props = { ...standardActionServiceProps };
    setup() {
        this.state = useState({ tab: "counter", inboxOpen: "", inboxEmp: null, reqTab: "", branch: "", key: 1, branches: null, bid: 0, unread: 0,
            login: "", ready: false });
        this.theme = useTheme();
        this.orm = useService("orm");
        this.DIR = DIR;
        this.LANG = LANG;
        onWillStart(async () => {
            const a = await api(this.orm, "branch_access");
            if (a.error) {
                this.error = a.message;
                return;
            }
            this.state.login = a.login || "";
            this.state.ready = true;
            if (a.device) {
                CTX.branch = 0;
                this.state.branch = a.branch.name;
                await this.loadUnread();
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
        // the «My alerts» badge: new alerts of all the branch's employees (counts only), every minute
        onMounted(() => (this.unreadTimer = setInterval(() => this.loadUnread(), 60000)));
        onWillUnmount(() => {
            clearInterval(this.unreadTimer);
            CTX.branch = 0;
        });
    }
    async loadUnread() {
        if (this.error || (this.state.branches && !this.state.bid)) {
            this.state.unread = 0;
            return;
        }
        const r = await api(this.orm, "branch_unread");
        this.state.unread = r.error ? 0 : Object.values(r.unread).reduce((t, n) => t + n, 0);
    }
    get title() {
        return this.state.branch || _t("Branch device");
    }
    /** The tour's welcome: «The {branch} girls» (site). */
    get greeting() {
        return this.state.branch ? _t("The %s girls", this.state.branch) : _t("Branch device");
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
        this.loadUnread();
    }
    go(tab, opts = {}) {
        Object.assign(this.state, { tab, inboxOpen: opts.open || "", inboxEmp: opts.emp || null, reqTab: opts.reqTab || "" });
        this.state.key++;
        this.loadUnread();
    }
}

registry.category("actions").add("navitta_branch_app", withNavittaTerms(BranchApp));
