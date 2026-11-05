#!/bin/bash
# Install, update or remove the touch e-paper status display. Runs on the Pi.
#
#   sudo ./deploy.sh install    install from this folder (a git clone) and start the display
#   sudo ./deploy.sh update     ask GitHub for its newest commit; if it is newer than the
#                               installed one, install it and restart the display (only the
#                               display's service, not the Pi)
#   sudo ./deploy.sh remove     stop the display and delete it (same: uninstall, purge)
#
# The program goes to /opt/rpi_mesh_epaper_stats and runs as epaper-stats.service.
# /opt/rpi_mesh_epaper_stats/version.txt holds the git commit it was installed from
# ("-dirty" added when the clone had uncommitted changes). The version before the last
# install is kept in /opt/rpi_mesh_epaper_stats.prev; if a new version does not keep
# running, deploy.sh puts that one back by itself.

set -euo pipefail

# The variables below can be changed from the environment to test this script without
# touching the system (sudo resets the environment, so they are normally the defaults).
REPO_URL=${REPO_URL:-https://github.com/F1shcake-onegai/rpi_mesh_epaper_stats.git}
DEST=${DEST:-/opt/rpi_mesh_epaper_stats}
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
BUS_DEVICES=${BUS_DEVICES:-/dev/spidev0.0 /dev/i2c-1}   # there once SPI and I2C are on
HEALTH_WAIT=${HEALTH_WAIT:-10}  # s the display must keep running after a restart

SERVICE=epaper-stats
PREV="$DEST.prev"
APT_PACKAGES="python3-gpiozero python3-lgpio python3-spidev python3-smbus python3-numpy
              python3-pil python3-qrcode fonts-dejavu-core rsync git"
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)    # the folder this script is in

export GIT_TERMINAL_PROMPT=0    # never wait for a GitHub password nobody can type
UPDATE_TMP=""                   # download folder of "update", deleted on exit
REBOOT_NEEDED=""                # SPI or I2C was just turned on
BACKED_UP=""                    # this run saved the installed version to $PREV

