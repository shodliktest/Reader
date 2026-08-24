"""
watermark.py
------------
Word ichidagi RASMLARNING O'ZIGA watermark qo'yish engine'i.
CRC-32 noto'g'ri bo'lgan DOCX media entry'larini imkon qadar tolerant o'qiydi.
"""
import io
import os
import zipfile
import zlib
from copy import deepcopy
from PIL import Image, ImageDraw, ImageFont

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_SETTINGS = {
    "enabled": True,
    "text": "© QuizMaker Bot",
    "style": "diagonal",
    "opacity": 18,
    "angle": -35,
    "size": 30,
    "color": "#FFFFFF",
    "bold": True,
    "font": "sans",
    "pattern_gap": 180,
    "stroke": 0,
}

STYLE_LABELS = {
    "diagonal": "Diagonal",
    "center": "Markaziy",
    "pattern": "Takrorlanuvchi",
    "corner": "Burchak",
    "double": "Ikki diagonal",
    "stamp": "Stamp",
    "outline": "Kontur",
}

FONT_LABELS = {"sans": "Sans", "serif": "Serif", "mono": "Mono"}


def _normalize_color(value):
    value = str(value or "#FFFFFF").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        return "#FFFFFF"
    return "#" + value.upper()


def normalize_settings(settings=None):
    out = dict(DEFAULT_SETTINGS)
    if settings:
        out.update({k: v for k, v in settings.items() if v is not None})
    out["enabled"] = bool(out.get("enabled", True))
    out["text"] = str(out.get("text", DEFAULT_SETTINGS["text"])).strip() or DEFAULT_SETTINGS["text"]
    out["style"] = str(out.get("style", "diagonal")).lower()
    if out["style"] not in STYLE_LABELS:
        out["style"] = "diagonal"
    out["opacity"] = max(1, min(100, int(out.get("opacity", 18))))
    out["angle"] = max(-180, min(180, int(out.get("angle", -35))))
    out["size"] = max(8, min(180, int(out.get("size", 30))))
    out["pattern_gap"] = max(40, min(600, int(out.get("pattern_gap", 180))))
    out["stroke"] = max(0, min(12, int(out.get("stroke", 0))))
    out["color"] = _normalize_color(out.get("color", "#FFFFFF"))
    out["bold"] = bool(out.get("bold", True))
    out["font"] = str(out.get("font", "sans")).lower()
    if out["font"] not in FONT_LABELS:
        out["font"] = "sans"
    return out


