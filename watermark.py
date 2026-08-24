"""
watermark.py
------------
Word fayli ichidagi RASMLARNING O'ZIGA watermark qo'yish engine'i.

Muhim:
- DOCX sahifa layout/XML'iga tegmaydi.
- Word ichidagi image relationship'lar va rasmning joylashuvi saqlanadi.
- Original fayl hech qachon ustidan yozilmaydi; yangi nusxa yaratiladi.
- Watermark faqat image pixel'lariga qo'shiladi.
"""

import io
import os
import zipfile
import tempfile
from copy import deepcopy

from PIL import Image, ImageDraw, ImageFont

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_SETTINGS = {
    "enabled": True,
    "text": "© QuizMaker Bot",
    "style": "diagonal",          # diagonal | center | pattern
    "opacity": 18,                 # 5..60
    "angle": -35,
    "size": 30,
    "color": "#FFFFFF",
    "bold": True,
    "pattern_gap": 180,
}


def normalize_settings(settings=None):
    out = dict(DEFAULT_SETTINGS)
    if settings:
        out.update({k: v for k, v in settings.items() if v is not None})

    out["enabled"] = bool(out.get("enabled", True))
    out["text"] = str(out.get("text", "© QuizMaker Bot")).strip() or "© QuizMaker Bot"
    out["style"] = str(out.get("style", "diagonal")).lower()
    if out["style"] not in {"diagonal", "center", "pattern"}:
        out["style"] = "diagonal"
    out["opacity"] = max(1, min(90, int(out.get("opacity", 18))))
    out["angle"] = max(-180, min(180, int(out.get("angle", -35))))
    out["size"] = max(8, min(180, int(out.get("size", 30))))
    out["pattern_gap"] = max(60, min(500, int(out.get("pattern_gap", 180))))
    out["color"] = _normalize_color(out.get("color", "#FFFFFF"))
    out["bold"] = bool(out.get("bold", True))
    return out


def _normalize_color(value):
    value = str(value or "#FFFFFF").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        return "#FFFFFF"
    return "#" + value.upper()


def _hex_rgb(hex_color):
    c = _normalize_color(hex_color).lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(size, bold=False):
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

    # Windows/local fallback paths
    candidates += [
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arialbd.ttf" if bold else "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "segoeuib.ttf" if bold else "segoeui.ttf"),
    ]

    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _text_layer(size, text, settings):
    """Transparent layer containing one watermark text."""
    font = _font(settings["size"], settings["bold"])
    dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=0)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    pad = max(8, settings["size"] // 3)
    layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rgb = _hex_rgb(settings["color"])
    alpha = int(255 * settings["opacity"] / 100)
    draw.text((pad, pad), text, font=font, fill=(*rgb, alpha))
    return layer


def apply_watermark(image, settings=None):
    """PIL Image -> yangi PIL Image. Original image object o'zgarmaydi."""
    s = normalize_settings(settings)
    if not s["enabled"]:
        return image.copy()

    base = image.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    if s["style"] == "center":
        layer = _text_layer((w, h), s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        x = (w - layer.width) // 2
        y = (h - layer.height) // 2
        overlay.alpha_composite(layer, (x, y))

    elif s["style"] == "pattern":
        layer = _text_layer((w, h), s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        step_x = max(60, s["pattern_gap"] + layer.width // 3)
        step_y = max(60, s["pattern_gap"] // 2 + layer.height)
        for row, y in enumerate(range(-layer.height, h + layer.height, step_y)):
            offset = 0 if row % 2 == 0 else step_x // 2
            for x in range(-layer.width + offset, w + layer.width, step_x):
                overlay.alpha_composite(layer, (x, y))

    else:  # diagonal
        layer = _text_layer((w, h), s["text"], s)
        layer = layer.rotate(s["angle"], expand=True, resample=Image.Resampling.BICUBIC)
        x = (w - layer.width) // 2
        y = (h - layer.height) // 2
        overlay.alpha_composite(layer, (x, y))

    return Image.alpha_composite(base, overlay)


def watermark_image_bytes(image_bytes, settings=None):
    """Bytes -> watermarkli bytes. Rasmning pixel o'lchami saqlanadi."""
    src = Image.open(io.BytesIO(image_bytes))
    fmt = (src.format or "PNG").upper()
    mode = src.mode
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
        # PNG transparency va o'lchami saqlanadi.
        result.save(out, format="PNG", optimize=False)
    return out.getvalue()


def watermark_docx(input_path, output_path, settings=None):
    """Existing DOCX ichidagi word/media/* rasmlariga watermark qo'yadi.

    ZIP entry'lar va document XML o'z holicha qoladi; faqat rasm bytes'lari
    almashtiriladi. input_path hech qachon yozilmaydi.
    """
    s = normalize_settings(settings)
    if not s["enabled"]:
        # Shunchaki xavfsiz nusxa
        with open(input_path, "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        return output_path, 0

    changed = 0
    with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            lower = item.filename.lower()
            ext = os.path.splitext(lower)[1]
            if lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS:
                try:
                    new_data = watermark_image_bytes(data, s)
                    data = new_data
                    changed += 1
                except Exception:
                    # Buzuq/qo'llab bo'lmaydigan rasm bo'lsa, original bytes qoladi.
                    pass
            # metadata va fayl tartibini imkon qadar saqlaymiz
            zout.writestr(item, data)

    return output_path, changed


def preview_bytes(image_bytes, settings=None, max_size=(900, 650)):
    """Preview uchun PNG bytes qaytaradi."""
    img = Image.open(io.BytesIO(image_bytes))
    wm = apply_watermark(img, settings)
    wm.thumbnail(max_size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    wm.save(out, format="PNG")
    return out.getvalue()
