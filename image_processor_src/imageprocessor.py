#!/usr/bin/env python3
"""
imageprocessor.exe
==================
Double-click to launch. Opens a local web UI in your browser.
All processing happens locally — no internet required.
"""

import sys
import os
import io
import json
import threading
import webbrowser
import socket
import time
import zipfile
from pathlib import Path

# ── Flask ──────────────────────────────────────────────────────────────────────
try:
    from flask import Flask, request, jsonify, send_file, Response
except ImportError:
    print("Flask not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])
    from flask import Flask, request, jsonify, send_file, Response

# ── Pillow ─────────────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageOps
except ImportError:
    print("Pillow not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageOps


app = Flask(__name__)

FORMAT_MAP = {
    "png":  ("PNG",  ".png"),
    "jpeg": ("JPEG", ".jpg"),
    "jpg":  ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "tiff": ("TIFF", ".tiff"),
    "bmp":  ("BMP",  ".bmp"),
}

def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def process_image_bytes(img_bytes, filename, fmt, padding, border, border_color_hex, pad_color_hex):
    pil_format, ext = FORMAT_MAP[fmt]
    border_rgb = hex_to_rgb(border_color_hex)
    pad_rgb    = hex_to_rgb(pad_color_hex)

    with Image.open(io.BytesIO(img_bytes)) as img:
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")

        # Step 1: Add padding
        padded = Image.new("RGBA", (img.width + padding * 2, img.height + padding * 2), pad_rgb + (255,))
        padded.paste(img, (padding, padding))

        # Step 2: Add border
        bordered = ImageOps.expand(padded, border=border, fill=border_rgb + (255,))

        # Step 3: Flatten for formats that don't support alpha
        if pil_format in ("JPEG", "BMP"):
            final = Image.new("RGB", bordered.size, pad_rgb)
            if bordered.mode == "RGBA":
                final.paste(bordered, mask=bordered.split()[3])
            else:
                final.paste(bordered)
        else:
            final = bordered.convert("RGBA") if pil_format == "PNG" else bordered.convert("RGB")

        buf = io.BytesIO()
        kwargs = {"quality": 95, "subsampling": 0} if pil_format == "JPEG" else {}
        final.save(buf, format=pil_format, **kwargs)
        buf.seek(0)

        stem = Path(filename).stem
        out_name = stem + ext
        return buf.read(), out_name


# ── Embedded HTML UI ───────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Image Processor</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap" rel="stylesheet"/>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c0c0c; --surface: #111111; --surface2: #181818;
    --border: #222222; --accent: #c8ff00; --accent-dim: #1c2200;
    --text: #e2e2e2; --muted: #555555; --warn: #c8a000;
  }
  body { font-family: 'DM Mono', monospace; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }

  header { border-bottom: 1px solid var(--border); padding: 18px 32px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-hex { font-size: 22px; color: var(--accent); }
  .logo-text { font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 800; color: #fff; letter-spacing: 0.04em; }
  .logo-sub { font-size: 10px; color: var(--muted); letter-spacing: 0.12em; margin-top: 2px; }
  .badge { font-size: 10px; color: var(--accent); background: var(--accent-dim); border: 1px solid #2e3d00; border-radius: 20px; padding: 4px 14px; letter-spacing: 0.08em; }

  .layout { display: flex; flex: 1; overflow: hidden; height: calc(100vh - 61px); }

  sidebar {
    width: 260px; min-width: 260px; background: var(--surface);
    border-right: 1px solid var(--border); padding: 24px 20px;
    display: flex; flex-direction: column; gap: 6px; overflow-y: auto;
  }
  .label { font-size: 9px; letter-spacing: 0.15em; color: var(--muted); margin-top: 16px; margin-bottom: 5px; }
  .label:first-child { margin-top: 0; }
  .opt { color: #333; font-size: 8px; text-transform: none; letter-spacing: 0; }

  .fmt-grid { display: flex; flex-wrap: wrap; gap: 6px; }
  .fmt-btn {
    padding: 4px 11px; border-radius: 4px; border: 1px solid var(--border);
    background: var(--surface2); color: #666; font-size: 11px; cursor: pointer;
    letter-spacing: 0.06em; font-family: inherit; transition: all 0.12s;
  }
  .fmt-btn:hover { border-color: #444; color: #999; }
  .fmt-btn.active { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }

  .slider-row { display: flex; align-items: center; gap: 10px; }
  input[type=range] { flex: 1; accent-color: var(--accent); cursor: pointer; }
  .val { font-size: 12px; color: #aaa; min-width: 34px; text-align: right; }

  .color-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
  .color-label { flex: 1; font-size: 11px; color: #666; }
  .swatch { width: 14px; height: 14px; border-radius: 3px; border: 1px solid rgba(255,255,255,0.08); flex-shrink: 0; }
  input[type=color] { width: 28px; height: 22px; border: none; border-radius: 4px; cursor: pointer; background: none; padding: 0; }

  input[type=text] {
    width: 100%; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 4px; color: #ccc; padding: 7px 10px; font-size: 11px; font-family: inherit;
    transition: border-color 0.12s;
  }
  input[type=text]:focus { outline: none; border-color: #444; }

  .stats { margin-top: 20px; padding: 13px 14px; background: var(--surface2); border-radius: 6px; border: 1px solid var(--border); }
  .stat-row { display: flex; justify-content: space-between; font-size: 11px; color: #555; margin-bottom: 7px; }
  .stat-row:last-child { margin-bottom: 0; }
  .stat-row strong { color: #888; }

  main { flex: 1; padding: 24px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

  .drop-zone {
    border: 1.5px dashed #2a2a2a; border-radius: 10px; padding: 38px 24px;
    text-align: center; cursor: pointer; transition: all 0.15s; flex-shrink: 0;
  }
  .drop-zone:hover, .drop-zone.drag { border-color: var(--accent); background: #0f1500; }
  .drop-icon { font-size: 28px; color: #2a2a2a; margin-bottom: 10px; transition: color 0.15s; }
  .drop-zone:hover .drop-icon, .drop-zone.drag .drop-icon { color: var(--accent); }
  .drop-text { font-size: 13px; color: #666; margin-bottom: 4px; }
  .drop-sub { font-size: 10px; color: #3a3a3a; letter-spacing: 0.08em; }
  #file-input { display: none; }

  .run-bar {
    display: flex; align-items: center; gap: 14px; padding: 14px 18px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; flex-shrink: 0;
  }
  .run-btn {
    padding: 10px 30px; background: var(--accent); color: #0c0c0c;
    border: none; border-radius: 6px; font-family: 'Syne', sans-serif;
    font-weight: 800; font-size: 13px; cursor: pointer; letter-spacing: 0.06em;
    transition: all 0.12s; flex-shrink: 0;
  }
  .run-btn:hover:not(:disabled) { background: #d8ff20; transform: translateY(-1px); }
  .run-btn:disabled { background: #1e2a00; color: #3a4800; cursor: not-allowed; }
  .run-info { font-size: 11px; color: var(--muted); flex: 1; line-height: 1.6; }
  .run-info strong { color: #999; }
  .progress-wrap { height: 2px; background: var(--border); border-radius: 2px; overflow: hidden; margin-top: 5px; display: none; }
  .progress-bar { height: 100%; background: var(--accent); width: 0%; transition: width 0.25s; }
  .dl-btn {
    padding: 9px 18px; background: transparent; color: var(--accent);
    border: 1px solid var(--accent); border-radius: 6px; font-family: inherit;
    font-size: 12px; cursor: pointer; letter-spacing: 0.04em; transition: all 0.12s;
    flex-shrink: 0; display: none;
  }
  .dl-btn:hover { background: var(--accent-dim); }
  .dl-btn.show { display: block; }

  .file-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(185px, 1fr)); gap: 14px; }
  .file-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    overflow: hidden; position: relative; transition: border-color 0.2s;
  }
  .file-card.done { border-color: #2e4000; cursor: pointer; }
  .file-card.done:hover { border-color: var(--accent); }
  .file-card.err { border-color: #4a1a00; }
  .card-preview {
    background: var(--surface2); display: flex; justify-content: center;
    align-items: center; padding: 16px; min-height: 120px;
  }
  .card-img-wrap img { display: block; max-width: 110px; max-height: 85px; object-fit: contain; }
  .card-info { padding: 8px 10px 10px; }
  .card-name { font-size: 10px; color: #777; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card-arrow { font-size: 10px; color: #333; margin: 2px 0; }
  .card-out { font-size: 10px; color: #555; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card-status { font-size: 10px; margin-top: 5px; min-height: 14px; }
  .status-ok { color: var(--accent); }
  .status-err { color: #ff6b35; }
  .status-proc { color: var(--warn); animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .rm-btn {
    position: absolute; top: 6px; right: 6px; background: rgba(0,0,0,0.6);
    border: 1px solid #333; color: #555; border-radius: 4px; width: 20px; height: 20px;
    cursor: pointer; font-size: 13px; line-height: 19px; text-align: center; transition: all 0.1s;
  }
  .rm-btn:hover { color: #ccc; border-color: #666; }

  .empty { text-align: center; color: #2a2a2a; font-size: 13px; padding: 40px 0; }

  ::-webkit-scrollbar { width: 5px; }
  ::-webkit-scrollbar-thumb { background: #222; border-radius: 3px; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <span class="logo-hex">⬡</span>
    <div>
      <div class="logo-text">Image Processor</div>
      <div class="logo-sub">PAD · BORDER · CONVERT</div>
    </div>
  </div>
  <div class="badge">● RUNNING LOCALLY</div>
</header>

<div class="layout">
  <sidebar>
    <div class="label">OUTPUT FORMAT</div>
    <div class="fmt-grid" id="fmt-grid"></div>

    <div class="label">PADDING</div>
    <div class="slider-row">
      <input type="range" id="sl-padding" min="0" max="40" value="5"/>
      <span class="val" id="lbl-padding">5px</span>
    </div>
    <div class="color-row">
      <div class="swatch" id="sw-pad" style="background:#ffffff"></div>
      <span class="color-label">Pad color</span>
      <input type="color" id="col-pad" value="#ffffff"/>
    </div>

    <div class="label">BORDER</div>
    <div class="slider-row">
      <input type="range" id="sl-border" min="0" max="20" value="2"/>
      <span class="val" id="lbl-border">2px</span>
    </div>
    <div class="color-row">
      <div class="swatch" id="sw-border" style="background:#000000"></div>
      <span class="color-label">Border color</span>
      <input type="color" id="col-border" value="#000000"/>
    </div>

    <div class="label">SAVE TO <span class="opt">(optional)</span></div>
    <input type="text" id="output-dir" placeholder="default: .\processed"/>

    <div class="label">FILENAME SUFFIX <span class="opt">(optional)</span></div>
    <input type="text" id="suffix" placeholder="e.g. _web"/>

    <div class="stats">
      <div class="stat-row"><span>Canvas growth</span><strong id="st-growth">+14px</strong></div>
      <div class="stat-row"><span>Files queued</span><strong id="st-files">0</strong></div>
      <div class="stat-row"><span>Format</span><strong id="st-fmt">PNG</strong></div>
    </div>
  </sidebar>

  <main>
    <div class="drop-zone" id="drop-zone">
      <input type="file" id="file-input" multiple accept="image/*"/>
      <div class="drop-icon">↑</div>
      <div class="drop-text">Drag & drop images here, or click to browse</div>
      <div class="drop-sub">JPG · PNG · WEBP · TIFF · BMP · GIF</div>
    </div>

    <div class="run-bar">
      <button class="run-btn" id="run-btn" disabled>Run</button>
      <div style="flex:1">
        <div class="run-info" id="run-info">Add images to get started</div>
        <div class="progress-wrap" id="progress-wrap">
          <div class="progress-bar" id="progress-bar"></div>
        </div>
      </div>
      <button class="dl-btn" id="dl-btn">⬇ Download All</button>
    </div>

    <div class="file-grid" id="file-grid"></div>
    <div class="empty" id="empty-msg">No images added yet</div>
  </main>
</div>

<script>
const FORMATS = ["png","jpeg","webp","tiff","bmp"];
let selFmt = "png";
let files = [];
let uid = 0;

// Format buttons
const fmtGrid = document.getElementById("fmt-grid");
FORMATS.forEach(f => {
  const b = document.createElement("button");
  b.className = "fmt-btn" + (f==="png"?" active":"");
  b.textContent = f.toUpperCase();
  b.onclick = () => {
    selFmt = f;
    fmtGrid.querySelectorAll(".fmt-btn").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    refresh();
  };
  fmtGrid.appendChild(b);
});

// Sliders
["padding","border"].forEach(id => {
  const sl = document.getElementById("sl-"+id);
  sl.oninput = () => { document.getElementById("lbl-"+id).textContent = sl.value+"px"; refresh(); };
});

// Color pickers
document.getElementById("col-pad").oninput = e => { document.getElementById("sw-pad").style.background = e.target.value; refresh(); };
document.getElementById("col-border").oninput = e => { document.getElementById("sw-border").style.background = e.target.value; refresh(); };
document.getElementById("output-dir").oninput = updateRunBar;
document.getElementById("suffix").oninput = refresh;

function getSettings() {
  return {
    format: selFmt,
    padding: +document.getElementById("sl-padding").value,
    border: +document.getElementById("sl-border").value,
    border_color: document.getElementById("col-border").value,
    pad_color: document.getElementById("col-pad").value,
    output_dir: document.getElementById("output-dir").value.trim(),
    suffix: document.getElementById("suffix").value.trim(),
  };
}

function getOutName(name) {
  const s = getSettings();
  const ext = s.format==="jpeg"?"jpg":s.format;
  return name.replace(/\.[^.]+$/,"") + (s.suffix||"") + "."+ext;
}

function updateStats() {
  const s = getSettings();
  document.getElementById("st-growth").textContent = "+" + (s.padding*2 + s.border*2) + "px";
  document.getElementById("st-files").textContent = files.length;
  document.getElementById("st-fmt").textContent = selFmt.toUpperCase();
}

function updateRunBar() {
  const btn = document.getElementById("run-btn");
  btn.disabled = files.length === 0;
  const outDir = document.getElementById("output-dir").value.trim() || ".\\processed";
  document.getElementById("run-info").innerHTML =
    files.length === 0
      ? "Add images to get started"
      : `<strong>${files.length} file${files.length!==1?"s":""}</strong> ready &rarr; <strong>${outDir}</strong>`;
}

function refresh() { updateStats(); updateRunBar(); renderGrid(); }

// Drop zone
const dz = document.getElementById("drop-zone");
dz.onclick = () => document.getElementById("file-input").click();
dz.ondragover = e => { e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave = () => dz.classList.remove("drag");
dz.ondrop = e => { e.preventDefault(); dz.classList.remove("drag"); addFiles(Array.from(e.dataTransfer.files).filter(f=>f.type.startsWith("image/"))); };
document.getElementById("file-input").onchange = e => { addFiles(Array.from(e.target.files).filter(f=>f.type.startsWith("image/"))); e.target.value=""; };

function addFiles(newFiles) {
  newFiles.forEach(f => files.push({ file:f, id:uid++, status:"ready", blob:null }));
  refresh();
}

function renderGrid() {
  const grid = document.getElementById("file-grid");
  const empty = document.getElementById("empty-msg");
  empty.style.display = files.length===0 ? "block" : "none";
  grid.innerHTML = "";
  const s = getSettings();

  files.forEach((entry, i) => {
    const outName = getOutName(entry.file.name);
    const url = URL.createObjectURL(entry.file);
    const card = document.createElement("div");
    card.className = "file-card" + (entry.status==="done"?" done":entry.status==="error"?" err":"");

    card.innerHTML = `
      <div class="card-preview">
        <div class="card-img-wrap" style="padding:${s.padding}px;background:${s.pad_color};outline:${s.border}px solid ${s.border_color}">
          <img src="${url}"/>
        </div>
      </div>
      <div class="card-info">
        <div class="card-name" title="${entry.file.name}">${entry.file.name}</div>
        <div class="card-arrow">→</div>
        <div class="card-out" title="${outName}">${outName}</div>
        <div class="card-status">
          ${entry.status==="done" ? '<span class="status-ok">✓ Done — click to download</span>'
          : entry.status==="error" ? '<span class="status-err">✗ Error</span>'
          : entry.status==="processing" ? '<span class="status-proc">⟳ Processing…</span>'
          : ""}
        </div>
      </div>
      <button class="rm-btn" title="Remove">×</button>
    `;

    // Download on click
    if (entry.status==="done" && entry.blob) {
      card.onclick = e => {
        if (e.target.classList.contains("rm-btn")) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(entry.blob);
        a.download = outName; a.click();
      };
    }

    card.querySelector(".rm-btn").onclick = e => {
      e.stopPropagation();
      files.splice(i,1);
      refresh();
      document.getElementById("dl-btn").classList.remove("show");
    };

    grid.appendChild(card);
  });
}

// Run
document.getElementById("run-btn").onclick = async () => {
  if (!files.length) return;
  const runBtn = document.getElementById("run-btn");
  const pw = document.getElementById("progress-wrap");
  const pb = document.getElementById("progress-bar");
  const dlBtn = document.getElementById("dl-btn");

  runBtn.disabled = true;
  runBtn.textContent = "Processing…";
  pw.style.display = "block";
  pb.style.width = "0%";
  dlBtn.classList.remove("show");

  const s = getSettings();
  let done = 0;

  for (let i=0; i<files.length; i++) {
    files[i].status = "processing";
    renderGrid();

    const fd = new FormData();
    fd.append("file", files[i].file);
    fd.append("settings", JSON.stringify(s));

    try {
      const res = await fetch("/process", { method:"POST", body:fd });
      if (!res.ok) throw new Error();
      files[i].blob = await res.blob();
      files[i].status = "done";
    } catch {
      files[i].status = "error";
    }

    done++;
    pb.style.width = (done/files.length*100)+"%";
    renderGrid();
  }

  runBtn.disabled = false;
  runBtn.textContent = "Run";

  const doneCount = files.filter(f=>f.status==="done").length;
  const errCount  = files.filter(f=>f.status==="error").length;
  document.getElementById("run-info").innerHTML =
    `<strong>${doneCount} done</strong>${errCount ? ` · <span style="color:#ff6b35">${errCount} failed</span>` : " · click any image to download"}`;

  if (doneCount > 0) dlBtn.classList.add("show");
};

// Download all as zip
document.getElementById("dl-btn").onclick = async () => {
  const done = files.filter(f=>f.status==="done"&&f.blob);
  if (!done.length) return;

  if (done.length===1) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(done[0].blob);
    a.download = getOutName(done[0].file.name); a.click();
    return;
  }

  const fd = new FormData();
  done.forEach(f => fd.append("files", f.blob, getOutName(f.file.name)));
  const res = await fetch("/zip", { method:"POST", body:fd });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "processed_images.zip"; a.click();
};

refresh();
</script>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML

@app.route("/process", methods=["POST"])
def process():
    f        = request.files.get("file")
    settings = json.loads(request.form.get("settings", "{}"))

    fmt          = settings.get("format", "png")
    padding      = int(settings.get("padding", 5))
    border       = int(settings.get("border", 2))
    border_color = settings.get("border_color", "#000000")
    pad_color    = settings.get("pad_color", "#ffffff")
    output_dir   = settings.get("output_dir", "").strip()
    suffix       = settings.get("suffix", "").strip()

    # Apply suffix before processing so output name is correct
    stem     = Path(f.filename).stem
    suffixed = stem + suffix + Path(f.filename).suffix

    try:
        result_bytes, out_name = process_image_bytes(
            f.read(), suffixed, fmt, padding, border, border_color, pad_color
        )
    except Exception as e:
        return Response(str(e), status=500)

    # Optionally save to disk as well
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / out_name).write_bytes(result_bytes)

    return send_file(
        io.BytesIO(result_bytes),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=out_name,
    )

@app.route("/zip", methods=["POST"])
def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in request.files.getlist("files"):
            zf.writestr(f.filename, f.read())
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="processed_images.zip")


# ── Launch ─────────────────────────────────────────────────────────────────────

def find_free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]

def open_browser(port):
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    port = find_free_port()
    print(f"\n  Image Processor")
    print(f"  {'─'*35}")
    print(f"  Local:   http://127.0.0.1:{port}")
    print(f"  Browser opening automatically…")
    print(f"  Press Ctrl+C to quit\n")
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False)
