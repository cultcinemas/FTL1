FROM python:3.8-slim-bullseye

WORKDIR /app
RUN chmod 777 /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    p7zip-full \
    gcc \
    libc6-dev \
    qbittorrent-nox \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "start.sh"]
