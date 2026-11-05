#!/usr/bin/env python3
"""Touch e-Paper status display for a rpi_mesh_autodns node.

Four screens, a finite state machine with four states:

  STATS  CPU and memory                 tap anywhere            -> MESH
  MESH   mesh, wlan0, wlan1, AP QR      tap the button, twice   -> mesh on/off
                                        tap anywhere else       -> NET
  NET    data received and sent         tap anywhere            -> POWER
         (mesh and wlan1 while the mesh is on, wlan0 and wlan1 while it is off)
  POWER  reboot and power off           tap a button, twice     -> reboot / power off
                                        tap anywhere else       -> STATS
  any screen                            long press (1 s)        -> hard refresh

Every 30 s the CPU and network averages are taken, whatever the screen, and
the current screen is redrawn with fresh data. The panel only refreshes when
the picture changed: partially, or fully when ghosting has built up (after 20
partial refreshes). On exit the panel shows "powered off" if the whole Pi is
shutting down, else "display stopped".

  sudo python3 app.py              run on the Pi (root: mesh.service, batctl, reboot)
  sudo python3 app.py --debug      also log touch reports, gestures and refreshes
  python3 app.py --preview DIR     write every screen as a PNG, no hardware needed
"""
import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time

import mesh
import net
import power
import screens
import stats

STATS, MESH, NET, POWER = 0, 1, 2, 3
NAMES = {STATS: "STATS", MESH: "MESH", NET: "NET", POWER: "POWER"}
NEXT = {STATS: MESH, MESH: NET, NET: POWER, POWER: STATS}      # where a plain tap leads
ACTION = {"mesh": "toggle", "reboot": "reboot", "poweroff": "poweroff"}   # what a confirmed button does

UPDATE_EVERY = 30          # s between redraws with fresh data, and the CPU/network averaging window
FULL_AFTER_PARTIALS = 20   # partial refreshes before a full one is a must (~10 min at one per 30 s)
SETTLE_EVERY = 3           # s between status checks while the mesh switches or a reboot is under way
CONFIRM_FOR = 5            # s the button waits for the confirming second tap
TICK = 0.01                # s between touch polls
VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")

log = logging.getLogger("app")


def read_version():
    """The git commit this copy was installed from (version.txt, written by
    deploy.sh), or "" when it runs straight from a clone."""
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def button_at(state, x, y):
    """The button under (x, y) on this screen: "mesh", "reboot", "poweroff" or None."""
    if state == MESH:
        return "mesh" if screens.in_button(x, y) else None
    if state == POWER:
        return screens.power_button(x, y)
    return None


def handle_touch(state, gesture, x, y, armed, busy):
    """The state machine: what a gesture at (x, y) does.

    gesture: "tap" or "long" (finger held for a second).
    armed:   the button that was tapped once and waits for confirmation:
             None, "mesh", "reboot" or "poweroff". Only one at a time.
    busy:    the buttons on this screen do nothing: the mesh is starting or
             stopping, or the Pi is about to reboot or power off.
    Returns (new state, button to arm, action). The button is None or a name
    as for armed. The action is None, "toggle" (switch the mesh), "reboot",
    "poweroff" or "hard-refresh".
    Pure function: no hardware, no clock, so it is easy to test.
    """
    if gesture == "long":
        return state, None, "hard-refresh"    # any screen: clean redraw, cancels a pending confirm
    button = button_at(state, x, y)
    if button:
        if busy:
            return state, None, None
        if armed == button:
            return state, None, ACTION[button]  # second tap on the same button within CONFIRM_FOR
        return state, button, None            # first tap, or the other button: arm this one only
    return NEXT[state], None, None            # anywhere else: next screen


def refresh_kind(force, unchanged, partials):
    """Which refresh the panel needs: None, "partial", "full" or "hard".

    force:     "full" (start-up) or "hard" (long press), else None
    unchanged: the new picture is the one already on the glass
    partials:  partial refreshes since the last full one
    """
    if force:
        return force
    if unchanged:
        return None                           # nothing to do: no flash, no wear
    if partials >= FULL_AFTER_PARTIALS:
        return "full"                         # ghosting has built up
    return "partial"


