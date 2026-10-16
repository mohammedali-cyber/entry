/** @odoo-module **/
// Shipments (Navitta Pro 26.3): the shipment builder and log (management), the shipments on their way and receiving
// them by scanning (branch device). Shipments are Navitta records only (no Odoo stock moves), as on the site.
import { Component, onMounted, onWillUpdateProps, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { SHIPST, SUP, api, f0, jn, longDate, shipState } from "./utils";
import { FileThumb, NvModal, PhotoPicker, SignModal } from "./components";
import { DelBtn } from "./del";

const SUPS = ["roll_invoice", "roll_card", "bags"];
const sizeKey = (s) => parseFloat(s) || 0;
const TX = {
    size: (s) => _t("Size %s", s),
    pieces: (n) => _t("%s pieces", n),
    moreModels: (n) => _t("+ %s more models ▾", n),
    get showLess() { return _t("Show less ▴"); },
    sar: (a) => _t("%s SAR", a),
    get expenseCash() { return _t("💵 Expense cash"); },
    cashOf: (a) => _t("Cash %s SAR", a),
    shippedOn: (d) => _t("Shipped %s", d),
    arrivedOn: (d) => _t("Arrived %s", d),
    expected: (a, b) => _t("Expected %(a)s – %(b)s", { a, b }),
    shippedBy: (d) => _t("Shipped by management %s", d),
    modelsPieces: (n) => _t("Models (%s pieces)", n),
    arrivedOf: (a, b) => _t("%(a)s of %(b)s arrived", { a, b }),
    get scanPh() { return _t("Scan the item's barcode"); },
    get photoLabel() { return _t("📷 Photo of the rolls and bags that arrived *"); },
    get receiveTitle() { return _t("Receive shipment"); },
    get allArrived() { return _t("All of this item has already arrived"); },
    get notInShipment() { return _t("⚠️ This item isn't in the shipment! Put it aside and tell management"); },
    get doneOk() { return _t("Shipment received, balances updated"); },
    get doneShort() { return _t("Receipt recorded and the shortage sent to management"); },
    get reading() { return _t("Reading the file…"); },
    get shipped() { return _t("Shipped, the branch can see it"); },
    get yourRequest() { return _t("✓ Your request"); },
    get waiting() { return _t("📞 A customer is waiting"); },
    get notInFile() { return _t("Not in the models file"); },
    get urgent() { return _t("Urgent"); },
    get photo() { return _t("Photo"); },
    get cashMissing() { return _t("Expense cash"); },
    filePieces: (n, m) => _t("%(n)s pieces · %(m)s models", { n, m }),
    fileMissing: (n) => _t("⚠️ %s not in the models file", n),
    get fileAllFound() { return _t("All found in the models file"); },
};

/** Is a customer waiting (open lost sale with a phone) for this model / color / size? */
export function isWaiting(waiting, x) {
    return (waiting || []).some((w) => w.item_no && (x.item_no === w.item_no || String(x.model || "").includes(w.item_no))
        && (!w.color || w.color === x.color) && (!w.size || w.size === x.size));
}

// ---------------------------------------------------------------- the models of a shipment, grouped by model + color
export class ItemsTable extends Component {
    static template = "navitta_ops.ShipItems";
    static props = { items: Array, mark: { type: Function, optional: true } };
    setup() {
        this.state = useState({ all: false });
        this.TX = TX;
    }
    get groups() {
        const g = {};
        for (const x of this.props.items) {
            (g[[x.item_no || x.model, x.color].join("|")] ||= []).push(x);
        }
        return Object.values(g).map((rows, i) => ({
            key: i + "|" + (rows[0].item_no || "") + "|" + rows[0].model + "|" + (rows[0].color || ""),
            rows, first: rows[0], missing: rows.some((x) => x.in_catalog === false),
            sizes: rows.flatMap((x) => Array(Math.max(1, +x.qty || 1)).fill(x.size)).filter(Boolean).sort((a, b) => sizeKey(a) - sizeKey(b)).join(" · "),
            qty: rows.reduce((s, x) => s + (+x.qty || 0), 0),
            marks: this.props.mark ? this.props.mark(rows) : [],
        }));
    }
    get shown() {
        return this.state.all ? this.groups : this.groups.slice(0, 6);
    }
}

// ---------------------------------------------------------------- branch: receiving by scanning
export class ReceiveShipment extends Component {
    static template = "navitta_ops.ReceiveShipment";
    static components = { NvModal, PhotoPicker, SignModal };
    static props = { s: Object, emps: Array, onClose: Function, onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.TX = TX;
        this.SUP = SUP;
        this.KEY = "nav-recv-" + this.props.s.id;
        let got = {};
        try {
            got = JSON.parse(localStorage.getItem(this.KEY) || "{}") || {};
        } catch {
            got = {};
        }
        this.state = useState({ got, code: "", msg: null, photo: null, short: false, note: "", sign: false });
        this.inp = useRef("scan");
        onMounted(() => this.focus());
        // a keyboard-wedge scanner types the code and Enter wherever the focus is: send its keys to the scan box
        useExternalListener(window, "keydown", (e) => {
            if (this.state.sign || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) {
                return;
            }
            const t = e.target;
            if (t && t.closest && t.closest("input, textarea, select, [contenteditable], .nv-modal .nv-modal")) {
                return;
            }
            this.focus();
        });
    }
    focus() {
        if (this.inp.el && !this.state.short) {
            this.inp.el.focus();
        }
    }
    get lines() {
        const s = this.props.s;
        return [
            ...s.lines.map((x) => ({
                key: "l" + x.id, line_id: x.id, label: jn([x.item_no, x.model !== x.item_no && x.model]), sub: jn([x.color, x.size && TX.size(x.size)]),
                alu: x.alu ? String(x.alu).replace(/^0+/, "") : null, item_no: x.item_no, qty: Math.max(1, +x.qty || 1), scan: !!x.alu,
            })),
            ...SUPS.filter((k) => s[k] > 0).map((k) => ({ key: k, supply: k, label: SUP[k], sub: "", qty: +s[k], scan: false })),
            ...(+s.cash > 0 ? [{ key: "cash", supply: "cash", label: TX.expenseCash, sub: TX.sar(f0(s.cash)), qty: 1, scan: false }] : []),
        ];
    }
    left(l) {
        return l.qty - (this.state.got[l.key] || 0);
    }
    arrivedOf(l) {
        return Math.min(l.qty, this.state.got[l.key] || 0);
    }
    get total() {
        return this.lines.reduce((a, l) => a + l.qty, 0);
    }
    get arrived() {
        return this.lines.reduce((a, l) => a + this.arrivedOf(l), 0);
    }
    get rest() {
        return this.lines.filter((l) => this.left(l) > 0);
    }
    get restCount() {
        return this.rest.reduce((a, l) => a + this.left(l), 0);
    }
    get done() {
        return this.lines.filter((l) => (this.state.got[l.key] || 0) > 0);
    }
    get needPhoto() {
        return SUPS.some((k) => this.props.s[k] > 0);
    }
    get ready() {
        return !this.rest.length && (!this.needPhoto || !!this.state.photo);
    }
    get pct() {
        return this.total ? Math.round((this.arrived / this.total) * 100) : 0;
    }
    save() {
        try {
            localStorage.setItem(this.KEY, JSON.stringify(this.state.got));
        } catch {
            // private mode: the progress just isn't kept on this device
        }
    }
    add(l, n = 1) {
        this.state.got[l.key] = Math.min(l.qty, Math.max(0, (this.state.got[l.key] || 0) + n));
        this.save();
    }
    scan() {
        const c = this.state.code.trim().replace(/^0+/, "");
        this.state.code = "";
        if (!c) {
            return;
        }
        const ls = this.lines;
        const hit = ls.find((l) => l.scan && l.alu === c && this.left(l) > 0) || ls.find((l) => !l.scan && l.item_no === c && this.left(l) > 0);
        if (hit) {
            this.add(hit);
            this.state.msg = { k: "ok", t: "✓ " + hit.label + (hit.sub ? " · " + hit.sub : "") };
        } else if (ls.some((l) => l.alu === c || l.item_no === c)) {
            this.state.msg = { k: "warn", t: TX.allArrived };
        } else {
            this.state.msg = { k: "bad", t: TX.notInShipment };
        }
        this.focus();
    }
    onKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.scan();
        }
    }
    manual(l) {
        this.add(l, l.supply ? l.qty : 1);
        this.focus();
    }
    get missing() {
        return this.rest.map((l) => (l.line_id ? { line_id: l.line_id, qty: this.left(l) } : { supply: l.supply, qty: l.supply === "cash" ? 1 : this.left(l) }));
    }
    async send(emp, pin) {
        const st = this.state;
        const r = await api(this.orm, "shipment_receive", emp.id, pin, this.props.s.id, st.short ? this.missing : [], st.short ? st.note : null, st.photo);
        if (r.error) {
            return r.message;
        }
        try {
            localStorage.removeItem(this.KEY);
        } catch {
            // nothing kept
        }
        st.sign = false;
        this.notification.add(st.short ? TX.doneShort : TX.doneOk, { type: "success" });
        this.props.onDone();
        return null;
    }
}

