# Wall-HUB

**English** · [Русский](README.ru.md)

A wall panel built on an Orange Pi 3B (1024×600 touch screen): a clock, a
status bar and service information. An xray proxy will live here later too.

## Installation

On a clean console Armbian 13 (Debian trixie), after the first login:

```sh
git clone https://github.com/tesa4ello/wall-hub
cd wall-hub
sudo ./install.sh
```

Running `install.sh` again installs the version from the current folder and
keeps the settings. Usually you don't need to: the panel updates itself (see
below).

The installer disables the Wi‑Fi configured at Armbian's first login
(netplan + networkd): the files move to `/etc/netplan/wallhub-disabled/`, the
adapter is handed over to NetworkManager, and the network is chosen on the
panel itself at first start. Ethernet stays as it was.
At the end the script prints the admin panel address. It is also shown on the
panel under "Settings → System" as text and a QR code. There is no password at
first: you create one at the first admin sign-in, so open the admin panel right
after installing. To reset the password (a new one is set again at the next
sign-in): `sudo ./install.sh --reset-password`.

The text console is not shown on the panel's screen (kernel parameter
`fbcon=map:9` in `/boot/armbianEnv.txt`), so no text flashes during power on
and off. Use SSH for maintenance. If you need the console on screen, remove
this parameter from `extraargs` and reboot.

## How it works

| Part | Where | What it does |
|---|---|---|
| Backend | `wallhub/`, service `wallhub.service` | Python (venv inside the release), HTTP API and static files |
| Releases | `/opt/wallhub/releases/<commit>` | `current` and `previous` are symlinks to the active and previous releases |
| Kiosk | `http://127.0.0.1:8080/` | the panel's screen, no password, local access only |
| Admin panel | `http://<address>/` (port 80) | the same API, but only after signing in |
| Settings | `/var/lib/wallhub/config.json` | only differences from the defaults are stored |
| Graphics | user `kiosk`, tty1 | autologin → `startx` → openbox → `deploy/kiosk.sh` → chromium |

The service runs as the system user `wallhub`. It can open port 80 thanks to
`CAP_NET_BIND_SERVICE` in the unit file. Rights for Wi‑Fi (NetworkManager),
reboot/shutdown and time zone changes are granted through polkit
(`deploy/50-wallhub.rules`); it doesn't need root.

Chromium runs as a clean kiosk: the flags in `deploy/kiosk.sh` and the policies
in `deploy/chromium-policy.json` turn off the translator, password manager,
notifications, first-run screens and the offer to restore tabs after a power
cut.

### Theme and dimming

- **Theme:** manual, by time (day/night from set hours) or by the sun. Sunrise
  and sunset are computed on the panel from the coordinates set in the admin
  panel, without internet. Theme changes are animated.
- **Night dimming:** at night (by the same rule) the screen is constantly
  dimmed by a set percentage.
- **Idle dimming:** after N minutes without touches the screen darkens, never
  less than the night level.
- **Screen off:** after M minutes the X server turns the backlight off (DPMS);
  `deploy/display-sync.py` picks the setting up. The first touch after dimming
  or screen off only wakes the screen and doesn't press buttons.

### Background and glass tiles

The background image is uploaded in the admin panel (Display → Background and
tiles). The browser crops it to 1024×600 and also renders a blurred copy, so
the panel never decodes a full-size photo. Files live in
`/var/lib/wallhub/backgrounds`. The dark theme can have its own background.

Tiles over the background come in four styles: **Glass (light on CPU)** shows
the matching piece of the pre-blurred copy (looks like glass, costs nothing to
draw, the default), **Glass** uses a real `backdrop-filter` blur (heavier on the
Orange Pi, especially without GPU compositing), **Frosted** is translucent
without blur, **Solid** is opaque. Any widget can get or drop its tile with
"Tile under the widget" in the desktop editor.

### Updates

The panel updates from GitHub without reinstalling the system. There are two
channels:

- **Stable**: branch `stable`, the default (it was called `main` before 1.0.0;
  the updater still finds it under the old name);
- **Development**: branch `dev`. New changes land here first and reach stable
  when `dev` is merged into `stable`.

How a release is installed:

1. The updater `deploy/wallhub-update.py` (root, unit
   `wallhub-update@<mode>.service`) asks the GitHub API for the latest commit of
   the channel's branch and the version number in it.
