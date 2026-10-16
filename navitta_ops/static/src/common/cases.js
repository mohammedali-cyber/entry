/** @odoo-module **/
// Violations (late arrival / break overrun): chance dots and the management decision card (site: components/cases.tsx).
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { DIR, api, clock, longDate } from "./utils";

/** Chances as dots: filled = used; all red when they are used up. */
export class Dots extends Component {
    static template = "navitta_ops.Dots";
    static props = { used: Number, max: Number };
    get list() {
        return Array.from({ length: Math.max(0, this.props.max) }, (_, i) => i);
    }
    get label() {
        return _t("%(n)s of %(m)s", { n: this.props.used, m: this.props.max });
    }
}

/** One violation awaiting management: rejected (penalty from the table), a grace chance (while she has some), or an
 *  accepted excuse that counts as nothing (reason required); plus "accept duration change" for a break edit request.
 *  c.preview = {used, max, penalty: {text, tier, occ}} comes from the server. */
export class CaseDecide extends Component {
    static template = "navitta_ops.CaseDecide";
    static components = { Dots };
    static props = { c: Object, name: String, branch: String, onDone: Function };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ note: "", err: "", busy: false });
        this.longDate = longDate;
        this.ARROW = DIR === "rtl" ? "←" : "→";
    }
    get pv() {
        return this.props.c.preview || null;
    }
    get out() {
        return !!this.pv && this.pv.used >= this.pv.max;
    }
    get kind() {
        return this.props.c.kind === "break" ? _t("☕ Break overrun") : _t("Late arrival");
    }
    get line() {
        const c = this.props.c;
        if (c.kind === "break") {
            return _t("Break %(a)s min · over by %(m)s min", { a: c.break_used, m: c.minutes });
        }
        return _t("Late %s min", c.minutes) + (c.check_in ? " · " + _t("Arrived %s", clock(c.check_in)) : "");
    }
    get chancesLine() {
        const p = this.pv;
        return _t("Used %(n)s of %(m)s · %(r)s left", { n: p.used, m: p.max, r: p.max - p.used });
    }
    get chanceBtn() {
        const p = this.pv;
        return p && !this.out ? _t("🤝 Grace chance (%(n)s of %(m)s)", { n: p.used + 1, m: p.max }) : _t("🤝 Grace chance");
    }
    get usedUp() {
        return _t("Her chances are used up");
    }
    get occ() {
        return _t("occurrence %s", this.pv.penalty.occ);
    }
    async act(decision) {
        const note = this.state.note.trim();
        if (decision === "excuse" && !note) {
            this.state.err = _t("Write why the excuse is accepted");
            return;
        }
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        const r = await api(this.orm, "admin_decide_case", this.props.c.id, decision, note || null);
        this.state.busy = false;
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
            return;
        }
        const ok = {
            edit: _t("Duration change accepted"),
            penalty: _t("Rejected; the penalty was issued and sent to the employee"),
            chance: _t("Grace chance used"),
            excuse: _t("Excuse accepted; nothing was counted"),
        }[decision];
        this.notification.add(ok, { type: "success" });
        this.props.onDone();
    }
}
