# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY meshcore-bot/requirements.txt meshcore-bot/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 bluez dbus && \
    rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy meshcore-bot submodule
COPY meshcore-bot/ meshcore-bot/

# Symlink translations so default config path (translations/) resolves from /app
RUN ln -sf /app/meshcore-bot/translations /app/translations

# Copy community bot
COPY community_bot.py .
COPY community/ community/

# Copy config and entrypoint
COPY config.ini.example .
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Environment defaults
ENV MESHCORE_CONNECTION_TYPE=serial \
    MESHCORE_SERIAL_PORT=/dev/ttyUSB0 \
    TZ=America/Denver

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "community_bot.py" > /dev/null || exit 1

VOLUME ["/app/data", "/app/logs"]
EXPOSE 8081

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "community_bot.py"]
