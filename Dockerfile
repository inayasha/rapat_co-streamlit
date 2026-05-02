# 1. Gunakan OS Linux Debian dengan Python 3.11 yang super ringan (slim)
FROM python:3.11-slim

# 2. Instal FFmpeg langsung di level sistem operasi (Sangat Cepat & Stabil)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 3. Tentukan folder kerja di dalam server
WORKDIR /app

# 4. 🔥 TRIK CACHING: Salin requirements.txt duluan!
# Jika Anda hanya mengubah kode app.py, Docker TIDAK AKAN mendownload ulang library dari internet.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Salin seluruh sisa kode aplikasi Anda (app.py, dll) ke dalam server
COPY . .

# 6. Jalankan Streamlit. (Railway menggunakan variabel lingkungan $PORT untuk port-nya)
CMD streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0
