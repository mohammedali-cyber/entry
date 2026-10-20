/** @odoo-module **/
// «🔎 المعروض والجرد» (Navitta Pro 26.6): the display check and the blind stock count, from the branch's Odoo stock
// (navitta_ops_pos; the tab is hidden without it). The device never gets an Odoo quantity: the display check lists the
// models in stock, the count only shows what the staff counted. Both are saved on the server as the staff go and sent
// with an employee's PIN.
import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { api, clock, f0, jn, longDate, swatch } from "../common/utils";
import { CamScan, SignModal } from "../common/components";

const TX = {
    get sendCheck() { return _t("Display check"); },
    get sendCount() { return _t("Stock count"); },
    checkSent: (m) => (m ? _t("Sent to management: %s models not on display", m) : _t("Sent to management: everything is on display ✓")),
    get countSent() { return _t("Sent to management; Odoo's stock changes only when they approve it"); },
    progress: (n, m) => _t("%(n)s of %(m)s models checked", { n, m }),
    missingN: (n) => _t("%s not on display", n),
    sentBy: (n, t) => _t("Sent by %(n)s at %(t)s", { n, t }),
    pieces: (n) => _t("%s pieces", f0(n)),
    counted: (n) => _t("Counted so far: %s pieces", f0(n)),
    get searchPh() { return _t("Model number or name, or scan the barcode"); },
    get scanPh() { return _t("Scan the item's barcode"); },
    get addPh() { return _t("Model number (8 digits) or scan a barcode, to type its quantities"); },
    get otherPh() { return _t("Why? *"); },
    plusOne: (x, n) => _t("✓ +1 · %(x)s → %(n)s", { x, n }),
    get unknown() { return _t("This code isn't one of our products"); },
    get notInList() { return _t("This model isn't in your branch's stock list"); },
    countTitle: (n) => _t("📦 Stock count #%s", n),
    get recountTitle() { return _t("🔁 Management asked for a recount"); },
    sentAt: (t, n) => _t("⏳ Sent to management %(t)s by %(n)s, waiting for their review", { t, n }),
};
const STATEL = {
    get approved() { return ["good", _t("✅ Approved and applied")]; },
    get rejected() { return ["bad", _t("✕ Rejected")]; },
    get submitted() { return ["warn", _t("⏳ Waiting for management")]; },
    get draft() { return ["neutral", _t("Counting")]; },
};

