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
# pip solo hace falta para instalar: se quita de la imagen final (#104). Trae sus propias
# vulnerabilidades y nada lo usa en ejecución.
RUN pip install --no-cache-dir -r services/catalog/requirements.txt \
    && pip uninstall --yes pip

COPY db/ db/
COPY contracts/ contracts/
COPY services/catalog/ services/catalog/
# `scripts/seed_faq.py` (C-5) siembra la FAQ desde la imagen del catálogo: `make seed-faq`.
COPY scripts/ scripts/

# C-5: modelo de embeddings autoalojado (DT-7). Se descarga al construir la imagen y queda dentro:
# en ejecución no hay llamada a ningún proveedor ni descarga. `EMBEDDINGS_LOCAL_ONLY=1` solo deja
# usarlo desde la caché ya presente.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/.fastembed')"
ENV EMBEDDINGS_LOCAL_ONLY=1

ENV PYTHONPATH=/app:/app/services/catalog

USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
