/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/core/utils/numbers";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { LostSaleDialog } from "./lost_sale_dialog";

export class ProductLostFinder extends Component {
    static template = "product_lost_finder.SearchPage";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            isManager: false,
            warehouses: [],
            branchId: false,
            columns: [],
            term: "",
            choices: [],
            choicesTotal: 0,
            tables: [],
            selected: {},
            message: "",
            loading: false,
        });
        useAutofocus({ refName: "searchInput" });

        onWillStart(async () => {
            const ctx = await this.orm.call("product.lost.finder", "get_context", []);
            Object.assign(this.state, {
                isManager: ctx.is_manager,
                warehouses: ctx.warehouses,
                branchId: ctx.branch_id,
            });
        });
    }

    // ------------------------------------------------------------------
    // Getters
    // ------------------------------------------------------------------

    get branchName() {
        const branch = this.state.warehouses.find((w) => w.id === this.state.branchId);
        return branch ? branch.name : "";
    }

    get selectedIds() {
        // Keep the on-screen order for the Excel file.
        return this.state.tables.flatMap((table) =>
            table.variants.filter((v) => this.state.selected[v.id]).map((v) => v.id)
        );
    }

    get selectionSummary() {
        const tableCount = this.state.tables.filter((t) => t.variants.some((v) => this.state.selected[v.id])).length;
        return _t("%(variants)s variant(s) selected from %(products)s product(s)", {
            variants: this.selectedIds.length,
            products: tableCount,
        });
    }

    get choicesHint() {
        return _t("%(total)s products match, showing the first %(count)s: pick one to add it.", {
            total: this.state.choicesTotal,
            count: this.state.choices.length,
        });
    }

    formatQty(qty) {
        return formatFloat(qty);
    }

    qtyOf(variant, warehouse) {
        return variant.qty_by_warehouse[warehouse.id] || 0;
    }

    isTableSelected(table) {
        return table.variants.length > 0 && table.variants.every((v) => this.state.selected[v.id]);
    }

    // ------------------------------------------------------------------
    // Search and product tables
    // ------------------------------------------------------------------

    async onSearch() {
        const term = this.state.term.trim();
        if (!term || !this.state.branchId) {
            return;
        }
        this.state.loading = true;
        try {
            const result = await this.orm.call("product.lost.finder", "search_templates", [term]);
            this.state.choices = [];
            this.state.message = "";
            if (!result.templates.length) {
                this.state.message = _t("No product found with this name or number.");
            } else if (result.templates.length === 1) {
                await this.addTemplates([result.templates[0].id]);
                this.state.term = "";
            } else {
                Object.assign(this.state, { choices: result.templates, choicesTotal: result.total });
            }
        } finally {
            this.state.loading = false;
        }
    }

    async onPickChoice(choice) {
        this.state.choices = [];
        this.state.term = "";
        await this.addTemplates([choice.id]);
    }

    async addTemplates(templateIds) {
        const known = new Set(this.state.tables.map((t) => t.id));
        const newIds = templateIds.filter((id) => !known.has(id));
        if (!newIds.length) {
            this.notification.add(_t("This product is already in the list."), { type: "info" });
            return;
        }
        const result = await this.loadTables(newIds);
        this.state.tables.push(...result.tables);
    }

    async loadTables(templateIds) {
        const result = await this.orm.call("product.lost.finder", "get_product_tables", [
            templateIds,
            this.state.branchId,
        ]);
        this.state.columns = result.warehouses;
        return result;
    }

    async onBranchChange() {
        if (this.state.tables.length) {
            const result = await this.loadTables(this.state.tables.map((t) => t.id));
            this.state.tables = result.tables;
        }
    }

    removeTable(table) {
        for (const variant of table.variants) {
            delete this.state.selected[variant.id];
        }
        this.state.tables = this.state.tables.filter((t) => t.id !== table.id);
    }

    clearAll() {
        Object.assign(this.state, { tables: [], selected: {}, choices: [], message: "", term: "" });
    }

    // ------------------------------------------------------------------
    // Selection and Excel export
    // ------------------------------------------------------------------

    toggleVariant(variant) {
        if (this.state.selected[variant.id]) {
            delete this.state.selected[variant.id];
        } else {
            this.state.selected[variant.id] = true;
        }
    }

    toggleTable(table) {
        const select = !this.isTableSelected(table);
        for (const variant of table.variants) {
            if (select) {
                this.state.selected[variant.id] = true;
            } else {
                delete this.state.selected[variant.id];
            }
        }
    }

    async exportExcel() {
        const productIds = this.selectedIds;
        if (!productIds.length) {
            return;
        }
        await download({
            url: "/product_lost_finder/export_xlsx",
            data: {
                product_ids: JSON.stringify(productIds),
                warehouse_id: this.state.branchId,
            },
        });
    }

    // ------------------------------------------------------------------
    // Lost sales
    // ------------------------------------------------------------------

    recordLostSale(table, variant) {
        this.dialog.add(LostSaleDialog, {
            productName: table.name,
            variantName: variant.name,
            branchName: this.branchName,
            uomName: table.uom_name,
            onSave: async ({ quantity, note }) => {
                await this.orm.call("product.lost.sale", "record_lost_sale", [
                    variant.id,
                    this.state.branchId,
                    quantity,
                    note,
                ]);
                this.notification.add(_t("Lost sale recorded."), { type: "success" });
            },
        });
    }

    openLostSalesReport() {
        this.action.doAction("product_lost_finder.action_product_lost_sale");
    }

    openProduct(variant) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: variant.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("product_lost_finder.search_page", ProductLostFinder);