/** «👀 فحص المعروض»: every model in stock at the branch must be on display. */
export class DisplayCheck extends Component {
    static template = "navitta_ops.DisplayCheck";
    static components = { CamScan, SignModal };
    static props = {};
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, q: "", filter: "all", sign: false, hi: 0 });
        Object.assign(this, { TX, swatch, clock, longDate });
        this.timers = {};
        onWillStart(() => this.load());
        onWillUnmount(() => Object.values(this.timers).forEach(clearTimeout));
    }
    async load() {
        const d = await api(this.orm, "display_state");
        if (!d.error) {
            this.state.d = d;
        }
    }
    get c() {
        return this.state.d.check;
    }
    get open() {
        return this.c.state === "draft";
    }
    get checked() {
        return this.c.lines.filter((l) => l.status).length;
    }
    get missing() {
        return this.c.lines.filter((l) => l.status === "missing");
    }
    get lines() {
        const q = this.state.q.trim().toLowerCase(), f = this.state.filter;
        return this.c.lines.filter((l) => (f === "all" || (f === "todo" ? !l.status : l.status === "missing"))
            && (!q || l.item_no.toLowerCase().includes(q) || l.model.toLowerCase().includes(q) || l.colors.some((c) => c.toLowerCase().includes(q))));
    }
    get ready() {
        return this.open && this.c.lines.length > 0 && this.checked === this.c.lines.length
            && this.missing.every((l) => l.reason && (l.reason !== "other" || l.note.trim()));
    }
    get pct() {
        return this.c.lines.length ? Math.round((this.checked / this.c.lines.length) * 100) : 0;
    }
    toast(msg, type = "warning") {
        this.notification.add(msg, { type });
    }
    async save(l) {
        const r = await api(this.orm, "display_mark", l.id, l.status, l.reason, l.note);
        if (r.error) {
            this.toast(r.message, "danger");
            await this.load();
        }
    }
    mark(l, status) {
        if (!this.open) {
            return;
        }
        l.status = l.status === status ? "" : status;
        if (l.status !== "missing") {
            Object.assign(l, { reason: "", note: "" });
        }
        this.save(l);
    }
    reason(l, k) {
        l.reason = k;
        this.save(l);
    }
    note(l, v) {
        l.note = v;
        clearTimeout(this.timers[l.id]);
        this.timers[l.id] = setTimeout(() => this.save(l), 600);
    }
    /** A scanned barcode or a model number: jumps to its model in the list. */
    async find(code) {
        code = (code || "").trim();
        if (!code) {
            return;
        }
        const local = this.c.lines.find((l) => l.item_no === code);
        if (local) {
            return this.jump(local.id);
        }
        const r = await api(this.orm, "display_find", code);
        if (r.error) {
            this.toast(r.in_catalog ? TX.notInList : TX.unknown);
            return;
        }
        this.jump(r.line_id);
    }
    jump(id) {
        Object.assign(this.state, { q: "", filter: "all", hi: id });
        setTimeout(() => {
            const el = document.getElementById("dl-" + id);
            if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "center" });
                el.classList.remove("flash");
                void el.offsetWidth;
                el.classList.add("flash");
            }
        }, 60);
    }
    onKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.find(this.state.q);
        }
    }
    async send(emp, pin) {
        const r = await api(this.orm, "display_submit", emp.id, pin);
        if (r.error) {
            return r.message;
        }
        this.state.sign = false;
        this.toast(TX.checkSent(r.missing), "success");
        await this.load();
        return null;
    }
}

