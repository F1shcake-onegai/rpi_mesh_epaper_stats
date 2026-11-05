"""Tests that run on any computer, no e-Paper needed.

  python3 test_app.py        (or: python3 -m pytest test_app.py)
"""
import types

import app
import mesh
import net
import screens
import stats

INSIDE = (230, 20)     # in the button's touch area
OUTSIDE = (50, 60)


# ---- state machine -------------------------------------------------------------

def test_taps_cycle_through_the_screens():
    assert app.handle_touch(app.STATS, "tap", *OUTSIDE, False, False) == (app.MESH, False, None)
    assert app.handle_touch(app.MESH, "tap", *OUTSIDE, False, False) == (app.NET, False, None)
    assert app.handle_touch(app.NET, "tap", *OUTSIDE, False, False) == (app.STATS, False, None)


def test_button_area_only_matters_on_the_mesh_screen():
    assert app.handle_touch(app.STATS, "tap", *INSIDE, False, False) == (app.MESH, False, None)
    assert app.handle_touch(app.NET, "tap", *INSIDE, True, False) == (app.STATS, False, None)


def test_button_needs_two_taps():
    assert app.handle_touch(app.MESH, "tap", *INSIDE, False, False) == (app.MESH, True, None)       # arm
    assert app.handle_touch(app.MESH, "tap", *INSIDE, True, False) == (app.MESH, False, "toggle")   # toggle


def test_tap_elsewhere_disarms_and_moves_on():
    assert app.handle_touch(app.MESH, "tap", *OUTSIDE, True, False) == (app.NET, False, None)


def test_button_ignored_while_mesh_starts_or_stops():
    assert app.handle_touch(app.MESH, "tap", *INSIDE, True, True) == (app.MESH, False, None)


def test_long_press_hard_refreshes_and_stays():
    for state in (app.STATS, app.MESH, app.NET):
        for where in (INSIDE, OUTSIDE):
            assert app.handle_touch(state, "long", *where, True, False) == (state, False, "hard-refresh")


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
        clock[0] = 110.0
        counts["bat0"] = (21000, 1500)                 # +20000 / +1000 bytes in 10 s
        s = meter.sample()
        assert s["bat0"] == (2000.0, 100.0, 21000, 1500)
        assert s["wlan1"] is None                      # no such interface

        clock[0] = 120.0
        counts["bat0"] = (300, 200)                    # counters went down: bat0 was re-created
        assert meter.sample()["bat0"] == (None, None, 300, 200)
    finally:
        net.time, net.counters = real_time, real_counters


def test_human_bytes():
    assert screens.human_bytes(850) == "850 B"
    assert screens.human_bytes(12_300) == "12.3 kB"
    assert screens.human_bytes(210_000) == "210 kB"
    assert screens.human_bytes(1_230_000_000) == "1.2 GB"


# ---- drawing ---------------------------------------------------------------------

def test_touch_area_edges():
    assert screens.in_button(198, 0) and screens.in_button(249, 63)
    assert not screens.in_button(197, 10) and not screens.in_button(220, 64)


def test_wifi_qr_escapes_special_characters():
    assert screens.wifi_qr_payload("a;b") == "WIFI:T:nopass;S:a\\;b;;"


def test_every_screen_has_the_panel_size():
    s = stats.Stats("pi", 12.0, 1, 4)
    m = mesh.MeshStatus("active", "100.64.0.1", [mesh.Iface("wlan1", "AP", "x-mesh")], "x-mesh")
    n = net.NetStats("active", [net.Traffic("mesh", "bat0", 1.0, 2.0, 3, 4), net.Traffic("wlan1", "absent")])
    for image in (screens.render_stats(s), screens.render_mesh(m, "on"),
                  screens.render_net(n), screens.render_stopped()):
        assert image.size == (screens.W, screens.H) and image.mode == "1"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
