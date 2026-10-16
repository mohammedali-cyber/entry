/** @odoo-module **/
// Shared helpers for the Navitta screens: formatting in the branch time zone, choice lists, server calls.
//
// Language / direction (Odoo 18):
// - LANG ("ar" | "en") and DIR ("rtl" | "ltr") are plain string constants computed once when this module is
//   evaluated, from `user.lang` (@web/core/user). `user` is built from the session info that Odoo inlines in the
//   page before any asset module runs, so it is already correct at import time. `localization` is NOT usable here:
//   it is only filled by the localization service after modules are evaluated (reading it earlier throws).
//   Any language other than Arabic renders left-to-right with the English source texts (translated by _t if a
//   translation exists). lang() / dir() return the same values for code that prefers a function.
// - Translated labels (WD, MONTHS, LOSTL, SUP, RQST...) are objects/arrays whose entries are getters that call
//   _t() when read, so they yield plain strings at render time (after the translations are loaded), never lazy
//   String objects. Keys and stored values are unchanged; never compare a translated label with ===.
// - Stored Arabic choice values (COLORS, TYPES, TRIED, SAT) stay in Arabic (they are saved in the database);
//   show them with vlabel(value), which gives the English label when LANG is "en".
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";

const RAW_LANG = String(user.lang || document.documentElement.getAttribute("lang") || "").toLowerCase();
export const LANG = RAW_LANG.startsWith("ar") ? "ar" : "en";
export const DIR = LANG === "ar" ? "rtl" : "ltr";
export const lang = () => LANG;
export const dir = () => DIR;
const LOCALE = LANG === "ar" ? "ar-SA-u-ca-gregory-nu-latn" : "en-GB";

/** Builds an object (or array) whose entries are getters: lazy({a: () => _t("A")}).a is translated on each read. */
export function lazy(spec) {
    const o = Array.isArray(spec) ? [] : {};
    for (const k of Object.keys(spec)) {
        Object.defineProperty(o, k, { get: spec[k], enumerable: true });
    }
    return o;
}

export const WD = lazy([
    () => _t("Sunday"), () => _t("Monday"), () => _t("Tuesday"), () => _t("Wednesday"), () => _t("Thursday"), () => _t("Friday"),
    () => _t("Saturday"),
]);
export const MONTHS = lazy([
    () => _t("January"), () => _t("February"), () => _t("March"), () => _t("April"), () => _t("May"), () => _t("June"),
    () => _t("July"), () => _t("August"), () => _t("September"), () => _t("October"), () => _t("November"), () => _t("December"),
]);

// lost-sale reasons: stored key -> label on the screens
export const LOST = ["size_na", "color_na", "price", "design", "quality", "browsing", "other", "manufacture"];
export const LOSTL = lazy({
    size_na: () => _t("📏 Size sold out"), color_na: () => _t("🎨 Color sold out"), price: () => _t("💰 Price"),
    design: () => _t("👎 Didn't like the design"), quality: () => _t("🧵 Quality"), browsing: () => _t("👀 Just browsing"),
    other: () => _t("✏️ Other"), manufacture: () => _t("🏭 Needs manufacturing"),
});
export const LOSTFULL = lazy({
    size_na: () => _t("Size not available"), color_na: () => _t("Color not available"), price: () => _t("Price too high"),
    design: () => _t("Design not suitable"), quality: () => _t("Didn't like the quality"), browsing: () => _t("Just browsing"),
    other: () => _t("Other"), manufacture: () => _t("Needs manufacturing"),
});
export const OUTOF = ["size_na", "color_na"];
export const NOBUY = ["size_na", "price", "design", "color_na", "quality", "browsing", "other"];
// stored values (saved in the daily report): keep them in Arabic and display them with vlabel()
export const COLORS = ["أسود", "بيج", "أبيض", "فضي", "ذهبي", "بني", "وردي", "كحلي"];
export const SIZES = ["36", "37", "38", "39", "40", "41"];
export const TYPES = ["كعب عالي", "صندل", "حذاء رياضي", "باليرينا", "بوت", "شنطة"];
export const TRIED = ["المقاس غير مريح", "السعر مرتفع", "اللون غير مناسب", "التصميم", "الكعب متعب"];
export const SAT = ["", "غير راضٍ", "أقل من المتوقع", "مقبول", "راضٍ", "راضٍ جدًا"];
const VLABELS = lazy({
    "أسود": () => _t("Black"), "بيج": () => _t("Beige"), "أبيض": () => _t("White"), "فضي": () => _t("Silver"),
    "ذهبي": () => _t("Gold"), "بني": () => _t("Brown"), "وردي": () => _t("Pink"), "كحلي": () => _t("Navy"),
    "كعب عالي": () => _t("High heels"), "صندل": () => _t("Sandals"), "حذاء رياضي": () => _t("Sneakers"),
    "باليرينا": () => _t("Ballerina flats"), "بوت": () => _t("Boots"), "شنطة": () => _t("Bag"),
    "المقاس غير مريح": () => _t("Uncomfortable fit"), "السعر مرتفع": () => _t("Price too high"),
    "اللون غير مناسب": () => _t("Color not suitable"), "التصميم": () => _t("Design"), "الكعب متعب": () => _t("Heel is uncomfortable"),
    "غير راضٍ": () => _t("Not satisfied"), "أقل من المتوقع": () => _t("Below expectations"), "مقبول": () => _t("Acceptable"),
    "راضٍ": () => _t("Satisfied"), "راضٍ جدًا": () => _t("Very satisfied"),
});
/** Display label of a stored Arabic choice value: the English label when the UI is not Arabic, else the value itself. */
export const vlabel = (value) =>
    LANG === "ar" || typeof value !== "string" || !Object.prototype.hasOwnProperty.call(VLABELS, value) ? value : VLABELS[value];
