FROM python:3.8-slim-buster

WORKDIR /app
RUN chmod 777 /app

# Install ffmpeg and other dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    curl \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

CMD ["bash", "start.sh"]
