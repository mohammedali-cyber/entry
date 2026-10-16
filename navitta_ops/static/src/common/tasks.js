/** @odoo-module **/
// «عهدة اليوم» (today's float) and «مهام اليوم» (today's tasks), as on the site (Navitta Pro 26.3).
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { api, clock, f0, h12, minuteNow } from "./utils";
import { NvModal, PhotoLink, PhotoPicker, PinPad, SignModal } from "./components";

export const DENOMS = [100, 50, 10, 5, 1];
export const floatTotal = (c) => DENOMS.reduce((s, d) => s + d * (+c[d] || 0), 0);

/** The three periods of the day: [key, label]. */
export const phases = () => [["start", _t("🌅 Start of shift")], ["mid", _t("☀️ Mid-shift")], ["end", _t("🌙 End of shift")]];
const sideLabel = (part) => (part === 1 ? _t("➡️ Right side") : part === 2 ? _t("⬅️ Left side") : _t("Section %s", part));
/** Who a task belongs to, as a group heading. */
export const groupOf = (x) => (x.who === "any" ? "👥 " + _t("Either one") : x.who === "both" ? "👥 " + _t("Both") : `${x.for_name || ""} · ${sideLabel(x.part)}`);
/** A period's state at minute `now`: locked (not open yet), open, or past. */
export const phaseState = (w, now) => (now < w[0] ? "locked" : now > w[1] ? "past" : "open");

const counts = (c) => Object.fromEntries(DENOMS.map((d) => [String(d), Math.max(0, Math.floor(+c[d] || 0))]));

/** Banknote counts: one stepper per note, with the running total against the float amount. */
export class Denoms extends Component {
    static template = "navitta_ops.Denoms";
    static props = { c: Object, expected: Number };
    setup() {
        this.c = useState(this.props.c);
        this.DENOMS = DENOMS;
        this.f0 = f0;
    }
    get total() {
        return floatTotal(this.c);
    }
    step(d, n) {
        this.c[d] = Math.max(0, (+this.c[d] || 0) + n);
    }
    set(d, v) {
        this.c[d] = Math.max(0, Math.floor(+v || 0));
    }
    noteLabel(d) {
        return _t("%s riyal", d);
    }
    get cls() {
        const t = this.total;
        return t === this.props.expected ? "ok" : t ? "no" : "";
    }
    get status() {
        const t = this.total, e = this.props.expected;
        return t === e ? "✓ " + _t("Complete") : !t ? "" : t < e ? _t("%s short", f0(e - t)) : _t("%s over", f0(t - e));
    }
}

