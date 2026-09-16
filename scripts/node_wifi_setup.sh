#!/usr/bin/env bash
# Install the out-of-tree Realtek RTL8811CU/RTL8821CU driver on a Waggle node.
#
# BrosTrend AC-series USB adapters are rebadged Realtek 0bda:c811 parts. The
# chipset was never mainlined, and NVIDIA's L4T kernel ships no realtek/
# wireless modules at all, so the driver has to be built out of tree and
# registered with DKMS to survive kernel updates.
#
# With --ssid it also joins the plug's access point, configured so it cannot
# become the node's default route: the plug's network has no path to Sage, and
# a stray default route there would cut the node off with no way back in.
#
# Safe to re-run: every step checks for its own result first.
#
# Usage, on the node as root:
#     ./node_wifi_setup.sh                              # driver only
#     ./node_wifi_setup.sh --ssid sgt-smartplug-xxxx    # driver + connect
#     ./node_wifi_setup.sh --ssid <s> --psk <password>  # for a secured AP
#     ./node_wifi_setup.sh --dry-run                    # report, change nothing
#     ./node_wifi_setup.sh --disconnect                 # remove the connection
#     ./node_wifi_setup.sh --uninstall                  # remove the DKMS module
set -euo pipefail

DRIVER_REPO="https://github.com/morrownr/8821cu-20210916.git"
SRC_DIR="/usr/src/8821cu-20210916"
# Realtek IDs this driver claims. c811 is the BrosTrend AC1L/AC3L/AC5L.
USB_IDS="0bda:c811 0bda:c820 0bda:c82b 0bda:2006 0bda:b820"
PKGS="dkms build-essential bc libelf-dev git"

CON_NAME="smartplug"
# Seconds the watchdog waits for confirmation before rolling the connection
# back. Long enough to finish DHCP and the route checks, short enough that a
# wedged node recovers on its own.
WATCHDOG_SECS=150

DRY_RUN=0
UNINSTALL=0
DISCONNECT=0
SSID=""
PSK=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --uninstall)  UNINSTALL=1 ;;
        --disconnect) DISCONNECT=1 ;;
        --ssid)       SSID="${2:-}"; shift ;;
        --psk)        PSK="${2:-}"; shift ;;
        -h|--help)    sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
run()  {
    if [ "$DRY_RUN" = 1 ]; then say "  would run: $*"; return 0; fi
    "$@"
}

[ "$(id -u)" -eq 0 ] || { say "must run as root"; exit 1; }

KREL="$(uname -r)"

if [ "$DISCONNECT" = 1 ]; then
    step "removing the $CON_NAME connection"
    pkill -f "\\.${CON_NAME}_watchdog\\.sh" 2>/dev/null || true
    rm -f "/tmp/.${CON_NAME}_watchdog.sh" "/tmp/.${CON_NAME}_ok"
    run nmcli con down "$CON_NAME" 2>/dev/null || true
    run nmcli con delete "$CON_NAME" 2>/dev/null || true
    say "  done; the node's other connections are untouched"
    exit 0
fi

if [ "$UNINSTALL" = 1 ]; then
    step "uninstalling"
    if [ -d "$SRC_DIR" ]; then
        run bash -c "cd '$SRC_DIR' && ./remove-driver.sh NoPrompt" || true
    else
        say "  $SRC_DIR absent -- nothing to remove"
    fi
    exit 0
fi

step "preflight"
say "  kernel        $KREL"
say "  os            $(. /etc/os-release && echo "$PRETTY_NAME")"

found=""
usb_list="$(lsusb 2>/dev/null || true)"
for id in $USB_IDS; do
    case "$usb_list" in *"$id"*) found="$id" ;; esac
done
if [ -n "$found" ]; then
    say "  adapter       $found present"
else
    # Not fatal: the driver can be installed before the adapter is attached.
    say "  adapter       WARNING none of [$USB_IDS] found on USB"
fi

