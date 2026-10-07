"""Генератор иконки приложения в формате ICO.

Без внешних зависимостей: ICO собирается вручную из 32-битных DIB.
Так иконка остаётся воспроизводимой из исходника, а не бинарным файлом
неизвестного происхождения в репозитории.

Запуск: python scripts/make_icon.py [путь]
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

# Трей использует 16 px, окно и Alt+Tab — до 64 px. 256 px слой в
# несжатом DIB занял бы 270 КБ без пользы для этого приложения.
SIZES = (16, 20, 24, 32, 48, 64)
SUPERSAMPLE = 4

# Фон: индиго -> фиолетовый по диагонали. Снежинка белая: Yukishiro — белый снег.
BG_FROM = (0x4C, 0x5F, 0xE8)
BG_TO = (0x8A, 0x45, 0xDC)
FG = (0xFF, 0xFF, 0xFF)

CORNER_RATIO = 0.22
SPOKES = 6

# Детализация зависит от размера, как в настоящих наборах иконок:
# в трее иконка 16 px, и тонкие ответвления там превратились бы в кашу.
SMALL_DETAIL = {
    "spoke_length": 0.38,
    "spoke_width": 0.052,
    "branches": (),
    "branch_length": 0.0,
    "branch_width": 0.0,
    "hub_radius": 0.075,
}
LARGE_DETAIL = {
    "spoke_length": 0.40,
    "spoke_width": 0.034,
    "branches": (0.50, 0.76),
    "branch_length": 0.15,
    "branch_width": 0.026,
    "hub_radius": 0.058,
}
DETAIL_THRESHOLD = 32


def detail_for(size: int) -> dict:
    return LARGE_DETAIL if size >= DETAIL_THRESHOLD else SMALL_DETAIL


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _rounded_rect_contains(x: float, y: float, size: float, radius: float) -> bool:
    if x < 0 or y < 0 or x > size or y > size:
        return False
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 + 1e-9


def _segment_distance(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _snowflake_contains(x: float, y: float, size: float, detail: dict) -> bool:
    """Шесть лучей снежинки; ответвления — только на крупных размерах."""

    centre = size / 2
    px, py = x - centre, y - centre

    if math.hypot(px, py) <= detail["hub_radius"] * size:
        return True

    spoke_length = detail["spoke_length"] * size
    spoke_width = detail["spoke_width"] * size
    branch_length = detail["branch_length"] * size
    branch_width = detail["branch_width"] * size

    for index in range(SPOKES):
        # Снежинка гексагональная: один луч строго вверх, остальные через 60°.
        angle = math.pi / 2 + 2 * math.pi * index / SPOKES
        ux, uy = math.cos(angle), math.sin(angle)
        tip_x, tip_y = ux * spoke_length, uy * spoke_length
        if _segment_distance(px, py, 0.0, 0.0, tip_x, tip_y) <= spoke_width:
            return True

        for position in detail["branches"]:
            base_x = ux * spoke_length * position
            base_y = uy * spoke_length * position
            for sign in (1, -1):
                branch_angle = angle + sign * math.pi / 3
                end_x = base_x + math.cos(branch_angle) * branch_length
                end_y = base_y + math.sin(branch_angle) * branch_length
                if (
                    _segment_distance(px, py, base_x, base_y, end_x, end_y)
                    <= branch_width
                ):
                    return True
    return False


def render_bgra(size: int) -> bytes:
    """Отрисовать иконку в BGRA со сглаживанием через суперсэмплинг."""

    radius = CORNER_RATIO * size
    detail = detail_for(size)
    rows: list[bytes] = []

    for y in range(size):
        row = bytearray()
        for x in range(size):
            inside = 0
            snow = 0
            samples = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    px = x + (sx + 0.5) / SUPERSAMPLE
                    py = y + (sy + 0.5) / SUPERSAMPLE
                    samples += 1
                    if not _rounded_rect_contains(px, py, size, radius):
                        continue
                    inside += 1
                    if _snowflake_contains(px, py, size, detail):
                        snow += 1

            alpha = inside / samples
            if alpha == 0:
                row += bytes((0, 0, 0, 0))
                continue

            t = ((x / max(size - 1, 1)) + (y / max(size - 1, 1))) / 2
            background = tuple(_lerp(BG_FROM[i], BG_TO[i], t) for i in range(3))
            snow_ratio = snow / inside if inside else 0.0
            colour = tuple(
                _lerp(background[i], FG[i], snow_ratio) for i in range(3)
            )
            row += bytes(
                (
                    int(round(colour[2])),
                    int(round(colour[1])),
                    int(round(colour[0])),
                    int(round(alpha * 255)),
                )
            )
        rows.append(bytes(row))

    # DIB хранит строки снизу вверх.
    return b"".join(reversed(rows))


def build_dib(size: int) -> bytes:
    pixels = render_bgra(size)
    # BITMAPINFOHEADER: высота удвоена под XOR-изображение и AND-маску.
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(pixels),
        0,
        0,
        0,
        0,
    )
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)
    return header + pixels + mask


def build_ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    images = [(size, build_dib(size)) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in images:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    return bytes(directory) + b"".join(data for _size, data in images)


def preview(size: int = 32) -> str:
    """Текстовый предпросмотр: иконку видно, не открывая файл."""

    shades = " .:-=+*#%@"
    pixels = render_bgra(size)
    lines: list[str] = []
    for y in range(size):
        row_offset = (size - 1 - y) * size * 4
        line = []
        for x in range(size):
            b, g, r, a = pixels[row_offset + x * 4 : row_offset + x * 4 + 4]
            if a < 32:
                line.append(" ")
                continue
            luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
            line.append(shades[min(len(shades) - 1, int(luminance * len(shades)))])
        lines.append("".join(line))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--preview":
        print(preview(32))
        return 0
    target = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / "assets" / "sayuri.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = build_ico()
    target.write_bytes(data)
    print(f"иконка записана: {target} ({len(data)} байт, размеры {', '.join(map(str, SIZES))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
