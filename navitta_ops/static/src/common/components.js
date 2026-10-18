/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import {
    LANG, api, addDays, ago, barcodeUrl, compress, dcsText, f0, hourNow, icon, isBag, jn, longDate, minuteNow, mmss, monthEnd, monthName,
    monthStart, photoUrl, pc, sizesOf, stockQty, swatch, today,
} from "./utils";
import { Bulb } from "./help";

/** Texts used inside template expressions (OWL only translates static text nodes and attributes). */
const TX = {
    get enterPin() { return _t("Enter your PIN"); },
    ofFour: (n) => _t("%s of 4", n),
    get viewReceipt() { return _t("View receipt"); },
    get photo() { return _t("Photo"); },
    get uploading() { return _t("Uploading…"); },
    get chooseFile() { return _t("Choose file"); },
    get loading() { return _t("Loading…"); },
    get management() { return _t("Management"); },
    get branch() { return _t("Branch"); },
    get month() { return _t("Month"); },
    get goodMorning() { return _t("Good morning ☀️"); },
    get goodEvening() { return _t("Good evening 🌙"); },
    get searchPh() { return _t("Item number (8 digits) or scan the barcode"); },
    get pickPh() { return _t("Item number, model, or scan the barcode"); },
    get noSize() { return _t("No size"); },
    get showLess() { return _t("Show less ▴"); },
    moreColors: (n) => _t("+ %s more colors ▾", n),
    size: (x) => _t("Size %s", x),
    get empSales() { return _t("Employee sales"); },
    get fromPos() { return _t("From Point of Sale invoices: the device owner, or the cashier who made the sale (not the shared account)"); },
    get fromReport() { return _t("From the daily report"); },
    get sar() { return _t("SAR"); },
    invVsCounter: (inv, bought) => _t("Invoices %(inv)s, counter says bought %(bought)s", { inv, bought }),
    ownTarget: (n) => _t("%s% of her device target", n),
    stockHere: (n) => _t("Available here: %s pieces", n),
    stockAt: (b, n) => _t("Available at %(branch)s: %(n)s pieces", { branch: b, n }),
    stockOther: (n) => _t("Available in other branches: %s pieces", n),
    sizeHere: (n) => _t("This size here: %s pieces", n),
    get outOfStock() { return _t("Out of stock"); },
    inStock: (n) => _t("%s in stock", n),
};

export class NvModal extends Component {
    static template = "navitta_ops.NvModal";
    static props = { onClose: Function, wide: { type: Boolean, optional: true }, cls: { type: String, optional: true }, slots: Object };
    onBg(ev) {
        if (ev.target === ev.currentTarget) {
            this.props.onClose();
        }
    }
}

/** 4-digit PIN pad. onDone(pin) returns an error message to show, or null on success. */
export class PinPad extends Component {
    static template = "navitta_ops.PinPad";
    TX = TX;
    static props = { title: String, sub: { type: String, optional: true }, onDone: Function, onCancel: { type: Function, optional: true } };
    setup() {
        this.state = useState({ buf: "", err: "", busy: false });
        useExternalListener(window, "keydown", (e) => {
            // typing in a text box elsewhere on the page must not also fill the PIN
            if (e.target && e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) {
                return;
            }
            const map = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9" };
            const d = map[e.key] || e.key;
            if (/^[0-9]$/.test(d)) {
                this.press(d);
            } else if (e.key === "Backspace") {
                this.press("del");
            } else if (e.key === "Escape" && this.props.onCancel) {
                this.props.onCancel();
            }
        });
    }
    async press(k) {
        const s = this.state;
        if (s.busy) {
            return;
        }
        if (k === "del") {
            s.buf = s.buf.slice(0, -1);
            return;
        }
        s.buf = (s.buf + k).slice(0, 4);
        s.err = "";
        if (s.buf.length === 4) {
            s.busy = true;
            const e = await this.props.onDone(s.buf);
            s.busy = false;
            s.buf = "";
            if (e) {
                s.err = e;
            }
        }
    }
}

/**
 * Front-camera preview shown above the PIN pad for check-in / check-out ("بصمة بالصورة").
 * register({capture}) hands the parent a function returning a small JPEG (base64) of the current frame, or null.
 */
