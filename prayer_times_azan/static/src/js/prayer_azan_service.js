import { reactive } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";

const { DateTime } = luxon;

const TICK_DELAY = 1000;
// Events missed by less than this (sleeping laptop, throttled background tab)
// are still triggered; older ones are silently skipped.
const GRACE_PERIOD = 3 * 60 * 1000;
const RELOAD_DELAY = 60 * 60 * 1000;
const ICON_URL = "/prayer_times_azan/static/description/icon.png";

// Keys shared between the browser tabs (multi_tab service, localStorage).
const LAST_EVENT_KEY = "prayer_azan.last_event";
const STOP_KEY = "prayer_azan.stop";
const PLAYING_KEY = "prayer_azan.playing";

export const prayerAzanService = {
    dependencies: ["orm", "notification", "multi_tab"],

    start(env, { orm, notification, multi_tab: multiTab }) {
        const state = reactive({
            loaded: false,
            location: false,
            prayers: [],
            notify: true,
            playAzan: true,
            reminderMinutes: 0,
            volume: 100,
            now: Date.now(),
            playingHere: false,
            playingElsewhere: Boolean(multiTab.getSharedValue(PLAYING_KEY, false)),
        });
        // Server clock minus browser clock, so a wrong PC clock does not shift the azan.
        let clockSkew = 0;
        let lastLoad = 0;
        let loading = false;
        let audio = null;
        let closePlayingNotification = null;
        const firedEvents = new Set();

        function now() {
            return Date.now() + clockSkew;
        }

        async function load() {
            if (loading) {
                return;
            }
            loading = true;
            try {
                const data = await orm.silent.call(
                    "prayer.location",
                    "get_user_prayer_schedule",
                    []
                );
                clockSkew = deserializeDateTime(data.server_now).toMillis() - Date.now();
                Object.assign(state, {
                    location: data.location,
                    notify: data.notify,
                    playAzan: data.play_azan,
                    reminderMinutes: data.reminder_minutes || 0,
                    volume: data.volume ?? 100,
                    prayers: data.prayers.map((prayer) => ({
                        ...prayer,
                        ts: deserializeDateTime(prayer.time).toMillis(),
                    })),
                    loaded: true,
                });
                if (!lastLoad) {
                    // First load: never replay what happened before the page was opened.
                    const current = now();
                    for (const event of getEvents()) {
                        if (event.ts <= current) {
                            firedEvents.add(event.id);
                        }
                    }
                }
                lastLoad = Date.now();
            } catch {
                // Network error or access error: retry at the next reload delay.
                lastLoad = Date.now();
            } finally {
                loading = false;
            }
        }

        function getEvents() {
            const events = [];
            for (const prayer of state.prayers) {
                if (!prayer.azan) {
                    continue;
                }
                events.push({ id: `${prayer.date}:${prayer.key}:azan`, type: "azan", ts: prayer.ts, prayer });
                if (state.reminderMinutes > 0) {
                    events.push({
                        id: `${prayer.date}:${prayer.key}:reminder:${state.reminderMinutes}`,
                        type: "reminder",
                        ts: prayer.ts - state.reminderMinutes * 60 * 1000,
                        prayer,
                    });
                }
            }
            return events;
        }

        /**
         * Only one tab (the main one) plays the sound and shows the desktop
         * notification, the shared value protects against a double election.
         */
        function claimEvent(eventId) {
            if (!multiTab.isOnMainTab()) {
                return false;
            }
            if (multiTab.getSharedValue(LAST_EVENT_KEY) === eventId) {
                return false;
            }
            multiTab.setSharedValue(LAST_EVENT_KEY, eventId);
            return true;
        }

        function desktopNotify(title, body) {
            const Notification = browser.Notification;
            if (!Notification || Notification.permission !== "granted") {
                return;
            }
            try {
                new Notification(title, { body, icon: ICON_URL, tag: "prayer_azan" });
            } catch {
                // Some mobile browsers only allow notifications from a service worker.
            }
        }

        function setPlaying(playing) {
            state.playingHere = playing;
            multiTab.setSharedValue(PLAYING_KEY, playing);
            if (!playing && closePlayingNotification) {
                closePlayingNotification();
                closePlayingNotification = null;
            }
        }

        function stopLocal() {
            if (audio) {
                audio.pause();
                audio = null;
            }
            if (state.playingHere) {
                setPlaying(false);
            }
        }

        function stop() {
            stopLocal();
            state.playingElsewhere = false;
            multiTab.setSharedValue(PLAYING_KEY, false);
            multiTab.setSharedValue(STOP_KEY, Date.now());
        }

        function play(url, prayerName) {
            stopLocal();
            const player = new browser.Audio(url);
            player.volume = Math.min(Math.max(state.volume, 0), 100) / 100;
            player.addEventListener("ended", () => {
                if (audio === player) {
                    audio = null;
                    setPlaying(false);
                }
            });
            audio = player;
            setPlaying(true);
            player.play().catch((error) => {
                if (audio !== player) {
                    return;
                }
                audio = null;
                setPlaying(false);
                if (error.name === "NotAllowedError") {
                    // Autoplay policy: the page needs a click before playing sound.
                    const close = notification.add(
                        _t("Your browser blocked the azan sound. Click the button to play it."),
                        {
                            title: prayerName,
                            type: "warning",
                            sticky: true,
                            buttons: [
                                {
                                    name: _t("Play azan"),
                                    icon: "fa-play",
                                    primary: true,
                                    onClick: () => {
                                        close();
                                        play(url, prayerName);
                                    },
                                },
                            ],
                        }
                    );
                } else {
                    notification.add(_t("The azan sound could not be played."), {
                        type: "danger",
                    });
                }
            });
        }

        function triggerAzan(event) {
            const { prayer } = event;
            const title = _t("Azan time");
            const message = _t("It is now time for %s prayer.", prayer.name);
            const owner = claimEvent(event.id);
            const willPlay = owner && state.playAzan && prayer.sound_url;
            if (state.notify || willPlay) {
                const close = notification.add(message, {
                    title,
                    type: "success",
                    sticky: true,
                    className: "o_prayer_azan_notification",
                    buttons: [
                        {
                            name: _t("Stop azan"),
                            icon: "fa-stop",
                            onClick: () => {
                                stop();
                                close();
                            },
                        },
                    ],
                    onClose: () => {
                        if (closePlayingNotification === close) {
                            closePlayingNotification = null;
                        }
                    },
                });
                if (willPlay) {
                    closePlayingNotification = close;
                }
            }
            if (owner && state.notify) {
                desktopNotify(title, message);
            }
            if (willPlay) {
                play(prayer.sound_url, prayer.name);
            }
        }

        function triggerReminder(event) {
            if (!state.notify) {
                return;
            }
            const message = _t(
                "%(prayer)s azan in %(minutes)s minutes.",
                { prayer: event.prayer.name, minutes: state.reminderMinutes }
            );
            notification.add(message, { title: _t("Prayer reminder"), type: "info" });
            if (claimEvent(event.id)) {
                desktopNotify(_t("Prayer reminder"), message);
            }
        }

        function tick() {
            const current = now();
            state.now = current;
            for (const event of getEvents()) {
                if (firedEvents.has(event.id) || event.ts > current) {
                    continue;
                }
                firedEvents.add(event.id);
                if (current - event.ts > GRACE_PERIOD) {
                    continue;
                }
                if (event.type === "azan") {
                    triggerAzan(event);
                } else {
                    triggerReminder(event);
                }
            }
            // Reload hourly (settings changes, new day) and as soon as the
            // schedule runs out of upcoming prayers.
            const sinceLoad = Date.now() - lastLoad;
            const exhausted =
                state.prayers.length && !state.prayers.some((prayer) => prayer.ts > current);
            if (sinceLoad > RELOAD_DELAY || (exhausted && sinceLoad > 60 * 1000)) {
                load();
            }
        }

        multiTab.bus.addEventListener("shared_value_updated", ({ detail }) => {
            if (detail.key === STOP_KEY) {
                stopLocal();
                state.playingElsewhere = false;
            } else if (detail.key === PLAYING_KEY) {
                let value = false;
                try {
                    value = JSON.parse(detail.newValue);
                } catch {
                    value = false;
                }
                state.playingElsewhere = Boolean(value);
            }
        });

        load().then(() => {
            browser.setInterval(tick, TICK_DELAY);
        });

        return {
            state,
            stop,
            reload: load,
            get isPlaying() {
                return state.playingHere || state.playingElsewhere;
            },
            get nextPrayer() {
                const current = state.now;
                return state.prayers.find((prayer) => prayer.ts > current) || false;
            },
            get todayPrayers() {
                if (!state.prayers.length) {
                    return [];
                }
                const today = state.prayers[0].date;
                return state.prayers.filter((prayer) => prayer.date === today);
            },
            get testSoundUrl() {
                const prayer = state.prayers.find((p) => p.sound_url && p.key !== "fajr") ||
                    state.prayers.find((p) => p.sound_url);
                return prayer ? prayer.sound_url : false;
            },
            formatTime(prayer) {
                return DateTime.fromMillis(prayer.ts, {
                    zone: (state.location && state.location.tz) || "default",
                }).toFormat("HH:mm");
            },
            testAzan() {
                const url = this.testSoundUrl;
                if (url) {
                    play(url, _t("Test"));
                } else {
                    notification.add(_t("No azan sound is assigned to your location."), {
                        type: "warning",
                    });
                }
            },
            async setPlayAzan(value) {
                await orm.call("res.users", "set_prayer_preferences", [
                    { prayer_play_azan: value },
                ]);
                state.playAzan = value;
                if (!value) {
                    stop();
                }
            },
            get desktopPermission() {
                const Notification = browser.Notification;
                return Notification ? Notification.permission : "unsupported";
            },
            async requestDesktopPermission() {
                const Notification = browser.Notification;
                if (Notification && Notification.permission === "default") {
                    await Notification.requestPermission();
                }
                return this.desktopPermission;
            },
        };
    },
};

registry.category("services").add("prayer_azan", prayerAzanService);
