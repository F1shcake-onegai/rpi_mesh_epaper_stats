"""Tests that run on any computer, no e-Paper needed.

  python3 test_app.py        (or: python3 -m pytest test_app.py)
"""
import threading
import types

import app
import mesh
import net
import power
import screens
import stats

INSIDE = (230, 20)     # in the mesh button's touch area
OUTSIDE = (50, 10)     # on no button of any screen
REBOOT = (60, 56)      # in the POWER screen's reboot button
POWEROFF = (190, 56)   # in the POWER screen's poweroff button


# ---- state machine -------------------------------------------------------------

def test_taps_cycle_through_the_screens():
    assert app.handle_touch(app.STATS, "tap", *OUTSIDE, None, False) == (app.MESH, None, None)
    assert app.handle_touch(app.MESH, "tap", *OUTSIDE, None, False) == (app.NET, None, None)
    assert app.handle_touch(app.NET, "tap", *OUTSIDE, None, False) == (app.POWER, None, None)
    assert app.handle_touch(app.POWER, "tap", *OUTSIDE, None, False) == (app.STATS, None, None)


def test_button_areas_only_matter_on_their_screen():
    assert app.handle_touch(app.STATS, "tap", *INSIDE, None, False) == (app.MESH, None, None)
    assert app.handle_touch(app.NET, "tap", *INSIDE, "mesh", False) == (app.POWER, None, None)
    assert app.handle_touch(app.MESH, "tap", *REBOOT, None, False) == (app.NET, None, None)
    assert app.handle_touch(app.STATS, "tap", *POWEROFF, None, False) == (app.MESH, None, None)


def test_button_needs_two_taps():
    assert app.handle_touch(app.MESH, "tap", *INSIDE, None, False) == (app.MESH, "mesh", None)      # arm
    assert app.handle_touch(app.MESH, "tap", *INSIDE, "mesh", False) == (app.MESH, None, "toggle")  # toggle


def test_tap_elsewhere_disarms_and_moves_on():
    assert app.handle_touch(app.MESH, "tap", *OUTSIDE, "mesh", False) == (app.NET, None, None)
    assert app.handle_touch(app.POWER, "tap", *OUTSIDE, "reboot", False) == (app.STATS, None, None)


def test_button_ignored_while_mesh_starts_or_stops():
    assert app.handle_touch(app.MESH, "tap", *INSIDE, "mesh", True) == (app.MESH, None, None)


def test_power_buttons_need_two_taps():
    assert app.handle_touch(app.POWER, "tap", *REBOOT, None, False) == (app.POWER, "reboot", None)
    assert app.handle_touch(app.POWER, "tap", *REBOOT, "reboot", False) == (app.POWER, None, "reboot")
    assert app.handle_touch(app.POWER, "tap", *POWEROFF, None, False) == (app.POWER, "poweroff", None)
    assert app.handle_touch(app.POWER, "tap", *POWEROFF, "poweroff", False) == (app.POWER, None, "poweroff")


def test_tapping_the_other_power_button_moves_the_confirmation():
    # armed reboot, tap poweroff: poweroff asks, reboot is back to normal; and the other way round
    assert app.handle_touch(app.POWER, "tap", *POWEROFF, "reboot", False) == (app.POWER, "poweroff", None)
    assert app.handle_touch(app.POWER, "tap", *REBOOT, "poweroff", False) == (app.POWER, "reboot", None)


def test_power_buttons_ignored_while_a_reboot_or_poweroff_is_under_way():
    for where in (REBOOT, POWEROFF):
        assert app.handle_touch(app.POWER, "tap", *where, None, True) == (app.POWER, None, None)


def test_long_press_hard_refreshes_and_stays():
    for state in (app.STATS, app.MESH, app.NET, app.POWER):
        for where in (INSIDE, OUTSIDE, REBOOT, POWEROFF):
            assert app.handle_touch(state, "long", *where, "reboot", False) == (state, None, "hard-refresh")


# ---- the app around the state machine --------------------------------------------

class FakePanel:
    def __init__(self):
        self.refreshes = []

    def partial(self, image):
        self.refreshes.append("partial")

    full = hard = partial


class FakePowerSwitch:
    """Records what would have been run; never reboots anything."""
    def __init__(self):
        self.pending = None
        self.started = []

    def start(self, action):
        self.started.append(action)
        self.pending = action


def make_app():
    real_sample = stats.CpuMeter._sample
    stats.CpuMeter._sample = staticmethod(lambda: (1000, 500))   # no /proc needed
    try:
        a = app.App(FakePanel(), threading.Event())
    finally:
        stats.CpuMeter._sample = real_sample
    a.power = FakePowerSwitch()
    return a


def test_power_screen_arms_switches_and_confirms():
    a = make_app()
    a.state = app.POWER
    a.on_touch("tap", *REBOOT)
    assert a.power_shown == ("confirm", "idle") and a.power.started == []
    a.on_touch("tap", *POWEROFF)                        # the confirmation moves over
    assert a.power_shown == ("idle", "confirm") and a.power.started == []
    a.on_touch("tap", *REBOOT)                          # and back
    assert a.power_shown == ("confirm", "idle") and a.power.started == []
    a.on_touch("tap", *REBOOT)                          # second tap on the armed one
    assert a.power.started == ["reboot"] and a.power_shown == ("busy", "idle")
    a.on_touch("tap", *POWEROFF)                        # under way: the buttons do nothing
    assert a.power.started == ["reboot"] and a.state == app.POWER


