/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useAutofocus } from "@web/core/utils/hooks";

export class LostSaleDialog extends Component {
    static template = "product_lost_finder.LostSaleDialog";
    static components = { Dialog };
    static props = {
        productName: String,
        variantName: String,
        branchName: String,
        uomName: String,
        onSave: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ quantity: 1, note: "", saving: false });
        useAutofocus({ refName: "quantity" });
    }

    get isValid() {
        return Number(this.state.quantity) > 0;
    }

    async save() {
        if (!this.isValid || this.state.saving) {
            return;
        }
        this.state.saving = true;
        try {
            await this.props.onSave({
                quantity: Number(this.state.quantity),
                note: this.state.note.trim(),
            });
            this.props.close();
        } finally {
            this.state.saving = false;
        }
    }
}
