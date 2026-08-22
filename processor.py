"""
processor.py
------------
YHQ test skrinshotlarini (yoki shunga o'xshash rangli-quti formatidagi test
rasmlarini) qayta ishlab, har bir rasmdan (savol_matni, [variantlar], togri_index)
ni ajratib beruvchi asosiy mantiq.

Bu modul HECH QANDAY faylga yozmaydi va HECH QANDAY papkani o'zi aylanib
chiqmaydi - faqat tayyor rasm baytlari (yoki PIL Image) kiradi, tayyor
struktura (dict) chiqadi. Shu tarzda uni ham Telegram bot, ham Streamlit,
ham eski buyruq-qatoridagi skript bab-baravar ishlatishi mumkin.

Chaqiruvchi kodlar uchun asosiy funksiya: process_single_image()
"""

import os
import re
import json
import time
import base64
import io
import threading
import platform
import pytesseract
import numpy as np
import cv2
from PIL import Image

try:
    from groq import Groq
except ImportError:
    Groq = None


# --- Tesseract yo'lini avtomatik aniqlash (faqat Windows uchun kerak) ---
if platform.system() == "Windows":
    default_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if os.path.exists(default_path):
        pytesseract.pytesseract.tesseract_cmd = default_path

# OCR tili: o'zbek lotin alifbosi + inglizcha (raqam/lotincha so'zlar uchun)
OCR_LANG = 'uzb+eng'
OCR_CONFIG = '--psm 6'

# --- GROQ API sozlamalari ---
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL = 'openai/gpt-oss-120b'

_groq_client = None
_groq_rate_lock = threading.Lock()
_groq_last_call_time = 0.0
# Groq bepul rejasi 30 RPM (daqiqada 30 so'rov) bilan cheklangan. Bitta
# test-rasmi uchun savol + har bir variant alohida-alohida chaqiriladi,
# shuning uchun ketma-ket bir necha rasm tez yuborilsa, chaqiruvlar bir
# necha soniya ichida limitdan oshib, 429 (Too Many Requests) qaytaradi.
# Bu holatda Groq SDK avtomatik qayta uraynadi, lekin har bir urinish
# push-back kutish vaqtini qo'shadi va umumiy ishlash sekinlashadi.
# Chaqiruvlar orasiga ~1.3 soniyalik minimal oraliq qo'yish (taxminan
# 46 so'rov/daqiqa "shift"i o'rniga, xavfsizlik zaxirasi bilan 30 dan
# pastroq amaliy tezlikda) 429 xatolarining aksariyatini oldindan
# oldini oladi.
_GROQ_MIN_INTERVAL = 1.3


def _groq_throttle():
    global _groq_last_call_time
    with _groq_rate_lock:
        now = time.monotonic()
        wait = _GROQ_MIN_INTERVAL - (now - _groq_last_call_time)
        if wait > 0:
            time.sleep(wait)
        _groq_last_call_time = time.monotonic()


def get_groq_client():
    """Groq klientini bir marta yaratib, keyingi chaqiruvlarda shuni qayta ishlatadi."""
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    if Groq is None:
        return None
    if not GROQ_API_KEY:
        return None
    _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


GROQ_QUESTION_PROMPT = """Sen O'zbek tilidagi haydovchilik test savol matnini OCR xatolaridan
tozalaydigan yordamchisan. Senga Tesseract OCR orqali telefon skrinshotidan o'qilgan, xatolarga
to'la XOM SAVOL MATNI beriladi (variantlarsiz, faqat savolning o'zi).

Vazifang:
1. OCR xatolarini tuzat (harf almashinuvi, ortiqcha bo'sh joy, noto'g'ri belgilar), matnning
   MA'NOSINI o'zgartirma, hech narsa qo'shma yoki ayirma.
2. Matn oxirida yoki boshida ikonka/rasmdan chiqqan mazmunsiz harf-belgi chalajimchalari
   bo'lsa (masalan savol tugagandan keyin qolib ketgan yakka-yolg'iz 1-3 harfli bo'lak),
   ularni olib tashla - lekin savolning haqiqiy oxirgi so'zlarini hech qachon o'chirma.
Faqat tuzatilgan savol matnini qaytar, boshqa hech qanday izoh, kavichalar yoki markdown
belgisi qo'shma."""

