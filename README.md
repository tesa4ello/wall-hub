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

### Updates

The panel updates from GitHub without reinstalling the system. There are two
channels:

- **Stable**: branch `main`, the default;
- **Development**: branch `dev`. New changes land here first and reach stable
  when `dev` is merged into `main`.

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
4. The screen reloads the page by itself when it sees a new service version.
   The graphical session restarts only if the kiosk files changed.

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

- interface addresses, gateway and DNS;
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
  `providers`) and translations (`i18n`);
- `module.js`: exports `widgets = {id: mount}`, where
  `mount(el, {options, api})` returns `{destroy()}`;
- `module.css`: styles. Prefer `cqw`/`cqh` units: a widget is a container, so
  its content fits any size.

A widget type is `module/widget` (for example, `clock/clock`). One type can be
placed any number of times, each instance with its own settings. The layout is
stored in `desktops` in `config.json` and checked on the server
(`wallhub/desktops.py`): sizes, bounds, overlaps, settings types.

### On-screen controls

There is no gear icon; the interface is touch only:

- **swipe down from the top edge** (or tap the very edge) to open the shade:
  Wi‑Fi, theme, "Settings" and the new version notice;
- **long press (2 s)** anywhere on a desktop opens the settings;
- **a red dot at the top edge** means a new version is out.

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
# kiosk: http://localhost:8080/  (1024×600 window)
# admin: http://localhost:8088/  (the password is set at first sign-in)
python3 -m unittest discover -s tests
```

Python dependencies, if any, go into `requirements.txt`.
