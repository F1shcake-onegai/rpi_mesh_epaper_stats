#!/usr/bin/env python3
"""Example 1: init, draw, full refresh, sleep. The smallest complete e-paper program."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # finds ../TP_lib

from PIL import Image, ImageDraw, ImageFont
from TP_lib import epd2in13_V4          # importing claims the SPI bus and the GPIO pins

epd = epd2in13_V4.EPD()
epd.init(epd.FULL_UPDATE)               # hardware reset and full setup
epd.Clear(0xFF)                         # whole screen white (a full refresh)

# Landscape: 250 wide, 122 high. Mode "1" is black and white; 255 is white, 0 is black.
image = Image.new("1", (epd.height, epd.width), 255)
draw = ImageDraw.Draw(image)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
draw.text((10, 40), "Hello, e-paper", font=font, fill=0)
draw.rectangle((5, 5, 244, 116), outline=0)

epd.display(epd.getbuffer(image))       # full refresh: flashes, then shows the picture

epd.sleep()                             # deep sleep; the picture stays without power
epd.Dev_exit()                          # release SPI, I2C and the pins
