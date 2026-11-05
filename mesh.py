"""Mesh status and the on/off switch for a rpi_mesh_autodns node.

Everything here reads what mesh-config's scripts leave behind:
  mesh.service          on/off (systemctl is-active)
  /etc/default/mesh     MYIP=, the node address (written by mesh-up.sh)
  /run/mesh/gateway     the interface that shares its uplink with the mesh
  /etc/hostapd/hostapd.conf   ssid= of the access point
and asks iw and batctl about the radios. Needs root for batctl and the switch.
"""
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field

IFACES = ("wlan0", "wlan1")    # shown on the mesh screen, in this order
AP_IFACE = "wlan1"             # a QR code is shown when this one runs the access point
MESH_UNIT = "mesh.service"
MESH_DOWN = "/usr/local/sbin/mesh-down.sh"

STARTING_OR_STOPPING = ("activating", "deactivating")


@dataclass
class Iface:
    name: str
    job: str            # mesh, AP, client, gateway, idle, absent, or iw's mode
    ssid: str = ""
    detail: str = ""    # "3 peers", "2 clients", ...


@dataclass
class MeshStatus:
    state: str          # systemctl is-active: active, inactive, activating, deactivating, failed
    ip: str = ""        # node address while the mesh is on
    ifaces: list = field(default_factory=list)
    ap_ssid: str = ""   # set when AP_IFACE runs the access point -> QR code


def _run(*cmd, timeout=5):
    """stdout of a command, "" on any error (missing tool, timeout, ...)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _match(pattern, text):
    m = re.search(pattern, text, re.M)
    return m.group(1).strip() if m else ""


def gateway_iface():
    """The interface that shares its uplink with the mesh, or ""."""
    return _read("/run/mesh/gateway").strip()


def service_state():
    # is-active prints the state even when it exits non-zero (inactive, failed)
    return _run("systemctl", "is-active", MESH_UNIT).strip() or "unknown"


def iface_info(name, gateway_if):
    if not os.path.exists(f"/sys/class/net/{name}"):
        return Iface(name, "absent")          # the second radio is hot-pluggable

    info = _run("iw", "dev", name, "info")
    mode = _match(r"^\s*type (.+)$", info)
    ssid = _match(r"^\s*ssid (.+)$", info) or _match(r"^\s*SSID: (.+)$", _run("iw", "dev", name, "link"))

    if mode == "IBSS":                        # the ad-hoc cell batman-adv runs on
        peers = sum(1 for line in _run("batctl", "o").splitlines() if line.lstrip().startswith("*"))
        return Iface(name, "mesh", ssid, f"{peers} peers")
    if mode == "AP":
        ssid = ssid or _match(r"^ssid=(.*)$", _read("/etc/hostapd/hostapd.conf"))
        clients = sum(1 for line in _run("iw", "dev", name, "station", "dump").splitlines()
                      if line.startswith("Station"))
        return Iface(name, "AP", ssid, f"{clients} clients")
    if mode == "managed":
        if name == gateway_if:
            return Iface(name, "gateway", ssid, "uplink for the mesh")
        return Iface(name, "client" if ssid else "idle", ssid)
    return Iface(name, mode or "?", ssid)


def read_status():
    state = service_state()
    gateway_if = gateway_iface()
    ifaces = [iface_info(name, gateway_if) for name in IFACES]
    ap_ssid = next((i.ssid for i in ifaces if i.name == AP_IFACE and i.job == "AP"), "")
    # /etc/default/mesh outlives the mesh; only trust it while the mesh is on
    ip = _match(r"^MYIP=(.*)$", _read("/etc/default/mesh")) if state == "active" else ""
    return MeshStatus(state, ip, ifaces, ap_ssid)


class Toggler:
    """Turns the mesh on or off in a background thread.

    mesh.service is a oneshot unit that may take up to 120 s to start, far too
    long to freeze the touch screen. The app keeps running and shows "..."
    on the button until busy is False and systemd reports a settled state.
    """

    def __init__(self):
        self._thread = None

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, turn_on):
        if self.busy:
            return
        self._thread = threading.Thread(target=self._on if turn_on else self._off, daemon=True)
        self._thread.start()

    @staticmethod
    def _on():
        _run("systemctl", "start", MESH_UNIT, timeout=180)

    @staticmethod
    def _off():
        # Same as mesh_off() in mesh-config: after a failed start the unit is
        # inactive and "systemctl stop" does nothing, so run mesh-down.sh then.
        was_active = service_state() == "active"
        _run("systemctl", "stop", MESH_UNIT, timeout=180)
        _run("systemctl", "reset-failed", MESH_UNIT)
        if not was_active:
            _run(MESH_DOWN, timeout=120)