/** Today's float on the counter: whoever counts it photographs it in the drawer and signs; her colleague confirms it. */
export class FloatCard extends Component {
    static template = "navitta_ops.FloatCard";
    static components = { Denoms, NvModal, PhotoLink, PhotoPicker, PinPad };
    static props = { fl: Object, emps: Array, att: Array, onChange: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ c: {}, photo: null, note: "", pin: null, short: null, snote: "" });
        this.f0 = f0;
        this.clock = clock;
    }
    get f() {
        return this.props.fl.start;
    }
    get exp() {
        return +this.props.fl.amount;
    }
    get total() {
        return floatTotal(this.state.c);
    }
    get present() {
        return this.props.emps.filter((e) => this.props.att.some((a) => a.employee_id === e.id && !a.check_out));
    }
    get show() {
        return !!this.f || this.present.length > 0;
    }
    get mates() {
        return this.props.emps.filter((e) => e.id !== this.f.counted_by);
    }
    get ready() {
        return !!this.state.photo && this.total > 0 && (this.total === this.exp || !!this.state.note.trim());
    }
    get hint() {
        return !this.total ? _t("Count the money first") : !this.state.photo ? _t("The photo is required first") : _t("Write the reason for the difference");
    }
    get title() {
        return _t("💰 Today's float · %s SAR", f0(this.exp));
    }
    get sub() {
        return "📍 " + (this.props.fl.place || "—") + " · " + _t("Either of you can count it");
    }
    get photoLabel() {
        return "📷 " + _t("A clear photo of the money in the drawer");
    }
    get pinTitle() {
        return _t("Today's float · %s", this.state.pin.emp.name);
    }
    get counted() {
        const f = this.f;
        return _t("Counted by %(n)s %(t)s", { n: f.counted_name || "—", t: clock(f.counted_at) });
    }
    get pendingSub() {
        const f = this.f;
        return _t("%(n)s counted it %(t)s: %(a)s SAR", { n: f.counted_name || "—", t: clock(f.counted_at), a: f0(f.amount) });
    }
    get doneLine() {
        const f = this.f;
        let s = f.state === "ok" ? "✓ " : "⚠️ ";
        s += f0(f.amount) + " " + _t("SAR");
        if (f.state === "short") {
            s += " " + _t("of %s", f0(f.expected));
        }
        return s;
    }
    get confirmedLine() {
        const f = this.f;
        if (!f.confirmed_by) {
            return "";
        }
        return " · " + (f.state === "ok" ? _t("Confirmed by %s 👀", f.confirmed_name) : _t("%s reported it short", f.confirmed_name));
    }
    lookText(m) {
        return _t("%s, check the money with your own eyes and confirm it's complete 👀", m.name);
    }
    async run(method, args, ok) {
        const r = await api(this.orm, method, ...args);
        if (r.error) {
            return r.message;
        }
        this.notification.add(typeof ok === "function" ? ok(r) : ok, { type: r.state === "short" ? "warning" : "success" });
        Object.assign(this.state, { pin: null, short: null, snote: "" });
        this.props.onChange();
        return null;
    }
    sign(emp) {
        const s = this.state;
        s.pin = {
            emp, run: (code) => this.run("float_count", [emp.id, code, "start", counts(s.c), s.photo, this.total === this.exp ? null : s.note],
                (r) => (r.state === "pending" ? _t("Done, now your colleague confirms") : r.state === "short"
                    ? _t("Float recorded and the difference sent to management") : _t("Float recorded"))),
        };
    }
    startShort(m) {
        Object.assign(this.state, { short: m.id, snote: "" });
    }
    confirm(m) {
        this.state.pin = { emp: m, run: (code) => this.run("float_confirm", [m.id, code, this.f.id, true], _t("Float confirmed")) };
    }
    report(m) {
        this.state.pin = { emp: m, run: (code) => this.run("float_confirm", [m.id, code, this.f.id, false, this.state.snote], _t("The shortage report reached management")) };
    }
}

/** End-of-shift count inside the daily report, signed with a PIN. Required before the report can be sent. */
export class FloatEnd extends Component {
    static template = "navitta_ops.FloatEnd";
    static components = { Denoms, SignModal };
    static props = { fl: Object, emps: Array, onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ c: {}, note: "", sign: false, recount: false });
        this.f0 = f0;
    }
    get f() {
        return this.state.recount ? false : this.props.fl.end;
    }
    get exp() {
        return +this.props.fl.amount;
    }
    get total() {
        return floatTotal(this.state.c);
    }
    get can() {
        return this.total > 0 && (this.total === this.exp || !!this.state.note.trim());
    }
    get title() {
        return _t("End-of-shift float");
    }
    get line() {
        const f = this.f;
        return (f.state === "ok" ? "✓ " : "⚠️ ") + f0(f.amount) + " / " + f0(f.expected) + " " + _t("SAR");
    }
    get counted() {
        return _t("Counted by %(n)s %(t)s", { n: this.f.counted_name || "—", t: clock(this.f.counted_at) });
    }
    async onSign(emp, pin) {
        const s = this.state;
        const r = await api(this.orm, "float_count", emp.id, pin, "end", counts(s.c), null, this.total === this.exp ? null : s.note);
        if (r.error) {
            return r.message;
        }
        Object.assign(s, { sign: false, recount: false, c: {}, note: "" });
        this.notification.add(_t("End-of-shift float recorded"), { type: r.state === "short" ? "warning" : "success" });
        await this.props.onDone();
        return null;
    }
}