def _font(size, bold=False, family="sans"):
    names = {
        "sans": ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"),
        "serif": ("DejaVuSerif-Bold.ttf", "DejaVuSerif.ttf"),
        "mono": ("DejaVuSansMono-Bold.ttf", "DejaVuSansMono.ttf"),
    }
    bold_name, regular_name = names.get(family, names["sans"])
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/{bold_name if bold else regular_name}",
        f"/usr/share/fonts/truetype/liberation2/{'LiberationSerif-Bold.ttf' if family=='serif' and bold else 'LiberationSerif-Regular.ttf' if family=='serif' else 'LiberationMono-Bold.ttf' if family=='mono' and bold else 'LiberationMono-Regular.ttf' if family=='mono' else 'LiberationSans-Bold.ttf' if bold else 'LiberationSans-Regular.ttf'}",
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _text_layer(text, settings, alpha_override=None, outline=False):
    font = _font(settings["size"], settings["bold"], settings["font"])
    dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(dummy)
    stroke = settings["stroke"] if outline else 0
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = max(1, bbox[2]-bbox[0]), max(1, bbox[3]-bbox[1])
    pad = max(12, settings["size"] // 2)
    layer = Image.new("RGBA", (tw + pad*2, th + pad*2), (0,0,0,0))
    draw = ImageDraw.Draw(layer)
    rgb = tuple(int(_normalize_color(settings["color"])[i:i+2], 16) for i in (1,3,5))
    alpha = int(255 * (settings["opacity"] if alpha_override is None else alpha_override) / 100)
    if outline:
        draw.text((pad,pad), text, font=font, fill=(*rgb, alpha), stroke_width=max(1, settings["stroke"]), stroke_fill=(0,0,0,max(30, alpha//2)))
    else:
        draw.text((pad,pad), text, font=font, fill=(*rgb, alpha))
    return layer


def _centered(overlay, layer, w, h, x_shift=0, y_shift=0):
    overlay.alpha_composite(layer, ((w-layer.width)//2+x_shift, (h-layer.height)//2+y_shift))


def apply_watermark(image, settings=None):
    s = normalize_settings(settings)
    base = image.convert("RGBA")
    if not s["enabled"] or not s["text"].strip():
        return base.copy()
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0,0,0,0))
    style = s["style"]

    if style == "center":
        layer = _text_layer(s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, layer, w, h)
    elif style == "corner":
        layer = _text_layer(s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        pad = max(10, s["size"]//2)
        overlay.alpha_composite(layer, (max(0,w-layer.width-pad), max(0,h-layer.height-pad)))
    elif style == "pattern":
        layer = _text_layer(s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        step_x = max(60, s["pattern_gap"] + layer.width//3)
        step_y = max(50, s["pattern_gap"]//2 + layer.height)
        for row, y in enumerate(range(-layer.height, h+layer.height, step_y)):
            offset = 0 if row % 2 == 0 else step_x//2
            for x in range(-layer.width+offset, w+layer.width, step_x):
                overlay.alpha_composite(layer, (x,y))
    elif style == "double":
        layer = _text_layer(s["text"], s, alpha_override=max(1, s["opacity"]-4))
        layer = layer.rotate(-35, expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, layer, w, h, y_shift=-max(20,h//5))
        layer2 = _text_layer(s["text"], s, alpha_override=max(1, s["opacity"]-4))
        layer2 = layer2.rotate(35, expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, layer2, w, h, y_shift=max(20,h//5))
    elif style == "stamp":
        layer = _text_layer(s["text"], s, outline=True)
        pad = max(16, s["size"]//2)
        stamp = Image.new("RGBA", (layer.width+pad*2, layer.height+pad*2), (0,0,0,0))
        sd = ImageDraw.Draw(stamp)
        rgb = tuple(int(_normalize_color(s["color"])[i:i+2],16) for i in (1,3,5))
        a = int(255*s["opacity"]/100)
        sd.rounded_rectangle((2,2,stamp.width-3,stamp.height-3), radius=max(8,s["size"]//3), outline=(*rgb,a), width=max(2,s["stroke"] or 2))
        stamp.alpha_composite(layer, (pad,pad))
        stamp = stamp.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, stamp, w, h)
    elif style == "outline":
        layer = _text_layer(s["text"], s, outline=True)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, layer, w, h)
    else:  # diagonal
        layer = _text_layer(s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        _centered(overlay, layer, w, h)

    return Image.alpha_composite(base, overlay)


def watermark_image_bytes(image_bytes, settings=None):
    src = Image.open(io.BytesIO(image_bytes))
    src.load()
    fmt = (src.format or "PNG").upper()
    result = apply_watermark(src, settings)
    out = io.BytesIO()
    if fmt in {"JPEG", "JPG"}:
        result.convert("RGB").save(out, format="JPEG", quality=95, optimize=False, subsampling=0)
    elif fmt == "WEBP":
        result.save(out, format="WEBP", quality=95, method=4)
    elif fmt == "BMP":
        result.convert("RGB").save(out, format="BMP")
    elif fmt in {"TIFF", "TIF"}:
        result.save(out, format="TIFF")
    else:
        result.save(out, format="PNG", optimize=False)
    return out.getvalue()


def _read_zip_entry_tolerant(zf, info):
    try:
        return zf.read(info), False
    except (zipfile.BadZipFile, RuntimeError, EOFError) as first_error:
        tolerant_info = deepcopy(info)
        tolerant_info.CRC = None
        try:
            with zf.open(tolerant_info, "r") as fp:
                return fp.read(), True
        except Exception:
            raise first_error


def watermark_docx(input_path, output_path, settings=None):
    s = normalize_settings(settings)
    if not s["enabled"]:
        with open(input_path, "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        return output_path, 0
    changed = repaired = 0
    failed_media = []
    with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            lower = item.filename.lower(); ext = os.path.splitext(lower)[1]
            try:
                data, tolerant = _read_zip_entry_tolerant(zin, item)
            except Exception:
                if lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS:
                    failed_media.append(item.filename); continue
                raise
            if tolerant and item.CRC is not None and (zlib.crc32(data)&0xffffffff) != item.CRC:
                repaired += 1
            if lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS:
                try:
                    data = watermark_image_bytes(data, s); changed += 1
                except Exception:
                    pass
            zout.writestr(item, data)
    return output_path, changed


def preview_bytes(image_bytes, settings=None, max_size=(1000, 700)):
    img = Image.open(io.BytesIO(image_bytes)); img.load()
    wm = apply_watermark(img, settings)
    wm.thumbnail(max_size, Image.Resampling.LANCZOS)
    out = io.BytesIO(); wm.save(out, format="PNG")
    return out.getvalue()
