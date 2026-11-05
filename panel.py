"""Hardware layer: the only module that talks to the e-Paper and the touch controller.

Importing TP_lib opens SPI and I2C and claims the GPIO pins at once, so nothing
else in the app imports this module except app.py, and only when it runs on the
Pi. Everything above this layer works with 250 x 122 landscape images and
gestures ("tap" or "long") at landscape coordinates.
"""
import logging
import threading
import time

from TP_lib import epd2in13_V4, epdconfig, gt1151

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 250, 122   # landscape, the size the app draws
RELEASE_GAP = 0.2          # s without touch reports: the finger has lifted
LONG_PRESS = 1.0           # s a finger stays down to make a long press


def to_landscape(x, y):
    """Turn a touch point into landscape image coordinates.

    The touch controller reports portrait coordinates (122 x 250), the same as
    a portrait image handed to getbuffer(). getbuffer() rotates a portrait
    image by 180 degrees and a landscape image by 270, so a landscape image is
    the portrait one turned 90 degrees clockwise. The point turns with it.
    Run app.py --debug and tap the corners to check this on your panel.
    """
    lx = (WIDTH - 1) - y
    ly = x
    return min(max(lx, 0), WIDTH - 1), min(max(ly, 0), HEIGHT - 1)


class Panel:
    def __init__(self):
        self.epd = epd2in13_V4.EPD()           # first: it also sets the touch I2C address (0x14)
        self.gt = gt1151.GT1151()
        self.dev = gt1151.GT_Development()     # latest touch frame
        self.old = gt1151.GT_Development()     # previous frame (GT_Scan fills it)

        # The touch in progress. down_at is None while no finger is down.
        self.down_at = None        # when the finger landed
        self.down_pos = None       # where it landed
        self.last_report = 0.0     # time of the latest report
        self.long_sent = False     # this touch already became a long press
        self.ignore = False        # this touch was there during a refresh: ignore it

        # INT goes low when the touch controller has a new frame. A short pulse
        # can fall between two polls, so let the GPIO library catch the edge.
        # gpiozero sees INT as a Button with pull_up=False: low is "released".
        self.int_seen = threading.Event()
        epdconfig.GPIO_INT.when_released = self.int_seen.set

        self.epd.init(self.epd.FULL_UPDATE)
        self.gt.GT_Init()

    # ---- display -----------------------------------------------------------

    def partial(self, image):
        """Partial refresh: no flash, fast, but leaves a little ghosting."""
        self.epd.displayPartial_Wait(self.epd.getbuffer(image))
        self._after_refresh()

    def full(self, image):
        """Full refresh (flashes, about 2 s). Clears most ghosting and sets the
        reference image that the following partial refreshes compare against."""
        self.epd.init(self.epd.FULL_UPDATE)
        self.epd.displayPartBaseImage(self.epd.getbuffer(image))
        self.epd.init(self.epd.PART_UPDATE)
        self._after_refresh()

    def hard(self, image):
        """Hard refresh: a full refresh to white, then a full refresh of the
        image. Takes twice as long; removes ghosting a single full refresh
        leaves behind."""
        self.epd.init(self.epd.FULL_UPDATE)
        self.epd.Clear(0xFF)
        self.epd.displayPartBaseImage(self.epd.getbuffer(image))
        self.epd.init(self.epd.PART_UPDATE)
        self._after_refresh()

    def _after_refresh(self):
        # Touches made while the panel was busy are dropped: the screen they
        # were aimed at may already be gone. Act as if a finger were down until
        # RELEASE_GAP passes without reports, and ignore that "touch".
        now = time.monotonic()
        self.int_seen.clear()
        self.down_at, self.down_pos, self.last_report = now, None, now
        self.long_sent, self.ignore = False, True

    # ---- touch -------------------------------------------------------------

    def _read_point(self):
        """One touch report: (x, y) in landscape pixels, "up" for the
        controller's lift frame, or None (no new report, or a bad read)."""
        if not (self.int_seen.is_set() or self.gt.digital_read(self.gt.INT) == 0):
            return None
        self.int_seen.clear()

        self.dev.Touch = 1
        self.gt.GT_Scan(self.dev, self.old)
        if not self.dev.TouchpointFlag:
            return None
        self.dev.TouchpointFlag = 0
        if self.dev.TouchCount == 0:
            return "up"
        if self.dev.TouchCount > 5:
            return None
        x, y = self.dev.X[0], self.dev.Y[0]
        lx, ly = to_landscape(x, y)
        log.debug("report raw (%d, %d) -> landscape (%d, %d)", x, y, lx, ly)
        return lx, ly

    def poll(self):
        """Call every few ms. Returns ("tap", x, y), ("long", x, y) or None.

        A finger on the glass sends a stream of reports. The stream becomes:
          tap   the finger lifted before LONG_PRESS (reported on lift)
          long  the finger stayed down for LONG_PRESS (reported while it is
                still down; its lift is then ignored)
        Lifted means a lift frame, or RELEASE_GAP without reports.
        """
        now = time.monotonic()
        point = self._read_point()
        if isinstance(point, tuple):
            if self.down_at is None:                       # a finger landed
                self.down_at, self.down_pos = now, point
                self.long_sent = self.ignore = False
            self.last_report = now
        elif self.down_at is not None and (point == "up" or now - self.last_report > RELEASE_GAP):
            return self._lifted()

        if (self.down_at is not None and not self.long_sent and not self.ignore
                and now - self.down_at >= LONG_PRESS):
            self.long_sent = True
            log.debug("long press at %s", self.down_pos)
            return ("long",) + self.down_pos
        return None

    def _lifted(self):
        gesture = None
        if not self.long_sent and not self.ignore:
            gesture = ("tap",) + self.down_pos
            log.debug("tap at %s", self.down_pos)
        self.down_at = None
        self.long_sent = self.ignore = False
        return gesture

    # ---- shutdown ----------------------------------------------------------

    def close(self):
        """Put the panel into deep sleep and release SPI, I2C and the pins.
        The image stays on the glass without power."""
        self.epd.sleep()
        self.epd.Dev_exit()
