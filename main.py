import math
import os
import random
import struct
import time
import zlib


WIDTH = 1024
HEIGHT = 1024
PIXELS = bytearray(WIDTH * HEIGHT * 3)


def set_px(x, y, color, alpha=1.0):
    if not (0 <= x < WIDTH and 0 <= y < HEIGHT) or alpha <= 0:
        return
    alpha = min(alpha, 1.0)
    offset = (y * WIDTH + x) * 3
    inv = 1.0 - alpha
    PIXELS[offset] = int(PIXELS[offset] * inv + color[0] * alpha)
    PIXELS[offset + 1] = int(PIXELS[offset + 1] * inv + color[1] * alpha)
    PIXELS[offset + 2] = int(PIXELS[offset + 2] * inv + color[2] * alpha)


def draw(shape_test, bbox, color, alpha=1.0):
    left, top, right, bottom = bbox
    left = max(0, int(math.floor(left)))
    top = max(0, int(math.floor(top)))
    right = min(WIDTH, int(math.ceil(right)))
    bottom = min(HEIGHT, int(math.ceil(bottom)))
    samples = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    for y in range(top, bottom):
        for x in range(left, right):
            covered = sum(shape_test(x + sx, y + sy) for sx, sy in samples)
            if covered:
                set_px(x, y, color, alpha * covered * 0.25)


def ellipse(cx, cy, rx, ry, rot_deg=0):
    angle = math.radians(rot_deg)
    cosine, sine = math.cos(angle), math.sin(angle)

    def test(x, y):
        dx, dy = x - cx, y - cy
        u = dx * cosine + dy * sine
        v = -dx * sine + dy * cosine
        return (u / rx) ** 2 + (v / ry) ** 2 <= 1

    extent_x = math.sqrt((rx * cosine) ** 2 + (ry * sine) ** 2)
    extent_y = math.sqrt((rx * sine) ** 2 + (ry * cosine) ** 2)
    return test, (cx - extent_x, cy - extent_y, cx + extent_x, cy + extent_y)


def polygon(points):
    def test(x, y):
        inside = False
        previous = points[-1]
        for current in points:
            x1, y1 = previous
            x2, y2 = current
            if (y1 > y) != (y2 > y):
                crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < crossing_x:
                    inside = not inside
            previous = current
        return inside

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return test, (min(xs), min(ys), max(xs), max(ys))


def capsule(x1, y1, x2, y2, radius):
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy

    def test(x, y):
        if length_squared:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_squared))
        else:
            t = 0.0
        nearest_x, nearest_y = x1 + t * dx, y1 + t * dy
        return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius * radius

    return test, (
        min(x1, x2) - radius,
        min(y1, y2) - radius,
        max(x1, x2) + radius,
        max(y1, y2) + radius,
    )


def ring(cx, cy, radius, width):
    outer_squared = (radius + width / 2) ** 2
    inner_squared = max(0, radius - width / 2) ** 2

    def test(x, y):
        distance_squared = (x - cx) ** 2 + (y - cy) ** 2
        return inner_squared <= distance_squared <= outer_squared

    extent = radius + width / 2
    return test, (cx - extent, cy - extent, cx + extent, cy + extent)


def vertical_gradient(stops):
    for y in range(HEIGHT):
        for index in range(len(stops) - 1):
            y0, color0 = stops[index]
            y1, color1 = stops[index + 1]
            if y <= y1:
                amount = max(0.0, min(1.0, (y - y0) / (y1 - y0)))
                color = tuple(int(a + (b - a) * amount) for a, b in zip(color0, color1))
                row = bytes(color) * WIDTH
                start = y * WIDTH * 3
                PIXELS[start : start + WIDTH * 3] = row
                break


def radial_gradient(cx, cy, radius, center_color, edge_color):
    left, top = max(0, int(cx - radius)), max(0, int(cy - radius))
    right, bottom = min(WIDTH, int(cx + radius + 1)), min(HEIGHT, int(cy + radius + 1))
    radius_squared = radius * radius
    for y in range(top, bottom):
        for x in range(left, right):
            distance_squared = (x - cx) ** 2 + (y - cy) ** 2
            if distance_squared <= radius_squared:
                fraction = math.sqrt(distance_squared / radius_squared)
                color = tuple(
                    int(a + (b - a) * fraction) for a, b in zip(center_color, edge_color)
                )
                set_px(x, y, color)