class App:
    def __init__(self, panel, stop):
        self.panel = panel
        self.stop = stop                   # threading.Event, set by SIGTERM / Ctrl+C
        self.cpu = stats.CpuMeter()
        self.net = net.NetMeter()
        self.toggler = mesh.Toggler()
        self.power = power.PowerSwitch()
        self.state = STATS
        self.armed = None                  # the button waiting for its confirming tap
        self.armed_until = 0.0
        self.mesh_shown = None             # (MeshStatus, button mode) on the glass, MESH only
        self.power_shown = None            # (reboot mode, poweroff mode) on the glass, POWER only
        self.shown_bytes = None            # the picture on the glass, to skip needless refreshes
        self.partials = 0                  # partial refreshes since the last full one
        self.version = read_version()      # shown on the POWER screen

    # ---- data --------------------------------------------------------------

    def sample(self):
        """Close the CPU and network averaging window and open the next.
        Runs every UPDATE_EVERY seconds whatever the screen, so STATS and NET
        always show the average of the last UPDATE_EVERY seconds, even right
        after switching to them."""
        self.cpu.sample()
        self.net.sample()

    # ---- what to draw ------------------------------------------------------

    def button_mode(self, status):
        if self.toggler.busy or status.state in mesh.STARTING_OR_STOPPING:
            return "busy"
        on = status.state == "active"
        if self.armed == "mesh":
            return "confirm-off" if on else "confirm-on"
        return "on" if on else "off"

    def power_modes(self):
        """The mode of each POWER button: "busy" (under way), "confirm" or "idle"."""
        return tuple("busy" if self.power.pending == b else "confirm" if self.armed == b else "idle"
                     for b in power.ACTIONS)

    def render(self):
        """The current state as a picture, with fresh data."""
        self.mesh_shown = self.power_shown = None
        if self.state == STATS:
            return screens.render_stats(stats.read(self.cpu))
        if self.state == NET:
            return screens.render_net(net.read(self.net))
        if self.state == POWER:
            self.power_shown = self.power_modes()
            return screens.render_power(socket.gethostname(), *self.power_shown, version=self.version)
        status = mesh.read_status()
        button = self.button_mode(status)
        self.mesh_shown = (status, button)
        return screens.render_mesh(status, button)

    def refresh(self, force=None):
        """Redraw the current state; touch the panel only if needed."""
        image = self.render()
        data = image.tobytes()
        kind = refresh_kind(force, data == self.shown_bytes, self.partials)
        if kind is None:
            return
        log.debug("refresh: %s", kind)
        {"partial": self.panel.partial, "full": self.panel.full, "hard": self.panel.hard}[kind](image)
        self.partials = self.partials + 1 if kind == "partial" else 0
        self.shown_bytes = data

    def settling(self):
        """A switch is under way on this screen, or its end is not shown yet."""
        if self.state == MESH and self.mesh_shown is not None:
            return self.toggler.busy or self.mesh_shown[1] == "busy"
        if self.state == POWER and self.power_shown is not None:
            return "busy" in self.power_shown      # redraws the button if systemctl failed
        return False

    def buttons_busy(self):
        """The buttons on the current screen do nothing right now."""
        if self.state == MESH:
            return self.mesh_shown is not None and self.mesh_shown[1] == "busy"
        if self.state == POWER:
            return self.power.pending is not None
        return False

    # ---- events ------------------------------------------------------------

    def on_touch(self, gesture, x, y):
        now = time.monotonic()
        armed = self.armed if now < self.armed_until else None
        new_state, arm, action = handle_touch(self.state, gesture, x, y, armed, self.buttons_busy())
        log.info("%s (%d, %d): %s -> %s%s", gesture, x, y, NAMES[self.state], NAMES[new_state],
                 f" {action}" if action else f" {arm} confirm?" if arm else "")
        self.state = new_state
        self.armed = arm
        self.armed_until = now + CONFIRM_FOR if arm else 0.0
        if action == "toggle":
            turn_on = self.mesh_shown[0].state != "active"     # what the button promised
            log.info("mesh %s", "on" if turn_on else "off")
            self.toggler.start(turn_on)
        elif action in power.ACTIONS:
            log.info("%s requested", action)
            self.power.start(action)           # systemd answers with SIGTERM: see main()
        self.refresh(force="hard" if action == "hard-refresh" else None)

    def run(self):
        self.refresh(force="full")            # the panel needs one full refresh to start from
        next_update = time.monotonic() + UPDATE_EVERY
        next_check = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            gesture = self.panel.poll()
            if gesture:
                self.on_touch(*gesture)
            elif self.armed and now >= self.armed_until:
                self.armed, self.armed_until = None, 0.0   # no second tap: disarm the button
                self.refresh()
            elif now >= next_update:
                next_update = now + UPDATE_EVERY
                self.sample()                       # whatever the screen: see sample()
                self.refresh()
            elif self.settling() and now >= next_check:
                next_check = now + SETTLE_EVERY
                self.refresh()                      # redraws only when something changed
            self.stop.wait(TICK)