GROQ_OPTION_PROMPT = """Sen O'zbek tilidagi haydovchilik test javob variantini OCR xatolaridan
tozalaydigan yordamchisan. Senga Tesseract OCR orqali o'qilgan, xatolarga to'la XOM VARIANT MATNI
beriladi (masalan "F1", "F2 |" kabi variant-belgisi bilan boshlangan bo'lishi mumkin).

Vazifang:
1. Boshidagi variant-belgisini (F1, F2, F3, F4, F, yoki OCR buzib yuborgan shunga o'xshash
   belgi/raqam, undan keyingi | . ) kabi tinish belgilari bilan birga) olib tashla.
2. Qolgan matndagi OCR xatolarini tuzat, lekin MA'NOSINI o'zgartirma.
3. Faqat tozalangan variant matnini qaytar - boshqa hech qanday izoh yoki belgi qo'shma."""


GROQ_BATCH_PROMPT = """Sen O'zbek tilidagi haydovchilik test savoli va uning javob variantlarini
OCR xatolaridan tozalaydigan yordamchisan. Senga Tesseract OCR orqali telefon skrinshotidan
o'qilgan, xatolarga to'la XOM matnlar JSON massiv ko'rinishida beriladi: birinchi element -
savol matni, undan keyingilari - javob variantlari (ba'zilari boshida "F1", "F2" kabi
variant-belgisi bilan boshlangan bo'lishi mumkin).

Vazifang har bir element uchun:
1. OCR xatolarini tuzat (harf almashinuvi, ortiqcha bo'sh joy, noto'g'ri belgilar), matnning
   MA'NOSINI o'zgartirma, hech narsa qo'shma yoki ayirma.
2. Savol matnida (faqat 1-elementda): oxirida yoki boshida ikonka/rasmdan chiqqan mazmunsiz
   harf-belgi chalajimchalari bo'lsa, ularni olib tashla - lekin savolning haqiqiy oxirgi
   so'zlarini hech qachon o'chirma.
3. Variant matnlarida (2-elementdan boshlab): boshidagi variant-belgisini (F1, F2, F3, F4, F,
   yoki OCR buzib yuborgan shunga o'xshash belgi/raqam, undan keyingi | . ) kabi tinish
   belgilari bilan birga) olib tashla.

Natijani FAQAT quyidagi JSON formatida qaytar, boshqa hech qanday izoh yoki matn qo'shma:
{"cleaned": ["<tuzatilgan 1-element>", "<tuzatilgan 2-element>", ...]}
Massiv uzunligi va tartibi kiruvchi massiv bilan AYNAN bir xil bo'lishi shart."""


GROQ_VISION_MODEL = 'qwen/qwen3.6-27b'

GROQ_VISION_BATCH_PROMPT = """Sen O'zbek tilidagi haydovchilik test savoli va uning javob
variantlarini OCR xatolaridan tozalaydigan yordamchisan.

Senga ikki narsa beriladi:
1. Rasm - test savolining skrinshoti (savol matni + rangli quti ichidagi javob variantlari).
2. JSON massiv - Tesseract OCR orqali o'sha rasmdan avtomatik o'qilgan, xatolarga to'la XOM
   matnlar: birinchi element savol matni, undan keyingilari javob variantlari (ba'zilari
   boshida "F1", "F2" kabi variant-belgisi bilan boshlangan bo'lishi mumkin).

Vazifang: RASMGA QARAB, har bir OCR elementini tuzat:
1. Rasmdagi haqiqiy matn bilan solishtirib, OCR xatolarini tuzat (harf almashinuvi, ortiqcha
   bo'sh joy, noto'g'ri belgilar). Matnning MA'NOSINI o'zgartirma, hech narsa qo'shma/ayirma.
2. Savol matnida (1-element): oxiri/boshidagi ikonka-shovqin bo'lsa olib tashla, lekin
   haqiqiy oxirgi so'zlarni hech qachon o'chirma.
3. Variant matnlarida (2-elementdan boshlab): boshidagi variant-belgisini (F1, F2, F3, F4,
   yoki shunga o'xshash) tinish belgilari bilan birga olib tashla.
4. AGAR biror element uchun rasmdagi mos matnni ANIQ o'qib bo'lmasa (juda xira, qisman
   yopilgan, yoki OCR matni bilan rasm butunlay mos kelmasa) - o'sha element uchun hech
   qanday taxminiy yoki uzr-matn YOZMA, o'rniga JSON massivda shu o'rinni aynan `null`
   (JSON null qiymati, matn emas) qilib qo'y.

Natijani FAQAT quyidagi JSON formatida qaytar, boshqa hech qanday izoh yoki matn qo'shma:
{"cleaned": ["<tuzatilgan yoki null>", "<tuzatilgan yoki null>", ...]}
Massiv uzunligi va tartibi kiruvchi massiv bilan AYNAN bir xil bo'lishi shart."""


