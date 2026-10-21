#!/bin/bash
# Установка Wall-HUB на чистый консольный Armbian 13 (Debian trixie).
#
#   git clone https://github.com/tesa4ello/wall-hub && cd wall-hub && sudo ./install.sh
#
# Повторный запуск обновляет установленную версию и не трогает настройки.
#
# Что делает:
#   * ставит минимальную графику: Xorg + openbox + chromium;
#   * создаёт пользователя kiosk с автологином на tty1, который запускает
#     chromium в режиме киоска на http://localhost:8080/;
#   * создаёт системного пользователя wallhub и сервис wallhub.service;
#   * раскладывает код по версиям: /opt/wallhub/releases/<коммит> (у каждой
#     свой venv), рабочая версия — симлинк /opt/wallhub/current, прошлая —
#     /opt/wallhub/previous; настройки в /var/lib/wallhub;
#   * ставит автообновление (deploy/wallhub-update.py и ночной таймер);
#   * разрешает сервису через polkit управлять Wi‑Fi, питанием, часовым поясом
#     и запускать проверку/установку обновлений.
#
# Сбросить пароль админки: sudo ./install.sh --reset-password
#
# Обновлятор запускает install.sh скачанной версии с --updater: тогда пакеты
# ставятся, только если чего-то не хватает, а сеанс киоска перезапускается,
# только если поменялись его файлы (страницу экран перезагрузит сам).

set -euo pipefail

APP_DIR=/opt/wallhub
RELEASES_DIR="$APP_DIR/releases"
CURRENT="$APP_DIR/current"
STATE_DIR=/var/lib/wallhub
SERVICE_USER=wallhub
KIOSK_USER=kiosk
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PACKAGES=(
    # бэкенд
    python3 python3-venv python3-yaml curl ca-certificates
    # сеть и время
    network-manager polkitd systemd-timesyncd
    # графика: минимальный X11 + openbox + chromium
    xserver-xorg-core xserver-xorg-input-libinput xinit x11-xserver-utils
    openbox chromium
    # шрифты с кириллицей
    fonts-inter fonts-dejavu-core
    # заставка при загрузке
    plymouth
    # трансляция экрана в админку
    x11vnc
    # для модулей: ssh (мониторинг серверов) и цветные эмодзи (флаги стран)
    openssh-client fonts-noto-color-emoji
)

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# Сообщения переводятся по тем же каталогам, что и интерфейс
# (web/i18n/<язык>.json, ключ — русский текст; подстановки здесь — %s).
# Язык берётся из настроек панели, а при первой установке — из $LANG.
declare -A TR=()
detect_lang() {
    local lang=""
    if [ -r "$STATE_DIR/config.json" ] && command -v python3 >/dev/null; then
        lang="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("ui", {}).get("language", ""))' \
            "$STATE_DIR/config.json" 2>/dev/null || true)"
        [ -n "$lang" ] || lang=ru
    fi
    if [ -z "$lang" ]; then
        case "${LANG:-}" in ru*) lang=ru ;; *) lang=en ;; esac
    fi
    echo "$lang"
}
load_tr() {
    TR=()
    local catalog="$SRC_DIR/web/i18n/$UI_LANG.json" k v
    [ -r "$catalog" ] && command -v python3 >/dev/null || return 0
    while IFS= read -r -d '' k && IFS= read -r -d '' v; do
        TR["$k"]="$v"
    done < <(python3 -c 'import json, sys
for k, v in json.load(open(sys.argv[1], encoding="utf-8")).items():
    sys.stdout.write(k + "\0" + v + "\0")' "$catalog")
}
# L "Текст с %s" значение — перевод с подстановкой.
L() {
    local text="${TR[$1]:-$1}"
    shift
    # shellcheck disable=SC2059
    printf "$text" "$@"
}
UI_LANG="$(detect_lang)"
load_tr

if [ "$(id -u)" -ne 0 ]; then
    echo "$(L 'Запустите от root: sudo %s' "$0")" >&2
    exit 1
fi

UPDATER=0
case "${1:-}" in
    "") ;;
    --updater) UPDATER=1 ;;
    --reset-password)
        # Сброс пароля админки без переустановки.
        systemctl stop wallhub.service
        cd "$CURRENT"
        runuser -u "$SERVICE_USER" -- "$CURRENT/venv/bin/python" -m wallhub \
            --config "$STATE_DIR/config.json" --reset-password
        systemctl start wallhub.service
        echo "$(L 'Пароль админки сброшен. Новый задаётся при следующем входе в админку.')"
        exit 0
        ;;
    *)
        echo "$(L 'Использование: %s [--reset-password]' "$0")" >&2
        exit 2
        ;;
