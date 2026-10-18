/** @odoo-module **/
// Branch requests (Navitta Pro 26.3): the shortage list (several items, sent with one PIN) and defective items.
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { DFST, SREASONL, api, dcsText, f0, icon, jn, longDate, sizesOf, stockQty, swatch } from "../common/utils";
import { ModelPicker, PhotoLink, PhotoPicker, SignModal } from "../common/components";

const PHONE = /^(05\d{8}|9665\d{8})$/;
const TX = {
    size: (s) => _t("Size %s", s),
    sar: (a) => _t("%s SAR", a),
    get colorPh() { return _t("Type the color, e.g. BLACK"); },
    get sizePh() { return _t("Type the size"); },
    get enterItem() { return _t("Type the item number or scan the barcode"); },
    get chooseColor() { return _t("Choose the color"); },
    get chooseSize() { return _t("Choose the size"); },
    get badQty() { return _t("Invalid quantity"); },
    get chooseReason() { return _t("Choose the reason"); },
    get contactReq() { return _t("Customer request: write her name and mobile number"); },
    get writeReason() { return _t("Write the reason"); },
    get added() { return _t("Added to the list"); },
    get merged() { return _t("Already in the list: the quantity was increased"); },
    get tooMany() { return _t("Too many items in one list, send them in two lists"); },
    listOf: (n) => _t("List (%s)", n),
    sendAll: (n) => _t("Send all (%s) with one PIN", n),
    sent: (n) => _t("%s requests sent to management", n),
    get sendTitle() { return _t("Send shortage list"); },
    get chooseDefect() { return _t("Choose the defect type"); },
    get chooseSource() { return _t("Choose where the item came from"); },
    get describe() { return _t("Describe the defect"); },
    get photoFirst() { return _t("Photograph the defect first"); },
    get defectTitle() { return _t("Defective item"); },
    get defectSent() { return _t("Sent to management; their reply shows here"); },
    get defectPhoto() { return _t("📷 Defect photo *"); },
    get detailsReq() { return _t("Describe the defect *"); },
    get detailsOpt() { return _t("Description of the defect (optional)"); },
    get photo() { return _t("Photo"); },
};
const MAX_LIST = 50;
const blank = () => ({ m: null, manual: "", color: "", size: "", alu: "" });

/** Picks an item from the models file (item number or a scanned barcode), then its color and size; or free text. */
export class ItemChooser extends Component {
    static template = "navitta_ops.ItemChooser";
    static components = { ModelPicker };
    static props = { v: Object };
    setup() {
        this.v = useState(this.props.v);
        Object.assign(this, { TX, f0, icon, dcsText, swatch, stockQty });
    }
    pick(m, it) {
        Object.assign(this.v, { m, manual: "", color: (it && it.color) || "", size: (it && it.size) || "", alu: (it && it.alu) || "" });
    }
    manual(q) {
        Object.assign(this.v, blank(), { manual: q });
    }
    reset() {
        Object.assign(this.v, blank());
    }
    get hasSizes() {
        return !this.v.m || sizesOf(this.v.m).length > 0;
    }
    get sizes() {
        const m = this.v.m;
        if (!m) {
            return [];
        }
        const c = this.v.color && m.colors.find((x) => x.color === this.v.color);
        return c ? c.sizes : sizesOf(m);
    }
    setColor(c) {
        Object.assign(this.v, { color: this.v.color === c ? "" : c, size: "" });
    }
    setSize(z) {
        this.v.size = this.v.size === z ? "" : z;
    }
}

const desc = (x) => jn([x.item_no, x.model && x.model !== x.item_no && x.model, x.color, x.size && TX.size(x.size)]);

