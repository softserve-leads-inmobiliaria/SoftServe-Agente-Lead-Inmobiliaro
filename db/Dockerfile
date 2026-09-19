# build.context = raíz del repo (ver docker-compose.yml). Aplica las migraciones y termina.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY db/requirements.txt db/requirements.txt
RUN pip install --no-cache-dir -r db/requirements.txt

COPY alembic.ini alembic.ini
COPY db/ db/
COPY contracts/ contracts/

ENV PYTHONPATH=/app

CMD ["alembic", "upgrade", "head"]
