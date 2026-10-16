/** @odoo-module **/
// «💡 الأفكار» (site: app/admin/ideas): improvement ideas for the next releases, grouped by release, with a status
// (not yet / done / no), a release selector, an optional photo and 🗑️ with the delete password.
import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { api, compress, longDate } from "../common/utils";
import { FileThumb } from "../common/components";
import { DelBtn } from "../common/del";

const ST = () => [["todo", _t("⏳ Not yet"), "neutral"], ["done", _t("✅ Done"), "good"], ["no", _t("✕ No"), "bad"]];

export class Ideas extends Component {
    static template = "navitta_ops.AdminIdeas";
    static components = { FileThumb, DelBtn };
    static props = { f: { type: Object, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, open: false, f: { title: "", details: "", version: "" }, photo: null, busy: false });
        this.ST = ST();
        onWillStart(() => this.load());
    }
    async load() {
        const d = await api(this.orm, "admin_ideas");
        if (!d.error) {
            this.state.d = d;
            if (!this.state.f.version) {
                this.state.f.version = d.version;
            }
        }
    }
    vlabel(v) {
        return v === "later" ? _t("Later") : v;
    }
    get versions() {
        const d = this.state.d;
        return [...new Set([...d.versions, ...d.ideas.map((x) => x.version)])];
    }
    get groups() {
        const d = this.state.d;
        return this.versions.map((v) => {
            const xs = d.ideas.filter((x) => x.version === v);
            return { v, xs, icon: v === d.version ? "🚧" : v === "later" ? "🗂️" : "📦", done: xs.filter((x) => x.state === "done").length };
        }).filter((g) => g.xs.length);
    }
    get counts() {
        const d = this.state.d;
        return _t("Current release %s", d.version) + " · " + this.ST.map(([k, l]) => `${l} ${d.ideas.filter((x) => x.state === k).length}`).join(" · ");
    }
    groupTitle(g) {
        return `${g.icon} ${_t("Navitta Pro")} ${this.vlabel(g.v)}`;
    }
    groupNote(g) {
        return _t("%(n)s ideas · %(d)s done", { n: g.xs.length, d: g.done });
    }
    meta(x) {
        let t = _t("Added %s", longDate(x.created_at));
        if (x.state !== "todo" && x.updated_at) {
            t += " · " + _t("Last update %s", longDate(x.updated_at));
        }
        return t;
    }
    photoSrc(x) {
        return `/web/image/navitta.idea/${x.id}/photo`;
    }
    async pickPhoto(ev) {
        const f = ev.target.files[0];
        ev.target.value = "";
        if (f) {
            this.state.photo = await compress(f);
        }
    }
    async save() {
        const f = this.state.f;
        if (!f.title.trim() || this.state.busy) {
            return;
        }
        this.state.busy = true;
        const r = await api(this.orm, "admin_idea_save", false, { title: f.title, details: f.details, version: f.version, photo: this.state.photo || false });
        this.state.busy = false;
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
            return;
        }
        Object.assign(this.state, { open: false, photo: null, f: { title: "", details: "", version: f.version } });
        this.notification.add(_t("Idea saved"), { type: "success" });
        await this.load();
    }
    async upd(x, vals) {
        const r = await api(this.orm, "admin_idea_save", x.id, vals);
        if (r.error) {
            this.notification.add(r.message, { type: "danger" });
            return;
        }
        await this.load();
    }
}
