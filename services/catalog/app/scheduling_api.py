"""Endpoints HTTP de la agenda (C-2): `POST /scheduling/<operación>`, con el cuerpo del contrato.

Por HTTP y no por MCP a propósito (DT-32): todo lo que el catálogo publica por MCP, `core` se lo da al
LLM como tool (DT-13), y reservar una visita o elegir asesor lo decide el código, nunca el modelo.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.scheduling import SchedulingError, SchedulingService
from db.repo_root import repo_root

logger = logging.getLogger(__name__)

OPERATIONS = ("assign_advisor", "get_slots", "book")
STATUS = {"NOT_FOUND": 404, "INVALID_INPUT": 422, "SLOT_TAKEN": 409, "UPSTREAM_UNAVAILABLE": 503}


def _input_validators() -> dict[str, Draft202012Validator]:
    """El esquema de entrada sale de contracts/scheduling: el contrato es la única fuente de verdad."""
    folder = repo_root() / "contracts" / "scheduling"
    return {name: Draft202012Validator(
                json.loads((folder / f"{name}.schema.json").read_text(encoding="utf-8"))["inputSchema"],
                format_checker=FormatChecker())
            for name in OPERATIONS}


def _error(error: SchedulingError) -> JSONResponse:
    return JSONResponse(error.to_dict(), status_code=STATUS[error.error_code])


def create_scheduling_router(service: SchedulingService) -> APIRouter:
    router = APIRouter(prefix="/scheduling")
    validators = _input_validators()

    async def run(name: str, request: Request, call: Callable[..., dict[str, Any]]) -> JSONResponse:
        try:
            arguments = await request.json()
        except ValueError:
            return _error(SchedulingError("INVALID_INPUT", "El cuerpo debe ser JSON."))
        if not validators[name].is_valid(arguments):
            return _error(SchedulingError("INVALID_INPUT", "Los argumentos no cumplen el esquema de la operación."))
        try:
            # La sesión de SQLAlchemy es síncrona: fuera del event loop para no frenar /mcp.
            return JSONResponse(await run_in_threadpool(call, **arguments))
        except SchedulingError as error:
            return _error(error)
        except SQLAlchemyError:
            logger.exception("la agenda no pudo hablar con la base", extra={"operation": name})
            return _error(SchedulingError("UPSTREAM_UNAVAILABLE", "La agenda no está disponible por ahora."))

    @router.post("/assign_advisor")
    async def assign_advisor(request: Request) -> JSONResponse:
        return await run("assign_advisor", request, service.assign_advisor)

    @router.post("/get_slots")
    async def get_slots(request: Request) -> JSONResponse:
        return await run("get_slots", request, service.get_slots)

    @router.post("/book")
    async def book(request: Request) -> JSONResponse:
        return await run("book", request, service.book)

    return router
