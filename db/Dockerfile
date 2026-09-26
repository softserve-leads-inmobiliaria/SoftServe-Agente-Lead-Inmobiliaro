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
# El seed del catálogo (C-9, `scripts/seed_catalog.py`) importa el inventario desde
# `services/catalog/app/data.py` para no mantener una segunda copia a mano. Se copian solo esos dos
# archivos y no el paquete entero: `data.py` no importa nada del servicio, así que alcanza. Si algún
# día `data.py` gana una dependencia, hay que copiar también lo que necesita o el seed falla en
# docker, no en local.
COPY services/catalog/app/__init__.py services/catalog/app/__init__.py
COPY services/catalog/app/data.py services/catalog/app/data.py

ENV PYTHONPATH=/app

USER app
CMD ["alembic", "upgrade", "head"]
