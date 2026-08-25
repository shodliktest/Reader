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
import struct
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
    """Read a ZIP entry even when its stored CRC is wrong.

    zipfile.ZipFile.read() validates CRC and raises BadZipFile.  For DOCX
    media this is unnecessarily fatal: if the compressed bytes themselves
    are intact, the image can still be decompressed perfectly.  This fallback
    reads the local file header directly, skips the CRC check, decompresses
    the raw bytes, and then validates the resulting payload separately.
    """
    try:
        return zf.read(info), False
    except (zipfile.BadZipFile, RuntimeError, EOFError) as first_error:
        pass

    # Directly parse the local-file header.  This deliberately avoids
    # ZipExtFile's CRC check.  It supports the compression methods used by
    # normal DOCX archives: STORE (0) and DEFLATE (8).
    try:
        fp = zf.fp
        if fp is None:
            raise first_error
        fp.seek(info.header_offset)
        header = fp.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise first_error
        _, _, flag_bits, compress_type, _, _, _, comp_size, uncomp_size, name_len, extra_len = struct.unpack(
            "<4s5H3I2H", header
        )
        if flag_bits & 0x08 and (comp_size == 0 or uncomp_size == 0):
            # Data-descriptor archives may not expose sizes in the local
            # header.  info has the authoritative values in the central dir.
            comp_size = info.compress_size
            uncomp_size = info.file_size
        fp.seek(name_len + extra_len, 1)
        raw = fp.read(comp_size)
        if len(raw) != comp_size:
            raise first_error

        if compress_type == zipfile.ZIP_STORED:
            data = raw
        elif compress_type == zipfile.ZIP_DEFLATED:
            data = zlib.decompress(raw, -15)
        else:
            # Let Python's normal ZIP reader handle other methods if possible.
            tolerant_info = deepcopy(info)
            tolerant_info.CRC = None
            with zf.open(tolerant_info, "r") as ext:
                data = ext.read()

        # We intentionally do NOT reject a CRC mismatch here.  Report it to
        # callers through the second return value.
        crc_bad = (zlib.crc32(data) & 0xffffffff) != (info.CRC & 0xffffffff)
        if uncomp_size and len(data) != uncomp_size:
            raise first_error
        return data, crc_bad
    except Exception:
        raise first_error


def extract_docx_media(input_source, supported_extensions=None):
    """DOCX ichidan rasmlarni CRC xatosiga chidamli tarzda o'qiydi.

    input_source: fayl yo'li, bytes yoki file-like object.
    Natija: [{'name': ..., 'data': ..., 'repaired': bool, 'error': str|None}, ...]

    Muhim: bitta media fayl (masalan image14.jpeg) CRC xatosi bersa,
    qolgan rasmlar o'qilishda davom etadi. CRC noto'g'ri bo'lsa, ZIP
    ma'lumot oqimi baribir o'qiladi va faqat markaziy katalogdagi CRC
    tekshiruvi chetlab o'tiladi.
    """
    exts = {e.lower() for e in (supported_extensions or SUPPORTED_EXTENSIONS)}
    source = input_source
    close_source = False
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
        close_source = True
    try:
        with zipfile.ZipFile(source, "r") as zf:
            results = []
            for info in zf.infolist():
                lower = info.filename.lower()
                ext = os.path.splitext(lower)[1]
                if not lower.startswith("word/media/") or ext not in exts:
                    continue
                try:
                    data = zf.read(info)
                    results.append({"name": info.filename, "data": data, "repaired": False, "error": None})
                    continue
                except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as normal_error:
                    # Faqat shu entry uchun CRC tekshiruvini o'chiramiz.
                    try:
                        tolerant_info = deepcopy(info)
                        tolerant_info.CRC = None
                        with zf.open(tolerant_info, "r") as fp:
                            data = fp.read()
                        # ZIP oqimi o'qilganini alohida tekshiramiz; CRC noto'g'ri
                        # bo'lsa bu qiymat markaziy katalogdagi xato CRC ekanini
                        # ko'rsatadi. Haqiqiy fayl ma'lumoti bo'lsa preview davom etadi.
                        actual_crc = zlib.crc32(data) & 0xffffffff
                        results.append({
                            "name": info.filename,
                            "data": data,
                            "repaired": info.CRC != actual_crc,
                            "error": None,
                        })
                    except Exception as tolerant_error:
                        results.append({
                            "name": info.filename,
                            "data": None,
                            "repaired": False,
                            "error": str(tolerant_error) or str(normal_error),
                        })
            return results
    finally:
        if close_source:
            source.close()


