# ================================================
# Base image: Python 3.11 slim (ringan, production-ready)
# ================================================
FROM python:3.11-slim

# Set working directory di dalam container
WORKDIR /app

# Install system dependencies yang dibutuhkan
# - build-essential: untuk compile beberapa library Python
# - portaudio19-dev: untuk PyAudio (audio recording)
# - ffmpeg: untuk pydub (audio processing)
RUN apt-get update && apt-get install -y \
    build-essential \
    portaudio19-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements terlebih dahulu (sebelum kode)
# Teknik ini memanfaatkan Docker layer cache:
# Jika requirements tidak berubah, layer ini di-skip saat rebuild
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy seluruh kode aplikasi ke dalam container
COPY . .

# Expose port Streamlit (default: 8501)
EXPOSE 8501

# Perintah default saat container dijalankan
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]