export class FaceCam extends Component {
    static template = "navitta_ops.FaceCam";
    static props = { register: Function };
    setup() {
        this.state = useState({ ready: false, failed: false });
        this.videoRef = useRef("video");
        this.props.register({ capture: () => this.capture() });
        onMounted(() => this.start());
        onWillUnmount(() => this.stop());
    }
    async start() {
        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error("no camera api");
            }
            this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 } }, audio: false });
            const v = this.videoRef.el;
            if (!v) {
                return this.stop();
            }
            v.srcObject = this.stream;
            await v.play();
            this.state.ready = true;
        } catch {
            this.state.failed = true;
        }
    }
    stop() {
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
    }
    async capture() {
        const v = this.videoRef.el;
        if (!this.state.ready || !v || !v.videoWidth) {
            return null;
        }
        try {
            return await compress(v, 640, 0.7);
        } catch {
            return null;
        }
    }
}

/** Signs an action with an employee's PIN: pick the employee, then the PIN pad. */
export class SignModal extends Component {
    static template = "navitta_ops.SignModal";
    static components = { NvModal, PinPad };
    static props = { emps: Array, title: String, onSign: Function, onClose: Function };
    setup() {
        this.state = useState({ emp: this.props.emps.length === 1 ? this.props.emps[0] : null });
    }
}

/** "📎 view" link that opens a stored photo. */
export class PhotoLink extends Component {
    static template = "navitta_ops.PhotoLink";
    TX = TX;
    static components = { NvModal };
    static props = { id: Number, label: { type: String, optional: true } };
    setup() {
        this.state = useState({ open: false });
        this.photoUrl = photoUrl;
    }
}

/** A small thumbnail of a stored photo; tapping it opens it large with «Close» (site: FileThumb). `src` for photos that
 * are not branch photos (e.g. an idea's photo). */
export class FileThumb extends Component {
    static template = "navitta_ops.FileThumb";
    TX = TX;
    static components = { NvModal };
    static props = { id: { type: Number, optional: true }, src: { type: String, optional: true }, label: { type: String, optional: true } };
    setup() {
        this.state = useState({ open: false, broken: false });
    }
    get url() {
        return this.props.src || photoUrl(this.props.id);
    }
    get thumb() {
        return this.props.src ? this.props.src : `/web/image/navitta.photo/${this.props.id}/image/160x160`;
    }
}

/** Receipt / item photo: phone via QR code, a file from this device, or the device camera. */
export class PhotoPicker extends Component {
    static template = "navitta_ops.PhotoPicker";
    TX = TX;
    static components = { NvModal };
    static props = { value: { optional: true }, onChange: Function, label: String };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ mode: "", busy: false, qr: "", left: 600, err: "", camErr: "" });
        this.fileRef = useRef("file");
        this.videoRef = useRef("video");
        this.photoUrl = photoUrl;
        this.mmss = mmss;
        onWillUnmount(() => this.stopAll());
    }
    stopAll() {
        clearInterval(this.poll);
        clearInterval(this.tick);
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
    }
    close() {
        this.stopAll();
        this.state.mode = "";
    }
    async upload(b64) {
        const r = await api(this.orm, "photo_upload", b64, "image/jpeg");
        if (r.error) {
            this.notification.add(r.message || _t("Couldn't upload the photo"), { type: "danger" });
            return;
        }
        this.props.onChange(r.id);
    }
    async fromFile(ev) {
        const f = ev.target.files[0];
        ev.target.value = "";
        if (!f) {
            return;
        }
        this.state.busy = true;
        try {
            await this.upload(await compress(f));
        } finally {
            this.state.busy = false;
        }
    }
    async openQr() {
        this.state.mode = "qr";
        this.state.err = "";
        const r = await api(this.orm, "token_new");
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.state.qr = barcodeUrl("QR", r.url, 440, 440);
        const end = Date.parse(r.expires_at);
        this.tick = setInterval(() => (this.state.left = Math.max(0, Math.round((end - Date.now()) / 1000))), 1000);
        this.poll = setInterval(async () => {
            const p = await api(this.orm, "token_poll", r.token);
            if (p.photo_id) {
                this.close();
                this.props.onChange(p.photo_id);
            } else if (p.expired) {
                clearInterval(this.poll);
            }
        }, 2500);
    }
    async openCam() {
        this.state.mode = "cam";
        this.state.camErr = "";
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
            const v = this.videoRef.el;
            if (v) {
                v.srcObject = this.stream;
                v.play();
            }
        } catch {
            this.state.camErr = _t("Couldn't open the camera. Use your phone or choose a file.");
        }
    }
    async shot() {
        const v = this.videoRef.el;
        if (!v) {
            return;
        }
        const b64 = await compress(v);
        this.close();
        await this.upload(b64);
    }
    view() {
        window.open(photoUrl(this.props.value), "_blank");
    }
}

