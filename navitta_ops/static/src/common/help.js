/** @odoo-module **/
// Help bulbs, the guided tour and the light/dark switch of the Navitta screens (site: components/help.tsx and
// ThemeToggle in lib/i18n.tsx, Navitta Pro 26.3).
//
// - Every icon with a data-help id gets a 💡 bulb: tapping it explains what the icon is for and how to use it.
// - After each sign-in a welcome shows once, then a step per icon present on the screen (spotlighted). The checkbox
//   «Don't show it again» is remembered on this device; the «💡 Help» button in the header replays it any time.
// - The light / dark switch is remembered on this device; until chosen, the device setting decides. Dark mode is a
//   class on the .o_navitta root (CSS variables), Odoo's own theme is not touched.
// Browser storage is wrapped in try/catch everywhere: in a private window the screens work, just without memory.
import { Component, onMounted, onWillUnmount, reactive, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { lazy } from "./utils";

const ls = {
    get(k) {
        try {
            return localStorage.getItem(k);
        } catch {
            return null;
        }
    },
    set(k, v) {
        try {
            if (v === null || v === undefined) {
                localStorage.removeItem(k);
            } else {
                localStorage.setItem(k, v);
            }
        } catch {
            // private mode: just not remembered
        }
    },
};

// ---------------------------------------------------------------- texts (branch: to the employee, admin: to the manager)
const H = (icon, title, what, how) => ({ icon, title, what, how });
export const HELP = lazy({
    "b-counter": () => H("⏱️", _t("Visit counter"), _t("The main page: your check-in and check-out, the break, and the counter of every customer who comes into the shop."),
        [_t("Check in with your PIN as soon as you arrive."), _t("When a customer comes in tap “Customer in”, and when she leaves “Customer out” and choose what happened."),
            _t("The break is the ☕ button, and the check-out at the end of the shift with your PIN.")]),
    "b-inbox": () => H("🔔", _t("My alerts"), _t("Every reply from management reaches you here: leaves, excuses, penalties and rules. The red number means there is an alert you haven't read."),
        [_t("Tap “My alerts”."), _t("Choose your name and enter your PIN."), _t("Read the alert, and if there is a penalty or a new rule, sign that you read it.")]),
    "b-requests": () => H("📦", _t("Branch requests"), _t("Here you request supplies and missing models, record expenses and defective items, and receive shipments."),
        [_t("Choose the kind of request at the top."), _t("Fill in the request and attach a photo if needed."), _t("Follow the request's status, and when the shipment arrives scan it and receive it.")]),
    "b-report": () => H("📝", _t("Daily report"), _t("The end-of-shift report for management: sales, cash and card, the photos, and the day's key events."),
        [_t("At the end of the shift open “Daily report”."), _t("Cash and card come from the Point of Sale; match them with the terminal, and if there is a difference write the reason."),
            _t("Photograph the settlement and the invoices and tap “Send”.")]),
    "b-stock": () => H("🔎", _t("Display & stock"), _t("Check that every model in stock is on display, and count the stock room; both come from the branch's Odoo stock."),
        [_t("“👀 Display check”: mark each model on display or not (with the reason), then send it with your PIN."),
            _t("“📦 Stock count”: scan every piece or type the quantities; you don't see Odoo's figures."),
            _t("Send the count with your PIN; Odoo's stock changes only when management approves it.")]),
    "b-search": () => H("🔎", _t("Find a model"), _t("You find out whether a model is available, its price, sizes and colors, without searching the stockroom."),
        [_t("Tap “Find a model”."), _t("Type the model number or scan the barcode with the camera."), _t("If the size isn't there, record it as a “Lost sale” so management can provide it.")]),
    "b-target": () => H("🎯", _t("Today's and this month's target"), _t("Shows today's target, how much you achieved, how much is left, and this month's target percentage."),
        [_t("The figures update by themselves from the Point of Sale."), _t("The green bar grows as sales increase."), _t("“Edit target” needs the management password.")]),
    "b-float": () => H("💰", _t("Today's float"), _t("Count the float at the start and the end of the shift, so any shortage is known right away."),
        [_t("Start of the shift: count the money by denomination and photograph it."), _t("Your colleague confirms the count with her PIN."), _t("End of the shift: count it again before the report.")]),
    "b-tasks": () => H("📋", _t("Today's tasks"), _t("The tasks of each period: start, middle and end of the shift. They must be done before check-out."),
        [_t("Tap “Show” to see the tasks."), _t("Tap a task when you finish it; some of them ask for a photo."), _t("If a task is missed, management gets an alert.")]),
    "b-emp": () => H("👩", _t("Employee card"), _t("A card for each employee: her attendance, break, visits and sales today."),
        [_t("Each one works from her own card only."), _t("Check-in, check-out and break, all with your PIN."), _t("The bell 🔔 on your name means there is a new alert for you.")]),
    "a-home": () => H("🏠", _t("Dashboard"), _t("Today's summary: sales, attendance, leave requests, the excuses awaiting your decision, and the employees' messages."),
        [_t("Start with “Awaiting your decision” and decide each case."), _t("Approve or reject leaves and change requests."), _t("Reply to the employees from “Employee messages”.")]),
    "a-sales": () => H("📈", _t("Sales and targets"), _t("Each branch's sales day by day, with the target, the refunds, and the cash and card difference."),
        [_t("Choose the branch and the period."), _t("⚠️ next to the date means something changed after closing; tap it to see the details."), _t("Upload the targets from “Upload target file”.")]),
    "a-requests": () => H("📦", _t("Branch requests"), _t("All the branches' requests: supplies, missing models, expenses, defective items and shipments."),
        [_t("Approve or reject the request with a note."), _t("Prepare the shipment and upload the models being shipped."), _t("Follow what arrived and what's missing after receiving.")]),
    "a-lost": () => H("💸", _t("Lost sales"), _t("The models and sizes customers asked for that weren't available."),
        [_t("See the most requested."), _t("Turn them into a supply request."), _t("Contact the customer once it's available.")]),
    "a-day": () => H("📅", _t("Daily log"), _t("The month calendar: each day's color follows the target, with marks for the report, lateness, differences and the float."),
        [_t("Choose a branch or all branches."), _t("Tap any day and its details open below."), _t("You can delete any record with 🗑️ and the delete password.")]),
    "a-settings": () => H("⚙️", _t("Settings"), _t("Branches, employees and PINs, shift and task times, and the delete password."),
        [_t("Add an employee or change her PIN; the PIN reaches her by email."), _t("Change the shift, break and chances."), _t("Set the delete password and see the delete log.")]),
    "a-rules": () => H("📜", _t("Rules and violations"), _t("The penalty table, the branch rules, this month's violations, and the penalties for payroll."),
        [_t("Decide the violation: rejected, a grace chance, or an accepted excuse with a reason."), _t("Add or edit the branch rules; the employees sign them."),
            _t("At the end of the month download the penalties file for the accountant.")]),
    "a-ideas": () => H("💡", _t("Ideas"), _t("Improvement ideas you want in the program, ordered by priority."),
        [_t("Write the idea."), _t("Set its priority."), _t("Mark it when it's done.")]),
});

export const TOUR = {
    branch: ["b-counter", "b-emp", "b-target", "b-search", "b-float", "b-tasks", "b-inbox", "b-requests", "b-stock", "b-report"],
    admin: ["a-home", "a-sales", "a-day", "a-requests", "a-lost", "a-rules", "a-settings", "a-ideas"],
};
const TOUR_OFF = "nav-tour-off", TOUR_LOGIN = "nav-tour-login";

/** The open bulb or tour, shared by every bulb and the layer of the screen. */
export const helpStore = reactive({ tip: null, phase: "off", steps: [], i: 0, rect: null, never: false });

function tipBox(rect) {
    const vw = window.innerWidth, vh = window.innerHeight;
    const W = Math.min(340, vw - 24), TH = Math.min(460, vh * 0.7);     // the tip's height (at most 70% of the screen)
    const pos = !rect ? "mid" : rect.bottom + TH < vh ? "dn" : rect.top - TH > 0 ? "up" : "dock";
    const left = rect ? Math.max(12, Math.min(vw - W - 12, rect.left + rect.width / 2 - W / 2)) : (vw - W) / 2;
    const ax = rect ? Math.max(16, Math.min(W - 30, rect.left + rect.width / 2 - left - 7)) : W / 2;
    // physical left: the tip is placed by the screen coordinates of its icon in both directions
    const s = pos === "dn" ? `top:${rect.bottom + 14}px;` : pos === "up" ? `bottom:${vh - rect.top + 14}px;` : pos === "dock" ? "bottom:12px;" : "top:30%;";
    return { pos, style: `${s}left:${left}px;width:${W}px;--ax:${ax}px` };
}

/** 💡 inside an element with data-help: tap it for what the icon is for and how to use it. */
export class Bulb extends Component {
    static template = "navitta_ops.Bulb";
    static props = { id: String };
    get label() {
        const h = HELP[this.props.id];
        return h ? _t("Help: %s", h.title) : "";
    }
    open(ev) {
        const host = ev.currentTarget.parentElement;
        helpStore.tip = { id: this.props.id, rect: host.getBoundingClientRect() };
    }
    key(ev) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            ev.stopPropagation();
            this.open(ev);
        }
    }
}