def first_valid_docx_preview(input_source, settings=None, max_size=(1000, 700)):
    """DOCX ichidagi birinchi sog'lom/tiklanadigan rasmga preview yaratadi.

    Bitta buzilgan rasm butun preview'ni to'xtatmaydi. Qaysi rasm tanlangani
    va nechta rasm tiklanganini metadata sifatida qaytaradi.
    """
    media = extract_docx_media(input_source)
    repaired = sum(1 for x in media if x.get("repaired"))
    errors = [x for x in media if x.get("data") is None]
    for item in media:
        if not item.get("data"):
            continue
        try:
            return preview_bytes(item["data"], settings, max_size=max_size), {
                "name": item["name"],
                "total": len(media),
                "repaired": repaired,
                "failed": len(errors),
                "errors": errors,
            }
        except Exception as exc:
            item["error"] = str(exc)
            continue
    return None, {
        "name": None,
        "total": len(media),
        "repaired": repaired,
        "failed": len(errors),
        "errors": errors,
    }


def watermark_docx(input_path, output_path, settings=None):
    s = normalize_settings(settings)
    if not s["enabled"]:
        with open(input_path, "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        watermark_docx.last_report = {"changed": 0, "repaired": 0, "failed_media": []}
        return output_path, 0

    changed = 0
    repaired = 0
    failed_media = []

    # Muhim: ZIP yozish vaqtida har bir entryni alohida o'qiymiz. Bitta
    # image14.jpeg CRC xatosi boshqa rasmlarni to'xtatmaydi.
    with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            lower = item.filename.lower()
            ext = os.path.splitext(lower)[1]
            try:
                data, tolerant = _read_zip_entry_tolerant(zin, item)
            except Exception:
                if lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS:
                    failed_media.append(item.filename)
                    # Rasmni tashlab yubormasdan, imkon qolsachi original
                    # entryni yozishga urinamiz. CRC buzilgan entryni zipfile
                    # bilan qayta o'qib bo'lmasa, uni saqlab qolishning iloji
                    # yo'q; qolgan DOCX strukturasi esa saqlanadi.
                    continue
                raise

            if tolerant:
                repaired += 1

            if lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS:
                try:
                    data = watermark_image_bytes(data, s)
                    changed += 1
                except Exception:
                    # Rasm o'qilmasa, imkon qadar original bytesni saqlaymiz.
                    # Bu bitta rasm sabab butun Word'ni yiqitmaslik uchun.
                    pass

            # Agar tolerant o'qilgan entry bo'lsa, eski (noto'g'ri) CRC ni
            # qayta yozmaslik kerak. ZIP yozuvchisi yangi CRC ni o'zi hisoblaydi.
            write_info = deepcopy(item)
            if tolerant:
                write_info.CRC = None
                write_info.file_size = len(data)
                write_info.compress_size = 0
            zout.writestr(write_info, data)

    watermark_docx.last_report = {
        "changed": changed,
        "repaired": repaired,
        "failed_media": failed_media,
    }
    return output_path, changed


def preview_bytes(image_bytes, settings=None, max_size=(1000, 700)):
    """Create a preview from raw image bytes.

    This function is intentionally independent of ZIP/CRC handling.  DOCX
    callers should first extract the media with read_zip_media_tolerant().
    """
    img = Image.open(io.BytesIO(image_bytes)); img.load()
    wm = apply_watermark(img, settings)
    wm.thumbnail(max_size, Image.Resampling.LANCZOS)
    out = io.BytesIO(); wm.save(out, format="PNG")
    return out.getvalue()


def extract_docx_media_tolerant(docx_bytes):
    """Return all readable DOCX images, including images with bad CRC metadata.

    Returns a list of dicts: {name, data, crc_bad}.  One broken media entry
    never prevents the remaining images from being returned.
    """
    result = []
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
        for info in zf.infolist():
            lower = info.filename.lower()
            ext = os.path.splitext(lower)[1]
            if not lower.startswith("word/media/") or ext not in SUPPORTED_EXTENSIONS:
                continue
            try:
                data, crc_bad = _read_zip_entry_tolerant(zf, info)
                # PIL validation: if the bytes are really damaged, skip only
                # that image instead of killing the entire preview.
                img = Image.open(io.BytesIO(data))
                img.verify()
                result.append({"name": info.filename, "data": data, "crc_bad": bool(crc_bad)})
            except Exception as exc:
                result.append({"name": info.filename, "data": None, "crc_bad": True, "error": str(exc)})
    return result