/** «📦 جرد المخزن»: a blind count by scanning (+1 each) or typing quantities, the whole stock or chosen models. */
export class StockCount extends Component {
    static template = "navitta_ops.StockCount";
    static components = { CamScan, SignModal };
    static props = {};
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, code: "", add: "", msg: null, sign: false, busy: false });
        Object.assign(this, { TX, STATEL, clock, longDate, f0 });
        this.inp = useRef("scan");
        this.timers = {};
        onWillStart(() => this.load());
        onMounted(() => this.focus());
        onWillUnmount(() => Object.values(this.timers).forEach(clearTimeout));
        // a keyboard-wedge scanner types the code and Enter wherever the focus is: its keys go to the scan box
        useExternalListener(window, "keydown", (e) => {
            if (!this.c || this.c.state !== "draft" || this.state.sign || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) {
                return;
            }
            const t = e.target;
            if (t && t.closest && t.closest("input, textarea, select, [contenteditable], .nv-modal-bg")) {
                return;
            }
            this.focus();
        });
    }
    focus() {
        if (this.inp.el) {
            this.inp.el.focus();
        }
    }
    async load() {
        const d = await api(this.orm, "count_state");
        if (!d.error) {
            this.state.d = d;
        }
    }
    get c() {
        return this.state.d && this.state.d.count;
    }
    get groups() {
        const out = [];
        for (const l of this.c.lines) {
            let g = out.find((x) => x.tmpl_id === l.tmpl_id);
            if (!g) {
                g = { tmpl_id: l.tmpl_id, item_no: l.item_no, model: l.model, lines: [] };
                out.push(g);
            }
            g.lines.push(l);
        }
        for (const t of this.c.models) {
            if (!out.some((g) => g.tmpl_id === t.id)) {
                out.push({ tmpl_id: t.id, item_no: t.item_no, model: t.model, lines: [] });
            }
        }
        return out;
    }
    total(g) {
        return g.lines.reduce((s, l) => s + (+l.counted || 0), 0);
    }
    get pieces() {
        return this.c.lines.reduce((s, l) => s + (+l.counted || 0), 0);
    }
    get canSend() {
        return this.pieces > 0 || (this.c.scope === "models" && this.c.models.length > 0);
    }
    label(l) {
        return jn([l.item_no, l.model && l.model !== l.item_no && l.model, l.color, l.size]);
    }
    toast(msg, type = "warning") {
        this.notification.add(msg, { type });
    }
    async start(scope) {
        const r = await api(this.orm, "count_start", scope);
        if (r.error) {
            return this.toast(r.message, "danger");
        }
        await this.load();
        this.focus();
    }
    put(line) {
        const ls = this.c.lines, i = ls.findIndex((x) => x.product_id === line.product_id);
        if (i >= 0) {
            ls[i] = line;
        } else {
            ls.push(line);
        }
        if (this.c.scope === "models" && !this.c.models.some((t) => t.id === line.tmpl_id)) {
            this.c.models.push({ id: line.tmpl_id, item_no: line.item_no, model: line.model });
        }
    }
    async scan() {
        const code = this.state.code.trim();
        this.state.code = "";
        if (!code) {
            return;
        }
        const r = await api(this.orm, "count_scan", code);
        if (r.error) {
            this.state.msg = { k: "bad", t: r.error === "unknown_code" ? TX.unknown : r.message };
        } else {
            this.put(r.line);
            this.state.msg = { k: "ok", t: TX.plusOne(this.label(r.line), r.line.counted) };
        }
        this.focus();
    }
    camScanned(code) {
        this.state.code = code;
        this.scan();
    }
    onKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.scan();
        }
    }
    async addModel() {
        const q = this.state.add.trim();
        if (!q) {
            return;
        }
        const r = await api(this.orm, "count_add", q);
        if (r.error) {
            return this.toast(r.error === "unknown_code" ? TX.unknown : r.message);
        }
        this.state.add = "";
        this.state.d.count = r.count;
    }
    onAddKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.addModel();
        }
    }
    set(l, v) {
        l.counted = Math.max(0, Math.floor(+v || 0));
        clearTimeout(this.timers[l.product_id]);
        this.timers[l.product_id] = setTimeout(async () => {
            const r = await api(this.orm, "count_set", l.product_id, l.counted);
            if (r.error) {
                this.toast(r.message, "danger");
                await this.load();
            }
        }, 500);
    }
    step(l, n) {
        this.set(l, (+l.counted || 0) + n);
    }
    async drop(g) {
        const r = await api(this.orm, "count_drop_model", g.tmpl_id);
        if (!r.error) {
            this.state.d.count = r.count;
        }
    }
    async send(emp, pin) {
        // the typed quantities still waiting to be saved go first
        for (const k of Object.keys(this.timers)) {
            clearTimeout(this.timers[k]);
            const l = this.c.lines.find((x) => String(x.product_id) === k);
            if (l) {
                await api(this.orm, "count_set", l.product_id, l.counted);
            }
        }
        this.timers = {};
        const r = await api(this.orm, "count_submit", emp.id, pin);
        if (r.error) {
            return r.message;
        }
        this.state.sign = false;
        this.toast(TX.countSent, "success");
        await this.load();
        return null;
    }
}

/** The tab: two sub-tabs, as on the site. */
export class StockTab extends Component {
    static template = "navitta_ops.StockTab";
    static components = { DisplayCheck, StockCount };
    static props = {};
    setup() {
        let tab = "display";
        try {
            tab = localStorage.getItem("nav-stock-tab") || "display";
        } catch {
            tab = "display";
        }
        this.state = useState({ tab: tab === "count" ? "count" : "display" });
    }
    go(tab) {
        this.state.tab = tab;
        try {
            localStorage.setItem("nav-stock-tab", tab);
        } catch {
            // private mode: the sub-tab just isn't remembered
        }
    }
}