// quick reasons: only inserted into a text box, so they are translated
export const QLATE = lazy([
    () => _t("Heavy traffic"), () => _t("Transport delay"), () => _t("Medical appointment"), () => _t("Family matter"),
    () => _t("Car broke down"),
]);
export const QBREAK = lazy([
    () => _t("A customer kept me just before my break"), () => _t("Prayer"), () => _t("Urgent family call"),
    () => _t("The restaurant was crowded"),
]);
export const SUP = lazy({
    roll_invoice: () => _t("🧾 Receipt roll"), roll_card: () => _t("💳 Card roll"), bags: () => _t("🛍️ Bags"), other: () => _t("📦 Other"),
});
export const EXPICON = { delivery: "🚚", worker: "👷", cleaning: "🧹", hospitality: "☕", maintenance: "🔧", cover: "👩", refund: "💵", other: "📦" };
export const RQST = lazy({
    pending: () => ["info", _t("Awaiting management")], approved: () => ["good", _t("Approved by management")],
    shipped: () => ["warn", _t("Shipped 🚚")], received: () => ["good", _t("Received ✓")], rejected: () => ["bad", _t("Rejected")],
});
export const EXST = lazy({
    pending: () => ["info", _t("Awaiting approval")], approved: () => ["good", _t("Approved ✓")], rejected: () => ["bad", _t("Rejected")],
});
// 26.3: shortage reasons (stored key -> label with its icon), defective item answers, shipment states
export const SREASONL = lazy({
    customer: () => _t("🙋‍♀️ Customer request"), sold_out: () => _t("📦 Sold out at the branch"), other: () => _t("✏️ Other"),
});
export const DFST = lazy({
    pending: () => ["neutral", _t("⏳ Management hasn't replied yet")], approved: () => ["good", _t("✅ Approved")], rejected: () => ["bad", _t("✕ Rejected")],
});
export const SHIPST = lazy({
    shipped: () => ["warn", _t("On the way")], received: () => ["good", _t("Arrived ✓")], short: () => ["bad", _t("Arrived short")],
});
export const shipState = (s) => (s.state === "received" && s.short ? "short" : s.state);
export const CASEST = lazy({
    reply: () => ["bad", _t("Needs your reply")], review: () => ["warn", _t("Awaiting management decision")],
    forgiven: () => ["good", _t("Counted as a grace chance")], rejected: () => ["bad", _t("A penalty was issued")],
    edited: () => ["good", _t("Duration adjusted")], excused: () => ["good", _t("Excuse accepted")],
});
export const LEAVEST = lazy({
    cover: () => ["info", _t("Awaiting colleague's approval to cover")], cover_no: () => ["bad", _t("Colleague declined to cover")],
    pending: () => ["warn", _t("Awaiting management approval")], approved: () => ["good", _t("Leave approved")],
    rejected: () => ["bad", _t("Leave declined")],
});

