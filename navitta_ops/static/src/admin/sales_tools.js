/** @odoo-module **/
// Sales (26.3): correcting a day by hand with its edit log, the figures that changed after the day was closed, and the
// target file (Excel / CSV) with a preview before it is applied.
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { ago, api, downloadCsv, f0, longDate, DIR } from "../common/utils";
import { NvModal } from "../common/components";

const ARROW = DIR === "rtl" ? "←" : "→";
const FIELDS = [["target", _t("🎯 Target")], ["sales", _t("💰 Sales")], ["invoices", _t("🧾 Invoices")], ["items", _t("🛍️ Items")],
    ["returns", _t("🔄 Returns")], ["cash", _t("💵 Cash")], ["card", _t("💳 Card")], ["deposit", _t("🏦 Deposit")]];
const str = (x) => (x === null || x === undefined || x === false ? "" : String(+x));

/** «✏️ Edit a day»: the old figure next to each box, a required reason, and the log of earlier edits. */
export class DayEdit extends Component {
    static template = "navitta_ops.DayEdit";
    static components = { NvModal };
    static props = { branchId: Number, day: String, onClose: Function, onSaved: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.FIELDS = FIELDS;
        Object.assign(this, { f0, ago, longDate, ARROW });
        this.state = useState({ d: null, v: {}, why: "", err: "", busy: false });
        onWillStart(async () => {
            const d = await api(this.orm, "admin_day_get", this.props.branchId, this.props.day);
            if (d.error) {
                this.state.err = d.message;
                return;
            }
            this.state.d = d;
            this.state.v = Object.fromEntries(FIELDS.map(([k]) => [k, str(d.values[k])]));
        });
    }
    setV(k, v) {
        this.state.v[k] = v;
    }
    old(k) {
        return str(this.state.d.values[k]);
    }
    changed(k) {
        return this.old(k) !== String(this.state.v[k] ?? "");
    }
    beforeText(k) {
        const o = this.old(k), v = this.state.v[k];
        return _t("Before:") + " " + (o === "" ? "—" : f0(+o)) + (this.changed(k) ? ` ${ARROW} ` + (v === "" ? "—" : f0(+v)) : "");
    }
    get subtitle() {
        const d = this.state.d;
        return longDate(d.day) + (d.system ? " · " + _t("The figures come from the Point of Sale; your edit pins them") : "");
    }
    get log() {
        // one entry per save: same time and reason
        const out = [];
        for (const x of this.state.d.log) {
            const last = out[out.length - 1];
            const line = `${x.label}: ${f0(x.old)} ${ARROW} ${f0(x.new)}`;
            if (last && last.at === x.at && last.reason === x.reason) {
                last.lines.push(line);
            } else {
                out.push({ at: x.at, reason: x.reason, by: x.by, lines: [line] });
            }
        }
        return out;
    }
    async save() {
        if (!FIELDS.some(([k]) => this.changed(k))) {
            this.state.err = _t("You didn't change anything");
            return;
        }
        if (!this.state.why.trim()) {
            this.state.err = _t("Write the reason for the edit");
            return;
        }
        this.state.busy = true;
        const vals = Object.fromEntries(FIELDS.filter(([k]) => this.changed(k)).map(([k]) => [k, this.state.v[k]]));
        const r = await api(this.orm, "admin_day_edit", this.props.branchId, this.props.day, vals, this.state.why);
        this.state.busy = false;
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.notification.add(_t("The day was corrected"), { type: "success" });
        this.props.onSaved();
    }
}

/** «⚠️ Changed after closing»: the figures that changed after the employee sent the day's report. */
export class DayChanges extends Component {
    static template = "navitta_ops.DayChanges";
    static components = { NvModal };
    static props = { day: String, branchId: { optional: true }, onClose: Function };
    setup() {
        this.orm = useService("orm");
        Object.assign(this, { f0, ago, longDate });
        this.state = useState({ rows: null });
        onWillStart(async () => {
            const r = await api(this.orm, "admin_day_changes", this.props.day, this.props.branchId || false);
            this.state.rows = r.error ? [] : r.rows;
        });
    }
}