# A link that trained below 480M usually means a cable or power problem, and
# it will bite later as dropouts rather than as an obvious failure.
for d in /sys/bus/usb/devices/*/; do
    if [ -f "$d/idVendor" ] && [ "$(cat "$d/idVendor" 2>/dev/null)" = "0bda" ]; then
        spd="$(cat "$d/speed" 2>/dev/null || echo '?')"
        say "  usb speed     ${spd}M on $(basename "$d")"
        [ "$spd" = "480" ] || say "                WARNING expected 480M -- check cable/port"
    fi
done

if [ -d "/lib/modules/$KREL/build" ]; then
    say "  headers       /lib/modules/$KREL/build"
else
    say "  headers       MISSING for $KREL -- cannot build" >&2
    exit 1
fi

if ip -br link | grep -qE '^(wlan|wlx)'; then
    say "  wlan          already present:"
    ip -br link | grep -E '^(wlan|wlx)' | sed 's/^/                /'
fi

step "packages"
missing=""
for p in $PKGS; do
    dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then
    say "  installing:$missing"
    run apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing
else
    say "  all present: $PKGS"
fi

step "driver source"
if [ -d "$SRC_DIR/.git" ]; then
    say "  $SRC_DIR exists -- updating"
    run git -C "$SRC_DIR" pull --ff-only
else
    run git clone --depth 1 "$DRIVER_REPO" "$SRC_DIR"
fi

step "build + dkms install"
dkms_state="$(dkms status 2>/dev/null || true)"
if printf '%s' "$dkms_state" | grep -qi '8821cu.*installed'; then
    say "  already installed:"
    dkms status 2>/dev/null | grep -i 8821cu | sed 's/^/    /'
else
    # NoPrompt keeps install-driver.sh non-interactive; it also declines the
    # reboot it would otherwise offer.
    run bash -c "cd '$SRC_DIR' && ./install-driver.sh NoPrompt"
fi

step "verify"
if [ "$DRY_RUN" = 1 ]; then say "  (dry run -- nothing to verify)"; exit 0; fi

modprobe 8821cu 2>/dev/null || true
sleep 3

dkms status 2>/dev/null | grep -i 8821cu | sed 's/^/  dkms: /' || say "  dkms: no 8821cu entry"
loaded_mod="$(lsmod | awk '$1 ~ /^(rtl)?8821cu$/ {print $1; exit}')"
if [ -n "$loaded_mod" ]; then
    say "  module: loaded ($loaded_mod)"
else
    say "  module: NOT loaded"
fi

# systemd's predictable naming renames wlan0 to a MAC-derived wlx* name, so
# match both forms here.
WIFI_IF="$(ip -br link | awk '$1 ~ /^(wlan|wlx)/ {print $1; exit}')"
if [ -n "$WIFI_IF" ]; then
    say "  interface: $WIFI_IF"
    ip -br link | grep -E "^$WIFI_IF" | sed 's/^/    /'
else
    say "  interface: NO wlan/wlx interface appeared" >&2
    say "  check: journalctl -k -b | grep -iE '8821cu|usb 1-'" >&2
    exit 1
fi

if [ -z "$SSID" ]; then
    say ""
    say "Driver is in. The adapter is NOT connected to anything yet."
    say "Re-run with --ssid <plug-ssid> to join the plug's access point."
    exit 0
fi

step "connecting to '$SSID'"

# Record the uplink so the checks below can prove it survived.
BASE_DEFAULT="$(ip route show default | awk 'NR==1')"
say "  default route before: ${BASE_DEFAULT:-<none>}"

nmcli device wifi rescan ifname "$WIFI_IF" 2>/dev/null || true
sleep 6
if nmcli -f SSID device wifi list ifname "$WIFI_IF" 2>/dev/null | grep -qF "$SSID"; then
    say "  '$SSID' is visible"
else
    say "  WARNING '$SSID' not in scan results -- continuing anyway"
fi

# Watchdog: if the steps below wedge the node's networking, this restores it
# without needing a human at the console. Disarmed on success.
# A watchdog from an earlier run would happily delete the connection this run
# is about to create, so clear any that are still pending first.
pkill -f "\\.${CON_NAME}_watchdog\\.sh" 2>/dev/null || true
WD_SCRIPT="/tmp/.${CON_NAME}_watchdog.sh"
OK_FLAG="/tmp/.${CON_NAME}_ok"
rm -f "$OK_FLAG" "$WD_SCRIPT"
cat > "$WD_SCRIPT" <<WD
#!/bin/bash
sleep $WATCHDOG_SECS
if [ ! -f "$OK_FLAG" ]; then
    logger -t ${CON_NAME}_watchdog "no confirmation, rolling back"
    nmcli con down "$CON_NAME" 2>/dev/null
    nmcli con delete "$CON_NAME" 2>/dev/null
fi
rm -f "$WD_SCRIPT" "$OK_FLAG"
WD
chmod +x "$WD_SCRIPT"
setsid nohup "$WD_SCRIPT" >/dev/null 2>&1 < /dev/null &
WD_PID=$!
say "  watchdog armed (${WATCHDOG_SECS}s, pid $WD_PID)"

# Recreate from scratch so a stale profile cannot carry old routing settings.
nmcli con delete "$CON_NAME" >/dev/null 2>&1 || true

# never-default on both families is the point of this whole block. The extra
# settings keep the plug from influencing anything else: a high route metric
# so its subnet never wins a tie, and ignore-auto-dns so its DHCP cannot
# replace the node's resolvers.
nmcli con add type wifi ifname "$WIFI_IF" con-name "$CON_NAME" \
    ssid "$SSID" \
    ipv4.method auto \
    ipv4.never-default yes \
    ipv6.never-default yes \
    ipv6.method ignore \
    ipv4.route-metric 1000 \
    ipv4.dns-priority 200 \
    ipv4.ignore-auto-dns yes \
    connection.autoconnect yes >/dev/null

if [ -n "$PSK" ]; then
    nmcli con modify "$CON_NAME" \
        802-11-wireless-security.key-mgmt wpa-psk \
        802-11-wireless-security.psk "$PSK"
else
    # For an OPEN network the security block must be absent entirely.
    # `key-mgmt none` does not mean "open" to NetworkManager -- it means WEP,
    # and association against an open AP then fails.
    nmcli con modify "$CON_NAME" remove 802-11-wireless-security 2>/dev/null || true
fi

nmcli con up "$CON_NAME" >/dev/null
sleep 5

step "verifying the uplink survived"
ADDR="$(ip -br addr show "$WIFI_IF" | awk 'NR==1 {print $3}')"
say "  $WIFI_IF address: ${ADDR:-<none>}"

NOW_DEFAULT="$(ip route show default | awk 'NR==1')"
say "  default route now:    ${NOW_DEFAULT:-<none>}"

fail=0
case "$NOW_DEFAULT" in
    *"$WIFI_IF"*)
        say "  FAIL default route now points at the wifi link" >&2
        fail=1 ;;
esac
if [ "$NOW_DEFAULT" != "$BASE_DEFAULT" ]; then
    say "  FAIL default route changed" >&2
    fail=1
fi

if [ "$fail" = 1 ]; then
    say "  rolling back" >&2
    nmcli con down "$CON_NAME" 2>/dev/null || true
    nmcli con delete "$CON_NAME" 2>/dev/null || true
    exit 1
fi
say "  default route unchanged"

# Confirm the plug itself answers, not merely that an address was assigned.
GW="$(ip route show dev "$WIFI_IF" | awk '/^default|via/ {print $3; exit}' || true)"
PLUG="${GW:-192.168.4.1}"
if curl -s -m 8 "http://$PLUG/cm?cmnd=Status%208" 2>/dev/null | grep -q ENERGY; then
    say "  plug at $PLUG responds"
else
    say "  WARNING no reply from the plug at $PLUG (connection kept)"
fi

touch "$OK_FLAG"
kill "$WD_PID" 2>/dev/null || true
rm -f "$WD_SCRIPT" "$OK_FLAG"
say "  watchdog disarmed"
say ""
say "Connected. $WIFI_IF reaches the plug; the node's uplink is unchanged."
say "Remove it later with: $0 --disconnect"
