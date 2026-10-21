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
#   * создаёт системного пользователя wallhub и сервис wallhub.service
#     (бэкенд в /opt/wallhub, venv в /opt/wallhub/venv, настройки в /var/lib/wallhub);
#   * разрешает сервису через polkit управлять Wi‑Fi, питанием и часовым поясом.
#
# Сбросить пароль админки: sudo ./install.sh --reset-password

set -euo pipefail

APP_DIR=/opt/wallhub
STATE_DIR=/var/lib/wallhub
SERVICE_USER=wallhub
KIOSK_USER=kiosk
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PACKAGES=(
    # бэкенд
    python3 python3-venv curl ca-certificates
    # сеть и время
    network-manager polkitd systemd-timesyncd
    # графика: минимальный X11 + openbox + chromium
    xserver-xorg-core xserver-xorg-input-libinput xinit x11-xserver-utils
    openbox chromium
    # шрифты с кириллицей
    fonts-inter fonts-dejavu-core
    # заставка при загрузке
    plymouth
)

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root: sudo $0" >&2
    exit 1
fi

# Сброс пароля админки без переустановки.
if [ "${1:-}" = "--reset-password" ]; then
    systemctl stop wallhub.service
    cd "$APP_DIR"
    PASSWORD="$(runuser -u "$SERVICE_USER" -- "$APP_DIR/venv/bin/python" -m wallhub \
        --config "$STATE_DIR/config.json" --reset-password)"
    systemctl start wallhub.service
    echo "Новый пароль админки: $PASSWORD"
    exit 0
fi

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${VERSION_CODENAME:-}" != "trixie" ]; then
        warn "Ожидается Debian trixie (Armbian 13), а здесь ${PRETTY_NAME:-неизвестно}. Продолжаю."
    fi
fi

# --- пакеты -------------------------------------------------------------------

log "Устанавливаю пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update
TO_INSTALL=()
for pkg in "${PACKAGES[@]}"; do
    # Если время уже синхронизирует chrony, второй демон не нужен.
    if [ "$pkg" = systemd-timesyncd ] && dpkg -s chrony >/dev/null 2>&1; then
        continue
    fi
    TO_INSTALL+=("$pkg")
done
apt-get install -y --no-install-recommends "${TO_INSTALL[@]}"

systemctl enable --now NetworkManager
if ! dpkg -s chrony >/dev/null 2>&1; then
    timedatectl set-ntp true || true
fi
if ! nmcli -t -f TYPE device 2>/dev/null | grep -qx wifi; then
    warn "NetworkManager не видит Wi‑Fi адаптер: управление сетями на экране будет недоступно."
fi

# --- пользователи ---------------------------------------------------------------

log "Создаю пользователей"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$KIOSK_USER"
    passwd -l "$KIOSK_USER" >/dev/null
fi
# Доступ к видео и устройствам ввода для Xorg без root.
usermod -aG video,input,render,tty "$KIOSK_USER" 2>/dev/null || usermod -aG video,input,tty "$KIOSK_USER"

# --- приложение -------------------------------------------------------------------

log "Копирую приложение в $APP_DIR"
install -d -m 0755 "$APP_DIR"
rm -rf "$APP_DIR/wallhub" "$APP_DIR/web" "$APP_DIR/deploy"
cp -r "$SRC_DIR/wallhub" "$SRC_DIR/web" "$SRC_DIR/deploy" "$SRC_DIR/requirements.txt" "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$APP_DIR"
chmod 0755 "$APP_DIR/deploy/kiosk.sh"

log "Готовлю venv"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
if grep -qv '^\s*\(#\|$\)' "$APP_DIR/requirements.txt"; then
    "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
fi

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"

# --- сервис и права ----------------------------------------------------------------

log "Настраиваю сервис и polkit"
install -m 0644 "$SRC_DIR/deploy/wallhub.service" /etc/systemd/system/wallhub.service
install -d /etc/polkit-1/rules.d
install -m 0644 "$SRC_DIR/deploy/50-wallhub.rules" /etc/polkit-1/rules.d/50-wallhub.rules
systemctl restart polkit 2>/dev/null || true

# Пароль админки: генерируется один раз, при обновлении сохраняется.
ADMIN_PASSWORD="$(cd "$APP_DIR" && runuser -u "$SERVICE_USER" -- \
    "$APP_DIR/venv/bin/python" -m wallhub --config "$STATE_DIR/config.json" --init-password)"

systemctl daemon-reload
systemctl enable wallhub.service
systemctl restart wallhub.service

# --- киоск ----------------------------------------------------------------------------

log "Настраиваю киоск"
install -d /etc/chromium/policies/managed
install -m 0644 "$SRC_DIR/deploy/chromium-policy.json" /etc/chromium/policies/managed/wallhub.json

KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KIOSK_HOME/.config/openbox"
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
        warn "Отключил чужой автологин: $f"
    fi
done
systemctl daemon-reload
systemctl set-default multi-user.target >/dev/null

# --- тихая загрузка ----------------------------------------------------------------

log "Настраиваю заставку загрузки"
install -d /usr/share/plymouth/themes/wallhub
install -m 0644 "$SRC_DIR"/deploy/plymouth/wallhub/* /usr/share/plymouth/themes/wallhub/
install -d /etc/systemd/system/plymouth-quit.service.d
install -m 0644 "$SRC_DIR/deploy/plymouth-quit.conf" /etc/systemd/system/plymouth-quit.service.d/wallhub.conf

# Параметры ядра: заставка вместо текста, без логов и мигающего курсора.
QUIET_ARGS="quiet splash loglevel=0 vt.global_cursor_default=0 systemd.show_status=false rd.udev.log_level=3 plymouth.ignore-serial-consoles consoleblank=0"
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
    warn "Не найден $ARMBIAN_ENV: добавьте в параметры ядра вручную: $QUIET_ARGS"
fi
# -R пересобирает initramfs, чтобы заставка появлялась как можно раньше.
plymouth-set-default-theme -R wallhub

log "Перезапускаю сеанс киоска на tty1"
# На части образов Armbian getty@tty1 выключен и стартует только при
# переключении на tty1, тогда киоск после загрузки не появляется.
systemctl enable getty@tty1.service
systemctl restart getty@tty1.service
chvt 1 || true

# --- итог ------------------------------------------------------------------------------

IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1)}')"
echo
log "Готово"
echo "  Экран:   http://localhost:8080/ (откроется на tty1)"
echo "  Админка: http://${IP:-<адрес панели>}:8088/"
if [ -n "$ADMIN_PASSWORD" ]; then
    echo "  Пароль админки: $ADMIN_PASSWORD"
    echo "  (он же виден на экране в Настройки → Система, пока его не сменят)"
else
    echo "  Пароль админки не менялся. Сбросить: sudo $SRC_DIR/install.sh --reset-password"
fi