/** «📤 Upload target file»: template, Excel / CSV upload, preview against the current targets, then «Approve». */
export class TargetFile extends Component {
    static template = "navitta_ops.TargetFile";
    static components = { NvModal };
    static props = { onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.fileRef = useRef("file");
        Object.assign(this, { f0, ARROW });
        this.state = useState({ open: false, file: "", rows: null, msg: "", busy: false, all: false });
    }
    async template() {
        const t = await api(this.orm, "admin_targets_template");
        if (!t.error) {
            downloadCsv(t.name, t.rows);
        }
    }
    async read(ev) {
        const f = ev.target.files[0];
        ev.target.value = "";
        if (!f) {
            return;
        }
        Object.assign(this.state, { msg: _t("Reading the file…"), rows: null, file: f.name, all: false });
        const b64 = await new Promise((resolve) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result).split(",")[1] || "");
            r.onerror = () => resolve("");
            r.readAsDataURL(f);
        });
        const res = await api(this.orm, "admin_targets_preview", b64, f.name);
        if (res.error) {
            this.state.msg = "⚠️ " + res.message;
            return;
        }
        this.state.rows = res.rows;
        this.state.msg = "";
    }
    get ok() {
        return (this.state.rows || []).filter((r) => !r.err);
    }
    get bad() {
        return (this.state.rows || []).filter((r) => r.err);
    }
    isChanged(r) {
        return r.old !== null && r.old !== undefined && +r.old !== +r.target;
    }
    get changedRows() {
        return this.ok.filter((r) => this.isChanged(r));
    }
    get fresh() {
        return this.ok.filter((r) => r.old === null || r.old === undefined);
    }
    get shown() {
        const all = [...this.bad, ...this.changedRows, ...this.fresh];
        return this.state.all ? all : all.slice(0, 8);
    }
    get hiddenCount() {
        return this.bad.length + this.changedRows.length + this.fresh.length - 8;
    }
    get byBranch() {
        const t = {};
        for (const r of this.ok) {
            t[r.bname] = (t[r.bname] || 0) + +r.target;
        }
        return Object.entries(t);
    }
    num(v) {
        // a figure of the file as it is shown in the preview (the wrong ones as typed)
        return v === "" || v === null || v === undefined ? "—" : Number.isFinite(+v) ? f0(+v) : String(v);
    }
    rowCls(r) {
        return r.err ? "e" : this.isChanged(r) ? "r" : "";
    }
    status(r) {
        return r.err ? "✕ " + r.err : r.old === null || r.old === undefined ? "✓ " + _t("New") : this.isChanged(r)
            ? "⟳ " + _t("Was %(a)s %(arrow)s becomes %(b)s", { a: f0(r.old), b: f0(+r.target), arrow: ARROW }) : "= " + _t("Same");
    }
    get sums() {
        const n = this.ok.length, f = this.fresh.length, c = this.changedRows.length;
        return {
            fresh: f && "✓ " + _t("%s new days", f), changed: c && "⟳ " + _t("%s days will change", c),
            same: n - f - c && "= " + _t("%s unchanged", n - f - c), bad: this.bad.length && "✕ " + _t("%s rows with an error, not saved", this.bad.length),
        };
    }
    get approveLabel() {
        return "✓ " + _t("Approve %s rows", this.ok.length);
    }
    get moreLabel() {
        return this.state.all ? _t("Show less ▴") : _t("+ %s more rows ▾", this.hiddenCount);
    }
    get rowsCount() {
        return _t("%s rows", (this.state.rows || []).length);
    }
    get fileLabel() {
        return this.state.file || _t("Choose an Excel or CSV file");
    }
    get pickLabel() {
        return this.state.file ? _t("Change the file") : "📤 " + _t("Upload a file");
    }
    close() {
        Object.assign(this.state, { open: false, rows: null, file: "", msg: "" });
    }
    async save() {
        const ok = this.ok;
        this.state.busy = true;
        for (let i = 0; i < ok.length; i += 500) {
            const r = await api(this.orm, "admin_targets_load", ok.slice(i, i + 500), this.state.file);
            if (r.error) {
                this.state.busy = false;
                this.state.msg = "⚠️ " + r.message;
                return;
            }
        }
        this.state.busy = false;
        this.notification.add(_t("%s days saved from the file", ok.length), { type: "success" });
        this.close();
        this.props.onDone();
    }
}
