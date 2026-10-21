#!/bin/sh
# Запускается из автозапуска openbox под пользователем киоска.
# Держит Chromium открытым на интерфейсе панели и перезапускает его при падении.

URL="http://localhost:8080/"
PROFILE="$HOME/.config/chromium"

# Хранитель экрана X не нужен: затемнением управляет интерфейс,
# а гашением подсветки (DPMS) — display-sync.py по настройкам панели.
xset s off
xset s noblank
xset -dpms
xsetroot -solid black

# Ждём, пока поднимется бэкенд, чтобы не показывать страницу ошибки.
i=0
while ! curl -fs -o /dev/null "${URL}api/session" && [ $i -lt 60 ]; do
    sleep 1
    i=$((i + 1))
done

python3 /opt/wallhub/deploy/display-sync.py &

while true; do
    # После выключения питания Chromium считает, что упал, и предлагает
    # восстановить вкладки. Помечаем прошлый запуск как штатный.
    PREFS="$PROFILE/Default/Preferences"
    if [ -f "$PREFS" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
    fi

    chromium \
        --kiosk "$URL" \
        --user-data-dir="$PROFILE" \
        --ozone-platform=x11 \
        --start-fullscreen \
        --window-position=0,0 \
        --noerrdialogs \
        --disable-infobars \
        --no-first-run \
        --no-default-browser-check \
        --disable-session-crashed-bubble \
        --disable-restore-session-state \
        --disable-features=Translate,TranslateUI,MediaRouter,GlobalMediaControls,HardwareMediaKeyHandling,OptimizationHints,PasswordManagerOnboarding,AutofillServerCommunication,InterestFeedContentSuggestions \
        --disable-component-update \
        --disable-background-networking \
        --disable-sync \
        --check-for-update-interval=31536000 \
        --password-store=basic \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        --touch-events=enabled \
        --hide-scrollbars \
        --default-background-color=ff0c0e11 \
        --autoplay-policy=no-user-gesture-required \
        >/dev/null 2>&1
    sleep 2
done