let TZ = "Asia/Riyadh";
let OFFSET = 0; // server clock - device clock (ms): timers follow the server, not the iPad clock
export function setClock(tz, serverNowIso) {
    if (tz) {
        TZ = tz;
    }
    if (serverNowIso) {
        OFFSET = Date.parse(serverNowIso) - Date.now();
    }
}
export const now = () => Date.now() + OFFSET;

/** Joins the non-empty values (templates can't use Boolean). */
export const jn = (arr, sep = " · ") => arr.filter((x) => x).join(sep);
export const f0 = (n) => new Intl.NumberFormat("en-US").format(Math.round(+n || 0));
export const pc = (a, b) => (b ? Math.round((a / b) * 100) : 0);
export const mmss = (s) => {
    s = Math.max(0, Math.round(s || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
export const h12 = (m) => {
    const pm = Math.floor(m / 60) >= 12;
    const ampm = LANG === "ar" ? (pm ? "م" : "ص") : pm ? "PM" : "AM";
    return `${Math.floor(m / 60) % 12 || 12}:${String(m % 60).padStart(2, "0")} ${ampm}`;
};
export const clock = (ts) =>
    ts ? new Intl.DateTimeFormat(LANG === "ar" ? "ar-SA-u-nu-latn" : "en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" }).format(new Date(ts)) : "";
export const longDate = (iso) =>
    iso ? new Date(iso.slice(0, 10) + "T00:00:00Z").toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }) : "";
export const dmy = (iso) =>
    new Date(iso + "T00:00:00Z").toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
export const monthName = (iso) =>
    new Date(iso.slice(0, 8) + "01T00:00:00Z").toLocaleDateString(LOCALE, { month: "long", year: "numeric", timeZone: "UTC" });
export const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(now()));
export const minuteNow = () => {
    const p = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(now()));
    return (+p.find((x) => x.type === "hour").value % 24) * 60 + +p.find((x) => x.type === "minute").value;
};
export const hourNow = () => Math.floor(minuteNow() / 60);
export const addDays = (iso, n) => {
    const t = new Date(iso + "T00:00:00Z");
    t.setUTCDate(t.getUTCDate() + n);
    return t.toISOString().slice(0, 10);
};
export const monthStart = (iso) => iso.slice(0, 8) + "01";
export const monthEnd = (iso) => {
    const d = new Date(iso.slice(0, 8) + "01T00:00:00Z");
    d.setUTCMonth(d.getUTCMonth() + 1);
    d.setUTCDate(0);
    return d.toISOString().slice(0, 10);
};
export const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1;
// Arabic keeps its own dual/plural forms; other languages get the English singular/plural.
export const dTxt = (n) =>
    LANG === "ar"
        ? n === 1 ? "يوم واحد" : n === 2 ? "يومين" : n <= 10 ? `${n} أيام` : `${n} يومًا`
        : n === 1 ? _t("1 day") : _t("%s days", n);
export const ago = (ts) => {
    const m = Math.round((now() - Date.parse(ts)) / 6e4);
    return m < 1
        ? _t("Just now")
        : m < 60 ? _t("%s min ago", m) : m < 1440 ? _t("%s h ago", Math.round(m / 60)) : _t("%s d ago", Math.round(m / 1440));
};
export const weekday = (iso) => WD[new Date(iso + "T00:00:00Z").getUTCDay()];
export const photoUrl = (id) => `/web/image/navitta.photo/${id}/image`;
export const barcodeUrl = (type, value, w, h, human = 0) =>
    `/report/barcode/?barcode_type=${type}&value=${encodeURIComponent(value)}&width=${w}&height=${h}&humanreadable=${human}`;

