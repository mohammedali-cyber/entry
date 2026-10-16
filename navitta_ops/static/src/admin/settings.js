/** @odoo-module **/
// Management settings tab (site: app/admin/settings, the parts that are not Odoo forms here): the shift toggles
// (branches with a morning shift, each employee's shift), the delete password with the delete log, and the current
// and upcoming leaves with 🗑️. Everything else stays in Navitta Branches → Settings.
import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { LANG, WD, ago, api, h12, longDate, today } from "../common/utils";
import { DelBtn } from "../common/del";

export class AdminSettings extends Component {
    static template = "navitta_ops.AdminSettings";
    static components = { DelBtn };
    static props = { f: { type: Object, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.state = useState({ d: null, cur: "", pw: "", busy: false });
        Object.assign(this, { ago, h12, longDate, today });
        onWillStart(() => this.load());
    }
    async load() {
        const d = await api(this.orm, "admin_settings");
        if (!d.error) {
            d.E = Object.fromEntries(d.employees.map((e) => [e.id, e]));
            this.state.d = d;
        }
    }
    toast(msg, type = "success") {
        this.notification.add(msg, { type });
    }
    bName(id) {
        return (this.state.d.branches.find((b) => b.id === id) || {}).name || "";
    }
    eName(id) {
        return (this.state.d.E[id] || {}).name || "—";
    }
    get br() {
        return (this.props.f && this.props.f.br) || 0;
    }
    get emps() {
        return this.state.d.employees.filter((e) => !this.br || e.branch_id === this.br);
    }
    get noAmText() {
        const sep = LANG === "ar" ? " و" : ", ";
        return _t("No morning shift on %s: morning employees work the evening shift.", this.state.d.settings.no_am_days.map((i) => WD[i]).join(sep));
    }
    twoShifts(id) {
        return !!(this.state.d.branches.find((b) => b.id === id) || {}).two_shifts;
    }
    async setTwo(b, on) {
        const r = await api(this.orm, "admin_set_two_shifts", b.id, on);
        if (r.error) {
            return this.toast(r.message, "danger");
        }
        this.toast(on ? _t("%s: now has a morning and an evening shift", b.name) : _t("%s: evening only", b.name));
        await this.load();
    }
    async setShift(e, k) {
        if (e.shift.shift === k) {
            return;
        }
        const r = await api(this.orm, "admin_set_shift", e.id, k);
        if (r.error) {
            return this.toast(r.message, "danger");
        }
        this.toast(_t("%(n)s is now on the %(s)s", { n: e.name, s: k === "am" ? _t("Morning shift") : _t("Evening shift") }));
        await this.load();
    }
    async savePw() {
        const s = this.state, set = s.d.delete.set;
        if (set && !s.cur) {
            return this.toast(_t("Enter the current delete password"), "warning");
        }
        s.busy = true;
        const r = await api(this.orm, "admin_set_delete_pw", s.pw, set ? s.cur : false);
        s.busy = false;
        if (r.error) {
            return this.toast(r.message, "danger");
        }
        s.pw = s.cur = "";
        this.toast(_t("Password saved"));
        await this.load();
    }
    logTitle(n) {
        return _t("📜 Delete log (%s)", n);
    }
    leaveWhat(l) {
        return `${this.eName(l.employee_id)} · ${_t("Leave: %s", l.type_label)} · ${longDate(l.from_day)} – ${longDate(l.to_day)}`;
    }
    leaveDates(l) {
        return _t("From %(a)s to %(b)s", { a: longDate(l.from_day), b: longDate(l.to_day) });
    }
    openSettings() {
        this.action.doAction("navitta_ops.action_navitta_settings");
    }
}
