# build.context = raíz del repo (ver docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY harness/requirements.txt harness/requirements.txt
RUN pip install --no-cache-dir -r harness/requirements.txt

COPY contracts/ contracts/
COPY harness/ harness/

# CORE_URL lo define docker-compose.yml: con él el arnés habla con el core por HTTP.
CMD ["python", "harness/scripts/run_all.py"]
