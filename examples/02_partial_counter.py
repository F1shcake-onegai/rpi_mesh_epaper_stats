#!/usr/bin/env python3
"""Example 2: partial refresh. Counts to 30, once per second, without flashing;
every 10th number is a full refresh to clear the ghosting partial refreshes leave."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # finds ../TP_lib

from PIL import Image, ImageDraw, ImageFont
from TP_lib import epd2in13_V4

epd = epd2in13_V4.EPD()
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)


def picture(n):
    """The whole screen for number n, as panel bytes."""
    image = Image.new("1", (epd.height, epd.width), 255)
    ImageDraw.Draw(image).text((20, 30), str(n), font=font, fill=0)
    return epd.getbuffer(image)


def full(buf):
    """Full refresh that also stores the picture as the base for partial refreshes."""
    epd.init(epd.FULL_UPDATE)
    epd.displayPartBaseImage(buf)
    epd.init(epd.PART_UPDATE)


full(picture(0))
try:
    for n in range(1, 31):
        time.sleep(1)
        if n % 10 == 0:
            full(picture(n))                      # flashes; ghosting gone
        else:
            epd.displayPartial_Wait(picture(n))   # fast, no flash
finally:
    epd.sleep()
    epd.Dev_exit()
