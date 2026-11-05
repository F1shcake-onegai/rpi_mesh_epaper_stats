#!/usr/bin/env python3
"""Example 3: read the touch controller and print every report. Ctrl+C to stop.

Tap the four corners: this is how you learn the coordinate system of your panel.
Hold a finger still: you will see that one touch is a stream of reports, not one event.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # finds ../TP_lib

from TP_lib import epd2in13_V4, epdconfig, gt1151

epd = epd2in13_V4.EPD()                 # first: it also sets the touch I2C address (0x14)
gt = gt1151.GT1151()
dev = gt1151.GT_Development()           # the latest report
old = gt1151.GT_Development()           # the one before (GT_Scan fills it)
gt.GT_Init()                            # reset the touch controller, read its version

# INT goes low when a report is ready. Waveshare's demo watches the level in a loop that
# never sleeps; here the GPIO library also catches the falling edge, so a short pulse
# between two checks is not lost. (gpiozero sees INT as a button wired to 3.3 V, so
# "low" is "released".)
reported = threading.Event()
epdconfig.GPIO_INT.when_released = reported.set

try:
    while True:
        if reported.is_set() or gt.digital_read(gt.INT) == 0:
            reported.clear()
            dev.Touch = 1                   # GT_Scan only reads when this is set
            gt.GT_Scan(dev, old)
            if dev.TouchpointFlag:
                dev.TouchpointFlag = 0
                if 1 <= dev.TouchCount <= 5:
                    print(f"{time.monotonic():10.2f}  x={dev.X[0]:3d}  y={dev.Y[0]:3d}  "
                          f"size={dev.S[0]:3d}  fingers={dev.TouchCount}")
        time.sleep(0.01)                    # check 100 times a second, not millions
except KeyboardInterrupt:
    pass
finally:
    epd.Dev_exit()
