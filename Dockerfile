# Lightweight Python image
FROM python:3.12-slim

# System deps for lxml + (optional) Playwright chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (including yt-dlp)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optionally install Playwright + chromium (when ENABLE_PLAYWRIGHT=true)
ARG ENABLE_PLAYWRIGHT=false
RUN if [ "$ENABLE_PLAYWRIGHT" = "true" ]; then \
        pip install playwright && \
        playwright install --with-deps chromium; \
    fi

# Copy app code
COPY . .

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=10000

EXPOSE 10000

CMD ["python", "app.py"]
