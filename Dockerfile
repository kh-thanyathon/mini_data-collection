# image ทางการของ Playwright: มี Python + Chromium + library ของระบบครบ
# ⚠️ tag ต้องตรงกับ playwright==X.Y.Z ใน requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV TZ=Asia/Bangkok \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ติดตั้ง library ก่อน copy โค้ด -> แก้โค้ดแล้ว build ใหม่ ไม่ต้องติดตั้งซ้ำ (ใช้ cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "run.py"]