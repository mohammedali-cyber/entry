/** @odoo-module **/
// The day log as a month calendar (site: components/daycal.tsx): each day shows sales against the target in colour
// (hit / 80–99% / under 80%), with marks for the report, lateness, a cash/card difference, a float short, refunds and
// changes after closing. Tapping a day opens it below. One branch or all branches.
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { DIR, WD, api, f0, monthName, today } from "../common/utils";

const shiftMonth = (m, n) => {
    const d = new Date(Date.UTC(+m.slice(0, 4), +m.slice(5, 7) - 1 + n, 1));
    return d.toISOString().slice(0, 10);
};

export class DayCalendar extends Component {
    static template = "navitta_ops.DayCalendar";
    static props = { branchId: { type: Number, optional: true }, day: String, onPick: Function };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ m: this.props.day.slice(0, 8) + "01", d: null });
        this.WD = WD;
        this.f0 = f0;
        this.prevArrow = DIR === "rtl" ? "→" : "←";
        this.nextArrow = DIR === "rtl" ? "←" : "→";
        onWillStart(() => this.load());
        onWillUpdateProps(async (np) => {
            const m = np.day.slice(0, 8) + "01";
            if (m !== this.state.m || (np.branchId || 0) !== (this.props.branchId || 0)) {
                this.state.m = m;
                await this.load(np.branchId);
            }
        });
    }
    async load(br = this.props.branchId) {
        const my = (this.req = (this.req || 0) + 1);
        const d = await api(this.orm, "admin_day_calendar", this.state.m, br || false);
        if (!d.error && my === this.req) {
            this.state.d = d;
        }
    }
    async go(n) {
        this.state.m = shiftMonth(this.state.m, n);
        await this.load();
    }
    get label() {
        return monthName(this.state.m);
    }
    get canNext() {
        return this.state.m < today().slice(0, 8) + "01";
    }
    get v() {
        const m = this.state.m, d = this.state.d, D = (d && d.days) || {}, now = today();
        const first = new Date(m + "T00:00:00Z").getUTCDay();
        const len = new Date(Date.UTC(+m.slice(0, 4), +m.slice(5, 7), 0)).getUTCDate();
        const cells = Array.from({ length: len }, (_v, i) => m.slice(0, 8) + String(i + 1).padStart(2, "0"));
        const nB = (d && d.branches) || 1;
        const past = cells.filter((x) => x < now);
        const sum = (arr, k) => arr.reduce((s, x) => s + ((D[x] && D[x][k]) || 0), 0);
        const pT = sum(past, "target"), pS = sum(past, "sales");
        return {
            blanks: Array.from({ length: first }, (_v, i) => i),
            days: cells.map((day) => this.cell(day, D[day], now, nB)),
            mS: sum(cells, "sales"), mT: sum(cells, "target"),
            pct: pT ? Math.round((pS / pT) * 100) + "%" : "—",
            hit: past.filter((x) => D[x] && D[x].target && D[x].sales >= D[x].target).length, past: past.length,
            nLate: sum(past, "late"), nDiff: past.filter((x) => Math.abs((D[x] && D[x].diff) || 0) >= 1).length, nFl: sum(past, "float"),
        };
    }
    cell(day, x, now, nB) {
        const fut = day > now, isT = day === now;
        const p = x && x.target ? x.sales / x.target : null;
        const k = fut || !x || !x.has || p === null ? "" : p >= 1 ? "g" : p >= 0.8 ? "w" : "b";
        const marks = [];
        if (!fut && x) {
            if (x.rep > 0) {
                marks.push(["g", "📝" + (nB > 1 ? `${x.rep}/${nB}` : "✓"), ""]);
            }
            if (!isT && x.has && x.rep < nB) {
                marks.push(["b", "📝✕", ""]);
            }
            if (x.late) {
                marks.push(["w", "⏰" + x.late, ""]);
            }
            if (Math.abs(x.diff) >= 1) {
                marks.push(["b", "⚠️" + (x.diff > 0 ? "+" : "−") + f0(Math.abs(x.diff)), ""]);
            }
            if (x.float) {
                marks.push(["b", "💰", ""]);
            }
            if (x.refund) {
                marks.push(["", "↩️" + f0(x.refund), ""]);
            }
            if (x.chg) {
                marks.push(["w", "✎", _t("Changed after closing")]);
            }
        }
        return {
            day, x, fut, isT, p, k, marks, n: +day.slice(8),
            cls: "dcal-d" + (fut ? " fut" : "") + (isT ? " now" : "") + (day === this.props.day ? " sel" : "") + (isT || !k ? "" : " " + k),
            pctText: p !== null && x && x.has && !fut ? Math.round(p * 100) + "%" : "",
            sales: x && x.has ? f0(x.sales) : "—",
            target: x && x.target ? (fut ? _t("Target %s", f0(x.target)) : _t("of %s", f0(x.target))) : "",
            bar: p !== null && !fut ? `width:${Math.min(100, p * 100)}%` : "",
            dotLate: !fut && x && x.late, dotBad: !fut && x && (Math.abs(x.diff) >= 1 || x.float),
        };
    }
    hitText(v) {
        return _t("%(n)s of %(m)s", { n: v.hit, m: v.past });
    }
    alertsText(v) {
        return _t("%(a)s late · %(b)s difference · %(c)s float", { a: v.nLate, b: v.nDiff, c: v.nFl });
    }
}
