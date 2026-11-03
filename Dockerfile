# Inventory Intelligence Platform - production image (backend API + static frontend).
# Build:  docker build -t iip:2.7.0 .
# Run:    docker run -p 8000:8000 --env-file deploy/env.production.example iip:2.7.0
# Migrations are an explicit deployment step (never run implicitly at start):
#         docker run --rm --env-file <env> iip:2.7.0 python scripts/db_admin.py upgrade
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IIP_ENV=production \
    IIP_LOG_FORMAT=json

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY sample_data/ sample_data/

RUN useradd --create-home --uid 10001 iip && chown -R iip:iip /app
USER iip
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["sh", "scripts/start.sh"]
