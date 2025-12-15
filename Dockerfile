FROM python:3.11-slim

# تثبيت ffmpeg
RUN apt-get update && apt-get install -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# مجلد العمل
WORKDIR /app

# نسخ الملفات
COPY . .

# تثبيت المتطلبات
RUN pip install --no-cache-dir -r requirements.txt

# تشغيل البوت
CMD ["python", "godstring.py"]
