# build.context = raíz del repo (ver docker-compose.yml): así se puede copiar db/ y contracts/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/catalog/requirements.txt services/catalog/requirements.txt
RUN pip install --no-cache-dir -r services/catalog/requirements.txt

COPY db/ db/
COPY contracts/ contracts/
COPY services/catalog/ services/catalog/

ENV PYTHONPATH=/app:/app/services/catalog

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
