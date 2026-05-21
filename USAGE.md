# Usage

This repository includes a Python script to convert animated PNG assets from `assets/apng/` into GIF files in `assets/gif/`.

## Script

`convert_apng_to_gif.py`

### What it does

- Reads APNG files from a source directory
- Converts each animation frame into GIF format
- Writes GIF files to a destination directory

### Default directories

- Input: `assets/apng`
- Output: `assets/gif`

## Run

From the repository root:

```powershell
.venv\Scripts\python.exe convert_apng_to_gif.py
```

## Custom input/output

```powershell
.venv\Scripts\python.exe convert_apng_to_gif.py --input-dir assets/apng --output-dir assets/gif
```

## Quality options

- The script now builds a single global GIF palette for the full animation to preserve color consistency across frames.
- It uses adaptive palette quantization and Floyd-Steinberg dithering by default.
- When Pillow is built with libimagequant support, the script will use that for higher-quality GIF palettes.
- To disable dithering, add `--no-dither`.

## Parameters

- `--input-dir`: Source directory containing APNG/PNG files. Default: `assets/apng`.
- `--output-dir`: Destination directory for generated GIF files. Default: `assets/gif`.
- `--pattern`: Filename glob pattern used to match source files. Default: `*_animated.png`. Use `"*.png"` to match all PNGs.
- `--convert-static`: When set, also convert static PNGs that match `--pattern` (useful if you want non-animated PNGs converted).
- `--no-dither`: Disable Floyd–Steinberg dithering during quantization. By default the script applies dithering which can reduce visible banding but may increase local error.

## Examples

- Convert the default animated files (default pattern):

```powershell
.venv\Scripts\python.exe convert_apng_to_gif.py
```

- Convert every PNG in `assets/apng` (including static images):

```powershell
.venv\Scripts\python.exe convert_apng_to_gif.py --pattern "*.png" --convert-static
```

- Convert but disable dithering (faster, sometimes lower SSE):

```powershell
.venv\Scripts\python.exe convert_apng_to_gif.py --no-dither
```

## Behavior notes

- Palette generation: the script builds a shared palette from visible (alpha-cropped) pixels across frames to avoid per-frame palette flashing.
- Matte handling: semitransparent edge pixels are composited using local neighbor blending so edges blend smoothly into transparency (avoids hard white/gray outlines).
- GIF transparency: the script selects an unused palette index for transparency and assigns it to fully transparent pixels.
- Requirements: `Pillow` must be installed in the active Python environment.

## Notes

- The script requires `Pillow`.
- It converts matching `*_animated.png` files by default.