2. It downloads the commit archive to `/opt/wallhub/releases/<commit>` and runs
   its `install.sh --updater`: only missing packages are installed, the
   release's venv is created, the `current` symlink is switched and the service
   restarts.
3. If the service doesn't answer with the new version within a minute,
   `current` goes back to the previous release. The last three releases are
   kept.
4. Once the new version answers, the panel reboots (the screen shows
   "Rebooting the panel…" for a few seconds before that), so the new release
   starts clean. A rollback does not reboot: the screen just reloads the page.

The automatic check schedule (never, hourly, every 6 hours or daily at a set
time), the channel and automatic installation are set on the screen under
"Settings → System" and in the admin panel on the "Updates" tab. The
`wallhub-update-auto.timer` wakes the updater every 15 minutes, and the updater
decides from the schedule whether a check is due. If automatic installation is
off, the panel only announces a new version: with a banner in "System" and a
red dot at the top edge of the screen.

The installer takes the token for a private repository from the `git remote`
address (`https://<user>:<token>@github.com/...`) and stores it in
`/var/lib/wallhub/github-token` (mode 600); you can change it in the admin
panel. Update progress is logged to `/var/lib/wallhub/update.log`.

From the console: `sudo systemctl start wallhub-update@install` (or `check`,
`rollback`).

### Network

On the screen, under "Settings → Network":

- Ethernet and Wi‑Fi: current addresses, gateway and DNS; DHCP or a manual
  address (address with mask, gateway and DNS are typed on the on-screen
  keyboard), a "Back to DHCP" button and "Manage from the panel" for Ethernet
  under netplan, the same as in the admin panel (see below);
- the external address from several services (Yandex, Cloudflare, ipify,
  icanhazip, ifconfig.me, ipinfo.io). If the services see different addresses
  (for example, some traffic goes through a proxy), all of them are shown with
  which service returned what;
- a speed test via Speedtest (the `speedtest-cli` module) and Yandex
  Internetometer. The latest results are stored in
  `/var/lib/wallhub/speedtest.json`.

The admin panel address is shown under "System" as text and a QR code when
the panel is online.

Before Wi‑Fi is turned off, the panel asks for confirmation if there is no
other connection (cable). If Wi‑Fi is off anyway and there has been no
connection for 10 minutes, the service turns it back on: otherwise the panel
would lose its admin panel, and NetworkManager remembers the radio being off
even after a reboot.

The "Network" section of the admin panel (and of the screen) covers both Wi‑Fi and Ethernet. For
the connected Wi‑Fi network and for the cable you can choose DHCP or set the
address with mask, gateway and DNS by hand. The panel applies the change in
the background, waits up to 25 seconds for an address and a reply from the
gateway, and restores the previous settings if there is no connection. If
Ethernet is still configured by netplan (systemd-networkd), the "Manage from
the panel" button starts `wallhub-net-ethernet.service` (as root): it moves
the cable settings into a NetworkManager profile, moves the netplan files to
`/etc/netplan/wallhub-disabled/` and checks the connection for 45 seconds. If
the link does not come up, everything is restored, so this is safe even over
SSH through the cable. Netplan files that contain more than Ethernet are not
moved automatically.

### Desktops and widgets

The screen consists of one or more desktops. Swipe left and right to switch
between them; dots at the bottom show where you are. After a set time without
touches the panel returns to the first desktop. Each desktop is a 16×10 grid
(64×60 px per cell), and a widget takes a rectangle of cells. The status bar is
a widget too, so it can go anywhere or be removed.

The layout is edited only in the admin panel, on the "Desktops" tab. It has a
scaled preview of the screen with real widgets. Drag widgets with the mouse and
resize them by the corner; the palette is on the left, the selected widget's
settings on the right. Changes are saved immediately and appear on the panel by
themselves.

Widgets come from modules. A module is a folder `web/modules/<module>/`:

- `module.json`: the title and the list of widget types, each with an `id`,
  title, icon, sizes (`size.default/min/max` in cells), `card` (whether to draw
  a backing card), a settings schema (`bool`, `text`, `number`, `select`,
  `providers`, `location`, `choice` with choices served by the module backend)
  and translations (`i18n`);
- `module.js`: exports `widgets = {id: mount}`, where
  `mount(el, {options, api, sound})` returns `{destroy()}`; `sound.play(name?)` plays a notification sound on the panel (respecting the sound settings and quiet hours; silent in the admin preview);