PREVIEW_VERSION = "4d29278d1e0c3b6a9f5e2d7c8b1a0f9e6d5c4b3a"


def preview(outdir):
    """Render every screen with made-up data into PNGs (1 px = 1 panel pixel)."""
    os.makedirs(outdir, exist_ok=True)
    s = stats.Stats("m782li", 38.0, int(1.9 * 2**30), int(3.7 * 2**30))
    ifaces = [mesh.Iface("wlan0", "mesh", "fine204.mesh", "3 peers"),
              mesh.Iface("wlan1", "AP", "fine204-mesh", "2 clients")]
    ap = mesh.MeshStatus("active", "100.66.12.1", ifaces, "fine204-mesh")
    uplink = mesh.MeshStatus("active", "100.66.12.1", [
        ifaces[0], mesh.Iface("wlan1", "gateway", "Home-WiFi-5G", "uplink for the mesh")])
    off = mesh.MeshStatus("inactive", "", [
        mesh.Iface("wlan0", "client", "Home-WiFi-5G"), mesh.Iface("wlan1", "absent")])
    shots = {
        "stats": screens.render_stats(s),
        "mesh_ap": screens.render_mesh(ap, "on"),
        "mesh_ap_confirm": screens.render_mesh(ap, "confirm-off"),
        "mesh_gateway": screens.render_mesh(uplink, "on"),
        "mesh_off": screens.render_mesh(off, "off"),
        "mesh_starting": screens.render_mesh(mesh.MeshStatus("activating", "", off.ifaces), "busy"),
        "net_mesh_on": screens.render_net(net.NetStats("active", [
            net.Traffic("mesh", "bat0", 12_300, 3_100, 45_200_000, 12_000_000),
            net.Traffic("wlan1", "AP", 40_500, 210_000, 1_230_000_000, 300_000_000)])),
        "net_mesh_off": screens.render_net(net.NetStats("inactive", [
            net.Traffic("wlan0", "client", 850, 120, 734_000_000, 21_400_000),
            net.Traffic("wlan1", "absent")])),
        "power": screens.render_power("m782li", "idle", "idle", version=PREVIEW_VERSION),
        "power_confirm": screens.render_power("m782li", "idle", "confirm", version=PREVIEW_VERSION),
        "power_busy": screens.render_power("m782li", "busy", "idle", version=PREVIEW_VERSION),
        "stopped": screens.render_stopped(),
        "powered_off": screens.render_stopped(powered_off=True),
    }
    for name, image in shots.items():
        path = os.path.join(outdir, name + ".png")
        image.save(path)
        print(path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--preview", metavar="DIR", help="write PNGs of every screen and exit")
    p.add_argument("--debug", action="store_true", help="log touch reports, gestures and refreshes")
    args = p.parse_args()

    if args.preview:
        preview(args.preview)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.debug:
        logging.getLogger("panel").setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)
    # first thing in the journal, so a failed update shows which version failed
    log.info("version %s", read_version() or "unknown (no version.txt: not installed by deploy.sh)")

    # systemctl stop, reboot and poweroff send SIGTERM, Ctrl+C sends SIGINT.
    # All only set a flag: the loop finishes its current refresh, then the
    # last screen is drawn and the panel is put to sleep.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Run by hand, a closing terminal sends SIGHUP: an SSH session that ends,
    # or the login session being stopped at a reboot (SIGTERM, then SIGHUP at
    # once). Its default would kill the program before it can draw. Left
    # alone when ignored already (nohup).
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, lambda *_: stop.set())

    from panel import Panel      # here, not at the top: importing TP_lib claims the GPIO pins
    panel = Panel()
    try:
        App(panel, stop).run()
    finally:
        # A reboot or poweroff stops this program the same way "systemctl
        # stop" does; ask systemd which of the two this is.
        powered_off = power.system_stopping()
        try:
            panel.full(screens.render_stopped(powered_off))
        except Exception:
            log.exception("could not draw the last screen")
        panel.close()
        log.info("stopped: %s", "powered off" if powered_off else "display stopped")


if __name__ == "__main__":
    sys.exit(main())