// models file helpers (DCS = department + type + heel/style, e.g. LADSANCHU)
const DEPT = lazy({ LAD: () => _t("Women"), GIR: () => _t("Girls"), BOY: () => _t("Boys"), BAG: () => _t("Bag") });
const TYPE = lazy({
    SAN: () => _t("Sandals"), SLI: () => _t("Slippers"), BOO: () => _t("Boots"), SPO: () => _t("Sports"), OXF: () => _t("Closed shoes"),
});
const STYLE = lazy({
    FLA: () => _t("Flat"), WED: () => _t("Wedge"), CHU: () => _t("Block heel"), PLA: () => _t("Platform"), STI: () => _t("Stiletto"),
    KIT: () => _t("Kitten heel"), CAS: () => _t("Casual"), PAR: () => _t("Evening"), CLA: () => _t("Classic"),
});
export const dcsText = (dcs) => {
    if (!dcs) {
        return "";
    }
    const c = dcs.toUpperCase().replace(/^SHO/, "");
    return [DEPT[c.slice(0, 3)], TYPE[c.slice(3, 6)], STYLE[c.slice(6, 9)]].filter(Boolean).join(" · ");
};
export const isBag = (dcs) => !!dcs && dcs.toUpperCase().startsWith("BAG");
export const icon = (dcs) => {
    const c = (dcs || "").toUpperCase();
    return c.startsWith("BAG") ? "👜" : c.includes("SPO") ? "👟" : c.includes("BOO") ? "🥾" : c.includes("SAN") || c.includes("SLI") ? "👡" : "👠";
};
const SW = [
    [/^(F ?)?(BLACK|BLAK|BLCK|BK)/, "#151515"], [/^(L ?)?(BEIGE|BEIG|BEGE|BAIGE|BIEGE|BG|NUDE|ND|APRICOT|CREAM)/, "#dcc6a2"], [/^(CAMEL|CAMAL)/, "#b5835a"],
    [/^(OF ?W|OFWH|WHITE|WHIT|WH)/, "#ffffff"], [/^(F ?)?(SILVER|SILVR|SLIVER|SLVR|SL)/, "#c3c4cc"], [/^(L ?)?(GOLD|GD)|^LGOLD|^CHAMP/, "#d8b45a"], [/^(NAVY|NAVE|NV)/, "#1f2a52"],
    [/^(PINK|PK|FUSHI)/, "#e8a0b4"], [/^(L ?)?(GREY|GRY|GR\b|GRAY)|^LGREY/, "#8d8d92"], [/^(KHAKI|KH)/, "#a89a6e"], [/^(GREEN|GREN|GRN|GN|OLIV|PIST)/, "#5f8a5a"],
    [/^(MAHRON|MARON|MAROON|MRON|MH|WINE|SCARLT)/, "#6d1f2c"], [/^RED/, "#c62828"], [/^(BROWN|BRWON|COF|MOCA|MOCHA)/, "#6b4a35"], [/^(BRONZ|BROZ)/, "#a0703a"],
    [/^(BLUE|JEANS)/, "#3d6fb6"], [/^(L ?)?PURPL/, "#7d4f9e"], [/^(YELLOW|YELOW|YL)/, "#e7c843"],
];
export const swatch = (color) => (SW.find(([r]) => r.test(String(color || "").toUpperCase().trim())) || [0, "linear-gradient(135deg,#ddd,#888)"])[1];
const sizeKey = (s) => parseFloat(s || "0") || 0;
export const sizesOf = (m) => [...new Set(m.items.map((i) => i.size).filter(Boolean))].sort((a, b) => sizeKey(a) - sizeKey(b));

/** Shrinks a photo to at most `max` px on the long side; returns base64 JPEG without the data: prefix. */
export async function compress(src, max = 1280, quality = 0.7) {
    let w, h, draw;
    if (src instanceof HTMLVideoElement) {
        w = src.videoWidth;
        h = src.videoHeight;
        draw = src;
    } else {
        const bmp = await createImageBitmap(src);
        w = bmp.width;
        h = bmp.height;
        draw = bmp;
    }
    const k = Math.min(1, max / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * k);
    c.height = Math.round(h * k);
    c.getContext("2d").drawImage(draw, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", quality).split(",")[1];
}

/** Branch picked by management on the branch screen (device accounts always work on their own branch). */
export const CTX = { branch: 0 };

/** Calls navitta.api; returns the result, or {error, message} for business errors. */
export async function api(orm, method, ...args) {
    try {
        return await orm.call("navitta.api", method, args, CTX.branch ? { context: { navitta_branch: CTX.branch } } : {});
    } catch (e) {
        const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Something went wrong, please try again.");
        return { error: "server", message: msg };
    }
}

export const downloadCsv = (name, rows) => {
    const a = document.createElement("a");
    const body = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
    a.href = URL.createObjectURL(new Blob(["﻿" + body], { type: "text/csv;charset=utf-8" }));
    a.download = name;
    a.click();
};
