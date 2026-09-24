# build.context = raíz del repo (ver docker-compose.yml): así se puede copiar db/ y contracts/.
FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios (#65): el proceso no corre como root. El código queda de root y solo
# se lee; ningún servicio escribe en disco.
RUN useradd --system --uid 10001 --user-group --no-create-home app

WORKDIR /app

COPY db/requirements.txt db/requirements.txt
COPY services/catalog/requirements.txt services/catalog/requirements.txt
RUN pip install --no-cache-dir -r services/catalog/requirements.txt

COPY db/ db/
COPY contracts/ contracts/
COPY services/catalog/ services/catalog/

ENV PYTHONPATH=/app:/app/services/catalog

USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
