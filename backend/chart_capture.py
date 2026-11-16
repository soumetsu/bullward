"""Validate the bounded, tagged PNG emitted by the dashboard chart export."""

from base64 import b64decode
from binascii import Error as Base64Error, crc32
from struct import unpack
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DATA_PREFIX = "data:image/png;base64,"
TAG = b"invest-snapshot\0"
MAX_DATA_URL = 3_000_000


def decode_capture(data_url):
    """Return PNG bytes and the embedded snapshot ID, or raise ValueError."""
    if not data_url.startswith(DATA_PREFIX) or len(data_url) > MAX_DATA_URL:
        raise ValueError("Use a chart PNG data URL within the size limit.")
    try:
        png = b64decode(data_url[len(DATA_PREFIX):], validate=True)
        if not png.startswith(PNG_SIGNATURE):
            raise ValueError
        offset = len(PNG_SIGNATURE)
        compressed = bytearray()
        snapshot_id = None
        stride = height = 0
        while offset + 12 <= len(png):
            length, kind = unpack(">I4s", png[offset:offset + 8])
            end = offset + length + 12
            if end > len(png):
                raise ValueError
            data = png[offset + 8:end - 4]
            if crc32(kind + data) != int.from_bytes(png[end - 4:end], "big"):
                raise ValueError
            if offset == len(PNG_SIGNATURE):
                if kind != b"IHDR" or length != 13:
                    raise ValueError
                width, height, depth, color, compression, filters, interlace = unpack(">IIBBBBB", data)
                if (not 1 <= width <= 2048 or not 1 <= height <= 1536 or depth != 8 or
                        color not in (2, 6) or any((compression, filters, interlace))):
                    raise ValueError
                stride = 1 + width * (3 if color == 2 else 4)
            elif kind == b"tEXt" and data.startswith(TAG):
                if snapshot_id is not None:
                    raise ValueError
                snapshot_id = data[len(TAG):].decode("ascii")
            elif kind == b"IDAT":
                compressed.extend(data)
            elif kind == b"IEND":
                if length or end != len(png):
                    raise ValueError
                decoder = zlib.decompressobj()
                pixels = decoder.decompress(compressed, stride * height + 1)
                if (not decoder.eof or decoder.unused_data or len(pixels) != stride * height
                        or any(byte > 4 for byte in pixels[::stride])):
                    raise ValueError
                return png, snapshot_id
            elif kind != b"PLTE" and kind[0] & 32 == 0:
                raise ValueError
            offset = end
    except (Base64Error, ValueError, zlib.error):
        pass
    raise ValueError("The PNG is unreadable. Recapture the chart and try again.")
