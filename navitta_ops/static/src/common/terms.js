/** @odoo-module **/
// Odoo merges the code translations of all installed modules into one flat table, so a word that
// another module translates differently ("Employee", "Confirm", "items" ...) can win over the Navitta
// wording, depending on module order. While a Navitta screen is open, the Navitta translations take
// precedence; the previous values are put back when the screen closes.
// The wrapper loads them before the screen is created, because OWL translates a template when it is
// compiled (the first time its component is created).
import { Component, onWillDestroy, onWillStart, xml } from "@odoo/owl";
import { translatedTerms } from "@web/core/l10n/translation";
import { jsToPyLocale } from "@web/core/l10n/utils";
import { user } from "@web/core/user";

const MODS = "navitta_ops,navitta_ops_pos";
let ours = null;
let active = 0;
let saved = {};

async function loadOurs() {
    if (ours) {
        return ours;
    }
    const lang = jsToPyLocale(user.lang || document.documentElement.getAttribute("lang"));
    ours = {};
    if (!lang || lang.startsWith("en")) {
        return ours;
    }
    try {
        const res = await fetch(`/web/webclient/translations/${Date.now()}?mods=${MODS}&lang=${encodeURIComponent(lang)}`);
        const data = await res.json();
        for (const mod of Object.values(data.modules || {})) {
            for (const msg of mod.messages || []) {
                ours[msg.id] = msg.string;
            }
        }
    } catch {
        // keep Odoo's merged translations
    }
    return ours;
}

function apply(terms) {
    if (active++ === 0) {
        saved = {};
        for (const [id, text] of Object.entries(terms)) {
            if (translatedTerms[id] !== text) {
                saved[id] = Object.prototype.hasOwnProperty.call(translatedTerms, id) ? translatedTerms[id] : undefined;
                translatedTerms[id] = text;
            }
        }
    }
}

function restore() {
    if (--active === 0) {
        for (const [id, text] of Object.entries(saved)) {
            if (text === undefined) {
                delete translatedTerms[id];
            } else {
                translatedTerms[id] = text;
            }
        }
        saved = {};
    }
}

/** Wrap a Navitta client action so it renders with the Navitta translations. */
export function withNavittaTerms(App) {
    class NavittaTerms extends Component {
        static template = xml`<App t-props="props"/>`;
        static components = { App };
        static props = App.props;
        setup() {
            let applied = false;
            onWillStart(async () => {
                apply(await loadOurs());
                applied = true;
            });
            onWillDestroy(() => applied && restore());
        }
    }
    return NavittaTerms;
}
