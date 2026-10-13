/** @odoo-module **/
// Shared helpers for the Navitta screens: formatting in the branch time zone, choice lists, server calls.

export const WD = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];
export const MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"];

// lost-sale reasons: stored key -> label on the screens
export const LOST = ["size_na", "color_na", "price", "design", "quality", "browsing", "other", "manufacture"];
export const LOSTL = {
    size_na: "📏 المقاس مخلص", color_na: "🎨 اللون مخلص", price: "💰 السعر", design: "👎 التصميم ما عجبها",
    quality: "🧵 الجودة", browsing: "👀 تتصفح بس", other: "✏️ أخرى", manufacture: "🏭 يحتاج تصنيع",
};
export const LOSTFULL = {
    size_na: "المقاس غير متوفر", color_na: "اللون غير متوفر", price: "السعر مرتفع", design: "التصميم غير مناسب",
    quality: "الجودة لم تعجبهم", browsing: "يتصفح فقط", other: "أخرى", manufacture: "يحتاج تصنيع",
};
export const OUTOF = ["size_na", "color_na"];
export const NOBUY = ["size_na", "price", "design", "color_na", "quality", "browsing", "other"];
export const COLORS = ["أسود", "بيج", "أبيض", "فضي", "ذهبي", "بني", "وردي", "كحلي"];
export const SIZES = ["36", "37", "38", "39", "40", "41"];
export const TYPES = ["كعب عالي", "صندل", "حذاء رياضي", "باليرينا", "بوت", "شنطة"];
export const TRIED = ["المقاس غير مريح", "السعر مرتفع", "اللون غير مناسب", "التصميم", "الكعب متعب"];
export const SAT = ["", "غير راضٍ", "أقل من المتوقع", "مقبول", "راضٍ", "راضٍ جدًا"];
export const QLATE = ["زحمة الطريق", "تأخر المواصلات", "موعد طبي", "ظرف عائلي", "تعطل السيارة"];
export const QBREAK = ["عميلة طولت معي قبل الاستراحة", "أداء الصلاة", "مكالمة عائلية ضرورية", "زحمة في المطعم"];
export const SUP = { roll_invoice: "🧾 رول فاتورة", roll_card: "💳 رول شبكة", bags: "🛍️ أكياس", other: "📦 أخرى" };
export const EXPICON = { delivery: "🚚", worker: "👷", cleaning: "🧹", hospitality: "☕", maintenance: "🔧", cover: "👩", refund: "💵", other: "📦" };
export const RQST = { pending: ["info", "بانتظار الإدارة"], approved: ["good", "وافقت الإدارة"], shipped: ["warn", "انشحن 🚚"], received: ["good", "وصل ✓"], rejected: ["bad", "مرفوض"] };
export const EXST = { pending: ["info", "بانتظار الاعتماد"], approved: ["good", "معتمد ✓"], rejected: ["bad", "مرفوض"] };
export const CASEST = {
    reply: ["bad", "يحتاج ردك"], review: ["warn", "بانتظار قرار الإدارة"], forgiven: ["good", "احتُسبت فرصة"],
    rejected: ["bad", "مرفوض"], edited: ["good", "عُدّلت المدة"],
};
export const LEAVEST = {
    cover: ["info", "بانتظار موافقة الزميلة على التغطية"], cover_no: ["bad", "اعتذرت الزميلة عن التغطية"],
    pending: ["warn", "بانتظار موافقة الإدارة"], approved: ["good", "تمت الموافقة"], rejected: ["bad", "مرفوضة"],
};

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
export const h12 = (m) => `${Math.floor(m / 60) % 12 || 12}:${String(m % 60).padStart(2, "0")} ${Math.floor(m / 60) >= 12 ? "م" : "ص"}`;
export const clock = (ts) =>
    ts ? new Intl.DateTimeFormat("ar-SA-u-nu-latn", { timeZone: TZ, hour: "numeric", minute: "2-digit" }).format(new Date(ts)) : "";
export const longDate = (iso) =>
    iso ? new Date(iso.slice(0, 10) + "T00:00:00Z").toLocaleDateString("ar-SA-u-ca-gregory-nu-latn", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }) : "";
export const dmy = (iso) =>
    new Date(iso + "T00:00:00Z").toLocaleDateString("ar-SA-u-ca-gregory-nu-latn", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
export const monthName = (iso) =>
    new Date(iso.slice(0, 8) + "01T00:00:00Z").toLocaleDateString("ar-SA-u-ca-gregory-nu-latn", { month: "long", year: "numeric", timeZone: "UTC" });
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
export const dTxt = (n) => (n === 1 ? "يوم واحد" : n === 2 ? "يومين" : n <= 10 ? `${n} أيام` : `${n} يومًا`);
export const ago = (ts) => {
    const m = Math.round((now() - Date.parse(ts)) / 6e4);
    return m < 1 ? "الآن" : m < 60 ? `قبل ${m} د` : m < 1440 ? `قبل ${Math.round(m / 60)} س` : `قبل ${Math.round(m / 1440)} يوم`;
};
export const weekday = (iso) => WD[new Date(iso + "T00:00:00Z").getUTCDay()];
export const photoUrl = (id) => `/web/image/navitta.photo/${id}/image`;
export const barcodeUrl = (type, value, w, h, human = 0) =>
    `/report/barcode/?barcode_type=${type}&value=${encodeURIComponent(value)}&width=${w}&height=${h}&humanreadable=${human}`;

// models file helpers (DCS = department + type + heel/style, e.g. LADSANCHU)
const DEPT = { LAD: "نسائي", GIR: "بناتي", BOY: "أولادي", BAG: "شنطة" };
const TYPE = { SAN: "صندل", SLI: "سليبر", BOO: "بوت", SPO: "رياضي", OXF: "حذاء مقفل" };
const STYLE = { FLA: "فلات", WED: "ويدج", CHU: "كعب عريض", PLA: "بلاتفورم", STI: "كعب رفيع", KIT: "كعب صغير", CAS: "كاجوال", PAR: "سهرة", CLA: "كلاسيك" };
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
        const msg = (e && e.data && e.data.message) || (e && e.message) || "حدث خطأ، حاولي مرة ثانية";
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
