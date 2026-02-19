FROM python:3.8-slim-buster

WORKDIR /app
RUN chmod 777 /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "start.sh"]
