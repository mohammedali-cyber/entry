/** @odoo-module **/
// 🗑️ for management (site: components/del.tsx): a reason and the separate delete password; the server deletes the
// record and keeps a copy in the delete log.
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { api } from "./utils";
import { NvModal } from "./components";

export class DelBtn extends Component {
    static template = "navitta_ops.DelBtn";
    static components = { NvModal };
    static props = { kind: String, id: Number, what: { type: String, optional: true }, onDone: Function, small: { type: Boolean, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ open: false, why: "", pw: "", err: "", busy: false });
        this.TX = { del: _t("Delete"), pwPh: "🔒 " + _t("Delete password") };
    }
    close() {
        Object.assign(this.state, { open: false, why: "", pw: "", err: "", busy: false });
    }
    async go() {
        const s = this.state;
        if (!s.why.trim()) {
            s.err = _t("Write the reason for deleting");
            return;
        }
        if (!s.pw) {
            s.err = _t("Enter the delete password");
            return;
        }
        s.busy = true;
        const r = await api(this.orm, "admin_delete", this.props.kind, this.props.id, s.why, s.pw);
        s.busy = false;
        if (r.error) {
            s.err = r.message;
            return;
        }
        this.close();
        this.notification.add(_t("Deleted, and a copy was kept in the delete log"), { type: "success" });
        await this.props.onDone();
    }
    onKey(ev) {
        if (ev.key === "Enter") {
            this.go();
        }
    }
}