def _pil_to_data_url(pil_img, max_dim=900):
    """PIL rasmni Groq vizion API kutgan base64 data-URL formatiga o'giradi.
    Token sarfini kamaytirish uchun rasm oldindan max_dim gacha kichraytiriladi."""
    img = pil_img
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/jpeg;base64,{encoded}"


def groq_clean_batch_vision(pil_img, question_text, option_texts):
    """groq_clean_batch bilan bir xil, lekin OCR matnlari bilan birga rasmning
    o'zini ham yuboradi - shunda model xato/tushunarsiz OCR natijasini rasmga
    qarab tuzatishi mumkin. Model biror elementni aniq o'qiy olmasa, o'sha
    o'rinda `null` qaytaradi (o'zbekcha/inglizcha "tushunmadim" gapini emas) -
    bu chaqiruvchi kodda alohida aniqlanadi va log'ga yoziladi, lekin hech
    qachon savol/variant matni sifatida saqlanmaydi. Muvaffaqiyatsiz bo'lsa
    (tarmoq xatosi, JSON buzuq va h.k.) None qaytaradi."""
    client = get_groq_client()
    items = [question_text] + list(option_texts)
    if client is None or not any(t.strip() for t in items):
        return None
    try:
        _groq_throttle()
        data_url = _pil_to_data_url(pil_img)
        response = client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {"role": "system", "content": GROQ_VISION_BATCH_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(items, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        cleaned = parsed.get("cleaned")
        if not isinstance(cleaned, list) or len(cleaned) != len(items):
            print(f"[Groq vizion] natija uzunligi mos kelmadi: {raw[:200]}")
            return None
        result = []
        for idx, c in enumerate(cleaned):
            if c is None:
                label = "savol" if idx == 0 else f"variant #{idx}"
                print(f"[Groq vizion] {label} uchun rasmdan aniq matn o'qib bo'lmadi - xom OCR matni ishlatiladi")
                result.append(None)
            elif isinstance(c, str):
                result.append(c.strip())
            else:
                result.append(None)
        return result
    except Exception as e:
        print(f"[Groq xatosi - vision batch] model={GROQ_VISION_MODEL}: {e}")
        return None


def groq_clean_batch(question_text, option_texts):
    """Savol matni va barcha variant matnlarini BITTA Groq chaqiruvida birga
    tozalaydi (alohida-alohida emas). Bu bitta rasm uchun ~4-5 ta so'rovni
    bitta so'rovga tushirib, bepul rejadagi 30 RPM chegarasiga tez-tez
    urilib qolishning oldini oladi. Muvaffaqiyatsiz bo'lsa (yoki natija soni
    mos kelmasa) None qaytaradi - chaqiruvchi kod bunda xom matnlarga
    qaytishi kerak."""
    client = get_groq_client()
    items = [question_text] + list(option_texts)
    if client is None or not any(t.strip() for t in items):
        return None
    try:
        _groq_throttle()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": GROQ_BATCH_PROMPT},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        cleaned = parsed.get("cleaned")
        if not isinstance(cleaned, list) or len(cleaned) != len(items):
            return None
        return [c.strip() if isinstance(c, str) else "" for c in cleaned]
    except Exception as e:
        print(f"[Groq xatosi - batch] model={GROQ_MODEL}: {e}")
        return None


def _groq_clean_text(text, system_prompt):
    """Berilgan xom matnni Groq API orqali tozalaydi. Muvaffaqiyatsiz bo'lsa None qaytaradi."""
    client = get_groq_client()
    if client is None or not text.strip():
        return None
    try:
        _groq_throttle()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text.strip()},
            ],
            temperature=0,
            max_tokens=500,
        )
        cleaned = response.choices[0].message.content.strip()
        return cleaned if cleaned else None
    except Exception as e:
        print(f"[Groq xatosi] model={GROQ_MODEL}: {e}")
        return None