def radial_glow(cx, cy, radius, color, strength, additive=False):
    left, top = max(0, int(cx - radius)), max(0, int(cy - radius))
    right, bottom = min(WIDTH, int(cx + radius + 1)), min(HEIGHT, int(cy + radius + 1))
    radius_squared = radius * radius
    for y in range(top, bottom):
        for x in range(left, right):
            distance_squared = (x - cx) ** 2 + (y - cy) ** 2
            if distance_squared < radius_squared:
                falloff = (1.0 - math.sqrt(distance_squared / radius_squared)) ** 2
                amount = strength * falloff
                if additive:
                    offset = (y * WIDTH + x) * 3
                    for channel in range(3):
                        PIXELS[offset + channel] = min(
                            255, int(PIXELS[offset + channel] + color[channel] * amount)
                        )
                else:
                    set_px(x, y, color, amount)


def transform(x, y, center_x, center_y, cosine, sine):
    return center_x + x * cosine - y * sine, center_y + x * sine + y * cosine


def draw_local_capsule(center, cosine, sine, start, end, radius, color, alpha=1.0):
    x1, y1 = transform(*start, *center, cosine, sine)
    x2, y2 = transform(*end, *center, cosine, sine)
    draw(*capsule(x1, y1, x2, y2, radius), color, alpha)


def write_png(path):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(PIXELS[y * WIDTH * 3 : (y + 1) * WIDTH * 3])
        for y in range(HEIGHT)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as output:
        output.write(png)


