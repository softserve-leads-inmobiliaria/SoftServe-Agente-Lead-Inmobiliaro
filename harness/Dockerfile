# build.context = raíz del repo (ver docker-compose.yml).
FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios (#65): el proceso no corre como root. El código queda de root y solo
# se lee; ningún servicio escribe en disco.
RUN useradd --system --uid 10001 --user-group --no-create-home app

WORKDIR /app

COPY harness/requirements.txt harness/requirements.txt
RUN pip install --no-cache-dir -r harness/requirements.txt

COPY contracts/ contracts/
COPY harness/ harness/

# CORE_URL lo define docker-compose.yml: con él el arnés habla con el core por HTTP.
USER app
CMD ["python", "harness/scripts/run_all.py"]