def groq_clean_question(raw_question_text):
    return _groq_clean_text(raw_question_text, GROQ_QUESTION_PROMPT)


def groq_clean_option(raw_option_text):
    return _groq_clean_text(raw_option_text, GROQ_OPTION_PROMPT)


# Rangli (qizil/yashil) fonli qutilar chegarasini aniqlashda "to'yinganlik"
# darajasi shu qiymatdan katta bo'lsa - rangli quti deb hisoblanadi (0-255).
COLOR_BOX_SATURATION_THRESHOLD = 60
COLOR_BOX_MIN_AREA = 2000


def detect_answer_option_regions(pil_img):
    """Rasmdagi har bir javob-variant qutisining Y-chegarasini (y1, y2, is_green)
    ko'rinishida qaytaradi, yuqoridan-pastga tartiblangan holda."""
    img = np.array(pil_img.convert('RGB'))
    h_img, w_img = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    s = hsv[..., 1]
    h = hsv[..., 0]

    green_mask = ((h > 35) & (h < 85) & (s > COLOR_BOX_SATURATION_THRESHOLD)).astype(np.uint8) * 255
    red_mask = (((h < 12) | (h > 165)) & (s > COLOR_BOX_SATURATION_THRESHOLD)).astype(np.uint8) * 255
    colored_mask = cv2.bitwise_or(green_mask, red_mask)
    kernel = np.ones((5, 5), np.uint8)
    colored_closed = cv2.morphologyEx(colored_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    colored_closed = cv2.morphologyEx(colored_closed, cv2.MORPH_OPEN, kernel, iterations=1)

    regions = []
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(colored_closed, connectivity=8)
    for label in range(1, num_labels):
        x, y, w, ht, area = stats[label]
        if area < COLOR_BOX_MIN_AREA or w < w_img * 0.5 or ht < 60:
            continue
        comp_mask = (labels[y:y + ht, x:x + w] == label)
        green_overlap = np.count_nonzero(green_mask[y:y + ht, x:x + w][comp_mask])
        is_green = green_overlap > area * 0.3
        regions.append([y, y + ht, is_green])

    edges = cv2.Canny(gray, 50, 150)
    edges_dilated = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges_dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidate_boxes = []
    for c in contours:
        x, y, w, ht = cv2.boundingRect(c)
        if (w > w_img * 0.75 and 60 < ht < h_img * 0.5
                and 0.05 * w_img < x < 0.12 * w_img):
            candidate_boxes.append((y, y + ht))

    candidate_boxes.sort(key=lambda b: b[0])
    merged_candidates = []
    for y1, y2 in candidate_boxes:
        if merged_candidates and y1 <= merged_candidates[-1][1] + 20 and abs(y1 - merged_candidates[-1][0]) < 40:
            merged_candidates[-1] = (
                min(merged_candidates[-1][0], y1),
                max(merged_candidates[-1][1], y2),
            )
        else:
            merged_candidates.append((y1, y2))

    def overlaps_existing(y1, y2):
        for ry1, ry2, _ in regions:
            overlap = min(y2, ry2) - max(y1, ry1)
            if overlap > 0.5 * min(y2 - y1, ry2 - ry1):
                return True
        return False

    for y1, y2 in merged_candidates:
        if not overlaps_existing(y1, y2):
            regions.append([y1, y2, False])

    regions.sort(key=lambda r: r[0])
    return regions


def ocr_region(pil_img, y1, y2, lang, config):
    img = np.array(pil_img.convert('RGB'))
    crop = img[max(0, y1):min(img.shape[0], y2), :]
    crop_pil = Image.fromarray(crop)
    text = pytesseract.image_to_string(crop_pil, lang=lang, config=config)
    return text.strip()


def fix_colored_boxes(pil_img):
    """Rangli (yashil/qizil) fonli qutilarni OCR uchun qulay (oq fon/qora matn)
    holga o'tkazadi, qolgan qismni tegmaydi."""
    img = np.array(pil_img.convert('RGB'))
    gray_full = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    saturation = hsv[..., 1]

    mask = (saturation > COLOR_BOX_SATURATION_THRESHOLD).astype(np.uint8) * 255
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    out = gray_full.copy()
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < COLOR_BOX_MIN_AREA:
            continue
        crop = gray_full[y:y + h, x:x + w]
        _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        white_count = np.count_nonzero(bw == 255)
        black_count = np.count_nonzero(bw == 0)
        if white_count < black_count:
            bw = 255 - bw
        out[y:y + h, x:x + w] = bw

    return Image.fromarray(out)


def check_tesseract_available():
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


# --- Rasmning "chinakam foto" qismini avtomatik topish ---
#
# Ko'p test-ilovalari (masalan YHQ testlari) savol rasmini aniq rangli
# (odatda ko'k) ramka bilan o'rab, ramka ichida yuqorida savol matnini oq
# fonda, pastida esa chinakam fotosuratni ko'rsatadi. Bu funksiya avval
# ramkani (eng katta rangli to'rtburchak konturni) topadi, keyin ramka
# ichida qatorlar bo'yicha rang to'yinganligini (saturation) tekshirib,
# oq/matn qismi bilan rangli foto qismini bir-biridan ajratadi.
#
# Natija: (left, top, right, bottom) piksel koordinatalari, yoki hech narsa
# topilmasa None - bunda chaqiruvchi kod butun rasmni ishlatishi kerak.

BORDER_COLOR_RANGES_HSV = [
    # (h_min, h_max, s_min, v_min) - ko'k ramka (eng ko'p uchraydigan holat)
    (95, 125, 90, 90),
    # och-ko'k / moviy variant
    (85, 100, 60, 120),
]


def detect_photo_region(pil_img):
    """Rasmdan rangli ramka bilan o'ralgan "chinakam foto" qismini avtomatik
    topishga harakat qiladi. Muvaffaqiyatli bo'lsa (left, top, right, bottom)
    qaytaradi, aks holda None."""
    try:
        arr = np.array(pil_img.convert('RGB'))
        h_img, w_img = arr.shape[:2]
        if h_img < 50 or w_img < 50:
            return None

        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        hh, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

        best_box = None
        best_area = 0

        for h_min, h_max, s_min, v_min in BORDER_COLOR_RANGES_HSV:
            mask = ((hh > h_min) & (hh < h_max) & (s > s_min) & (v > v_min)).astype(np.uint8) * 255
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x, y, w, ht = cv2.boundingRect(c)
                area = w * ht
                # Ramka deb hisoblanishi uchun: ekranning kamida yarmicha keng,
                # va yetarlicha baland bo'lishi kerak (nozik chiziqlar, tugmalar
                # va h.k. chiqarib tashlanadi)
                if w < w_img * 0.5 or ht < 100:
                    continue
                if area > best_area:
                    best_area = area
                    best_box = (x, y, w, ht)

        if best_box is None:
            return None

        x, y, w, ht = best_box
        pad = max(4, int(min(w, ht) * 0.01))
        inner_x0 = x + pad
        inner_y0 = y + pad
        inner_x1 = min(x + w - pad, w_img)
        inner_y1 = min(y + ht - pad, h_img)
        if inner_x1 <= inner_x0 or inner_y1 <= inner_y0:
            return None

        # ESLATMA: avval bu yerda ramka ichidagi "to'yinganlik" (saturation)ga
        # qarab qatorlarni kesib tashlaydigan qo'shimcha tekshiruv bor edi -
        # ya'ni faqat rangga boy (to'yingan) piksellar joylashgan qatorlar
        # "chinakam foto" deb hisoblanardi. Bu noto'g'ri edi: agar rasmning
        # katta qismi tabiiy ravishda kam to'yingan bo'lsa (asfalt, osmon,
        # kulrang mashina/bino) va faqat bitta kichik element (masalan
        # markazdagi qizil mashina yoki sariq chiroq) yorqin rangli bo'lsa,
        # algoritm butun kerakli rasm o'rniga FAQAT o'sha kichik elementni
        # qirqib olardi. Rangli tashqi ramkaning o'zi allaqachon to'g'ri
        # chegarani bildiradi, shuning uchun endi unga to'g'ridan-to'g'ri
        # ishonamiz - qo'shimcha "kontent" heuristikasi qo'llanmaydi.
        return (inner_x0, inner_y0, inner_x1, inner_y1)
    except Exception:
        return None


NOISE_PATTERNS = [
    'yakunlash', 'savol', 'ovozli tushuntirish', 'tushuntirishlar',
    'izohlarni ochish', 'tarif sotib', 'sotib oling', 'kb/s', 'kb / s',
]

TIMER_MIXED_RE = re.compile(r'^\D{0,3}\d{1,2}\s*:\s*\d{2}\D{0,15}$')
STATUS_BAR_RE = re.compile(r'^\d{1,2}\s*:\s*\d{2}\b')
TIMER_RE = re.compile(r'^[\s.,:%]*\d{1,3}[\s.,:%]*\d{0,2}[\s.,:%]*$')
LONE_FKEY_RE = re.compile(r'^\s*F[1-4]\s*$')
PAGE_NUMBER_STRIP_RE = re.compile(r'^[\d\s]+$')
JUNK_SYMBOLS_RE = re.compile(r'^[\[\]{}()<>|_\-.,;:\'"`~!@#$%^&*+=\\/\s]*$')


def _is_nav_number_row(tokens):
    if len(tokens) < 5:
        return False
    digit_like = sum(1 for t in tokens if re.fullmatch(r'[\d«»=,.]{1,4}', t))
    return digit_like / len(tokens) >= 0.7


def _looks_like_real_word(token):
    return bool(re.search(r'[A-Za-zʻʼ\'’]{3,}', token))


FKEY_TO_LETTER = ['A', 'B', 'C', 'D', 'E', 'F']
VARIANT_MARKER_RE = re.compile(r'^\s*F\s*(?:[1-4]|A)\b\s*[|).\]]?\s*', re.MULTILINE)


def clean_ocr_text(raw_text):
    """OCR natijasidan doimiy interfeys qismlarini (soat, taymer, tugmalar) tozalaydi."""
    cleaned_lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()

        if any(pattern in lowered for pattern in NOISE_PATTERNS):
            continue
        if TIMER_RE.match(stripped):
            continue
        if PAGE_NUMBER_STRIP_RE.match(stripped):
            continue
        if _is_nav_number_row(stripped.split()):
            continue
        tokens_for_status = stripped.split()
        long_tokens_for_status = [t for t in tokens_for_status if _looks_like_real_word(t)]
        if STATUS_BAR_RE.match(stripped) and len(stripped) <= 35 and len(long_tokens_for_status) <= 1:
            continue
        if LONE_FKEY_RE.match(stripped):
            continue
        if JUNK_SYMBOLS_RE.match(stripped):
            continue
        if TIMER_MIXED_RE.match(stripped) and len(stripped) <= 20:
            continue
        if len(stripped) <= 2 and not stripped.isalpha():
            continue

        tokens = stripped.split()
        long_tokens = [t for t in tokens if _looks_like_real_word(t)]
        if len(tokens) >= 3 and len(long_tokens) == 0:
            continue

        cleaned_lines.append(stripped)

    return '\n'.join(cleaned_lines)


def process_single_image(pil_img, use_groq=True):
    """Bitta rasmni (PIL Image) qayta ishlab, natijani dict ko'rinishida qaytaradi:

    {
        "success": True/False,
        "question": "savol matni",
        "options": ["variant1", "variant2", ...],
        "correct_index": 0  # yoki None agar aniqlanmagan bo'lsa
        "error": "..."  # faqat success=False bo'lsa
    }

    Bu funksiya HECH QANDAY faylga yozmaydi - faqat tuzilgan ma'lumotni qaytaradi.
    Chaqiruvchi kod (bot yoki Streamlit) buni keyin o'zi saqlaydi/ko'rsatadi."""
    try:
        if pil_img.mode not in ('RGB', 'L'):
            pil_img = pil_img.convert('RGB')

        ocr_ready_img = fix_colored_boxes(pil_img)
        regions = detect_answer_option_regions(pil_img)

        if not regions:
            raw_text = pytesseract.image_to_string(ocr_ready_img, lang=OCR_LANG, config=OCR_CONFIG)
            text = clean_ocr_text(raw_text)
            return {
                "success": False,
                "question": ' '.join(text.split()) if text.strip() else "",
                "options": [],
                "correct_index": None,
                "error": "Variant qutilari topilmadi",
            }

        first_box_top = regions[0][0]
        question_raw = ocr_region(ocr_ready_img, 0, first_box_top, OCR_LANG, OCR_CONFIG)
        question_text_clean = clean_ocr_text(question_raw)
        question_text = ' '.join(question_text_clean.split())

        # Avval barcha xom OCR matnlarini (savol + har bir variant) yig'ib
        # olamiz, keyin ularni BITTA Groq chaqiruvida birgalikda tozalashga
        # harakat qilamiz (RPM chegarasiga kamroq tegish uchun).
        raw_option_texts = []
        raw_option_is_green = []
        for y1, y2, is_green in regions:
            option_raw = ocr_region(ocr_ready_img, y1, y2, OCR_LANG, OCR_CONFIG)
            option_clean_lines = clean_ocr_text(option_raw)
            option_no_marker = VARIANT_MARKER_RE.sub('', option_clean_lines, count=1)
            option_text = ' '.join(option_no_marker.split())
            if not option_text:
                continue
            raw_option_texts.append(option_text)
            raw_option_is_green.append(is_green)

        final_question = question_text
        final_option_texts = list(raw_option_texts)

        if use_groq and (question_text or raw_option_texts):
            batch_result = groq_clean_batch_vision(pil_img, question_text, raw_option_texts)
            if batch_result is None:
                # Vizion chaqiruv muvaffaqiyatsiz bo'ldi (masalan model hozircha
                # mavjud emas yoki tarmoq xatosi) - matn-only batch'ga qaytamiz.
                batch_result = groq_clean_batch(question_text, raw_option_texts)

            if batch_result is not None:
                final_question = batch_result[0] or question_text
                final_option_texts = [
                    cleaned or original
                    for cleaned, original in zip(batch_result[1:], raw_option_texts)
                ]
            else:
                # Batch chaqiruv muvaffaqiyatsiz bo'ldi (masalan JSON buzilgan
                # yoki tarmoq xatosi) - eski, bittalab tozalash usuliga
                # qaytamiz, shunda funksionallik hech qachon butunlay
                # to'xtab qolmaydi.
                if question_text:
                    groq_question = groq_clean_question(question_text)
                    final_question = groq_question or question_text
                final_option_texts = []
                for option_text in raw_option_texts:
                    groq_option = groq_clean_option(option_text)
                    final_option_texts.append(groq_option or option_text)

        options = []
        correct_index = None
        for final_option, is_green in zip(final_option_texts, raw_option_is_green):
            # Groq tozalashdan keyin ham (yoki asl OCR natijasi) bo'sh bo'lib
            # qolgan bo'lishi mumkin - bunday "bo'sh variant"larni umuman
            # ro'yxatga qo'shmaymiz (Word faylda keraksiz bo'sh D) kabi
            # qatorlar chiqib qolmasligi uchun).
            if not final_option.strip():
                continue

            options.append(final_option)
            if is_green:
                correct_index = len(options) - 1

        if final_question and options:
            return {
                "success": True,
                "question": final_question,
                "options": options,
                "correct_index": correct_index,
                "error": None,
            }
        else:
            return {
                "success": False,
                "question": final_question,
                "options": options,
                "correct_index": correct_index,
                "error": "Savol yoki variantlar bo'sh chiqdi",
            }

    except pytesseract.TesseractNotFoundError:
        return {
            "success": False, "question": "", "options": [], "correct_index": None,
            "error": "Tesseract topilmadi (serverda o'rnatilmagan)",
        }
    except Exception as e:
        return {
            "success": False, "question": "", "options": [], "correct_index": None,
            "error": f"Kutilmagan xatolik: {e}",
        }
