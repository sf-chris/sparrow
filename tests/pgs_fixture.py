"""Write Blu-ray (PGS) picture subtitles for tests: known lines at known times."""

import struct

from PIL import Image, ImageDraw, ImageFont


def _segment(kind, pts, payload):
    ticks = int(round(pts * 90000))
    return b"PG" + struct.pack(">IIBH", ticks, ticks, kind, len(payload)) + payload


def _rle(pixels, width, height):
    out = bytearray()
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        x = 0
        while x < width:
            color = row[x]
            run = 1
            while x + run < width and row[x + run] == color and run < 16383:
                run += 1
            if color == 0:
                out += bytes([0, run]) if run < 64 else bytes([0, 0x40 | (run >> 8), run & 0xFF])
            elif run < 3:
                out += bytes([color]) * run
            else:
                out += bytes([0, 0x80 | run, color]) if run < 64 else bytes([0, 0xC0 | (run >> 8), run & 0xFF, color])
            x += run
        out += bytes([0, 0])
    return bytes(out)


def render(text, size=(1920, 1080)):
    """White text on transparent, as a palette image (0 transparent, 1 white)."""
    font = ImageFont.load_default(size=48)
    lines = text.split("\n")
    width = max(int(font.getlength(line)) for line in lines) + 20
    height = 60 * len(lines) + 10
    image = Image.new("P", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for n, line in enumerate(lines):
        draw.text((10, 5 + 60 * n), line, fill=1, font=font)
    return image


def sup(events, size=(1920, 1080)):
    """PGS bytes for [(start, end, text)]."""
    out = bytearray()
    for number, (start, end, text) in enumerate(events):
        image = render(text, size)
        width, height = image.size
        x, y = (size[0] - width) // 2, size[1] - height - 60
        pcs = struct.pack(">HHBHBBBB", size[0], size[1], 0x10, number * 2, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)
        wds = struct.pack(">BBHHHH", 1, 0, x, y, width, height)
        pds = struct.pack(">BB", 0, 0) + bytes([0, 16, 128, 128, 0, 1, 235, 128, 128, 255])
        data = struct.pack(">HH", width, height) + _rle(list(image.getdata()), width, height)
        ods = struct.pack(">HBB", 0, 0, 0xC0) + len(data).to_bytes(3, "big") + data
        out += _segment(0x16, start, pcs) + _segment(0x17, start, wds) + _segment(0x14, start, pds)
        out += _segment(0x15, start, ods) + _segment(0x80, start, b"")
        clear = struct.pack(">HHBHBBBB", size[0], size[1], 0x10, number * 2 + 1, 0x00, 0, 0, 0)
        out += _segment(0x16, end, clear) + _segment(0x17, end, wds) + _segment(0x80, end, b"")
    return bytes(out)
