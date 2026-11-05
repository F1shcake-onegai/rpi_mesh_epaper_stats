"""CPU and memory readings, straight from /proc (no psutil needed)."""
import socket
from dataclasses import dataclass


@dataclass
class Stats:
    host: str
    cpu: float        # percent over the last sampling window; None until the first one closes
    mem_used: int     # bytes: MemTotal - MemAvailable
    mem_total: int    # bytes


class CpuMeter:
    """CPU use averaged over a sampling window.

    /proc/stat counts the time every CPU has spent in each state since boot.
    The busy share of the difference between two samples is the CPU use in
    between. sample() closes one window and opens the next. The app calls it
    every 30 s whatever the screen, so percent is always the average of the
    last 30 s, not of the time since the STATS screen was last drawn.
    """

    def __init__(self):
        self._last = self._sample()
        self.percent = None               # not known until the first window closes

    @staticmethod
    def _sample():
        with open("/proc/stat") as f:
            # cpu  user nice system idle iowait irq softirq steal guest guest_nice
            fields = [int(v) for v in f.readline().split()[1:]]
        total = sum(fields[:8])           # guest time is already part of user/nice
        idle = fields[3] + fields[4]      # idle + iowait
        return total, idle

    def sample(self):
        total, idle = self._sample()
        d_total = total - self._last[0]
        d_idle = idle - self._last[1]
        self._last = (total, idle)
        self.percent = 100.0 * (d_total - d_idle) / d_total if d_total > 0 else None


def memory():
    """(used, total) in bytes. "Used" is what applications hold: MemTotal
    minus MemAvailable, so the page cache does not count as used."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024     # values are in kB
    return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]


def read(cpu_meter):
    used, total = memory()
    return Stats(socket.gethostname(), cpu_meter.percent, used, total)
