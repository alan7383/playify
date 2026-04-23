
FROM ubuntu:24.04
WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.12, FFmpeg 6.1.1 (Ubuntu 24.04 official), and other deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3-pip \
    ffmpeg \
    git \
    chromium-browser && \
    rm -rf /var/lib/apt/lists/*

# Make python3.12 the default python/python3
RUN ln -sf /usr/bin/python3.12 /usr/bin/python && \
    ln -sf /usr/bin/python3.12 /usr/bin/python3

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --break-system-packages

# Install playwright and its dependencies
RUN pip install playwright --break-system-packages && \
    playwright install-deps && \
    playwright install

COPY . .

CMD ["python", "playify.py"]
