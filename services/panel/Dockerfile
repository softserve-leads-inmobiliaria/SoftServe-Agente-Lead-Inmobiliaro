# build.context = raíz del repo (ver docker-compose.yml): así se puede copiar db/ y contracts/.
FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios (#65): el proceso no corre como root. El código queda de root y solo
# se lee; ningún servicio escribe en disco.
RUN useradd --system --uid 10001 --user-group --no-create-home app

WORKDIR /app

COPY db/requirements.txt db/requirements.txt
COPY services/panel/requirements.txt services/panel/requirements.txt
RUN pip install --no-cache-dir -r services/panel/requirements.txt

COPY db/ db/
COPY contracts/ contracts/
COPY services/panel/ services/panel/

ENV PYTHONPATH=/app:/app/services/panel

USER app
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
