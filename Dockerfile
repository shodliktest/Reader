FROM python:3.10-slim

# Ishchi papkani yaratish
WORKDIR /app

# Kerakli kutubxonalarni nusxalash va o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Barcha fayllarni konteynerga nusxalash
COPY . .

# Botni ishga tushirish buyrug'i (faylingiz nomi main.py bo'lsa)
CMD ["python", "main.py"]
