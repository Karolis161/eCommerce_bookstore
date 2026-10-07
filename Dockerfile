FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOOKSTORE_HOST=0.0.0.0 \
    BOOKSTORE_PORT=8765 \
    BOOKSTORE_DB=/data/bookstore.db

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data \
    && chown app:app /data

COPY --chown=app:app app.py /app/app.py

USER app

EXPOSE 8765

CMD ["python", "app.py"]
