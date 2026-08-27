# Container image for Colophon: slim Python 3.12, non-root user, and explicitly
# no sqlite3 CLI — every SQLite operation goes through Python (T10), so nothing
# is apt-installed at all.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COLOPHON_DB_PATH=/data/colophon.sqlite3 \
    COLOPHON_UPLOAD_DIR=/data/uploads

RUN useradd --create-home --uid 10001 colophon

WORKDIR /srv/colophon

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# /data holds the SQLite file and uploads via a named volume at runtime. It must
# be writable by the service user and must never be web-served (T10).
RUN mkdir -p /data/uploads && chown -R colophon:colophon /data /srv/colophon

USER colophon

EXPOSE 8000

# T3: migrations run at container start. T2: exactly one uvicorn worker, always.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
