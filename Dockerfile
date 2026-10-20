FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 appuser

COPY main.py db.py models.py schemas.py base.py alembic.ini ./
COPY alembic/ ./alembic/

USER appuser
EXPOSE 8000

# Failed migrations stop startup; exec forwards shutdown signals to Uvicorn.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn main:app --host 0.0.0.0 --port 8000"]
