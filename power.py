"""Reboot and power off, and telling a shutdown of the whole Pi from a stop
of only this program.

Both need root, as the app already does for the mesh switch.
"""
import logging
import subprocess
import threading

log = logging.getLogger(__name__)

ACTIONS = ("reboot", "poweroff")    # the POWER screen's buttons, left to right


def system_stopping():
    """True while systemd shuts the Pi down (reboot, poweroff, halt), whoever
    started it: this screen, a shell, the power button. False when only this
    program stops (systemctl stop epaper-stats, Ctrl+C).

    "systemctl is-system-running" prints "stopping" as soon as shutdown.target
    is queued, which every reboot and poweroff does before it stops any
    service. It prints the state even when it exits non-zero.
    """
    try:
        out = subprocess.run(["systemctl", "is-system-running"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == "stopping"


class PowerSwitch:
    """Runs "systemctl reboot" or "systemctl poweroff" in a background thread,
    so the loop keeps running; only the main loop draws.

    pending is the action asked for. It stays set once systemctl succeeds:
    systemd then stops every service, this app included (SIGTERM), and the
    app draws "powered off". If systemctl fails, pending goes back to None
    and the button returns to normal.
    """

    def __init__(self):
        self.pending = None

    def start(self, action):
        if self.pending or action not in ACTIONS:
            return
        self.pending = action
        threading.Thread(target=self._run, args=(action,), daemon=True).start()

    def _run(self, action):
        try:
            # --no-ask-password: never wait for a password prompt nobody can see
            r = subprocess.run(["systemctl", "--no-ask-password", action],
                               capture_output=True, text=True, timeout=60)
            ok, why = r.returncode == 0, r.stderr.strip()
        except (OSError, subprocess.SubprocessError) as e:
            ok, why = False, str(e)
        # systemd may stop this service, and kill systemctl with it, before
        # systemctl has exited: that is the shutdown working, not a failure.
        if not ok and not system_stopping():
            log.error("systemctl %s failed: %s", action, why or "no reason given")
            self.pending = None
