"""Drawing: turns data into 250 x 122 black-and-white images.

No hardware here and no I/O except reading the clock, so every screen can be
rendered on a laptop (app.py --preview) and checked as a PNG.

Colours on a mode "1" image: 0 is black, 255 is white.
"""
import re
import time
from functools import lru_cache

import qrcode
from PIL import Image, ImageDraw, ImageFont

W, H = 250, 122
BLACK, WHITE = 0, 255

# ---- layout of the mesh screen ---------------------------------------------
# | text column 0..110 | QR code 112..206 | button 209..247 |
TEXT_W_WITH_QR = 110
QR_BOX = (112, 0, 206, H)          # the code is centred in here
BUTTON_BOX = (209, 3, 247, 45)     # what is drawn: 39 x 43 px
# What reacts to a finger: larger than the drawing and running to the screen
# edges. One pixel is about 0.19 mm, so this is about 10 x 12 mm, a fingertip.
BUTTON_HIT = (198, 0, W, 64)

STATE_LABEL = {"active": "on", "inactive": "off", "activating": "starting",
               "deactivating": "stopping", "failed": "failed"}

# button mode -> (small top line, large bottom line)
BUTTON_TEXT = {
    "on":          ("MESH", "ON"),
    "off":         ("MESH", "OFF"),
    "busy":        ("MESH", "..."),
    "confirm-on":  ("turn", "ON?"),     # armed: the next tap on the button toggles.
    "confirm-off": ("turn", "OFF?"),    # Drawn filled, the only filled state, to stand out.
}


def _inside(box, x, y):
    x0, y0, x1, y1 = box
    return x0 <= x < x1 and y0 <= y < y1


def in_button(x, y):
    return _inside(BUTTON_HIT, x, y)


# ---- helpers -----------------------------------------------------------------

@lru_cache(maxsize=None)
def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()     # no DejaVu (some laptops): ugly but works


def canvas():
    image = Image.new("1", (W, H), WHITE)
    return image, ImageDraw.Draw(image)


def fit(draw, text, fnt, width):
    """Shorten text with an ellipsis until it fits into width pixels."""
    if draw.textlength(text, font=fnt) <= width:
        return text
    while text and draw.textlength(text + "…", font=fnt) > width:
        text = text[:-1]
    return text + "…"


def text_right(draw, right, y, text, fnt):
    draw.text((right - draw.textlength(text, font=fnt), y), text, font=fnt, fill=BLACK)


def text_center(draw, cx, y, text, fnt, fill=BLACK):
    draw.text((cx - draw.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=fill)


def gigabytes(n):
    return f"{n / 2**30:.1f}"


# ---- STATS screen ------------------------------------------------------------

def bar(draw, y, label, percent):
    """percent None: not known yet (the first 30 s); an empty bar and "-"."""
    draw.text((4, y), label, font=font(14, True), fill=BLACK)
    x0, x1 = 48, 196
    draw.rectangle((x0, y + 2, x1, y + 16), outline=BLACK)
    if percent is None:
        text_right(draw, W - 4, y, "-", font(14, True))
        return
    inner = x1 - x0 - 4
    filled = round(inner * min(max(percent, 0.0), 100.0) / 100.0)
    if filled > 0:
        draw.rectangle((x0 + 2, y + 4, x0 + 2 + filled - 1, y + 14), fill=BLACK)
    text_right(draw, W - 4, y, f"{percent:.0f}%", font(14, True))


def render_stats(s):
    image, d = canvas()
    clock = time.strftime("%H:%M")
    d.text((4, 2), fit(d, s.host, font(13, True), 180), font=font(13, True), fill=BLACK)
    text_right(d, W - 4, 3, clock, font(12))
    d.line((0, 21, W, 21), fill=BLACK)

    bar(d, 30, "CPU", s.cpu)
    mem_pct = 100.0 * s.mem_used / s.mem_total if s.mem_total else 0.0
    bar(d, 58, "MEM", mem_pct)
    d.text((48, 78), f"{gigabytes(s.mem_used)} / {gigabytes(s.mem_total)} GB", font=font(11), fill=BLACK)

    text_right(d, W - 4, H - 15, "tap for mesh  >", font(10))
    return image


# ---- MESH screen -------------------------------------------------------------

def wifi_qr_payload(ssid, psk=None):
    """The text phones understand as "join this Wi-Fi". \\ ; , : " are escaped."""
    esc = lambda s: re.sub(r'([\\;,:"])', r"\\\1", s)
    if psk:
        return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(psk)};;"
    return f"WIFI:T:nopass;S:{esc(ssid)};;"


