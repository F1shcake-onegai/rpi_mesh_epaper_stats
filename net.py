"""Network traffic: how much data the mesh and the radios receive and send.

The kernel counts the bytes of every interface since it came up, in
/sys/class/net/<name>/statistics/{rx,tx}_bytes. A speed is the difference
between two readings divided by the time between them. Like the CPU
reading, the app takes one every 30 s whatever the screen, so a speed is
always the average of the last 30 s.

Rows on the screen:
  mesh on   mesh (bat0)  and  wlan1
  mesh off  wlan0        and  wlan1
"mesh" is bat0, batman-adv's interface: the data this Pi sends into the mesh
and receives from it. (The wlan0 radio itself would also count what this Pi
relays for other Pis, plus batman's own announcements.)
"""
import time
from dataclasses import dataclass, field

import mesh

SAMPLED = ("bat0", "wlan0", "wlan1")   # all read every time, so speeds stay right when the mesh switches
MESH_IFACE = "bat0"


@dataclass
class Traffic:
    name: str                  # row label: mesh, wlan0, wlan1
    detail: str = ""           # bat0, or the radio's job (AP, client, absent, ...)
    rx_rate: float = None      # bytes/s since the previous reading; None: not known yet
    tx_rate: float = None
    rx_total: int = None       # bytes since the interface came up; None: no such interface
    tx_total: int = None


@dataclass
class NetStats:
    mesh_state: str            # systemctl is-active mesh.service
    rows: list = field(default_factory=list)


def counters(name):
    """(received, sent) bytes of an interface, or None when it does not exist."""
    try:
        with open(f"/sys/class/net/{name}/statistics/rx_bytes") as f:
            rx = int(f.read())
        with open(f"/sys/class/net/{name}/statistics/tx_bytes") as f:
            tx = int(f.read())
        return rx, tx
    except (OSError, ValueError):
        return None


class NetMeter:
    """Turns byte counts into speeds, averaged over a sampling window.

    sample() closes one window and opens the next. The app calls it every
    30 s whatever the screen, so the speeds are always those of the last
    30 s, not of the time since the NET screen was last drawn.
    """

    def __init__(self):
        self._last = {}            # name -> (time, rx, tx) where the current window started
        self.rates = {}            # name -> (rx_rate, tx_rate) in bytes/s over the last window
        self.sample()

    def sample(self):
        now = time.monotonic()
        rates = {}
        for name in SAMPLED:
            c = counters(name)
            prev = self._last.pop(name, None)
            if c is None:
                continue                       # no such interface: no rate
            rx, tx = c
            self._last[name] = (now, rx, tx)
            # No rate after the first reading, or when the counters went down:
            # the interface was re-created (bat0 at every mesh start).
            if prev is not None and now > prev[0] and rx >= prev[1] and tx >= prev[2]:
                dt = now - prev[0]
                rates[name] = ((rx - prev[1]) / dt, (tx - prev[2]) / dt)
        self.rates = rates


def read(meter):
    """The NET screen's rows: speeds from the meter's last window; totals
    and the mesh state as they are now."""
    state = mesh.service_state()
    gateway_if = mesh.gateway_iface()
    names = (MESH_IFACE, "wlan1") if state == "active" else ("wlan0", "wlan1")
    rows = []
    for name in names:
        if name == MESH_IFACE:
            label, detail = "mesh", MESH_IFACE
        else:
            label, detail = name, mesh.iface_info(name, gateway_if).job
        totals = counters(name)
        if totals is None:
            rows.append(Traffic(label, detail))
        else:
            rows.append(Traffic(label, detail, *meter.rates.get(name, (None, None)), *totals))
    return NetStats(state, rows)
