# Prayer Times & Azan (`prayer_times_azan`) — Odoo 18 Community

Shows the daily prayer times inside the Odoo backend and plays an azan
(your own MP3) at each prayer time, along with Odoo and desktop notifications.

## Features

- **Times source per location**:
  - **Aladhan API** (default): the times are downloaded every day from the free
    public API `https://api.aladhan.com/v1/calendar` (no key needed, Umm Al-Qura =
    method 4). The Odoo **server** needs outbound HTTPS access to `api.aladhan.com`.
    If the API cannot be reached, the built-in calculation is used automatically
    and the error is shown on the location form; the next daily run retries.
    Ramadan (Umm Al-Qura Isha +30 min) is detected from the Hijri date returned by the API.
  - **Built-in calculation** (offline).
- **Offline calculation**: the module computes the times itself (astronomical
  formulas) with no external API. Umm Al-Qura is the default method; MWL, Egypt,
  Karachi, ISNA, Gulf, Kuwait, Qatar and Custom are also available.
- **Locations** (cities / branches): coordinates, timezone, method, Asr (standard /
  Hanafi), high-latitude rule, Ramadan mode (Isha +30 min, Umm Al-Qura),
  minute adjustments for each prayer, and which prayers get an azan.
- **Azan sounds**: upload MP3 / OGG / WAV / M4A / AAC files (max 15 MB), listen to
  them in the form, and assign a main azan plus an optional Fajr azan per location.
- **Systray** (top bar): the next prayer, a countdown, today's times, sound on/off,
  test azan, stop azan, and a button to enable desktop notifications.
- **At prayer time**: a sticky Odoo notification with a "Stop azan" button, a
  desktop notification (if allowed), and the azan sound. An optional reminder
  can fire X minutes before.
- **Several tabs open**: only one tab plays the sound (the `multi_tab` service from
  `bus`). "Stop" works from any tab.
- **Timetable**: generated every day (cron) for the next 30 days; lines older than
  90 days are removed.
- **User preferences** (My Preferences > Prayer Times): location, notifications,
  sound, volume, reminder.
- Multi-company: a location belongs to one company, or to all when Company is empty.
- Arabic translation included (`i18n/ar.po`).

## Dependencies

`web`, `bus` (both standard). No Python package beyond Odoo's requirements.

## Models

| Model | Purpose |
|---|---|
| `prayer.location` | Location + calculation settings + azan settings. `get_user_prayer_schedule()` is the RPC used by the web client. |
| `prayer.azan.sound` | Uploaded audio file (`attachment=True`). |
| `prayer.timetable` | Daily times per location (read-only, generated). |
| `res.users` (inherit) | `prayer_location_id`, `prayer_notify`, `prayer_play_azan`, `prayer_reminder_minutes`, `prayer_azan_volume` (self-editable). |

The calculation engine is `models/prayer_calc.py` (pure Python, unit tested).

## Security

- Internal users (`base.group_user`): read-only access to locations, sounds and the timetable.
- **Prayer Times / Manager** (`group_prayer_manager`, given to the admin): full access.
- Multi-company record rules on locations and the timetable.

## Configuration

1. *Prayer Times > Configuration > Azan Sounds* → **New** → upload the MP3 → Save.
2. *Prayer Times > Configuration > Locations* → open "Riyadh" (created at install)
   or create your city, then set the coordinates and the method → **Azan** tab →
   choose the sound(s) and the prayers.
3. Each user can choose their location and options in *My Preferences > Prayer Times*.

## Limitations (browser behaviour)

- The azan plays only while an Odoo tab is open in the browser.
- Browsers block automatic sound until the user has clicked once on the page.
  When that happens, a notification with a **Play azan** button appears. Clicking
  **Test azan** once after login avoids it.
- Background tabs can be throttled by the browser: the azan may be delayed by up to
  about one minute. Events missed by more than 3 minutes (e.g. a sleeping PC) are skipped.
- Calculated times can differ by 1–2 minutes from an official calendar (for example
  the Umm Al-Qura calendar or the local mosque). Use the minute adjustments to match it.
- With the built-in calculation, Ramadan mode is a manual switch (no automatic Hijri
  date detection). With the Aladhan API it is automatic.
- The Custom calculation method is only available with the built-in calculation.
- timesprayer.com was considered as a source but offers no public API; Aladhan is used instead.

## Tests

```bash
odoo-bin -d <db> -i prayer_times_azan --test-enable --test-tags=/prayer_times_azan --stop-after-init
```
