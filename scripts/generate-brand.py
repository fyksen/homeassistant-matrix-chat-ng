"""Generate our own simple lock icon without a graphics-library dependency."""

import struct
import zlib
from pathlib import Path


def icon() -> bytes:
    pixels = []
    for y in range(64):
        row = bytearray()
        for x in range(64):
            lock_body = 17 <= x <= 46 and 28 <= y <= 48
            shackle = 22 <= x <= 41 and 13 <= y <= 30 and not (27 <= x <= 36 and 18 <= y <= 27)
            keyhole = 30 <= x <= 33 and 35 <= y <= 42
            color = (255, 255, 255) if (lock_body or shackle) and not keyhole else (25, 105, 150)
            row.extend(color)
        pixels.append(b"\0" + row)

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(pixels)))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for destination in (
        root / "custom_components/matrix_ng/brand/icon.png",
        root / "apps/matrix_ng_bridge/icon.png",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(icon())