// ---------------------------------------------------------------- branch: shipments on their way
export class IncomingShipments extends Component {
    static template = "navitta_ops.IncomingShipments";
    static components = { ItemsTable, ReceiveShipment };
    static props = { ships: Array, emps: Array, waiting: Array, onDone: Function };
    setup() {
        this.state = useState({ recv: null });
        Object.assign(this, { TX, SUP, SUPS, SHIPST, f0, longDate });
    }
    pieces(s) {
        return s.lines.reduce((a, x) => a + (+x.qty || 0), 0);
    }
    marker(s) {
        return (rows) => [
            ...(rows.some((x) => x.request_id) ? [["good", TX.yourRequest]] : []),
            ...(rows.some((x) => isWaiting(this.props.waiting, x)) ? [["good", TX.waiting]] : []),
        ];
    }
    done() {
        this.state.recv = null;
        this.props.onDone();
    }
}

// ---------------------------------------------------------------- management: build and send a shipment
export class ShipBuilder extends Component {
    static template = "navitta_ops.ShipBuilder";
    static components = { ItemsTable };
    static props = { branches: Array, reqs: Array, start: { optional: true }, eta: Array, waiting: Array, onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        Object.assign(this, { TX, SUP, SUPS, f0, longDate, jn });
        this.state = useState({ br: this.props.start || (this.props.branches[0] || {}).id || 0, pick: {}, file: null, msg: "", busy: false,
            note: "", sup: { roll_invoice: "", roll_card: "", bags: "", cash: "" } });
        onWillUpdateProps((np) => {
            if (np.start && np.start !== this.props.start) {
                this.setBranch(np.start);
            }
        });
    }
    setBranch(id) {
        Object.assign(this.state, { br: +id, pick: {}, sup: { roll_invoice: "", roll_card: "", bags: "", cash: "" } });
    }
    get open() {
        return this.props.reqs.filter((r) => r.branch_id === this.state.br && ["pending", "approved"].includes(r.state));
    }
    reqLabel(r) {
        if (r.kind === "goods") {
            return "👠 " + jn([r.item_no, r.model !== r.item_no && r.model, r.color, r.size && TX.size(r.size)]) + " × " + r.qty;
        }
        return (r.supply === "other" ? "📦 " + r.model : SUP[r.supply]) + " × " + r.qty;
    }
    reqWaiting(r) {
        return r.kind === "goods" && isWaiting(this.props.waiting.filter((w) => w.branch_id === r.branch_id), r);
    }
    toggle(r) {
        const on = !this.state.pick[r.id];
        this.state.pick[r.id] = on;
        if (r.kind === "supply" && SUPS.includes(r.supply)) {
            const v = Math.max(0, (+this.state.sup[r.supply] || 0) + (on ? r.qty : -r.qty));
            this.state.sup[r.supply] = v ? String(v) : "";
        }
    }
    get items() {
        const fromReqs = this.open.filter((r) => this.state.pick[r.id] && r.kind === "goods")
            .map((r) => ({ item_no: r.item_no, model: r.model, color: r.color, size: r.size, qty: r.qty, request_id: r.id }));
        return [...fromReqs, ...((this.state.file && this.state.file.items) || [])];
    }
    get pieces() {
        return this.items.reduce((s, x) => s + (+x.qty || 0), 0);
    }
    get canSend() {
        const sup = this.state.sup;
        return !this.state.busy && (this.pieces > 0 || SUPS.some((k) => +sup[k] > 0) || +sup.cash > 0);
    }
    get fileSummary() {
        const f = this.state.file;
        return "✓ " + TX.filePieces(f.pieces, f.models) + " · " + (f.missing ? TX.fileMissing(f.missing) : TX.fileAllFound);
    }
    async readFile(ev) {
        const f = ev.target.files[0];
        ev.target.value = "";
        if (!f) {
            return;
        }
        this.state.msg = TX.reading;
        const b64 = await new Promise((resolve) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result).split(",")[1] || "");
            r.onerror = () => resolve("");
            r.readAsDataURL(f);
        });
        const res = await api(this.orm, "admin_ship_file", b64, f.name);
        if (res.error) {
            this.state.msg = "⚠️ " + res.message;
            return;
        }
        this.state.file = { name: f.name, items: res.items, pieces: res.pieces, models: res.models, missing: res.missing };
        this.state.msg = "";
    }
    async send() {
        const st = this.state;
        st.busy = true;
        st.msg = "";
        const r = await api(this.orm, "admin_ship", st.br, {
            request_ids: this.open.filter((x) => st.pick[x.id]).map((x) => x.id), items: (st.file && st.file.items) || [],
            roll_invoice: +st.sup.roll_invoice || 0, roll_card: +st.sup.roll_card || 0, bags: +st.sup.bags || 0, cash: +st.sup.cash || 0, note: st.note,
        });
        st.busy = false;
        if (r.error) {
            st.msg = r.message;
            return;
        }
        this.notification.add(TX.shipped, { type: "success" });
        Object.assign(st, { file: null, note: "", pick: {}, sup: { roll_invoice: "", roll_card: "", bags: "", cash: "" } });
        this.props.onDone();
    }
}

// ---------------------------------------------------------------- management: recent shipments
export class ShipLog extends Component {
    static template = "navitta_ops.ShipLog";
    static components = { FileThumb, DelBtn };
    static props = { ships: Array, bName: Function, onDone: { type: Function, optional: true } };
    setup() {
        Object.assign(this, { TX, SUP, SUPS, SHIPST, shipState, f0, longDate });
    }
    pieces(s) {
        return s.lines.reduce((a, x) => a + (+x.qty || 0), 0);
    }
    dates(s) {
        return s.state === "received" ? TX.arrivedOn(longDate(s.received_day)) : TX.expected(longDate(s.eta_from), longDate(s.eta_to));
    }
}

