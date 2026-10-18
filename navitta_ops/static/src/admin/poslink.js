/** @odoo-module **/
// Dashboard card: every point of sale with its orders, linked here to its Navitta branch and owner employee.
// The card opens by itself while a point of sale with orders is not linked to any branch (the dashboard sales stay
// at zero until then); otherwise it stays folded under a button. Only when navitta_ops_pos is installed.
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { api } from "../common/utils";

export class PosLink extends Component {
    static template = "navitta_ops.PosLink";
    static props = { onSaved: { type: Function, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, open: false, edit: {}, busy: 0 });
        onWillStart(() => this.load(true));
    }
    async load(first = false) {
        const d = await api(this.orm, "admin_pos_devices");
        if (d.error) {
            this.state.d = null;        // Point of Sale bridge not installed
            return;
        }
        this.state.d = d;
        this.state.edit = Object.fromEntries(d.devices.map((x) => [x.id, { branch: x.branch_id || 0, owner: x.owner_id || 0 }]));
        if (first && this.missing.length) {
            this.state.open = true;
        }
    }
    n(v) {
        return Number(v) || 0;          // OWL templates have no access to the Number global
    }
    get missing() {
        return (this.state.d?.devices || []).filter((x) => !x.branch_id && x.orders);
    }
    get linked() {
        return (this.state.d?.devices || []).filter((x) => x.branch_id).length;
    }
    employees(branchId) {
        return this.state.d.branches.find((b) => b.id === Number(branchId))?.employees || [];
    }
    changed(x) {
        const e = this.state.edit[x.id];
        return Number(e.branch) !== (x.branch_id || 0) || Number(e.owner) !== (x.owner_id || 0);
    }
    setBranch(x, value) {
        this.state.edit[x.id] = { branch: Number(value), owner: 0 };
    }
    async save(x) {
        const e = this.state.edit[x.id];
        this.state.busy = x.id;
        const r = await api(this.orm, "admin_pos_link", x.id, Number(e.branch) || false, Number(e.owner) || false);
        this.state.busy = 0;
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
            return;
        }
        this.notification.add(Number(e.branch) ? _t("Linked. Its sales were synced from the Point of Sale.") : _t("Unlinked"), { type: "success" });
        await this.load();
        this.props.onSaved?.();
    }
}
