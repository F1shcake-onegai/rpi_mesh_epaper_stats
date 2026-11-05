#!/usr/bin/env python3
"""Touch e-Paper status display for a rpi_mesh_autodns node.

Three screens, a finite state machine with three states:

  STATS  CPU and memory                 tap anywhere            -> MESH
  MESH   mesh, wlan0, wlan1, AP QR      tap the button, twice   -> mesh on/off
                                        tap anywhere else       -> NET
  NET    data received and sent         tap anywhere            -> STATS
         (mesh and wlan1 while the mesh is on, wlan0 and wlan1 while it is off)
  any screen                            long press (1 s)        -> hard refresh

Every 30 s the current screen is redrawn with fresh data. The panel only
refreshes when the picture changed: partially, or fully when ghosting has
built up (after 20 partial refreshes).

  sudo python3 app.py              run on the Pi (root: mesh.service, batctl)
  sudo python3 app.py --debug      also log touch reports, gestures and refreshes
  python3 app.py --preview DIR     write every screen as a PNG, no hardware needed
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time

import mesh
import net
import screens
import stats

STATS, MESH, NET = 0, 1, 2
NAMES = {STATS: "STATS", MESH: "MESH", NET: "NET"}
NEXT = {STATS: MESH, MESH: NET, NET: STATS}      # where a plain tap leads

UPDATE_EVERY = 30          # s between redraws with fresh data
FULL_AFTER_PARTIALS = 20   # partial refreshes before a full one is a must (~10 min at one per 30 s)
SETTLE_EVERY = 3           # s between status checks while the mesh starts or stops
CONFIRM_FOR = 5            # s the button waits for the confirming second tap
TICK = 0.01                # s between touch polls

log = logging.getLogger("app")


def handle_touch(state, gesture, x, y, armed, busy):
    """The state machine: what a gesture at (x, y) does.

    gesture: "tap" or "long" (finger held for a second).
    armed:   the button was tapped once and waits for confirmation.
    busy:    the mesh is starting or stopping; the button does nothing.
    Returns (new state, arm the button, action); action is None, "toggle"
    (switch the mesh) or "hard-refresh".
    Pure function: no hardware, no clock, so it is easy to test.
    """
    if gesture == "long":
        return state, False, "hard-refresh"   # any screen: clean redraw, cancels a pending confirm
    if state == MESH and screens.in_button(x, y):
        if busy:
            return MESH, False, None
        if armed:
            return MESH, False, "toggle"      # second tap within CONFIRM_FOR
        return MESH, True, None               # first tap: ask for confirmation
    return NEXT[state], False, None           # anywhere else: next screen


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
        self.state = STATS
        self.armed_until = 0.0
        self.mesh_shown = None             # (MeshStatus, button mode) on the glass, MESH only
        self.shown_bytes = None            # the picture on the glass, to skip needless refreshes
        self.partials = 0                  # partial refreshes since the last full one

    # ---- what to draw ------------------------------------------------------

    def button_mode(self, status):
        if self.toggler.busy or status.state in mesh.STARTING_OR_STOPPING:
            return "busy"
        on = status.state == "active"
        if time.monotonic() < self.armed_until:
            return "confirm-off" if on else "confirm-on"
        return "on" if on else "off"

    def render(self):
        """The current state as a picture, with fresh data."""
        if self.state == STATS:
            self.mesh_shown = None
            return screens.render_stats(stats.read(self.cpu))
        if self.state == NET:
            self.mesh_shown = None
            return screens.render_net(net.read(self.net))
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
        """On the mesh screen while a switch is under way or not yet shown done."""
        return self.state == MESH and self.mesh_shown is not None and (
            self.toggler.busy or self.mesh_shown[1] == "busy")

    # ---- events ------------------------------------------------------------

    def on_touch(self, gesture, x, y):
        now = time.monotonic()
        armed = now < self.armed_until
        busy = self.mesh_shown is not None and self.mesh_shown[1] == "busy"
        new_state, arm, action = handle_touch(self.state, gesture, x, y, armed, busy)
        log.info("%s (%d, %d): %s -> %s%s", gesture, x, y, NAMES[self.state], NAMES[new_state],
                 f" {action}" if action else " confirm?" if arm else "")
        self.state = new_state
        self.armed_until = now + CONFIRM_FOR if arm else 0.0
        if action == "toggle":
            turn_on = self.mesh_shown[0].state != "active"     # what the button promised
            log.info("mesh %s", "on" if turn_on else "off")
            self.toggler.start(turn_on)
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
            elif self.armed_until and now >= self.armed_until:
                self.armed_until = 0.0              # no second tap: disarm the button
                self.refresh()
            elif now >= next_update:
                next_update = now + UPDATE_EVERY
                self.refresh()
            elif self.settling() and now >= next_check:
                next_check = now + SETTLE_EVERY
                self.refresh()                      # redraws only when something changed
            self.stop.wait(TICK)


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
        "stopped": screens.render_stopped(),
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

    # systemctl stop sends SIGTERM, Ctrl+C sends SIGINT. Both only set a flag:
    # the loop finishes its current refresh, then the panel is put to sleep.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    from panel import Panel      # here, not at the top: importing TP_lib claims the GPIO pins
    panel = Panel()
    try:
        App(panel, stop).run()
    finally:
        try:
            panel.full(screens.render_stopped())
        except Exception:
            log.exception("could not draw the stopped screen")
        panel.close()
        log.info("stopped")


if __name__ == "__main__":
    sys.exit(main())
