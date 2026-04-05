# imageprocessor

Batch image processing CLI tool. Adds padding, a border, and converts images to any format — while preserving filenames.

---

## Folder structure

```
imageprocessor/
├── imageprocessor.py          ← Core tool (edit this to change behaviour)
├── build_imageprocessor.py    ← Run once to produce the .exe
└── README.md
```

---

## Step 1 — Install dependencies

```bash
pip install pillow pyinstaller
```

---

## Step 2 — Build the exe

```bash
python build_imageprocessor.py
```

This produces:
```
dist/
└── imageprocessor.exe    ← Windows
    imageprocessor        ← macOS / Linux
```

You can copy `imageprocessor.exe` anywhere — no Python needed on the target machine.

---

## Step 3 — Run it

```bash
# Process a whole folder, convert to JPEG
imageprocessor.exe ./images/ --format jpeg

# Single file, custom padding and border
imageprocessor.exe photo.png --padding 10 --border 4

# Custom border color, save to specific folder
imageprocessor.exe ./src/ --format webp --border-color "#CC0000" --output ./dist/images

# Add a suffix to output filenames  (photo.png → photo_web.png)
imageprocessor.exe ./images/ --suffix _web
```

---

## All options

| Option           | Default      | Description                              |
|------------------|--------------|------------------------------------------|
| `--format`       | `png`        | Output format: png, jpeg, webp, tiff, bmp |
| `--padding`      | `5`          | White space added on each side (px)      |
| `--border`       | `2`          | Border thickness (px)                    |
| `--border-color` | `#000000`    | Border color (hex)                       |
| `--pad-color`    | `#FFFFFF`    | Padding background color (hex)           |
| `--output`       | `./processed`| Output directory (created if missing)    |
| `--suffix`       | _(none)_     | Optional text added before extension     |

---

## Supported input formats

JPG · PNG · WebP · TIFF · BMP · GIF

---

## Future: merging into dita_toolkit.exe

This tool is designed to later be absorbed into a unified `dita_toolkit.exe` as a subcommand:

```bash
dita_toolkit.exe images ./photos/ --format png
dita_toolkit.exe convert ./docs/ --output ./out
```

The code in `imageprocessor.py` won't need to change — it will simply be imported by the unified launcher.