/** Today's and this month's target vs sales for one branch, with a password-protected editor on branch devices. */
export class TargetBar extends Component {
    static template = "navitta_ops.TargetBar";
    static props = { branchId: { type: Number, optional: true }, branchName: { type: String, optional: true }, admin: { type: Boolean, optional: true } };
    static components = { Bulb };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ d: null, edit: false });
        this.f0 = f0;
        this.mmss = mmss;
        this.monthName = monthName;
        onWillStart(() => this.load());
        onMounted(() => (this.timer = setInterval(() => this.load(), 120000)));
        onWillUnmount(() => clearInterval(this.timer));
    }
    async load() {
        const d = this.props.admin ? await api(this.orm, "admin_target_rows", this.props.branchId) : await api(this.orm, "target_bar");
        if (!d.error) {
            this.state.d = d;
        }
    }
    get v() {
        const d = this.state.d;
        const rows = d.rows;
        const td = rows.find((r) => r.day === d.day) || {};
        const tgt = +td.target || 0, got = +td.sales || 0;
        const mT = rows.reduce((s, r) => s + +r.target, 0), mS = rows.reduce((s, r) => s + +(r.sales || 0), 0);
        const inv = rows.reduce((s, r) => s + (r.invoices || 0), 0);
        const avgInv = inv ? mS / inv : 0;
        const left = Math.max(0, tgt - got);
        const hrs = Math.max(0, d.shift_end - minuteNow());
        return {
            tgt, got, left, pct: pc(got, tgt), mT, mS, mP: pc(mS, mT), daysLeft: rows.filter((r) => r.day > d.day).length + 1,
            avgInv, invLeft: avgInv ? Math.max(1, Math.ceil(left / avgInv)) : 0, hrs, month: monthName(d.day),
        };
    }
    async onSaved() {
        this.state.edit = false;
        await this.load();
    }
}

export class TargetEditor extends Component {
    static template = "navitta_ops.TargetEditor";
    TX = TX;
    static components = { NvModal };
    static props = {
        branchId: { type: Number, optional: true }, branchName: { type: String, optional: true }, admin: { type: Boolean, optional: true },
        rows: { type: Array, optional: true }, log: { optional: true }, onClose: Function, onSaved: Function,
    };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const day = today();
        const rows = this.props.rows || [];
        this.tgtOf = (d) => +((rows.find((r) => r.day === d) || {}).target || 0);
        this.state = useState({
            pw: this.props.admin ? "" : null, pwIn: "", err: "", d: day, amt: String(this.tgtOf(day) || ""), week: false,
            mon: monthStart(day), mAmt: String(rows.reduce((s, r) => s + +r.target, 0) || ""),
        });
        this.months = [monthStart(day), monthStart(addDays(monthEnd(day), 1))];
        this.monthName = monthName;
        this.longDate = longDate;
        this.f0 = f0;
    }
    async check() {
        const r = await api(this.orm, "target_check", this.state.pwIn);
        if (r.error) {
            this.state.err = r.message;
            return;
        }
        this.state.err = "";
        this.state.pw = this.state.pwIn;
    }
    setDay(v) {
        this.state.d = v;
        this.state.amt = String(this.tgtOf(v) || "");
    }
    async saveDay() {
        const s = this.state;
        const r = this.props.admin
            ? await api(this.orm, "admin_target_set_days", this.props.branchId, s.d, s.week ? 7 : 1, +s.amt)
            : await api(this.orm, "target_set_days", s.pw, s.d, s.week ? 7 : 1, +s.amt);
        if (r.error) {
            s.err = r.message;
            return;
        }
        this.notification.add(_t("Target saved"), { type: "success" });
        this.props.onSaved();
    }
    async saveMonth() {
        const s = this.state;
        const r = this.props.admin
            ? await api(this.orm, "admin_target_set_month", this.props.branchId, s.mon, +s.mAmt)
            : await api(this.orm, "target_set_month", s.pw, s.mon, +s.mAmt);
        if (r.error) {
            s.err = r.message;
            return;
        }
        this.notification.add(_t("Monthly target saved: %s SAR per day for the remaining days", f0(r.per_day)), { type: "success" });
        this.props.onSaved();
    }
}
TargetBar.components = { TargetEditor, Bulb };

