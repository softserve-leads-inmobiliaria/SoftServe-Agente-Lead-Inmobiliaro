# build.context = raíz del repo (ver docker-compose.yml). Aplica las migraciones y termina.
FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios (#65): el proceso no corre como root. El código queda de root y solo
# se lee; ningún servicio escribe en disco.
RUN useradd --system --uid 10001 --user-group --no-create-home app

WORKDIR /app

COPY db/requirements.txt db/requirements.txt
# pip solo hace falta para instalar: se quita de la imagen final (#104). Trae sus propias
# vulnerabilidades y nada lo usa en ejecución.
RUN pip install --no-cache-dir -r db/requirements.txt \
    && pip uninstall --yes pip

COPY alembic.ini alembic.ini
COPY db/ db/
COPY contracts/ contracts/
# Esta imagen corre también el seed de demo (servicio `seed` del compose, B-5).
COPY scripts/ scripts/

ENV PYTHONPATH=/app

USER app
CMD ["alembic", "upgrade", "head"]
