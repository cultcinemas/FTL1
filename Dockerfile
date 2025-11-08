FROM python:3.8-slim-buster

WORKDIR /app
RUN chmod 777 /app

# Fix Buster EOL - Use archive repositories
RUN sed -i 's/deb.debian.org/archive.debian.org/g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|archive.debian.org|g' /etc/apt/sources.list && \
    sed -i '/stretch-updates/d' /etc/apt/sources.list

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