- `module.css`: styles. Prefer `cqw`/`cqh` units: a widget is a container, so
  its content fits any size;
- `admin.js` (optional): the module settings page, `mount(el, {api, t})`,
  opened by the "Settings" button on the "Modules" tab;
- `backend/` (optional, `"backend": "backend/main.py"` in module.json): the
  server part. The core imports it in its own process (`wallhub/backends.py`)
  and calls `setup(ctx)`: `ctx.route(method, path, handler, admin=False)` adds
  a route `/api/m/<module>/<path>` (for the panel screen and the admin; with
  `admin=True` for the admin only), `ctx.settings()`/`ctx.save_settings()`
  keep the module settings in the config, `ctx.data_dir` is its folder in
  `/var/lib/wallhub/module-data/`. Modules keep their own secrets, such as SSH keys, in `ctx.data_dir`. A function
  (or an object with `stop()`) returned from `setup` is called when the module
  is removed or updated.

A widget type is `module/widget` (for example, `clock/clock`). One type can be
placed any number of times, each instance with its own settings. The layout is
stored in `desktops` in `config.json` and checked on the server
(`wallhub/desktops.py`): sizes, bounds, overlaps, settings types.

Clock, status bar and weather are built-in modules in `web/modules/`. Other
modules come from the module store, the repository
[tesa4ello/wallhub-modules](https://github.com/tesa4ello/wallhub-modules) (its
`CLAUDE.md` describes the format). The admin panel's "Modules" tab lists the
modules of all connected catalogs and installs, updates and deletes them;
deleting a module also removes its widgets from the desktops. A catalog is any
GitHub repository with the same layout (`modules/<id>/` plus `catalog.json`);
more catalogs are connected on the same tab, and if two catalogs have a module
with the same id, the one higher in the list wins. Connecting a catalog means
trusting its code. Private catalogs need a GitHub token with read access to
their repositories; it is the same token as for updates and can be set on the
"Modules" or "Updates" tab. Installed modules live in `/var/lib/wallhub/modules/<id>/` (`current` and
`previous` point to versions); the service installs them itself, without root.
Module server parts run inside the core process (see above); their data in
`/var/lib/wallhub/module-data/<id>/` is kept when the module is removed. Each `module.json` has `id`, `version` and
`core`, the major version of the core module API (`CORE_API` in
`wallhub/__init__.py`); a store module with a different `core` is not loaded.
A widget whose module is missing stays on its desktop as a placeholder.

Installed store modules update themselves like the system does
(`wallhub/modupdate.py`, settings in "Modules → Module updates"): on a
schedule (hourly, every 6 hours, daily at a set time or off) the core re-reads
the catalogs and, with automatic install on, installs newer compatible
versions. If the server part of a new version fails to start, the previous
version is restored. Without automatic install the admin panel and the shade
on the screen show how many modules can be updated. The result is kept in
`/var/lib/wallhub/module-update.json`.

### On-screen controls

There is no gear icon; the interface is made for touch:

- **swipe down from the top edge** (or tap the very edge) to open the shade:
  Wi‑Fi, theme, "Settings" and the new version notice;
- **long press (2 s)** anywhere on a desktop opens the settings;
- **a red dot at the top edge** means a new version is out.

Without a touch screen (udev has no `ID_INPUT_TOUCHSCREEN` device) the panel
is controlled with a mouse: the cursor is shown, the wheel switches desktops,
right click opens the settings and a click on the top edge opens the shade.

### Screen size

The interface is designed for 1024×600 with a 16×10 grid. On other screens
Chromium starts with `--force-device-scale-factor`, so the layout keeps its
proportions: the automatic scale is the largest 0.25 step that still fits
1024×600 (1366×768 → ×1.25, 1920×1080 → ×1.75, 800×480 → ×0.75). It can be set
by hand under "Display → Interface scale"; the kiosk restarts with the new
scale within 15 seconds. The resolution comes from the kiosk itself (screen
size × devicePixelRatio) and, before its first report, from DRM
(`/sys/class/drm`). `GET /api/info` returns it as `display`: `width`, `height`,
`scale`, `view` (the interface size in CSS pixels, used by the desktop
editor canvas), `touch` and `touch_devices`. "System" shows the screen and
the touch screen.