// daily quotes: employees (feminine, evening shift) and management (morning / evening); [Arabic, English] pairs picked by LANG
const EMPQ = [
    ["كل عميلة تدخل فرصة جديدة، وابتسامتك أول خطوة للبيع 🤍", "Every customer who walks in is a new chance, and your smile is the first step to a sale 🤍"],
    ["التارقت مو رقم بعيد، هو عميلات اليوم وحدة وحدة 💪", "The target isn't a distant number, it's today's customers, one by one 💪"],
    ["العميلة تشتري من اللي ترتاح له، خليها ترتاح معك ✨", "Customers buy from people they feel comfortable with, so make her feel at ease ✨"],
    ["اسألي العميلة وش المناسبة، وبتعرفين وش يناسبها 👠", "Ask the customer about the occasion, and you'll know what suits her 👠"],
    ["لا تقولين «مخلص» وبس، اقترحي البديل اللي يعجبها 🔄", "Don't just say “sold out”, suggest an alternative she'll like 🔄"],
    ["الشنطة تكمّل الجزمة، اعرضيها عليها 👜", "A bag completes the shoes, show her one 👜"],
    ["كل «لا» تقربك من «نعم» الجاية 🌟", "Every “no” brings you closer to the next “yes” 🌟"],
    ["العميلة الراضية ترجع ومعها صديقتها 💕", "A happy customer comes back with a friend 💕"],
    ["ترتيب الرف يبيع قبل لا تتكلمين 🛍️", "A tidy shelf sells before you say a word 🛍️"],
    ["خلي آخر ساعة مثل أول ساعة، نشاط وابتسامة ⏰", "Make the last hour like the first: energy and a smile ⏰"],
    ["كل قطعة تبيعينها تقربك من تارقتك 🎯", "Every item you sell brings you closer to your target 🎯"],
    ["تعبك ما يروح، الإدارة تشوفه وتقدّره 🙏", "Your hard work isn't wasted, management sees it and values it 🙏"],
    ["الكلمة الحلوة ما تكلف شي، وتسوي فرق كبير 🌸", "A kind word costs nothing and makes a big difference 🌸"],
    ["سجلي كل زيارة بصدق، أرقامك تساعدنا نجيب اللي تبيه العميلات 📋", "Log every visit honestly, your numbers help us stock what customers want 📋"],
];
const AMQ = [
    ["اجتهادك اليوم له ثمرته بكرة، استمر 🌱", "Today's effort pays off tomorrow, keep going 🌱"],
    ["كل يوم جديد فرصة لبداية أفضل ☀️", "Every new day is a chance for a better start ☀️"],
    ["ابدأ يومك بنية طيبة، والباقي على الله 🤲", "Start your day with good intentions, and leave the rest to God 🤲"],
    ["خلك متفائل، الأيام الحلوة جاية ✨", "Stay optimistic, good days are coming ✨"],
    ["اليوم صفحة جديدة، اكتب فيها إنجاز 📖", "Today is a new page, write an achievement on it 📖"],
    ["الهدف الكبير يبدأ بخطوة صغيرة الصبح 👣", "A big goal starts with a small step in the morning 👣"],
    ["النجاح الكبير مجموع أيام صغيرة منتظمة 📈", "Big success is the sum of small, steady days 📈"],
    ["فريقك ينتظر منك الحماس، وزّعه عليهم 🔥", "Your team is looking to you for energy, share it with them 🔥"],
    ["الفرص ما تجي مرتين، اغتنم يومك ⏳", "Opportunities don't come twice, make the most of your day ⏳"],
    ["التوفيق من الله، والسعي علينا 🤲", "Success comes from God, the effort is ours 🤲"],
    ["الفريق المرتاح يبيع أكثر، كلمة شكر اليوم تفرق 🙏", "A happy team sells more, a thank-you today makes a difference 🙏"],
    ["ما فيه شي مستحيل مع الصبر والعزيمة 💪", "Nothing is impossible with patience and determination 💪"],
];
const PMQ = [
    ["الرزق مقسوم، والسعي مشكور 🤲", "Provision is written, and effort is appreciated 🤲"],
    ["ما فيه تعب يضيع، كل خطوة تقربك من هدفك 🎯", "No effort is wasted, every step brings you closer to your goal 🎯"],
    ["اللي يزرع بصبر يحصد بفرح 🌾", "Those who sow with patience reap with joy 🌾"],
    ["يومك ما راح عبث، كل جهد له أثر 🌙", "Your day wasn't in vain, every effort leaves its mark 🌙"],
    ["الحمد لله على يوم ثاني من الرزق والعمل 🤍", "Thank God for another day of work and provision 🤍"],
    ["المبيعات الضائعة كنز: كل طلب ما توفر فرصة للطلبية الجاية 💡", "Lost sales are a treasure: every unavailable request is a chance for the next order 💡"],
    ["الموظفة اللي تحس إنها مسموعة، تعطي أكثر 🤝", "An employee who feels heard gives more 🤝"],
    ["اللي ما تحقق اليوم، بكرة فرصته أكبر 🌅", "What wasn't achieved today has a better chance tomorrow 🌅"],
    ["تعبك اليوم استثمار في بكرة 📈", "Today's hard work is an investment in tomorrow 📈"],
    ["ارتاح الليلة، وبكرة نكمل بقوة 😌", "Rest tonight, tomorrow we carry on strong 😌"],
    ["كل فرع حقق اليوم شي، ولو بسيط، يستاهل شكر 🙏", "Every branch that achieved something today, however small, deserves thanks 🙏"],
    ["النهاية الحلوة تبدأ بصبر جميل 🌟", "A happy ending starts with graceful patience 🌟"],
];
const START = Date.UTC(2026, 9, 13) / 864e5;
const pick = (list, day) => list[((((Date.parse(day + "T00:00:00Z") / 864e5 - START) % list.length) + list.length) % list.length)];

