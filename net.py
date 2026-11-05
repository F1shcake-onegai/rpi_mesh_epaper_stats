"""Network traffic: how much data the mesh and the radios receive and send.

The kernel counts the bytes of every interface since it came up, in
/sys/class/net/<name>/statistics/{rx,tx}_bytes. A speed is the difference
between two readings divided by the time between them, so, like the CPU
reading, it is the average since the previous reading (about 30 s).

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
    """Remembers the previous reading of every interface to turn byte counts into speeds."""

    def __init__(self):
        self._last = {}            # name -> (time, rx, tx)
        self.sample()

    def sample(self):
        """name -> (rx_rate, tx_rate, rx_total, tx_total), or None for a missing interface."""
        now = time.monotonic()
        out = {}
        for name in SAMPLED:
            c = counters(name)
            prev = self._last.pop(name, None)
            if c is None:
                out[name] = None
                continue
            rx, tx = c
            self._last[name] = (now, rx, tx)
            if prev is None or now <= prev[0] or rx < prev[1] or tx < prev[2]:
                # first reading, or the interface was re-created (bat0 at every mesh start)
                out[name] = (None, None, rx, tx)
            else:
                dt = now - prev[0]
                out[name] = ((rx - prev[1]) / dt, (tx - prev[2]) / dt, rx, tx)
        return out


def read(meter):
    state = mesh.service_state()
    samples = meter.sample()
    gateway_if = mesh.gateway_iface()
    names = (MESH_IFACE, "wlan1") if state == "active" else ("wlan0", "wlan1")
    rows = []
    for name in names:
        if name == MESH_IFACE:
            label, detail = "mesh", MESH_IFACE
        else:
            label, detail = name, mesh.iface_info(name, gateway_if).job
        s = samples.get(name)
        rows.append(Traffic(label, detail, *s) if s else Traffic(label, detail))
    return NetStats(state, rows)
