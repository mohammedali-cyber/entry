/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
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
    static components = { AutoComplete };
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

    get productSources() {
        return [{ options: (term) => this.loadProductOptions(term), placeholder: _t("Searching...") }];
    }

    async loadProductOptions(term) {
        // Without text: every product, numbered names first (21160003- ... before 22030022- ...).
        const result = await this.orm.call("product.lost.finder", "list_templates", [term || ""]);
        const options = result.templates.map((t) => ({
            label: t.default_code ? `[${t.default_code}] ${t.name}` : t.name,
            templateId: t.id,
        }));
        if (term) {
            // First option (selected by Enter): every product containing the typed text.
            options.unshift({ label: _t('All products containing "%s"', term), term });
        }
        return options;
    }

    onProductSelect(option) {
        const exists = this.state.productFilters.some((f) =>
            option.templateId ? f.templateId === option.templateId : !f.templateId && f.term === option.term
        );
        if (!exists) {
            this.state.productFilters.push({
                key: this.nextFilterKey++,
                templateId: option.templateId || 0,
                term: option.term || "",
                label: option.templateId ? option.label : `"${option.term}"`,
            });
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
