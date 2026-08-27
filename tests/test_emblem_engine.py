import io
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emblem import detect_emblem, replace_emblem_on_image


def make_logo(size=120, bg=(0, 0, 0, 255)):
    im = Image.new("RGBA", (size, size), bg)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8, 30, 75, 105), radius=18, fill=(20, 145, 225, 255))
    d.polygon([(75, 35), (112, 10), (95, 55), (115, 75), (95, 92), (77, 68)], fill=(20, 145, 225, 255))
    d.text((18, 68), "T", fill="white")
    return im


def png_bytes(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def test_scale_and_backgrounds():
    sample = make_logo()
    target = Image.new("RGB", (700, 700), "white")
    logo = sample.convert("RGB").resize((70, 70), Image.Resampling.LANCZOS)
    target.paste(logo, (500, 580))
    d = detect_emblem(png_bytes(sample), png_bytes(target), min_confidence=.40)
    assert d.found, d.reason


def test_blur_and_small_logo():
    sample = make_logo()
    target = Image.new("RGB", (500, 500), "#fdfdfd")
    logo = sample.convert("RGB").resize((28, 28), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.6))
    target.paste(logo, (420, 450))
    d = detect_emblem(png_bytes(sample), png_bytes(target), min_confidence=.35)
    assert d.found, d.reason


def test_replacement():
    sample = make_logo()
    target = Image.new("RGB", (500, 500), "white")
    target.paste(sample.convert("RGB").resize((70,70)), (400,400))
    new = Image.new("RGBA", (90,90), (0,0,0,0))
    ImageDraw.Draw(new).ellipse((5,5,85,85), fill=(230,60,60,255))
    out, d = replace_emblem_on_image(png_bytes(target), png_bytes(sample), png_bytes(new), min_confidence=.35)
    assert d.found and out
    Image.open(io.BytesIO(out)).verify()


if __name__ == "__main__":
    test_scale_and_backgrounds(); test_blur_and_small_logo(); test_replacement(); print("V11 emblem tests: OK")


def test_tiny_black_matte_on_white_page():
    """Regression: the user's black-matte emblem must match a ~20px logo on a white page."""
    sample = make_logo(size=500, bg=(0, 0, 0, 255))
    target = Image.new("RGB", (150, 150), "white")
    logo = sample.convert("RGB").resize((20, 20), Image.Resampling.LANCZOS)
    target.paste(logo, (34, 110))
    d = detect_emblem(png_bytes(sample), png_bytes(target), min_confidence=.35)
    assert d.found, d.reason
    assert 16 <= d.w <= 25 and 16 <= d.h <= 25, d


def test_tiny_replacement_stays_in_detected_box():
    sample = make_logo(size=500, bg=(0, 0, 0, 255))
    target = Image.new("RGB", (150, 150), "white")
    logo = sample.convert("RGB").resize((20, 20), Image.Resampling.LANCZOS)
    target.paste(logo, (34, 110))
    new = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(new).ellipse((25, 25, 375, 375), fill=(230, 60, 60, 255))
    out, d = replace_emblem_on_image(png_bytes(target), png_bytes(sample), png_bytes(new),
                                     scale_percent=0, min_confidence=.35,
                                     stretch=False, cleanup_padding=2)
    assert d.found and out
    assert Image.open(io.BytesIO(out)).size == target.size
