import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/** Audio player for the azan sound form view (needs the `audio_url` field). */
export class PrayerAzanPlayer extends Component {
    static template = "prayer_times_azan.PrayerAzanPlayer";
    static props = { ...standardWidgetProps };

    get url() {
        const { record } = this.props;
        return record.resId && !record.dirty ? record.data.audio_url : false;
    }
}

registry.category("view_widgets").add("prayer_azan_player", { component: PrayerAzanPlayer });