### Sound

The panel can play notification sounds; the kiosk synthesizes them itself
(`web/lib/sound.js`, Web Audio, no files). Settings are in the admin under
"Display → Sound": on/off, volume, the sound, quiet hours and events. For now
updates make a sound: a new version is out and an update has finished (a failed
one uses a different signal). "Test" plays the sound on the panel even when
sound is off. Modules play sounds via `sound.play()` in the widget context.
Audio goes to the default ALSA device; the kiosk user is in the audio group.

### Screen mirror

The "Screen" tab in the admin shows the panel screen live (noVNC, vendored in
`web/lib/vendor/`). It is view-only by default; the "Control" checkbox sends
clicks, gestures and keys to the panel. Under the hood x11vnc
(`wallhub-vnc.service`, running as the kiosk user) listens only on
`127.0.0.1:5900`, and the service exposes it over the `/api/screen/ws`
WebSocket to signed-in admin sessions only. The unit starts when the tab is
opened and stops a minute after it is closed.

### Weather and location

The Weather module (`web/modules/weather`, backend `wallhub/weather.py`)
provides widgets: Weather now, Now and hourly, Hourly forecast (curve), Daily
forecast (temperature range bars), Precipitation (next 2 hours in 15-minute
steps or 12 hours), Details and Weather: large. The forecast comes from
[Open-Meteo](https://open-meteo.com) without a key, the service caches it for
10 minutes and shows the last one it got while offline. If Open-Meteo is
unreachable (some ISPs block it), the forecast falls back to
[MET Norway](https://api.met.no) automatically and city search uses
OpenStreetMap. The Weather tab of the admin panel enables and orders the
sources (also OpenWeatherMap, WeatherAPI.com and Yandex Weather with a key),
tests each one and sets the refresh interval and default units. Each widget has its own
place ("same as panel" or a city found by name) and units (°C/°F,
m/s·km/h·mph, mmHg/hPa).

The panel location is set in the admin panel, Display → Location:

- **Automatically by IP** (default): no keys, city-level accuracy;
- **Yandex Locator**: by visible Wi‑Fi access points, more precise. Needs a
  Locator API key from the Yandex developer console; falls back to IP on error;
- **Manual**: city search or coordinates.

The automatic location is refreshed every 6 hours. Sunrise and sunset for the
"by the sun" theme use it as well.

### Status bar

A status bar item is a provider function in the `wallhub/status/` package,
marked with `@provider("id", "Title")`. It returns `{icon, text, title, level}`.
A new provider appears in the status bar widget's settings right away. The
service polls only the providers that at least one widget shows.

### Languages

The panel and admin interface, service errors, installer and updater messages
are translated. There is one language for both the panel and the admin panel:
change it under "Settings → Display" (or on the Wi‑Fi screen at first start)
or in the admin panel, and the other side follows. On first installation the
language comes from the system's `$LANG`.

Texts in the code are written in Russian: it is the source language and the
translation key at the same time. In the frontend a string is wrapped in
`t("Текст")` from `web/lib/i18n.js` (placeholders:
`t("Подключено к {ssid}", { ssid })`, plurals: `tn()`), in Python in `tr()` from
`wallhub/i18n.py`, in `install.sh` in `L 'Текст %s' value`. Translations live in
`web/i18n/<language>.json` as `{"Russian text": "translation"}`; modules add
their own in `module.json` (the `i18n` field). An untranslated string is shown
in Russian.

To add a language, list it in `web/i18n/languages.json` (code, name, locale for
dates) and put `<code>.json` next to it. A test checks that the placeholders in
translations match the source.

## Development

Only Python 3.11+ is needed. `--dry-run` mode runs no system commands and
serves a test list of Wi‑Fi networks:

```sh
python3 -m wallhub --dry-run --config var/config.json --admin-port 8088
# prefix with WALLHUB_FAKE_WEATHER=1 for a made-up forecast without network
# kiosk: http://localhost:8080/  (1024×600 window)
# admin: http://localhost:8088/  (the password is set at first sign-in)
python3 -m unittest discover -s tests
```

To try store modules, point `--modules-dir` at a checkout of the store (the
flag can repeat): `--modules-dir ../wallhub-modules/modules`. Files are served
as they are, so reloading the page picks up changes.

Python dependencies, if any, go into `requirements.txt`.
