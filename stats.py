"""CPU and memory readings, straight from /proc (no psutil needed)."""
import socket
from dataclasses import dataclass


@dataclass
class Stats:
    host: str
    cpu: float        # percent, averaged since the previous reading
    mem_used: int     # bytes: MemTotal - MemAvailable
    mem_total: int    # bytes


class CpuMeter:
    """CPU use averaged over the time between two readings.

    /proc/stat counts the time every CPU has spent in each state since boot.
    The busy share of the difference between two samples is the CPU use in
    between, so a reading every 30 s shows the average of those 30 s rather
    than a momentary spike.
    """

    def __init__(self):
        self._last = self._sample()

    @staticmethod
    def _sample():
        with open("/proc/stat") as f:
            # cpu  user nice system idle iowait irq softirq steal guest guest_nice
            fields = [int(v) for v in f.readline().split()[1:]]
        total = sum(fields[:8])           # guest time is already part of user/nice
        idle = fields[3] + fields[4]      # idle + iowait
        return total, idle

    def percent(self):
        total, idle = self._sample()
        d_total = total - self._last[0]
        d_idle = idle - self._last[1]
        self._last = (total, idle)
        if d_total <= 0:
            return 0.0
        return 100.0 * (d_total - d_idle) / d_total


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
    return Stats(socket.gethostname(), cpu_meter.percent(), used, total)
