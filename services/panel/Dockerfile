# build.context = raíz del repo (ver docker-compose.yml): así se puede copiar db/ y contracts/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY db/requirements.txt db/requirements.txt
COPY services/panel/requirements.txt services/panel/requirements.txt
RUN pip install --no-cache-dir -r services/panel/requirements.txt

COPY db/ db/
COPY contracts/ contracts/
COPY services/panel/ services/panel/

ENV PYTHONPATH=/app:/app/services/panel

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