def main():
    vertical_gradient(
        [
            (0, (8, 6, 28)),
            (700, (30, 12, 62)),
            (1023, (12, 8, 36)),
        ]
    )

    ring_x, ring_y = 512, 330
    radial_glow(ring_x, ring_y, 560, (255, 92, 92), 0.30, additive=True)

    sky_stars = random.Random(11)
    for _ in range(180):
        x, y = sky_stars.randrange(WIDTH), sky_stars.randrange(720)
        size = sky_stars.choice((1, 2, 3))
        color = sky_stars.choice(((255, 255, 255), (160, 240, 255)))
        alpha = sky_stars.uniform(0.35, 1.0)
        draw(*ellipse(x, y, size / 2, size / 2), color, alpha)
    for _ in range(6):
        x, y = sky_stars.randrange(35, WIDTH - 35), sky_stars.randrange(35, 680)
        color = (180, 240, 255)
        draw(*capsule(x - 4, y, x + 4, y, 0.8), color, 0.8)
        draw(*capsule(x, y - 4, x, y + 4, 0.8), color, 0.8)

    draw(*ellipse(ring_x, ring_y, 212, 212), (24, 18, 58))
    radial_gradient(ring_x, ring_y, 212, (24, 18, 58), (52, 26, 92))
    for _ in range(40):
        x = sky_stars.uniform(ring_x - 204, ring_x + 204)
        y = sky_stars.uniform(ring_y - 204, ring_y + 204)
        if (x - ring_x) ** 2 + (y - ring_y) ** 2 < 204**2:
            size = sky_stars.choice((1, 2))
            draw(
                *ellipse(x, y, size / 2, size / 2),
                sky_stars.choice(((220, 235, 255), (130, 225, 255))),
                sky_stars.uniform(0.4, 0.9),
            )
    draw(*ellipse(590, 250, 26, 26), (255, 170, 120))
    draw(*ring(590, 250, 40, 4), (255, 195, 160), 0.8)

    draw(*ring(ring_x, ring_y, 235, 46), (255, 92, 92))
    draw(*ring(ring_x, ring_y, 235 - 28, 10), (120, 245, 255))
    draw(*ring(ring_x, ring_y, 235 + 26, 6), (255, 150, 120))

    wave_random = random.Random(23)
    layers = []
    horizons = (690, 760, 840, 930)
    amplitudes = (18, 26, 36, 48)
    wave_colors = ((44, 24, 92), (34, 18, 74), (24, 12, 54), (15, 8, 36))
    for horizon, amplitude, color in zip(horizons, amplitudes, wave_colors):
        points = [(0, horizon + wave_random.randint(-amplitude, amplitude))]
        for x in range(64, WIDTH, 64):
            points.append((x, horizon + wave_random.randint(-amplitude, amplitude)))
        points.append((WIDTH - 1, horizon + wave_random.randint(-amplitude, amplitude)))
        skyline = points[:]
        shape, _ = polygon(skyline + [(WIDTH - 1, HEIGHT), (0, HEIGHT)])
        draw(shape, (0, min(y for _, y in skyline), WIDTH, HEIGHT), color)
        layers.append(skyline)

    for skyline, color in (
        (layers[2], (120, 80, 200)),
        (layers[3], (92, 240, 255)),
    ):
        for (x1, y1), (x2, y2) in zip(skyline, skyline[1:]):
            draw(*capsule(x1, y1, x2, y2, 2), color, 0.5 if color[0] == 120 else 0.35)
    radial_glow(512, 760, 380, (255, 92, 92), 0.12)

    center = (352, 470)
    angle = math.radians(-18)
    cosine, sine = math.cos(angle), math.sin(angle)
    for step in range(1, 6):
        t0, t1 = (step - 1) / 5, step / 5
        control = (-90, 38)
        endpoint = (-160, 120)
        start = tuple(
            (1 - t0) ** 2 * a + 2 * (1 - t0) * t0 * b + t0**2 * c
            for a, b, c in zip((-22, 4), control, endpoint)
        )
        end = tuple(
            (1 - t1) ** 2 * a + 2 * (1 - t1) * t1 * b + t1**2 * c
            for a, b, c in zip((-22, 4), control, endpoint)
        )
        draw_local_capsule(center, cosine, sine, start, end, 2, (200, 200, 220), 0.7)

    helmet_x, helmet_y = transform(0, -38, *center, cosine, sine)
    body_start = transform(0, -10, *center, cosine, sine)
    body_end = transform(0, 44, *center, cosine, sine)
    draw(*ellipse(helmet_x, helmet_y, 24, 24), (120, 130, 190), 0.75)
    draw(*capsule(*body_start, *body_end, 17), (120, 130, 190), 0.75)

    backpack_x, backpack_y = transform(-22, 4, *center, cosine, sine)
    draw(*ellipse(backpack_x, backpack_y, 10, 24, -18), (24, 20, 44))
    draw_local_capsule(center, cosine, sine, (-6, -2), (-34, 22), 8, (16, 14, 34))
    draw_local_capsule(center, cosine, sine, (8, 0), (34, -16), 8, (16, 14, 34))
    draw_local_capsule(center, cosine, sine, (-4, 42), (-18, 70), 8, (16, 14, 34))
    draw_local_capsule(center, cosine, sine, (6, 42), (24, 64), 8, (16, 14, 34))
    draw(*capsule(*body_start, *body_end, 15.64), (16, 14, 34))
    draw(*ellipse(helmet_x, helmet_y, 22.08, 22.08), (16, 14, 34))

    visor_x, visor_y = transform(3, -38, *center, cosine, sine)
    draw(*ellipse(visor_x, visor_y, 15, 12, -18), (120, 245, 255))
    highlight_x, highlight_y = transform(-3, -44, *center, cosine, sine)
    draw(*ellipse(highlight_x, highlight_y, 3, 3), (255, 255, 255), 0.95)

    draw(*capsule(760, 120, 850, 84, 2), (255, 255, 255), 0.8)
    radial_glow(850, 84, 28, (160, 240, 255), 0.55)

    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    write_png(os.path.join(output_dir, "entry.png"))


if __name__ == "__main__":
    started = time.perf_counter()
    main()
    print(f"Rendered entry.png in {time.perf_counter() - started:.2f}s")