/** Today's tasks on the counter: three periods, each opens at its time; each employee signs hers with her PIN. */
export class TasksCard extends Component {
    static template = "navitta_ops.TasksCard";
    static components = { NvModal, PhotoLink, PhotoPicker, SignModal };
    static props = { t: Object, emps: Array, att: Array, onChange: Function, tick: { optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ show: null, act: null, photo: null, step: "pin" });
        Object.assign(this, { clock, h12, groupOf });
    }
    get now() {
        return minuteNow();
    }
    get list() {
        return this.props.t.tasks;
    }
    get phases() {
        return phases();
    }
    of(ph) {
        return this.list.filter((x) => x.phase === ph);
    }
    st(ph) {
        return phaseState(this.props.t.windows[ph], this.now);
    }
    get open() {
        return (this.phases.find(([ph]) => this.st(ph) === "open") || [])[0];
    }
    get openLeft() {
        return this.open ? this.of(this.open).filter((x) => !x.done_at).length : 0;
    }
    get expanded() {
        return this.state.show === null ? this.openLeft > 0 : this.state.show;
    }
    get doneN() {
        return this.list.filter((x) => x.done_at).length;
    }
    get present() {
        return this.props.emps.filter((e) => this.props.att.some((a) => a.employee_id === e.id && !a.check_out));
    }
    get visible() {
        return this.list.length > 0 && this.props.att.length > 0;
    }
    groups(ph) {
        const out = [];
        for (const x of this.of(ph)) {
            const g = groupOf(x);
            let grp = out.find((y) => y.g === g);
            if (!grp) {
                grp = { g, items: [] };
                out.push(grp);
            }
            grp.items.push(x);
        }
        return out;
    }
    pill(ph, label) {
        const xs = this.of(ph), n = xs.filter((x) => x.done_at).length, s = this.st(ph), w = this.props.t.windows[ph];
        const txt = s === "locked" ? "🔒 " + _t("Opens %s", h12(w[0])) : n === xs.length ? "✓ " + _t("%(n)s of %(m)s done", { n, m: xs.length })
            : s === "past" ? _t("%s missed", xs.length - n) : _t("%(n)s of %(m)s", { n, m: xs.length });
        return { cls: `tk-p ${s} ${n === xs.length ? "all" : ""}`, txt: label + ": " + txt };
    }
    chip(ph) {
        const s = this.st(ph), w = this.props.t.windows[ph], all = this.of(ph).every((x) => x.done_at);
        if (s === "open") {
            return ["good", _t("Open · until %s", h12(w[1]))];
        }
        if (s === "locked") {
            return ["neutral", "🔒 " + _t("Opens %s", h12(w[0]))];
        }
        return all ? ["good", _t("All done ✓")] : ["bad", _t("Time's up")];
    }
    lockedText(ph) {
        const w = this.props.t.windows[ph];
        return _t("Opens at %(a)s and stays open until %(b)s", { a: h12(w[0]), b: h12(w[1]) });
    }
    get alertText() {
        const label = (this.phases.find((p) => p[0] === this.open) || [])[1];
        return [_t("%s is open!", label), _t("%(n)s tasks left · until %(t)s", { n: this.openLeft, t: h12(this.props.t.windows[this.open][1]) })];
    }
    get progress() {
        return _t("You've done %(n)s of %(m)s", { n: this.doneN, m: this.list.length });
    }
    get rules() {
        return "⏱️ " + _t("Each period opens at its time; a task not done in time shows “Missed” and management is alerted.")
            + " · ✓ " + _t("Tap a task and enter your PIN; it's saved with your name and time.")
            + (this.present.length === 1 ? " · 👤 " + _t("One employee on shift: both sides are hers.") : "");
    }
    can(x, ph) {
        return this.st(ph) === "open" && !x.done_at && !x.auto;
    }
    line(x) {
        return (x.who === "both" ? `${x.for_name}: ` : "") + x.title;
    }
    doneLine(x) {
        return "✓ " + (x.done_name || "") + " " + clock(x.done_at);
    }
    start(x, ph) {
        if (!this.can(x, ph)) {
            return;
        }
        Object.assign(this.state, { act: x, photo: null, step: x.photo ? "photo" : "pin" });
    }
    close() {
        this.state.act = null;
    }
    get signers() {
        const x = this.state.act;
        if (x.who === "both") {
            return this.props.emps.filter((e) => e.id === x.for_emp);
        }
        return this.present.length ? this.present : this.props.emps;
    }
    get photoLabel() {
        return _t("Photograph the section after arranging it");
    }
    async onSign(emp, pin) {
        const x = this.state.act;
        const r = await api(this.orm, "task_done", emp.id, pin, x.task_id, x.part, this.state.photo);
        if (r.error) {
            return r.message;
        }
        this.close();
        this.notification.add(_t("Done ✓"), { type: "success" });
        this.props.onChange();
        return null;
    }
}

/** End-of-shift tasks that still block an employee's check-out (same rule as the server): hers once the end period has
 * opened, and the shared «either one» ones only when she is the last one in the branch. */
export function endLeft(t, emp, att) {
    if (!t || minuteNow() < t.windows.end[0]) {
        return [];
    }
    const alone = !att.some((a) => a.employee_id !== emp.id && !a.check_out);
    return t.tasks.filter((x) => x.phase === "end" && !x.done_at && (x.for_emp === emp.id || (x.who === "any" && alone)));
}
