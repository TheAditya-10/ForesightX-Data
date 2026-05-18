FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY ForesightX-Data/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app/ForesightX-Data

COPY --from=builder /install /usr/local
COPY ForesightX-Data /app/ForesightX-Data

RUN useradd --create-home --shell /usr/sbin/nologin appuser && \
    chown -R appuser:appuser /app/ForesightX-Data
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
