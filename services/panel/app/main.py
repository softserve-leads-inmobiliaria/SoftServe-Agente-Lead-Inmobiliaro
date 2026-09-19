"""Panel de asesores — solo el esqueleto arrancable (CT-6). La lista, la ficha y la auth por token
llegan con B-5; aquí no hay lógica de negocio."""
from fastapi import FastAPI

app = FastAPI(title="Panel de asesores")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