say() { printf '%s\n' "$*"; }
die() { printf 'deploy.sh: %s\n' "$*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "needs root: sudo $0 $1"; }

short() {   # 4d29278 for a full hash; keeps "-dirty"; "unknown" stays as it is
    local v=$1 base=${1%-dirty}
    printf '%s' "${base:0:7}"
    [ "$v" = "$base" ] || printf -- '-dirty'
}

installed_version() { cat "$DEST/version.txt" 2>/dev/null || echo unknown; }

version_of() {  # the commit a source folder is a clone of, or "unknown" (not a clone)
    local dir=$1 hash owner
    # Ask git as the clone's owner (pi): git run by root on pi's clone refuses
    # ("dubious ownership"), and could leave root-owned files in pi's .git.
    local git=(git --no-optional-locks -C "$dir")
    owner=$(stat -c %U "$dir")
    [ "$owner" = "$(id -un)" ] || git=(runuser -u "$owner" -- "${git[@]}")
    if [ "$("${git[@]}" rev-parse --show-toplevel 2>/dev/null)" != "$dir" ] ||
       ! hash=$("${git[@]}" rev-parse --verify -q HEAD); then
        echo unknown
        return
    fi
    [ -z "$("${git[@]}" status --porcelain --untracked-files=no)" ] || hash+="-dirty"
    echo "$hash"
}

# ---- install --------------------------------------------------------------------------

check_packages() {
    if python3 -c "import gpiozero, lgpio, spidev, smbus, numpy, PIL, qrcode" 2>/dev/null &&
       [ -e /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ] &&
       command -v rsync >/dev/null && command -v git >/dev/null; then
        return
    fi
    say "installing missing packages"
    # shellcheck disable=SC2086
    apt-get install -y $APT_PACKAGES || { apt-get update && apt-get install -y $APT_PACKAGES; } ||
        die "apt-get failed (no internet?). Install them by hand: sudo apt install" $APT_PACKAGES
}

check_buses() {
    local dev missing=""
    for dev in $BUS_DEVICES; do [ -e "$dev" ] || missing=yes; done
    [ -n "$missing" ] || return 0
    command -v raspi-config >/dev/null ||
        die "SPI or I2C is off and there is no raspi-config: turn both on, reboot, install again"
    say "turning on SPI and I2C"
    raspi-config nonint do_spi 0
    raspi-config nonint do_i2c 0
    for dev in $BUS_DEVICES; do [ -e "$dev" ] || REBOOT_NEEDED=yes; done
}

# All copies compare file contents (--checksum), not size and time: two installs in the
# same second leave same-size files (version.txt) with the same time, and a backup that
# skipped them would roll back to a mix of two versions. The program is about 200 kB.

backup() {      # the installed version -> $PREV, for a rollback
    [ -d "$DEST" ] || return 0
    rsync -a --checksum --delete --exclude=__pycache__ --exclude='.lgd-nfy*' "$DEST"/ "$PREV"/
    BACKED_UP=yes
}

copy_files() {  # $1 -> $DEST; files end up owned by root, not writable by others
    mkdir -p "$DEST"
    rsync -rlpt --checksum --chmod=go-w --delete \
          --exclude=.git --exclude=__pycache__ --exclude='*.pyc' \
          --exclude='.lgd-nfy*' --exclude=version.txt \
          "$1"/ "$DEST"/
}

install_unit() {
    install -m 644 "$DEST/$SERVICE.service" "$UNIT_DIR/$SERVICE.service"
    systemctl daemon-reload
    systemctl enable --quiet "$SERVICE"
}

restart_and_check() {   # restart only the display; true if it keeps running
    local restarts
    systemctl restart "$SERVICE"
    restarts=$(systemctl show -p NRestarts --value "$SERVICE")
    say "restarted $SERVICE; checking that it keeps running (${HEALTH_WAIT} s)"
    sleep "$HEALTH_WAIT"
    # Restart=on-failure brings a crashed program back after 5 s, so "active" alone
    # is not enough: the restart counter must not have moved either.
    systemctl is-active --quiet "$SERVICE" &&
        [ "$(systemctl show -p NRestarts --value "$SERVICE")" = "$restarts" ]
}

rollback() {
    say "going back to $(short "$(cat "$PREV/version.txt" 2>/dev/null || echo unknown)")"
    rsync -a --checksum --delete --exclude=__pycache__ --exclude='.lgd-nfy*' "$PREV"/ "$DEST"/
    install_unit
    systemctl restart "$SERVICE"
}

cmd_install() {
    need_root install
    local version
    check_packages
    check_buses
    if [ "$SRC" = "$(realpath -m "$DEST")" ]; then
        say "already in $DEST: only reinstalling the service"
    else
        version=$(version_of "$SRC")
        say "installing $(short "$version") from $SRC"
        backup
        copy_files "$SRC"
        echo "$version" > "$DEST/version.txt"
    fi
    install_unit
    if [ -n "$REBOOT_NEEDED" ]; then
        say "installed. SPI/I2C were just turned on: reboot the Pi once, then the display starts"
        return
    fi
    if restart_and_check; then
        say "installed $(short "$(installed_version)"); the display is running"
        return
    fi
    journalctl -u "$SERVICE" -n 20 --no-pager || true
    if [ -n "$BACKED_UP" ]; then
        say "the new version did not keep running"
        rollback
        die "install failed and was rolled back; the log is above"
    fi
    die "$SERVICE does not keep running; the log is above (journalctl -u $SERVICE)"
}

# ---- update ---------------------------------------------------------------------------

cmd_update() {
    need_root update
    [ -d "$DEST" ] || die "not installed yet: sudo ./deploy.sh install"
    local installed latest out
    installed=$(installed_version)
    say "installed: $(short "$installed")"

    out=$(timeout 30 git ls-remote "$REPO_URL" HEAD) || die "cannot reach $REPO_URL (no internet?)"
    latest=$(cut -f1 <<< "$out")
    [ -n "$latest" ] || die "$REPO_URL has no commits yet"
    say "on GitHub: $(short "$latest")"
    if [ "$installed" = "$latest" ]; then
        say "up to date"
        return
    fi

    UPDATE_TMP=$(mktemp -d)
    trap 'rm -rf "$UPDATE_TMP"' EXIT
    timeout 300 git clone -q "$REPO_URL" "$UPDATE_TMP/src" || die "download from $REPO_URL failed"
    latest=$(git -C "$UPDATE_TMP/src" rev-parse HEAD)

    # Different is not always newer: only move forward along GitHub's history. A clean
    # copy of the same commit replaces a -dirty one; "unknown" (installed by hand) is
    # replaced too.
    if [ "$installed" != unknown ] &&
       ! git -C "$UPDATE_TMP/src" merge-base --is-ancestor "${installed%-dirty}" HEAD 2>/dev/null; then
        say "the installed version is not an older commit of GitHub's (a local build, or newer):"
        say "leaving it as it is"
        return
    fi
    say "updating $(short "$installed") -> $(short "$latest")"
    # Install with the new version's own deploy.sh, so changes to installing ship too.
    bash "$UPDATE_TMP/src/deploy.sh" install
}

# ---- remove ---------------------------------------------------------------------------

cmd_remove() {
    need_root remove
    if [ ! -e "$DEST" ] && [ ! -e "$PREV" ] && [ ! -e "$UNIT_DIR/$SERVICE.service" ]; then
        say "not installed; nothing to remove"
        return
    fi
    [ ! -e "$DEST" ] || [ -f "$DEST/app.py" ] || die "$DEST does not look like this program; not deleting it"
    if [ -e "$UNIT_DIR/$SERVICE.service" ]; then
        systemctl disable --now --quiet "$SERVICE" || true    # stopping draws "display stopped"
        rm -f "$UNIT_DIR/$SERVICE.service"
        systemctl daemon-reload
        systemctl reset-failed "$SERVICE" 2>/dev/null || true
    fi
    rm -rf "$DEST" "$PREV"
    say "removed $SERVICE and $DEST. SPI, I2C and the packages are left as they are."
}

usage() {
    sed -n '2,8s/^# \{0,1\}//p' "$0"
    exit 2
}

main() {
    case "${1:-}" in
        install) cmd_install ;;
        update) cmd_update ;;
        remove|uninstall|purge) cmd_remove ;;
        *) usage ;;
    esac
}

# All of the script is read before anything runs ("main ...; exit" on one line), so an
# update that replaces this file while it runs cannot change what is executing.
main "$@"; exit
