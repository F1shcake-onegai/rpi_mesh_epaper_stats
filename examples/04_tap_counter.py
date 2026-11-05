#!/usr/bin/env python3
"""Example 4: everything together. A counter with a "-" and a "+" button.

Portrait (122 x 250) like Waveshare's demo, so touch points and picture pixels are the
same coordinates. Taps are partial refreshes; every 20th refresh is a full one.
Ctrl+C or `systemctl stop` (SIGTERM) end it cleanly: the panel is put to sleep.
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # finds ../TP_lib

from PIL import Image, ImageDraw, ImageFont
from TP_lib import epd2in13_V4, epdconfig, gt1151

W, H = 122, 250                         # portrait
MINUS = (4, 170, 58, 245)               # x0, y0, x1, y1: about 10 x 14 mm, a fingertip
PLUS = (64, 170, 118, 245)
TAP_GAP = 0.2                           # s without reports before the next report is a new tap
FULL_EVERY = 20                         # partial refreshes before a full one

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
big, small = ImageFont.truetype(FONT, 44), ImageFont.truetype(FONT, 14)


def inside(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def render(count):
    """Data in, picture out. No hardware here, so it can be tried on a laptop."""
    image = Image.new("1", (W, H), 255)
    d = ImageDraw.Draw(image)
    d.text((6, 6), "Counter", font=small, fill=0)
    d.text((8, 60), str(count), font=big, fill=0)
    for box, label in ((MINUS, "-"), (PLUS, "+")):
        d.rectangle(box, outline=0, width=2)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        d.text((cx - d.textlength(label, font=big) / 2, cy - 28), label, font=big, fill=0)
    return image


running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)     # systemctl stop
signal.signal(signal.SIGINT, stop)      # Ctrl+C

epd = epd2in13_V4.EPD()
gt = gt1151.GT1151()
dev, old = gt1151.GT_Development(), gt1151.GT_Development()
reported = threading.Event()
epdconfig.GPIO_INT.when_released = reported.set

count, partials, last_report = 0, 0, 0.0
epd.init(epd.FULL_UPDATE)
gt.GT_Init()
epd.displayPartBaseImage(epd.getbuffer(render(count)))    # full refresh + base image
epd.init(epd.PART_UPDATE)

try:
    while running:
        # 1. read a report when the controller has one
        if reported.is_set() or gt.digital_read(gt.INT) == 0:
            reported.clear()
            dev.Touch = 1
            gt.GT_Scan(dev, old)

        # 2. turn reports into taps: only the first report after TAP_GAP of silence counts
        if dev.TouchpointFlag:
            dev.TouchpointFlag = 0
            if 1 <= dev.TouchCount <= 5:
                now = time.monotonic()
                is_tap = now - last_report > TAP_GAP
                last_report = now
                x, y = dev.X[0], dev.Y[0]

                # 3. hit test: what did the tap mean?
                new = count
                if is_tap and inside(PLUS, x, y):
                    new = count + 1
                elif is_tap and inside(MINUS, x, y):
                    new = count - 1

                # 4. redraw only when something changed
                if new != count:
                    count = new
                    buf = epd.getbuffer(render(count))
                    if partials >= FULL_EVERY:
                        epd.init(epd.FULL_UPDATE)
                        epd.displayPartBaseImage(buf)
                        epd.init(epd.PART_UPDATE)
                        partials = 0
                    else:
                        epd.displayPartial_Wait(buf)
                        partials += 1
                    last_report = time.monotonic()   # a finger still down is not a new tap

        time.sleep(0.01)
finally:
    epd.sleep()
    epd.Dev_exit()
