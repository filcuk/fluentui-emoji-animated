#!/usr/bin/env python
"""Convert all APNG assets in assets/apng to GIFs in assets/gif.

The script uses Pillow color quantization with dithering. If compiled with
libimagequant support, it will use that method for higher-quality GIF palettes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

try:
    from PIL import Image, ImageSequence
except ImportError as exc:
    raise SystemExit(
        "Pillow is required to run this script. Install it with: python -m pip install pillow"
    ) from exc


def _choose_marker_color(frames: list[Image.Image]) -> tuple[int, int, int]:
    used = set()
    total = [0, 0, 0]
    count = 0
    semitrans_total = [0, 0, 0]
    semitrans_count = 0
    low_alpha_total = [0, 0, 0]
    low_alpha_weight = 0
    neighbor_total = [0, 0, 0]
    neighbor_count = 0

    for frame in frames:
        rgb_frame = frame.convert("RGB")
        alpha = frame.getchannel("A")
        width, height = frame.size

        for y in range(height):
            for x in range(width):
                pixel = rgb_frame.getpixel((x, y))
                used.add(pixel)
                total[0] += pixel[0]
                total[1] += pixel[1]
                total[2] += pixel[2]
                count += 1

                alpha_value = alpha.getpixel((x, y))
                if 0 < alpha_value < 255:
                    semitrans_total[0] += pixel[0]
                    semitrans_total[1] += pixel[1]
                    semitrans_total[2] += pixel[2]
                    semitrans_count += 1

                    if alpha_value <= 64:
                        weight = 256 - alpha_value
                        low_alpha_total[0] += pixel[0] * weight
                        low_alpha_total[1] += pixel[1] * weight
                        low_alpha_total[2] += pixel[2] * weight
                        low_alpha_weight += weight

                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            nx, ny = x + dx, y + dy
                            if dx == 0 and dy == 0:
                                continue
                            if 0 <= nx < width and 0 <= ny < height:
                                if alpha.getpixel((nx, ny)) == 255:
                                    neighbor_pixel = rgb_frame.getpixel((nx, ny))
                                    neighbor_total[0] += neighbor_pixel[0]
                                    neighbor_total[1] += neighbor_pixel[1]
                                    neighbor_total[2] += neighbor_pixel[2]
                                    neighbor_count += 1

    if neighbor_count:
        target = (
            neighbor_total[0] // neighbor_count,
            neighbor_total[1] // neighbor_count,
            neighbor_total[2] // neighbor_count,
        )
    elif low_alpha_weight:
        target = (
            low_alpha_total[0] // low_alpha_weight,
            low_alpha_total[1] // low_alpha_weight,
            low_alpha_total[2] // low_alpha_weight,
        )
    elif semitrans_count:
        target = (
            semitrans_total[0] // semitrans_count,
            semitrans_total[1] // semitrans_count,
            semitrans_total[2] // semitrans_count,
        )
    else:
        target = (
            total[0] // count,
            total[1] // count,
            total[2] // count,
        )

    # Prefer the computed `target` (neighbor/low-alpha average) so edges
    # blend into transparency. Avoid forcing pure white/black which creates
    # visible outlines on contrasting backgrounds. If `target` is already
    # unused, we'll use it; otherwise the radius search below finds a nearby
    # unused color.
    candidates = [target]
    unused_candidates = [candidate for candidate in candidates if candidate not in used]
    if unused_candidates:
        return min(
            unused_candidates,
            key=lambda candidate: sum((candidate[i] - target[i]) ** 2 for i in range(3)),
        )

    for radius in range(1, 64):
        for dr in range(-radius, radius + 1):
            for dg in range(-radius, radius + 1):
                for db in range(-radius, radius + 1):
                    if max(abs(dr), abs(dg), abs(db)) != radius:
                        continue
                    candidate = (
                        min(max(target[0] + dr, 0), 255),
                        min(max(target[1] + dg, 0), 255),
                        min(max(target[2] + db, 0), 255),
                    )
                    if candidate not in used:
                        return candidate

    # As a final fallback, return the computed target so marker matches edge
    # colors and blends into transparency instead of using hard white.
    return target


def _apply_transparent_marker(frame_rgba: Image.Image, marker: tuple[int, int, int]) -> Image.Image:
    alpha = frame_rgba.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return frame_rgba.convert("RGB")

    rgb_frame = frame_rgba.convert("RGB")
    width, height = frame_rgba.size
    rgb_pixels = rgb_frame.load()
    alpha_pixels = alpha.load()

    output = Image.new("RGB", frame_rgba.size)
    output_pixels = output.load()

    for y in range(height):
        for x in range(width):
            a = alpha_pixels[x, y]
            if a == 255:
                output_pixels[x, y] = rgb_pixels[x, y]
                continue
            if a == 0:
                output_pixels[x, y] = marker
                continue

            neighbor_total = [0, 0, 0]
            neighbor_weight = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbor_alpha = alpha_pixels[nx, ny]
                        if neighbor_alpha > 0:
                            neighbor_rgb = rgb_pixels[nx, ny]
                            neighbor_total[0] += neighbor_rgb[0] * neighbor_alpha
                            neighbor_total[1] += neighbor_rgb[1] * neighbor_alpha
                            neighbor_total[2] += neighbor_rgb[2] * neighbor_alpha
                            neighbor_weight += neighbor_alpha

            if neighbor_weight:
                neighbor_bg = (
                    neighbor_total[0] // neighbor_weight,
                    neighbor_total[1] // neighbor_weight,
                    neighbor_total[2] // neighbor_weight,
                )
            else:
                neighbor_bg = marker

            source_rgb = rgb_pixels[x, y]
            output_pixels[x, y] = (
                (source_rgb[0] * a + neighbor_bg[0] * (255 - a)) // 255,
                (source_rgb[1] * a + neighbor_bg[1] * (255 - a)) // 255,
                (source_rgb[2] * a + neighbor_bg[2] * (255 - a)) // 255,
            )

    return output


def _build_palette_image(frames: list[Image.Image], markers: list[tuple[int, int, int]]) -> Image.Image:
    visible_frames = []
    for frame_rgba, marker in zip(frames, markers):
        alpha = frame_rgba.getchannel("A")
        bbox = alpha.getbbox()
        if bbox is None:
            continue
        cropped = frame_rgba.crop(bbox)
        visible_frames.append(_apply_transparent_marker(cropped, marker))

    if not visible_frames:
        visible_frames = [_apply_transparent_marker(frames[0], markers[0])]

    combined_width = sum(frame.width for frame in visible_frames)
    combined_height = max(frame.height for frame in visible_frames)
    combined = Image.new("RGB", (combined_width, combined_height))
    x_offset = 0
    for frame_rgb in visible_frames:
        combined.paste(frame_rgb, (x_offset, 0))
        x_offset += frame_rgb.width

    quantize_method = getattr(Image, "MAXCOVERAGE", None)
    if quantize_method is not None:
        return combined.quantize(colors=256, method=quantize_method, kmeans=1)

    quantize_method = getattr(Image, "FASTOCTREE", None)
    if quantize_method is not None:
        return combined.quantize(colors=256, method=quantize_method, kmeans=1)

    return combined.convert("P", palette=Image.ADAPTIVE, colors=256)


def _quantize_frame(frame_rgba: Image.Image, palette_image: Image.Image | None, marker: tuple[int, int, int], use_dither: bool) -> Image.Image:
    alpha = frame_rgba.getchannel("A")
    has_transparency = alpha.getextrema()[0] < 255
    dither_flag = Image.FLOYDSTEINBERG if use_dither else Image.NONE
    frame_rgb = _apply_transparent_marker(frame_rgba, marker)
    if palette_image is None:
        palette_image = _build_palette_image(frame_rgba, marker)
    quantize_method = (
        getattr(Image, "MAXCOVERAGE", None)
        or getattr(Image, "FASTOCTREE", None)
        or getattr(Image, "MEDIANCUT", 0)
    )
    frame_p = frame_rgb.quantize(palette=palette_image, dither=dither_flag, method=quantize_method)

    palette = palette_image.getpalette()
    marker_index = next(
        (i for i in range(256) if tuple(palette[3 * i : 3 * i + 3]) == marker),
        None,
    )

    if marker_index is not None:
        frame_pixels = frame_p.load()
        width, height = frame_rgba.size
        palette_colors = [tuple(palette[3 * i : 3 * i + 3]) for i in range(256)]
        for y in range(height):
            for x in range(width):
                if frame_pixels[x, y] != marker_index:
                    continue
                if alpha.getpixel((x, y)) == 0:
                    continue

                target_rgb = frame_rgb.getpixel((x, y))
                nearest_index = None
                nearest_dist = float("inf")
                for i, color in enumerate(palette_colors):
                    if i == marker_index:
                        continue
                    dr = color[0] - target_rgb[0]
                    dg = color[1] - target_rgb[1]
                    db = color[2] - target_rgb[2]
                    distance = dr * dr + dg * dg + db * db
                    if distance < nearest_dist:
                        nearest_dist = distance
                        nearest_index = i
                if nearest_index is not None:
                    frame_pixels[x, y] = nearest_index

    if has_transparency:
        used_indices = {index for count, index in (frame_p.getcolors(maxcolors=256) or [])}
        transparency_index = next((i for i in range(256) if i not in used_indices), None)
        if transparency_index is None:
            transparency_index = marker_index if marker_index is not None else 0

        pixels = frame_p.load()
        alpha_pixels = alpha.load()
        width, height = frame_rgba.size
        for y in range(height):
            for x in range(width):
                if alpha_pixels[x, y] == 0:
                    pixels[x, y] = transparency_index

        frame_p.info["transparency"] = transparency_index

    return frame_p


def convert_apng_to_gif(source: Path, target: Path, use_dither: bool = True) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        raw_frames = []
        durations = []
        for frame in ImageSequence.Iterator(img):
            raw_frames.append(frame.convert("RGBA"))
            durations.append(frame.info.get("duration", 100))

        if not raw_frames:
            raise ValueError(f"No frames extracted from {source}")

        markers = [_choose_marker_color([frame]) for frame in raw_frames]
        palette_image = _build_palette_image(raw_frames, markers=markers)
        frames = [
            _quantize_frame(frame_rgba, palette_image, marker, use_dither)
            for frame_rgba, marker in zip(raw_frames, markers)
        ]

        output_path = target.with_suffix(".gif")
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
            optimize=False,
        )

        return output_path


def _convert_single(source: Path, target: Path, use_dither: bool) -> str:
    output_path = convert_apng_to_gif(source, target, use_dither=use_dither)
    return output_path.name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert animations from assets/apng to assets/gif."
    )
    parser.add_argument(
        "--input-dir",
        default="assets/apng",
        help="Source directory containing PNG files.",
    )
    parser.add_argument(
        "--output-dir",
        default="assets/gif",
        help="Destination directory for generated GIF files.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip conversion for files whose GIF already exists in the destination.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel worker processes to use for conversion. Default: 1.",
    )
    parser.add_argument(
        "--no-dither",
        action="store_true",
        help="Disable Floyd-Steinberg dithering for GIF color quantization.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    sources = sorted(input_dir.glob("*.png"))

    if not sources:
        raise SystemExit("No PNG files found to convert.")

    entries = []
    skipped = 0
    for source in sources:
        target = output_dir / source.name
        target = target.with_suffix(".gif")
        if args.skip_existing and target.exists():
            skipped += 1
            continue
        entries.append((source, target, not args.no_dither))

    converted = 0
    if args.workers == 1:
        for source, target, use_dither in entries:
            output_name = _convert_single(source, target, use_dither)
            print(f"Converted: {source.name} -> {output_name}")
            converted += 1
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(_convert_single, source, target, use_dither): source
                for source, target, use_dither in entries
            }
            for future in as_completed(futures):
                future.result()
                converted += 1

    print(f"Done. Converted {converted} file(s) to {output_dir}.")
    if args.skip_existing:
        print(f"Skipped {skipped} existing file(s).")


if __name__ == "__main__":
    main()