/** The tooltip of a bulb or of a tour step. */
export class HelpTip extends Component {
    static template = "navitta_ops.HelpTip";
    static props = { id: String, rect: { optional: true }, step: { optional: true }, onNext: { type: Function, optional: true },
        onSkip: { type: Function, optional: true }, onClose: { type: Function, optional: true } };
    get h() {
        return HELP[this.props.id];
    }
    get box() {
        return tipBox(this.props.rect);
    }
    get stepText() {
        return _t("Step %(n)s of %(m)s", { n: this.props.step[0], m: this.props.step[1] });
    }
    get nextText() {
        return this.props.step[0] === this.props.step[1] ? _t("Done ✓") : _t("Next →");
    }
    get dots() {
        return Array.from({ length: this.props.step[1] }, (_v, i) => i);
    }
}

/** The bulbs' tooltip and the tour of the screen: mounted once in the screen's root. role: branch | admin. */
export class HelpLayer extends Component {
    static template = "navitta_ops.HelpLayer";
    static components = { HelpTip };
    static props = { role: String, name: { type: String, optional: true }, login: { optional: true } };
    setup() {
        this.s = useState(helpStore);
        Object.assign(helpStore, { tip: null, phase: "off", steps: [], i: 0, rect: null, never: false });
        this.fem = this.props.role === "branch";
        this.onStart = () => this.start();
        this.onMove = () => this.place();
        onMounted(() => {
            window.addEventListener("nav-tour", this.onStart);
            window.addEventListener("resize", this.onMove);
            window.addEventListener("scroll", this.onMove, true);
            // once after each sign-in (the user's latest login time), unless «don't show it again» on this device
            const login = this.props.login || "";
            if (login && ls.get(TOUR_LOGIN) !== login) {
                ls.set(TOUR_LOGIN, login);
                if (!ls.get(TOUR_OFF)) {
                    this.timer = setTimeout(() => this.start(), 900);       // let the page draw first
                }
            }
        });
        onWillUnmount(() => {
            clearTimeout(this.timer);
            window.removeEventListener("nav-tour", this.onStart);
            window.removeEventListener("resize", this.onMove);
            window.removeEventListener("scroll", this.onMove, true);
            Object.assign(helpStore, { tip: null, phase: "off", rect: null });
        });
    }
    get hello() {
        return _t("Hi %s!", this.props.name || "");
    }
    // the branch texts speak to the employee, the management ones to the manager (two Arabic forms, so two texts)
    get motto() {
        return this.fem ? _t("Open your eyes and mind, come with me") : _t("Open your eyes and mind with me");
    }
    get intro() {
        return this.fem ? _t("Let me show you what each icon on this page is for and how you use it. It only takes a minute.")
            : _t("Let me show you what each icon on the page is for and how to use it. It only takes a minute.");
    }
    get startText() {
        return this.fem ? _t("Start the tour") : _t("Start the explanation");
    }
    get neverText() {
        return this.fem ? _t("Don't show it to me again") : _t("Don't show it again");
    }
    start() {
        helpStore.tip = null;
        helpStore.i = 0;
        helpStore.never = false;
        helpStore.phase = "hi";
    }
    run() {
        const steps = TOUR[this.props.role].filter((id) => document.querySelector(`.o_navitta [data-help="${id}"]`));
        if (!steps.length) {
            return this.end();
        }
        helpStore.steps = steps;
        helpStore.i = 0;
        helpStore.phase = "run";
        this.focus();
    }
    focus() {
        const el = document.querySelector(`.o_navitta [data-help="${helpStore.steps[helpStore.i]}"]`);
        if (!el) {
            return this.next();
        }
        el.scrollIntoView({ block: el.offsetHeight > window.innerHeight * 0.45 ? "start" : "center", behavior: "instant" });
        helpStore.rect = el.getBoundingClientRect();
    }
    place() {
        if (helpStore.phase === "run") {
            const el = document.querySelector(`.o_navitta [data-help="${helpStore.steps[helpStore.i]}"]`);
            helpStore.rect = el ? el.getBoundingClientRect() : null;
        }
    }
    next() {
        if (helpStore.i + 1 < helpStore.steps.length) {
            helpStore.i++;
            this.focus();
        } else {
            this.end();
        }
    }
    end() {
        if (helpStore.never) {
            ls.set(TOUR_OFF, "1");
        }
        Object.assign(helpStore, { phase: "off", rect: null, tip: null });
    }
    hole(r) {
        return `left:${r.left - 5}px;top:${r.top - 5}px;width:${r.width + 10}px;height:${r.height + 10}px`;
    }
}

/** Header button: replays the explanation any time. */
export class TourBtn extends Component {
    static template = "navitta_ops.TourBtn";
    static props = {};
    play() {
        window.dispatchEvent(new Event("nav-tour"));
    }
}

// ---------------------------------------------------------------- light / dark
const THEME = "nav-theme";
function initialTheme() {
    const v = ls.get(THEME);
    if (v === "dark" || v === "light") {
        return v;
    }
    try {
        return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
        return "light";
    }
}
export const themeStore = reactive({ theme: initialTheme() });

/** «☀️ Light / 🌙 Dark», remembered on this device. */
export class ThemeToggle extends Component {
    static template = "navitta_ops.ThemeToggle";
    static props = {};
    setup() {
        this.t = useState(themeStore);
    }
    pick(v) {
        themeStore.theme = v;
        ls.set(THEME, v);
    }
}

/** For the screens' root: { dark } follows the switch. */
export function useTheme() {
    return useState(themeStore);
}
