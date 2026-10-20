/** @odoo-module **/
// Management side of «🔎 المعروض والجرد» (Navitta Pro 26.6), inside «📦 Branch requests» → «🔎 Display & stock»:
// the branches' display checks (the models not on display, with the reason) and their stock counts with the differences
// from Odoo. A count is approved (Odoo's stock is adjusted to the counted figures), rejected, or sent back for a recount.
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { api, clock, f2, jn, longDate } from "../common/utils";

const STATEC = { submitted: "warn", approved: "good", rejected: "bad", draft: "neutral" };

export class StockReviews extends Component {
    static template = "navitta_ops.StockReviews";
    static props = { br: { optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, notes: {}, confirm: 0, open: {}, busy: 0 });
        Object.assign(this, { clock, f2, jn, longDate, STATEC });
        this.TXapplied = _t("Applied");
        this.TXtoApply = _t("To apply");
        this.TXrecount = _t("🔁 Recount requested");
        onWillStart(() => this.load(this.props.br));
        onWillUpdateProps((np) => np.br !== this.props.br && this.load(np.br));
    }
    async load(br) {
        const d = await api(this.orm, "admin_stock_reviews", br || false);
        if (!d.error) {
            this.state.d = d;
        }
    }
    get waiting() {
        return this.state.d.counts.filter((c) => c.state === "submitted");
    }
    get others() {
        return this.state.d.counts.filter((c) => c.state !== "submitted");
    }
    get checks() {
        return this.state.d.checks;
    }
    what(r) {
        return jn([r.item_no, r.model && r.model !== r.item_no && r.model, r.color, r.size]);
    }
    sgn(n) {
        return (n > 0 ? "+" : "") + f2(n);
    }
    checkLine(c) {
        return c.state === "done"
            ? _t("%(n)s models in stock · %(s)s on display · %(m)s not on display", { n: c.total, s: c.shown, m: c.missing })
            : _t("In progress: %(k)s of %(n)s models checked", { k: c.shown + c.missing, n: c.total });
    }
    countLine(c) {
        return _t("%(p)s pieces counted · %(l)s lines · %(d)s with a difference", { p: f2(c.counted_total), l: c.lines_total, d: c.diff_count });
    }
    sentLine(c) {
        return c.submitted_at ? _t("Sent by %(n)s · %(d)s %(t)s", { n: c.employee || "—", d: longDate(c.submitted_at), t: clock(c.submitted_at) }) : "";
    }
    decidedLine(c) {
        return c.decided_at ? _t("%(s)s by %(n)s · %(d)s", { s: c.state_label, n: c.decided_by || "—", d: longDate(c.decided_at) }) : "";
    }
    toApply(c) {
        return c.rows.filter((r) => r.diff_now).length;
    }
    confirmText(c) {
        return _t("%(n)s variants will be adjusted in Odoo's stock to the counted figures (against the quantity now). Continue?", { n: this.toApply(c) });
    }
    async decide(c, decision) {
        const note = (this.state.notes[c.id] || "").trim();
        if (decision !== "approve" && !note) {
            this.notification.add(_t("Write a note for the branch"), { type: "warning" });
            return;
        }
        this.state.busy = c.id;
        const r = await api(this.orm, "admin_count_decide", c.id, decision, note || null);
        this.state.busy = 0;
        this.state.confirm = 0;
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
        } else {
            this.notification.add(decision === "approve" ? _t("Approved: Odoo's stock was adjusted") : decision === "reject"
                ? _t("Count rejected; the stock is unchanged") : _t("Recount requested; the stock is unchanged"), { type: "success" });
        }
        await this.load(this.props.br);
    }
}