def draw_qr(image, payload, box):
    """Draw a QR code as large as fits into box, in whole pixels per module
    (a module is one square of the code; half pixels would blur it)."""
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    x0, y0, x1, y1 = box
    scale = min(x1 - x0, y1 - y0) // (n + 2)       # +2: one white module on each side
    if scale < 1:
        return False
    size = n * scale
    left = x0 + (x1 - x0 - size) // 2
    top = y0 + (y1 - y0 - size) // 2
    d = ImageDraw.Draw(image)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                d.rectangle((left + c * scale, top + r * scale,
                             left + (c + 1) * scale - 1, top + (r + 1) * scale - 1), fill=BLACK)
    return True


def draw_button(d, mode):
    small, big = BUTTON_TEXT[mode]
    filled = mode.startswith("confirm")
    x0, y0, x1, y1 = BUTTON_BOX
    cx = (x0 + x1) / 2
    ink = WHITE if filled else BLACK
    d.rounded_rectangle(BUTTON_BOX, radius=4, outline=BLACK, fill=BLACK if filled else WHITE)
    text_center(d, cx, y0 + 6, small, font(10), fill=ink)
    text_center(d, cx, y0 + 21, big, font(12, True), fill=ink)


def render_mesh(m, button):
    image, d = canvas()
    show_qr = bool(m.ap_ssid)
    tw = TEXT_W_WITH_QR if show_qr else BUTTON_HIT[0] - 4

    clock = time.strftime("%H:%M")
    clock_w = d.textlength(clock, font=font(10)) + 4
    head = f"MESH {STATE_LABEL.get(m.state, m.state)}"
    d.text((2, 1), fit(d, head, font(13, True), tw - clock_w), font=font(13, True), fill=BLACK)
    text_right(d, tw, 4, clock, font(10))
    if m.ip:
        d.text((2, 17), fit(d, m.ip, font(11), tw), font=font(11), fill=BLACK)

    y = 34
    for iface in m.ifaces:
        d.text((2, y), fit(d, f"{iface.name}  {iface.job}", font(12, True), tw), font=font(12, True), fill=BLACK)
        if iface.ssid:
            d.text((8, y + 14), fit(d, iface.ssid, font(11), tw - 6), font=font(11), fill=BLACK)
        if iface.detail:
            d.text((8, y + 27), fit(d, iface.detail, font(11), tw - 6), font=font(11), fill=BLACK)
        y += 44

    if show_qr and not draw_qr(image, wifi_qr_payload(m.ap_ssid), QR_BOX):
        text_center(d, (QR_BOX[0] + QR_BOX[2]) / 2, H // 2, "SSID too long", font(10))
    draw_button(d, button)
    return image


# ---- NET screen --------------------------------------------------------------
# | name + job 4.. | received 84.. | sent 168.. |
COL_RX, COL_TX = 84, 168


def human_bytes(n):
    """1234567 -> "1.2 MB". Decimal units, as network tools use."""
    units = ("B", "kB", "MB", "GB", "TB")
    i = 0
    while n >= 1000 and i < len(units) - 1:
        n /= 1000
        i += 1
    if i == 0:
        return f"{n:.0f} B"
    return f"{n:.1f} {units[i]}" if n < 100 else f"{n:.0f} {units[i]}"


def render_net(n):
    image, d = canvas()
    clock = time.strftime("%H:%M")
    head = f"NET   mesh {STATE_LABEL.get(n.mesh_state, n.mesh_state)}"
    d.text((4, 2), fit(d, head, font(13, True), 190), font=font(13, True), fill=BLACK)
    text_right(d, W - 4, 3, clock, font(12))
    d.line((0, 21, W, 21), fill=BLACK)
    d.text((COL_RX, 24), "received", font=font(10), fill=BLACK)
    d.text((COL_TX, 24), "sent", font=font(10), fill=BLACK)

    y = 40
    for row in n.rows:
        d.text((4, y), row.name, font=font(13, True), fill=BLACK)
        d.text((4, y + 17), fit(d, row.detail, font(10), COL_RX - 10), font=font(10), fill=BLACK)
        for x, rate, total in ((COL_RX, row.rx_rate, row.rx_total), (COL_TX, row.tx_rate, row.tx_total)):
            speed = "-" if rate is None else human_bytes(rate) + "/s"
            d.text((x, y), fit(d, speed, font(12, True), COL_TX - COL_RX - 4), font=font(12, True), fill=BLACK)
            if total is not None:
                d.text((x, y + 17), "total " + human_bytes(total), font=font(10), fill=BLACK)
        y += 34

    text_right(d, W - 4, H - 15, "tap for power  >", font(10))
    return image


# ---- POWER screen ------------------------------------------------------------
# | POWER  host                                    clock |
# |   [    REBOOT    ]          [   POWER OFF    ]       |
# |                                tap here for stats  > |
# Each button reacts across its half of the screen, out to the side edge (a
# finger cannot overshoot an edge). The halves meet in the middle and never
# overlap. Above and below the buttons a tap goes to the next screen.
POWER_BOX = {"reboot": (10, 32, 117, 80), "poweroff": (133, 32, 240, 80)}    # drawn: 108 x 49 px
POWER_HIT = {"reboot": (0, 26, 125, 90), "poweroff": (125, 26, W, 90)}       # ~24 x 12 mm each
POWER_LABEL = {"reboot": "REBOOT", "poweroff": "POWER OFF"}
POWER_BUSY = {"reboot": "rebooting…", "poweroff": "powering off…"}


def power_button(x, y):
    """The POWER button whose touch area holds (x, y): "reboot", "poweroff" or None."""
    return next((name for name, box in POWER_HIT.items() if _inside(box, x, y)), None)


def draw_power_button(d, name, mode):
    """mode: "idle"; "confirm" (armed: filled, the next tap on it acts);
    "busy" (systemctl is on it)."""
    box = POWER_BOX[name]
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    filled = mode == "confirm"
    ink = WHITE if filled else BLACK
    d.rounded_rectangle(box, radius=6, outline=BLACK, width=2, fill=BLACK if filled else WHITE)
    if mode == "confirm":
        text_center(d, cx, y0 + 8, "tap again to", font(10), fill=ink)
        text_center(d, cx, y0 + 23, POWER_LABEL[name], font(14, True), fill=ink)
    elif mode == "busy":
        text_center(d, cx, y0 + 16, POWER_BUSY[name], font(12, True), fill=ink)
    else:
        text_center(d, cx, y0 + 16, POWER_LABEL[name], font(14, True), fill=ink)


def short_version(version):
    """4d29278 for a full commit hash, keeping deploy.sh's "-dirty"."""
    base = version.removesuffix("-dirty")
    return base[:7] + version[len(base):]


def render_power(host, reboot, poweroff, version=""):
    """reboot, poweroff: the mode of each button (see draw_power_button).
    version: the installed commit (version.txt); not shown when empty."""
    image, d = canvas()
    clock = time.strftime("%H:%M")
    d.text((4, 2), fit(d, f"POWER   {host}", font(13, True), 190), font=font(13, True), fill=BLACK)
    text_right(d, W - 4, 3, clock, font(12))
    d.line((0, 21, W, 21), fill=BLACK)
    draw_power_button(d, "reboot", reboot)
    draw_power_button(d, "poweroff", poweroff)
    if version:
        d.text((4, H - 15), "ver " + short_version(version), font=font(10), fill=BLACK)
    text_right(d, W - 4, H - 15, "tap here for stats  >", font(10))
    return image


# ---- shown when the app stops ------------------------------------------------

def render_stopped(powered_off=False):
    """E-paper keeps its last image without power. Without this screen, numbers
    from a dead program would look current.

    powered_off: the whole Pi is shutting down (reboot or poweroff), not only
    this program (systemctl stop, Ctrl+C)."""
    image, d = canvas()
    text_center(d, W / 2, 40, "powered off" if powered_off else "display stopped", font(14, True))
    text_center(d, W / 2, 62, time.strftime("%Y-%m-%d %H:%M"), font(12))
    return image
