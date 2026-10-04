import { Component, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class PrayerTimesSystray extends Component {
    static template = "prayer_times_azan.PrayerTimesSystray";
    static components = { Dropdown };
    static props = {};

    setup() {
        this.prayer = useService("prayer_azan");
        this.state = useState(this.prayer.state);
        this.ui = useState({ permission: this.prayer.desktopPermission });
    }

    get next() {
        return this.prayer.nextPrayer;
    }

    get countdown() {
        const next = this.next;
        if (!next) {
            return "";
        }
        const total = Math.max(0, Math.floor((next.ts - this.state.now) / 1000));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }

    isNext(prayer) {
        return this.next && this.next.key === prayer.key && this.next.date === prayer.date;
    }

    isPast(prayer) {
        return prayer.ts <= this.state.now;
    }

    async onTogglePlayAzan() {
        await this.prayer.setPlayAzan(!this.state.playAzan);
    }

    async onEnableDesktop() {
        this.ui.permission = await this.prayer.requestDesktopPermission();
    }
}

registry.category("systray").add(
    "prayer_times_azan.systray",
    { Component: PrayerTimesSystray },
    { sequence: 30 }
);