def test_power_confirmation_runs_out():
    a = make_app()
    a.state = app.POWER
    a.on_touch("tap", *POWEROFF)
    a.armed_until = 0.0                                 # CONFIRM_FOR has passed
    a.on_touch("tap", *POWEROFF)                        # counts as a first tap again
    assert a.power.started == [] and a.power_shown == ("idle", "confirm")


# ---- refresh rules -------------------------------------------------------------

def test_refresh_kind():
    n = app.FULL_AFTER_PARTIALS
    assert app.refresh_kind("full", True, 0) == "full"         # start-up, even if unchanged
    assert app.refresh_kind("hard", True, 0) == "hard"         # long press, even if unchanged
    assert app.refresh_kind(None, True, n) is None             # same picture: nothing
    assert app.refresh_kind(None, False, 0) == "partial"
    assert app.refresh_kind(None, False, n - 1) == "partial"
    assert app.refresh_kind(None, False, n) == "full"          # ghosting built up


# ---- network speeds ------------------------------------------------------------

def test_net_meter_turns_byte_counts_into_speeds():
    clock = [100.0]
    counts = {"bat0": (1000, 500), "wlan0": (0, 0), "wlan1": None}
    real_time, real_counters = net.time, net.counters
    net.time = types.SimpleNamespace(monotonic=lambda: clock[0])
    net.counters = lambda name: counts[name]
    try:
        meter = net.NetMeter()                         # first reading at t = 100
        assert meter.rates == {}                       # no window closed yet
        clock[0] = 110.0
        counts["bat0"] = (21000, 1500)                 # +20000 / +1000 bytes in 10 s
        meter.sample()
        assert meter.rates["bat0"] == (2000.0, 100.0)
        assert "wlan1" not in meter.rates              # no such interface

        clock[0] = 120.0
        counts["bat0"] = (300, 200)                    # counters went down: bat0 was re-created
        meter.sample()
        assert "bat0" not in meter.rates
    finally:
        net.time, net.counters = real_time, real_counters


def test_drawing_does_not_move_the_averaging_window():
    # The bug this guards against: a screen drawn after a long time elsewhere
    # showed the average of that whole time, not of the last 30 s.
    jiffies = [(1000, 500)]
    meter = stats.CpuMeter.__new__(stats.CpuMeter)
    meter._sample = lambda: jiffies[0]
    meter.__init__()
    assert meter.percent is None                       # not known before the first window
    jiffies[0] = (2000, 1250)                          # 1000 ticks, 750 idle: 25 % busy
    meter.sample()
    assert meter.percent == 25.0
    jiffies[0] = (9000, 1250)                          # flat out since, but no window closed
    real_memory = stats.memory
    stats.memory = lambda: (1, 4)
    try:
        assert stats.read(meter).cpu == 25.0           # reading for a screen does not sample
        assert stats.read(meter).cpu == 25.0
    finally:
        stats.memory = real_memory


def test_human_bytes():
    assert screens.human_bytes(850) == "850 B"
    assert screens.human_bytes(12_300) == "12.3 kB"
    assert screens.human_bytes(210_000) == "210 kB"
    assert screens.human_bytes(1_230_000_000) == "1.2 GB"


# ---- drawing ---------------------------------------------------------------------

def test_touch_area_edges():
    assert screens.in_button(198, 0) and screens.in_button(249, 63)
    assert not screens.in_button(197, 10) and not screens.in_button(220, 64)


def test_power_touch_areas_meet_in_the_middle_and_reach_the_sides():
    assert screens.power_button(0, 26) == "reboot" and screens.power_button(124, 89) == "reboot"
    assert screens.power_button(125, 26) == "poweroff" and screens.power_button(249, 89) == "poweroff"
    for x, y in ((60, 25), (190, 90), (125, 121), (0, 0)):   # header and footer: next screen
        assert screens.power_button(x, y) is None
    for name in power.ACTIONS:                               # the drawing sits inside its touch area
        bx0, by0, bx1, by1 = screens.POWER_BOX[name]
        assert screens.power_button(bx0, by0) == name and screens.power_button(bx1, by1) == name


def test_short_version():
    full = "4d29278d1e0c3b6a9f5e2d7c8b1a0f9e6d5c4b3a"
    assert screens.short_version(full) == "4d29278"
    assert screens.short_version(full + "-dirty") == "4d29278-dirty"
    assert screens.short_version("unknown") == "unknown"


def test_wifi_qr_escapes_special_characters():
    assert screens.wifi_qr_payload("a;b") == "WIFI:T:nopass;S:a\\;b;;"


def test_every_screen_has_the_panel_size():
    s = stats.Stats("pi", 12.0, 1, 4)
    m = mesh.MeshStatus("active", "100.64.0.1", [mesh.Iface("wlan1", "AP", "x-mesh")], "x-mesh")
    n = net.NetStats("active", [net.Traffic("mesh", "bat0", 1.0, 2.0, 3, 4), net.Traffic("wlan1", "absent")])
    for image in (screens.render_stats(s), screens.render_stats(stats.Stats("pi", None, 1, 4)),
                  screens.render_mesh(m, "on"), screens.render_net(n),
                  screens.render_power("pi", "confirm", "idle"), screens.render_power("pi", "idle", "busy"),
                  screens.render_power("pi", "idle", "idle", version="4d29278d1e0c-dirty"),
                  screens.render_stopped(), screens.render_stopped(powered_off=True)):
        assert image.size == (screens.W, screens.H) and image.mode == "1"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
