/** @odoo-module **/

import { Component, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/core/utils/numbers";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const { DateTime } = luxon;
const PAGE_SIZE = 50;

export class LostSaleDetailReport extends Component {
    static template = "product_lost_finder.DetailReport";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.periods = [
            { key: "all", label: _t("All time") },
            { key: "this_month", label: _t("This month") },
            { key: "last_month", label: _t("Last month") },
            { key: "last_3_months", label: _t("Last 3 months") },
            { key: "this_year", label: _t("This year") },
            { key: "custom", label: _t("Custom dates") },
        ];
        this.state = useState({
            options: null,
            productFilters: [],
            valueByAttribute: {},
            warehouseId: 0,
            columnAttributeId: 0,
            period: "this_month",
            status: "all",
            order: "requested",
            dateFrom: "",
            dateTo: "",
            report: null,
            loading: false,
            loadingMore: false,
        });
        this.loadSequence = 0;
        this.nextFilterKey = 1;
        this.state.picker = { open: false, term: "", items: [], itemsTerm: "", loading: false };
        this.pickerSequence = 0;
        this.pickerDirty = false;
        this.pickerRef = useRef("picker");
        // Close the product list when clicking anywhere else on the page.
        useExternalListener(window, "mousedown", (ev) => {
            if (this.state.picker.open && this.pickerRef.el && !this.pickerRef.el.contains(ev.target)) {
                this.closePicker();
            }
        });

        onWillStart(async () => {
            const options = await this.orm.call("product.lost.sale", "get_detail_options", []);
            this.state.options = options;
            this.state.columnAttributeId = options.column_attribute_id || 0;
            await this.loadReport();
        });
    }

    get filters() {
        const range = this.dateRange;
        return {
            products: this.state.productFilters.map((f) =>
                f.templateId ? { template_id: f.templateId } : { term: f.term }
            ),
            value_ids: Object.values(this.state.valueByAttribute).filter((id) => id),
            warehouse_id: this.state.warehouseId || false,
            column_attribute_id: this.state.columnAttributeId || false,
            status: this.state.status,
            order: this.state.order,
            date_from: range.from,
            date_to: range.to,
        };
    }

    get dateRange() {
        const today = DateTime.local().startOf("day");
        const iso = (d) => d.toISODate();
        switch (this.state.period) {
            case "this_month":
                return { from: iso(today.startOf("month")), to: iso(today) };
            case "last_month": {
                const last = today.minus({ months: 1 });
                return { from: iso(last.startOf("month")), to: iso(last.endOf("month")) };
            }
            case "last_3_months":
                return { from: iso(today.minus({ months: 3 })), to: iso(today) };
            case "this_year":
                return { from: iso(today.startOf("year")), to: iso(today) };
            case "custom":
                return { from: this.state.dateFrom || false, to: this.state.dateTo || false };
            default:
                return { from: false, to: false };
        }
    }

    get branchLabel() {
        const warehouses = this.state.options?.warehouses || [];
        return warehouses.length ? warehouses[0].name : "";
    }

    get rowsHint() {
        return _t("Showing %(count)s of %(total)s products", {
            count: this.state.report.rows.length,
            total: this.state.report.row_count,
        });
    }

    get hasMoreRows() {
        return this.state.report && this.state.report.rows.length < this.state.report.row_count;
    }

    formatStock(qty) {
        return formatFloat(qty, { trailingZeros: false });
    }

    formatQty(qty) {
        return qty ? formatFloat(qty, { trailingZeros: false }) : "—";
    }

    formatAmount(amount) {
        return formatCurrency(amount, this.state.report.currency_id);
    }

    async loadReport() {
        const sequence = ++this.loadSequence;
        this.state.loading = true;
        try {
            const report = await this.orm.call("product.lost.sale", "get_detail_report", [
                { ...this.filters, offset: 0, limit: PAGE_SIZE },
            ]);
            if (sequence === this.loadSequence) {
                this.state.report = report;
            }
        } finally {
            if (sequence === this.loadSequence) {
                this.state.loading = false;
            }
        }
    }

    async loadMore() {
        const sequence = this.loadSequence;
        this.state.loadingMore = true;
        try {
            const page = await this.orm.call("product.lost.sale", "get_detail_report", [
                { ...this.filters, offset: this.state.report.rows.length, limit: PAGE_SIZE },
            ]);
            // Drop the page if the filters changed meanwhile.
            if (sequence === this.loadSequence) {
                this.state.report.rows.push(...page.rows);
            }
        } finally {
            this.state.loadingMore = false;
        }
    }

    // ------------------------------------------------------------------
    // Product picker: list of every product with checkboxes
    // ------------------------------------------------------------------

    get selectedTemplateIds() {
        return new Set(this.state.productFilters.filter((f) => f.templateId).map((f) => f.templateId));
    }

    get pickerTermLabel() {
        return _t('Add every product containing "%s"', this.state.picker.term.trim());
    }

    get pickerSelectedCount() {
        return this.state.productFilters.filter((f) => f.templateId).length;
    }

    async openPicker() {
        if (!this.state.picker.open) {
            this.state.picker.open = true;
            await this.loadPickerItems();
        }
    }

    onPickerInput(ev) {
        this.state.picker.term = ev.target.value;
        this.state.picker.open = true;
        browser.clearTimeout(this.pickerTimeout);
        this.pickerTimeout = browser.setTimeout(() => this.loadPickerItems(), 250);
    }

    async loadPickerItems() {
        const sequence = ++this.pickerSequence;
        this.state.picker.loading = true;
        try {
            // Every product (numbered names first: 21160003- ... before 22030022- ...).
            const result = await this.orm.call("product.lost.finder", "list_templates", [
                this.state.picker.term.trim(),
            ]);
            if (sequence === this.pickerSequence) {
                this.state.picker.itemsTerm = this.state.picker.term.trim();
                this.state.picker.items = result.templates.map((t) => ({
                    id: t.id,
                    label: t.default_code ? `[${t.default_code}] ${t.name}` : t.name,
                }));
            }
        } finally {
            if (sequence === this.pickerSequence) {
                this.state.picker.loading = false;
            }
        }
    }

    togglePickerItem(item) {
        if (this.selectedTemplateIds.has(item.id)) {
            this.state.productFilters = this.state.productFilters.filter((f) => f.templateId !== item.id);
        } else {
            this.state.productFilters.push({ key: this.nextFilterKey++, templateId: item.id, term: "", label: item.label });
        }
        // The report is refreshed once, when the list is closed.
        this.pickerDirty = true;
    }

    addPickerTerm() {
        const term = this.state.picker.term.trim();
        if (term && !this.state.productFilters.some((f) => !f.templateId && f.term === term)) {
            this.state.productFilters.push({ key: this.nextFilterKey++, templateId: 0, term, label: `"${term}"` });
            this.pickerDirty = true;
        }
        this.closePicker();
    }

    onPickerKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.state.picker.term.trim() ? this.addPickerTerm() : this.closePicker();
        } else if (ev.key === "Escape") {
            this.closePicker();
        }
    }

    closePicker() {
        if (!this.state.picker.open) {
            return;
        }
        Object.assign(this.state.picker, { open: false, term: "" });
        if (this.state.picker.itemsTerm) {
            // The list was narrowed by a search: show the full list again next time.
            Object.assign(this.state.picker, { items: [], itemsTerm: "" });
        }
        if (this.pickerDirty) {
            this.pickerDirty = false;
            this.loadReport();
        }
    }

    removeProductFilter(filter) {
        this.state.productFilters = this.state.productFilters.filter((f) => f.key !== filter.key);
        this.loadReport();
    }

    clearProducts() {
        this.state.productFilters = [];
        this.loadReport();
    }

    onValueChange(attribute, ev) {
        this.state.valueByAttribute[attribute.id] = parseInt(ev.target.value) || 0;
        this.loadReport();
    }

    resetFilters() {
        Object.assign(this.state, {
            productFilters: [],
            valueByAttribute: {},
            warehouseId: 0,
            period: "this_month",
            status: "all",
            dateFrom: "",
            dateTo: "",
        });
        this.loadReport();
    }

    async exportExcel() {
        await download({
            url: "/product_lost_finder/export_detail_xlsx",
            data: { filters: JSON.stringify(this.filters) },
        });
    }
}

registry.category("actions").add("product_lost_finder.detail_report", LostSaleDetailReport);