/** «👠 Shortage list»: items added one by one, then sent all together with one PIN. Kept on this device until sent. */
export class ShortageList extends Component {
    static template = "navitta_ops.ShortageList";
    static components = { ItemChooser, SignModal };
    static props = { branch: Number, emps: Array, reasons: Array, onSent: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.KEY = "nav-short-" + this.props.branch;
        let list = [];
        try {
            list = JSON.parse(localStorage.getItem(this.KEY) || "[]") || [];
        } catch {
            list = [];
        }
        this.state = useState({ list, v: blank(), n: 1, qty: 1, reason: "", cname: "", phone: "", note: "", err: "", sign: false });
        Object.assign(this, { TX, desc, SREASONL });
    }
    save(list) {
        this.state.list = list;
        try {
            localStorage.setItem(this.KEY, JSON.stringify(list));
        } catch {
            // private mode: the list just isn't kept on this device
        }
    }
    get sizesNeeded() {
        const m = this.state.v.m;
        return !!m && sizesOf(m).length > 0;
    }
    add() {
        const st = this.state, v = st.v, p = st.phone.replace(/\D/g, "");
        const err = (t) => (st.err = t);
        if (!v.m && !v.manual) return err(TX.enterItem);
        if (v.m && !v.color) return err(TX.chooseColor);
        if (this.sizesNeeded && !v.size) return err(TX.chooseSize);
        if (!(+st.qty > 0)) return err(TX.badQty);
        if (!st.reason) return err(TX.chooseReason);
        if (st.reason === "customer" && (!st.cname.trim() || !PHONE.test(p))) return err(TX.contactReq);
        if (st.reason === "other" && !st.note.trim()) return err(TX.writeReason);
        const x = { item_no: v.m ? v.m.item_no : "", model: v.m ? v.m.model : v.manual.trim(), color: v.color, size: v.size, qty: +st.qty, reason: st.reason,
            contact_name: st.reason === "customer" ? st.cname.trim() : "", contact_phone: st.reason === "customer" ? p : "", note: st.reason === "other" ? st.note.trim() : "" };
        const same = (y) => ["item_no", "model", "color", "size", "reason", "contact_name", "contact_phone", "note"].every((k) => (y[k] || "") === (x[k] || ""));
        const i = st.list.findIndex(same);
        if (i < 0 && st.list.length >= MAX_LIST) return err(TX.tooMany);
        if (i >= 0) {
            this.save(st.list.map((y, j) => (j === i ? { ...y, qty: y.qty + x.qty } : y)));
        } else {
            this.save([...st.list, x]);
        }
        Object.assign(st, { v: blank(), n: st.n + 1, qty: 1, reason: "", cname: "", phone: "", note: "", err: "" });
        this.notification.add(i >= 0 ? TX.merged : TX.added, { type: "success" });
    }
    remove(i) {
        this.save(this.state.list.filter((_x, j) => j !== i));
    }
    step(n) {
        this.state.qty = Math.max(1, (+this.state.qty || 1) + n);
    }
    async send(emp, pin) {
        const r = await api(this.orm, "request_batch", emp.id, pin, this.state.list);
        if (r.error) {
            return r.message;
        }
        this.state.sign = false;
        this.save([]);
        this.notification.add(TX.sent(r.n), { type: "success" });
        this.props.onSent();
        return null;
    }
}

/** «🩹 Defective item»: photo, defect type and where it came from, signed with a PIN; management's reply shows in the list. */
export class Defects extends Component {
    static template = "navitta_ops.Defects";
    static components = { ItemChooser, SignModal, PhotoPicker, PhotoLink };
    static props = { emps: Array, rows: Array, types: Array, sources: Array, onSent: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ v: blank(), n: 1, qty: 1, defect: "", source: "", details: "", photo: null, err: "", sign: false });
        Object.assign(this, { TX, DFST, longDate });
    }
    step(n) {
        this.state.qty = Math.max(1, (+this.state.qty || 1) + n);
    }
    go() {
        const st = this.state, v = st.v;
        const err = (t) => (st.err = t);
        if (!v.m && !v.manual) return err(TX.enterItem);
        if (!st.defect) return err(TX.chooseDefect);
        if (!st.source) return err(TX.chooseSource);
        if (st.defect === "other" && !st.details.trim()) return err(TX.describe);
        if (!st.photo) return err(TX.photoFirst);
        st.err = "";
        st.sign = true;
    }
    async send(emp, pin) {
        const st = this.state, v = st.v;
        const r = await api(this.orm, "create_defect", emp.id, pin, {
            item_no: v.m ? v.m.item_no : "", alu: v.alu || "", model: v.m ? v.m.model : v.manual, color: v.color, size: v.size, qty: +st.qty || 1,
            defect: st.defect, source: st.source, details: st.details }, st.photo);
        if (r.error) {
            return r.message;
        }
        Object.assign(st, { sign: false, v: blank(), n: st.n + 1, qty: 1, defect: "", source: "", details: "", photo: null });
        this.notification.add(TX.defectSent, { type: "success" });
        this.props.onSent();
        return null;
    }
    what(x) {
        return desc(x) + (x.qty > 1 ? " × " + x.qty : "");
    }
}
