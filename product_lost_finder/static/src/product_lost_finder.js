/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/core/utils/numbers";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const WAREHOUSE_STORAGE_KEY = "product_lost_finder.warehouse_id";

export class ProductLostFinder extends Component {
    static template = "product_lost_finder.SearchPage";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            warehouses: [],
            warehouseId: false,
            term: "",
            searchedTerm: "",
            searchedWarehouseId: false,
            products: [],
            total: 0,
            searched: false,
            loading: false,
        });
        this.searchSequence = 0;
        useAutofocus({ refName: "searchInput" });

        onWillStart(async () => {
            const data = await this.orm.call("product.lost.finder", "get_warehouses", []);
            this.state.warehouses = data.warehouses;
            const savedId = this.loadSavedWarehouseId();
            const isAllowed = (id) => data.warehouses.some((w) => w.id === id);
            this.state.warehouseId = isAllowed(savedId) ? savedId : data.default_warehouse_id;
        });
    }

    get searchedWarehouseName() {
        const warehouse = this.state.warehouses.find((w) => w.id === this.state.searchedWarehouseId);
        return warehouse ? warehouse.name : "";
    }

    get showResultsHint() {
        return this.state.total > this.state.products.length;
    }

    get resultsHint() {
        return _t(
            "Showing the first %(count)s of %(total)s products, refine your search.",
            { count: this.state.products.length, total: this.state.total }
        );
    }

    loadSavedWarehouseId() {
        try {
            return parseInt(browser.localStorage.getItem(WAREHOUSE_STORAGE_KEY)) || false;
        } catch {
            return false;
        }
    }

    saveWarehouseId(warehouseId) {
        try {
            browser.localStorage.setItem(WAREHOUSE_STORAGE_KEY, String(warehouseId));
        } catch {
            // Browser storage unavailable: the choice is simply not remembered.
        }
    }

    formatQty(product) {
        return formatFloat(product.qty_available);
    }

    async onSearch() {
        const term = this.state.term.trim();
        if (!term || !this.state.warehouseId) {
            return;
        }
        const sequence = ++this.searchSequence;
        this.state.loading = true;
        try {
            const warehouseId = this.state.warehouseId;
            const result = await this.orm.call("product.lost.finder", "search_products", [
                term,
                warehouseId,
            ]);
            // Ignore answers of older searches that arrive after a newer one.
            if (sequence !== this.searchSequence) {
                return;
            }
            Object.assign(this.state, {
                products: result.products,
                total: result.total,
                searchedTerm: term,
                searchedWarehouseId: warehouseId,
                searched: true,
            });
        } finally {
            if (sequence === this.searchSequence) {
                this.state.loading = false;
            }
        }
    }

    onWarehouseChange() {
        this.saveWarehouseId(this.state.warehouseId);
        if (this.state.searched) {
            this.state.term = this.state.searchedTerm;
            this.onSearch();
        }
    }

    onClear() {
        Object.assign(this.state, { term: "", products: [], total: 0, searched: false });
    }

    openProduct(product) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: product.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("product_lost_finder.search_page", ProductLostFinder);