export class DailyQuote extends Component {
    static template = "navitta_ops.DailyQuote";
    TX = TX;
    static props = { admin: { type: Boolean, optional: true }, best: { optional: true } };
    get q() {
        const day = today(), am = hourNow() < 12;
        const quote = this.props.admin ? pick(am ? AMQ : PMQ, day) : pick(EMPQ, day);
        return { am, text: quote[LANG === "ar" ? 0 : 1], date: longDate(day) };
    }
}

/** Search the models file: 8-digit item number, model name, or a scanned barcode (scanners end with Enter). */
export class ModelSearchBox extends Component {
    static template = "navitta_ops.ModelSearchBox";
    TX = TX;
    static props = { onResult: Function, placeholder: { type: String, optional: true }, big: { type: Boolean, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ q: "", busy: false, done: false });
        this.inp = useRef("inp");
        onMounted(() => this.inp.el && this.inp.el.focus());
        onWillUnmount(() => clearTimeout(this.h));
    }
    onInput(ev) {
        this.state.q = ev.target.value;
        clearTimeout(this.h);
        const s = this.state.q.trim();
        if (s.length < 3) {
            this.state.done = false;
            this.props.onResult(null, s);
            return;
        }
        this.h = setTimeout(async () => {
            this.state.busy = true;
            const r = await api(this.orm, "catalog_find", s);
            this.state.busy = false;
            this.state.done = true;
            this.props.onResult(r.error ? { models: [] } : r, s);
        }, 350);
    }
    onKey(ev) {
        if (ev.key === "Enter") {
            ev.target.select();
        }
    }
}

export class CartonLabel extends Component {
    static template = "navitta_ops.CartonLabel";
    static props = { item: Object };
    get rows() {
        const i = this.props.item;
        return [["ALU", i.alu.padStart(11, "0")], ["ITEM NO (Desc1)", i.item_no], ["MODEL (Desc4)", i.model], ["COLOR (Attr)", i.color],
            ["SIZE", i.size], ["DCS", i.dcs], ["HEEL", i.heel], ["MATERIAL", i.material]];
    }
    get barcode() {
        return barcodeUrl("Code128", this.props.item.alu.padStart(11, "0"), 380, 70, 1);
    }
    get price() {
        return this.props.item.price != null ? f0(this.props.item.price) : "—";
    }
}