esac

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${VERSION_CODENAME:-}" != "trixie" ]; then
        warn "$(L 'Ожидается Debian trixie (Armbian 13), а здесь %s. Продолжаю.' "${PRETTY_NAME:-?}")"
    fi
fi

# --- пакеты -------------------------------------------------------------------

log "$(L 'Проверяю пакеты')"
export DEBIAN_FRONTEND=noninteractive
MISSING=()
for pkg in "${PACKAGES[@]}"; do
    # Если время уже синхронизирует chrony, второй демон не нужен.
    if [ "$pkg" = systemd-timesyncd ] && dpkg -s chrony >/dev/null 2>&1; then
        continue
    fi
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        MISSING+=("$pkg")
    fi
done
# Индекс apt обновляем, только когда есть что ставить: ночное
# автообновление не должно каждый раз ходить в репозитории Debian.
if [ ${#MISSING[@]} -gt 0 ]; then
    log "$(L 'Устанавливаю: %s' "${MISSING[*]}")"
    apt-get update
    apt-get install -y --no-install-recommends "${MISSING[@]}"
    # python3 мог появиться только сейчас: перечитываем переводы.
    load_tr
fi

systemctl enable --now NetworkManager
if ! dpkg -s chrony >/dev/null 2>&1; then
    timedatectl set-ntp true || true
fi
# Armbian поднимает Wi‑Fi через netplan + networkd, и NetworkManager его не
# трогает. Отдаём Wi‑Fi адаптер (только его) NetworkManager-у; сеть потом
# выбирается на самой панели при первом запуске.
if [ -d /etc/netplan ] && command -v netplan >/dev/null; then
    log "$(L 'Передаю Wi‑Fi под управление NetworkManager')"
    python3 "$SRC_DIR/deploy/netplan-release-wifi.py"
    sleep 3
fi
if nmcli -t -f TYPE,STATE device 2>/dev/null | grep -q '^wifi:unmanaged'; then
    warn "$(L 'Wi‑Fi адаптер всё ещё не под NetworkManager (unmanaged): проверьте /etc/netplan.')"
fi
if ! nmcli -t -f TYPE device 2>/dev/null | grep -qx wifi; then
    warn "$(L 'NetworkManager не видит Wi‑Fi адаптер: управление сетями на экране будет недоступно.')"
fi

# --- пользователи ---------------------------------------------------------------

log "$(L 'Создаю пользователей')"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$KIOSK_USER"
    passwd -l "$KIOSK_USER" >/dev/null
fi
# Доступ к видео, звуку и устройствам ввода для Xorg и Chromium без root.
usermod -aG video,input,render,tty,audio "$KIOSK_USER" 2>/dev/null || usermod -aG video,input,tty,audio "$KIOSK_USER"

# --- приложение -------------------------------------------------------------------

# Клон обычно принадлежит обычному пользователю, а скрипт идёт от root:
# без safe.directory git отказывается с ним работать.
src_git() { git -c safe.directory='*' -C "$SRC_DIR" "$@"; }

# Версия: из файла VERSION (его пишет обновлятор) или из git-клона.
if [ -f "$SRC_DIR/VERSION" ]; then
    COMMIT="$(sed -n 's/^commit=//p' "$SRC_DIR/VERSION")"
elif src_git rev-parse HEAD >/dev/null 2>&1; then
    COMMIT="$(src_git rev-parse HEAD)"
else
    COMMIT="local-$(date +%Y%m%d%H%M%S)"
fi
RELEASE_ID="${COMMIT:0:12}"
RELEASE="$RELEASES_DIR/$RELEASE_ID"

# Файлы, от которых зависит сеанс киоска: если они не менялись, обновлятору
# не нужно перезапускать X и Chromium.
kiosk_files_hash() {
    # При первой установке current ещё нет: cat падает, а с pipefail это
    # прервало бы весь скрипт, поэтому ошибку cat гасим.
    { cat "$1"/deploy/{kiosk.sh,display-sync.py,xinitrc,bash_profile,openbox-rc.xml,chromium-policy.json,autologin.conf} 2>/dev/null || true; } | sha256sum
}
OLD_KIOSK_HASH="$(kiosk_files_hash "$CURRENT")"
PREVIOUS="$(readlink "$CURRENT" 2>/dev/null || true)"

log "$(L 'Раскладываю версию %s' "$RELEASE_ID")"
install -d -m 0755 "$APP_DIR" "$RELEASES_DIR"
if [ "$(realpath "$SRC_DIR")" != "$(realpath -m "$RELEASE")" ]; then
    rm -rf "$RELEASE"
    install -d -m 0755 "$RELEASE"
    cp -r "$SRC_DIR/wallhub" "$SRC_DIR/web" "$SRC_DIR/deploy" "$SRC_DIR/requirements.txt" "$SRC_DIR/install.sh" "$RELEASE/"
fi
if [ ! -f "$RELEASE/VERSION" ]; then
    {
        echo "commit=$COMMIT"
        echo "ref=$(src_git rev-parse --abbrev-ref HEAD 2>/dev/null || echo local)"
        echo "date=$(src_git log -1 --format=%cI 2>/dev/null || date -Iseconds)"
    } > "$RELEASE/VERSION"
fi
find "$RELEASE" -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$RELEASE"
chmod 0755 "$RELEASE/deploy/kiosk.sh" "$RELEASE/install.sh"

if [ ! -x "$RELEASE/venv/bin/python" ]; then
    python3 -m venv "$RELEASE/venv"
fi
if grep -qv '^\s*\(#\|$\)' "$RELEASE/requirements.txt"; then
    "$RELEASE/venv/bin/pip" install --quiet -r "$RELEASE/requirements.txt"
fi

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
# Первая установка: язык панели — как у системы (дальше меняется в настройках).
if [ ! -e "$STATE_DIR/config.json" ]; then
    printf '{"ui": {"language": "%s"}}\n' "$UI_LANG" > "$STATE_DIR/config.json"
    chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR/config.json"
    chmod 0600 "$STATE_DIR/config.json"
fi

# Токен для приватного репозитория: если клон сделан по ссылке с токеном,
# сохраняем его для обновлятора (один раз, потом меняется в админке).
TOKEN_FILE="$STATE_DIR/github-token"
REMOTE_URL="$(src_git remote get-url origin 2>/dev/null || true)"
if [ ! -s "$TOKEN_FILE" ] && [[ "$REMOTE_URL" =~ ^https://[^:/@]+:([^@]+)@github\.com/ ]]; then
    install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_USER" /dev/null "$TOKEN_FILE"
    printf '%s' "${BASH_REMATCH[1]}" > "$TOKEN_FILE"
    log "$(L 'Токен GitHub для обновлений взят из git remote')"
fi

# --- сервис и права ----------------------------------------------------------------

log "$(L 'Переключаю рабочую версию')"
# Симлинк меняется атомарно: mv -T поверх старого.
ln -sfn "releases/$RELEASE_ID" "$APP_DIR/current.new"
mv -T "$APP_DIR/current.new" "$CURRENT"
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "releases/$RELEASE_ID" ]; then
    ln -sfn "$PREVIOUS" "$APP_DIR/previous.new"
    mv -T "$APP_DIR/previous.new" "$APP_DIR/previous"
fi
# Раскладка до версий: код лежал прямо в /opt/wallhub.
rm -rf "$APP_DIR/wallhub" "$APP_DIR/web" "$APP_DIR/deploy" "$APP_DIR/venv" "$APP_DIR/requirements.txt"
# Храним рабочую, прошлую и ещё пару последних версий.
keep="$(basename "$(readlink -f "$CURRENT")") $(basename "$(readlink -f "$APP_DIR/previous" 2>/dev/null || echo none)")"
for old in $(ls -1t "$RELEASES_DIR" | tail -n +4); do
    case " $keep " in *" $old "*) ;; *) rm -rf "${RELEASES_DIR:?}/$old" ;; esac
done

log "$(L 'Настраиваю сервисы и polkit')"
install -m 0644 "$SRC_DIR/deploy/wallhub.service" /etc/systemd/system/wallhub.service
install -m 0644 "$SRC_DIR/deploy/wallhub-update@.service" /etc/systemd/system/wallhub-update@.service
install -m 0644 "$SRC_DIR/deploy/wallhub-update-auto.timer" /etc/systemd/system/wallhub-update-auto.timer
install -m 0644 "$SRC_DIR/deploy/wallhub-vnc.service" /etc/systemd/system/wallhub-vnc.service
install -d /etc/polkit-1/rules.d
install -m 0644 "$SRC_DIR/deploy/50-wallhub.rules" /etc/polkit-1/rules.d/50-wallhub.rules
systemctl restart polkit 2>/dev/null || true

systemctl daemon-reload
systemctl enable wallhub.service
systemctl enable wallhub-update-auto.timer
# restart, а не start: так подхватывается новое расписание таймера.
systemctl restart wallhub-update-auto.timer
systemctl restart wallhub.service

# --- киоск ----------------------------------------------------------------------------

log "$(L 'Настраиваю киоск')"
install -d /etc/chromium/policies/managed
install -m 0644 "$SRC_DIR/deploy/chromium-policy.json" /etc/chromium/policies/managed/wallhub.json

KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
# install -d отдаёт пользователю только последний каталог, поэтому ~/.config
# создаём отдельно: иначе он остаётся root-овским и Chromium не может
# создать профиль (падает с "chrome_crashpad_handler: --database is required").
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KIOSK_HOME/.config" "$KIOSK_HOME/.config/openbox"
chown -R "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config"
# Старый автозапуск openbox (до перехода на запуск из .xinitrc).
rm -f "$KIOSK_HOME/.config/openbox/autostart"
install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$SRC_DIR/deploy/openbox-rc.xml" "$KIOSK_HOME/.config/openbox/rc.xml"
install -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "$SRC_DIR/deploy/xinitrc" "$KIOSK_HOME/.xinitrc"
install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$SRC_DIR/deploy/bash_profile" "$KIOSK_HOME/.bash_profile"
# Без приветствий в консоли перед стартом X.
touch "$KIOSK_HOME/.hushlogin"
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.hushlogin"

install -d /etc/systemd/system/getty@tty1.service.d
sed "s/--autologin kiosk/--autologin $KIOSK_USER/" "$SRC_DIR/deploy/autologin.conf" \
    > /etc/systemd/system/getty@tty1.service.d/autologin.conf
# У Armbian бывает собственный автологин на tty1, он бы перебил наш.
for f in /etc/systemd/system/getty@.service.d/override.conf /etc/systemd/system/getty@tty1.service.d/override.conf; do
    if [ -f "$f" ] && grep -q -- '--autologin' "$f"; then
        mv "$f" "$f.disabled-by-wallhub"
        warn "$(L 'Отключил чужой автологин: %s' "$f")"
    fi
done
systemctl daemon-reload
systemctl set-default multi-user.target >/dev/null

# --- тихая загрузка ----------------------------------------------------------------

log "$(L 'Настраиваю заставку загрузки')"
THEME_DIR=/usr/share/plymouth/themes/wallhub
THEME_CHANGED=0
if ! diff -rq "$SRC_DIR/deploy/plymouth/wallhub" "$THEME_DIR" >/dev/null 2>&1; then
    THEME_CHANGED=1
    install -d "$THEME_DIR"
    install -m 0644 "$SRC_DIR"/deploy/plymouth/wallhub/* "$THEME_DIR/"
fi
install -d /etc/systemd/system/plymouth-quit.service.d
install -m 0644 "$SRC_DIR/deploy/plymouth-quit.conf" /etc/systemd/system/plymouth-quit.service.d/wallhub.conf

# Параметры ядра: заставка вместо текста, без логов и мигающего курсора.
# fbcon=map:9 привязывает текстовую консоль к несуществующему fb9, то есть
# она вообще не рисуется на экране. Иначе между заставкой и киоском (и при
# выключении, когда X уже закрылся) на пару секунд проступает tty1.
# Plymouth и X работают через DRM напрямую, им консоль не нужна.
# Обслуживание: по SSH или через последовательный порт.
QUIET_ARGS="quiet splash loglevel=0 vt.global_cursor_default=0 systemd.show_status=false rd.udev.log_level=3 plymouth.ignore-serial-consoles consoleblank=0 fbcon=map:9"
ARMBIAN_ENV=/boot/armbianEnv.txt
set_env() {
    if grep -q "^$1=" "$ARMBIAN_ENV"; then
        sed -i "s|^$1=.*|$1=$2|" "$ARMBIAN_ENV"
    else
        echo "$1=$2" >> "$ARMBIAN_ENV"
    fi
}
if [ -f "$ARMBIAN_ENV" ]; then
    cp -n "$ARMBIAN_ENV" "$ARMBIAN_ENV.before-wallhub"
    set_env bootlogo true
    set_env verbosity 0
    # Сообщения ядра только в последовательный порт, на экране их не видно.
    set_env console serial
    extra="$(sed -n 's/^extraargs=//p' "$ARMBIAN_ENV")"
    for arg in $QUIET_ARGS; do
        case " $extra " in
            *" $arg "*) ;;
            *) extra="${extra:+$extra }$arg" ;;
        esac
    done
    set_env extraargs "$extra"
else
    warn "$(L 'Не найден %s: добавьте в параметры ядра вручную: %s' "$ARMBIAN_ENV" "$QUIET_ARGS")"
fi
# -R пересобирает initramfs, чтобы заставка появлялась как можно раньше.
# Это долго, поэтому только когда тема поменялась.
if [ "$THEME_CHANGED" = 1 ] || [ "$(plymouth-set-default-theme)" != wallhub ]; then
    plymouth-set-default-theme -R wallhub
fi

# На части образов Armbian getty@tty1 выключен и стартует только при
# переключении на tty1, тогда киоск после загрузки не появляется.
systemctl enable getty@tty1.service
if [ "$UPDATER" = 0 ] || [ "$OLD_KIOSK_HASH" != "$(kiosk_files_hash "$CURRENT")" ]; then
    log "$(L 'Перезапускаю сеанс киоска на tty1')"
    systemctl restart getty@tty1.service
    [ "$UPDATER" = 1 ] || chvt 1 || true
fi

# --- итог ------------------------------------------------------------------------------

if [ "$UPDATER" = 1 ]; then
    log "$(L 'Версия %s установлена' "$RELEASE_ID")"
    exit 0
fi

IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1)}')"
echo
log "$(L 'Готово')"
echo "  $(L 'Экран:   http://localhost:8080/ (откроется на tty1)')"
echo "  $(L 'Админка: %s' "http://${IP:-<IP>}/")"
echo "  $(L 'Пароль админки задаётся при первом входе. Сбросить: %s' "sudo $CURRENT/install.sh --reset-password")"