export class ModelCard extends Component {
    static template = "navitta_ops.ModelCard";
    TX = TX;
    static props = { m: Object, hi: { optional: true } };
    setup() {
        this.state = useState({ more: false });
        this.f0 = f0;
        this.icon = icon;
        this.dcsText = dcsText;
        this.swatch = swatch;
        this.stockQty = stockQty;
    }
    get sizes() {
        return sizesOf(this.props.m);
    }
    get bag() {
        return isBag(this.props.m.dcs) || !this.sizes.length;
    }
    get cols() {
        return this.state.more ? this.props.m.colors : this.props.m.colors.slice(0, 8);
    }
}

/** Customers waiting for one of these models (with a phone number, not contacted yet). */
export class Waiting extends Component {
    static template = "navitta_ops.Waiting";
    TX = TX;
    static props = { nos: Array };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ w: [] });
        this.ago = ago;
        this.jn = jn;
        onWillStart(() => this.load(this.props.nos));
        onWillUpdateProps((np) => np.nos.join() !== this.props.nos.join() && this.load(np.nos));
    }
    async load(nos) {
        const r = nos.length ? await api(this.orm, "catalog_waiting", nos) : [];
        this.state.w = Array.isArray(r) ? r : [];
    }
}

export class ModelSearch extends Component {
    static template = "navitta_ops.ModelSearch";
    static components = { NvModal, ModelSearchBox, CartonLabel, ModelCard, Waiting };
    static props = { onClose: Function };
    setup() {
        this.state = useState({ res: null, q: "" });
        this.f0 = f0;
        this.icon = icon;
        this.dcsText = dcsText;
        this.swatch = swatch;
        this.stockQty = stockQty;
        this.TX = TX;
    }
    onResult(res, q) {
        this.state.res = res;
        this.state.q = q;
    }
    get item() {
        return this.state.res && this.state.res.item;
    }
    get m0() {
        return this.state.res && this.state.res.models[0];
    }
    get itemSizes() {
        const c = this.m0 && this.m0.colors.find((x) => x.color === this.item.color);
        return c ? c.sizes : [];
    }
}

/** Picks the model a customer asked for; a scan also gives the color and size. */
export class ModelPicker extends Component {
    static template = "navitta_ops.ModelPicker";
    TX = TX;
    static components = { ModelSearchBox };
    static props = { onPick: Function, onManual: Function };
    setup() {
        this.state = useState({ res: null, q: "" });
        this.f0 = f0;
        this.icon = icon;
        this.dcsText = dcsText;
    }
    stockOf(m) {
        return m.stock && m.stock.here !== null && m.stock.here !== undefined ? m.stock.here : null;
    }
    onResult(res, q) {
        this.state.res = res;
        this.state.q = q;
        if (res && res.item && res.models[0]) {
            this.props.onPick(res.models[0], res.item);
        }
    }
}

/** Management: employees ranked by sales in a period. */
export class EmpBoard extends Component {
    static template = "navitta_ops.EmpBoard";
    TX = TX;
    static props = { from: String, to: String, branchId: { optional: true }, deviceId: { optional: true }, branches: Array,
        title: { type: String, optional: true }, wd: { type: Array, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.state = useState({ rows: null, targets: {}, own: {} });
        this.f0 = f0;
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((np) => {
            if (np.from !== this.props.from || np.to !== this.props.to || np.branchId !== this.props.branchId || np.deviceId !== this.props.deviceId ||
                (np.wd || []).join() !== (this.props.wd || []).join()) {
                return this.load(np);
            }
        });
    }
    get fromSystem() {
        return (this.state.rows || []).some((r) => r.source && r.source !== "report");
    }
    async load(p) {
        const my = (this.req = (this.req || 0) + 1);
        const r = await api(this.orm, "employee_summary", p.from, p.to, p.branchId || false, p.wd || [], p.deviceId || false);
        if (my !== this.req) {
            return;
        }
        if (!r.error) {
            this.state.rows = r.rows;
            this.state.targets = r.targets;
            this.state.own = r.own_targets || {};
        }
    }
    bName(id) {
        return (this.props.branches.find((b) => b.id === id) || {}).name || "";
    }
    pct(r) {
        const t = this.state.targets[r.branch_id] || 0;
        return t ? Math.round((r.amount / t) * 100) : 0;
    }
    /** The owner of a device: her sales against her device's target (its share of the branch target), or null. */
    ownPct(r) {
        const t = r.employee_id && this.state.own[r.employee_id];
        return t ? Math.round((r.amount / t) * 100) : null;
    }
}
