"""Observability: record every LLM call and HTTP request, and summarise them.

    MeteredLLM        wraps the LLM client: purpose, tokens, latency, errors → llm_calls
    record_request    HTTP middleware: method, route, status, latency → request_logs
    GET /metrics      calls, error rates, p50/p95 latency, tokens and estimated cost

Recording never breaks the thing being recorded: a failed write is logged and dropped.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings, get_settings
from app.db import SessionDep, get_engine
from app.llm import LLM, LLMResult, get_llm
from app.models import LlmCall, RequestLog

logger = logging.getLogger(__name__)


# --- LLM calls --------------------------------------------------------------------------


class MeteredLLM:
    """Same interface as LLM; records each call in llm_calls (its own short transaction, so
    the record survives even when the caller's transaction is rolled back)."""

    def __init__(self, llm: Any, engine: AsyncEngine, model: str) -> None:
        self.llm, self.engine, self.model = llm, engine, model

    async def generate(self, *, purpose: str = "other", **kwargs: Any) -> LLMResult:
        started = time.perf_counter()
        try:
            result = await self.llm.generate(purpose=purpose, **kwargs)
        except Exception as exc:
            await self._record(purpose, started, error=f"{type(exc).__name__}: {exc}")
            raise
        await self._record(purpose, started, result=result)
        return result

    async def _record(
        self, purpose: str, started: float, result: LLMResult | None = None, error: str | None = None
    ) -> None:
        try:
            async with AsyncSession(self.engine) as session:
                session.add(
                    LlmCall(
                        purpose=purpose,
                        model=self.model,
                        input_tokens=result.input_tokens if result else 0,
                        output_tokens=result.output_tokens if result else 0,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        ok=error is None,
                        error=error[:500] if error else None,
                    )
                )
                await session.commit()
        except Exception:
            logger.warning("Could not record an LLM call", exc_info=True)


def get_metered_llm(
    llm: Annotated[LLM, Depends(get_llm)],
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MeteredLLM:
    """FastAPI dependency: the LLM, metered. Overriding get_llm (tests) still applies."""
    return MeteredLLM(llm, engine, settings.llm_model)


# --- HTTP requests ----------------------------------------------------------------------


async def record_request(request: Request, call_next: Any) -> Any:
    """HTTP middleware: time every request (except health checks) and store it."""
    started = time.perf_counter()
    status = 500  # if the app raises, the client gets a 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if not request.url.path.startswith("/health"):
            # The route template ("/documents/{document_id}"), not the concrete URL, so
            # requests to the same endpoint group together.
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            try:
                async with AsyncSession(request.app.state.db_engine) as session:
                    session.add(
                        RequestLog(
                            method=request.method,
                            path=path,
                            status=status,
                            latency_ms=round((time.perf_counter() - started) * 1000),
                        )
                    )
                    await session.commit()
            except Exception:
                logger.warning("Could not record a request", exc_info=True)


# --- Summary ----------------------------------------------------------------------------


class LlmUsage(BaseModel):
    purpose: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    p50_ms: float
    p95_ms: float
    estimated_cost_usd: float


class EndpointUsage(BaseModel):
    method: str
    path: str
    requests: int
    server_errors: int  # 5xx
    p50_ms: float
    p95_ms: float


class RecentError(BaseModel):
    at: datetime
    source: str  # "llm:<purpose>" or "http:<METHOD path>"
    error: str


class Metrics(BaseModel):
    since: datetime
    llm: list[LlmUsage]
    endpoints: list[EndpointUsage]
    total_estimated_cost_usd: float
    recent_errors: list[RecentError]
    pricing_note: str


router = APIRouter(tags=["metrics"])


def _p(fraction: float, column: Any) -> Any:
    return func.percentile_cont(fraction).within_group(column)


@router.get("/metrics")
async def metrics(
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> Metrics:
    """LLM usage (per purpose), endpoint latency and errors over the last `days` days."""
    since = datetime.now(UTC) - timedelta(days=days)
    llm_rows = (
        await session.execute(
            select(
                LlmCall.purpose,
                func.count(),
                func.count().filter(LlmCall.ok.is_(False)),
                func.coalesce(func.sum(LlmCall.input_tokens), 0),
                func.coalesce(func.sum(LlmCall.output_tokens), 0),
                _p(0.5, LlmCall.latency_ms),
                _p(0.95, LlmCall.latency_ms),
            )
            .where(LlmCall.at >= since)
            .group_by(LlmCall.purpose)
            .order_by(func.count().desc())
        )
    ).all()
    price_in, price_out = settings.llm_input_usd_per_million, settings.llm_output_usd_per_million
    llm = [
        LlmUsage(
            purpose=purpose, calls=calls, errors=errors, input_tokens=tokens_in, output_tokens=tokens_out,
            p50_ms=p50 or 0, p95_ms=p95 or 0,
            estimated_cost_usd=round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6),
        )
        for purpose, calls, errors, tokens_in, tokens_out, p50, p95 in llm_rows
    ]
    endpoint_rows = (
        await session.execute(
            select(
                RequestLog.method, RequestLog.path, func.count(),
                func.count().filter(RequestLog.status >= 500),
                _p(0.5, RequestLog.latency_ms), _p(0.95, RequestLog.latency_ms),
            )
            .where(RequestLog.at >= since)
            .group_by(RequestLog.method, RequestLog.path)
            .order_by(func.count().desc())
        )
    ).all()
    endpoints = [
        EndpointUsage(method=m, path=p, requests=n, server_errors=e, p50_ms=p50 or 0, p95_ms=p95 or 0)
        for m, p, n, e, p50, p95 in endpoint_rows
    ]
    llm_errors = (
        await session.execute(
            select(LlmCall.at, LlmCall.purpose, LlmCall.error)
            .where(LlmCall.at >= since, LlmCall.ok.is_(False))
            .order_by(LlmCall.at.desc()).limit(10)
        )
    ).all()
    http_errors = (
        await session.execute(
            select(RequestLog.at, RequestLog.method, RequestLog.path, RequestLog.status)
            .where(RequestLog.at >= since, RequestLog.status >= 500)
            .order_by(RequestLog.at.desc()).limit(10)
        )
    ).all()
    recent = [RecentError(at=at, source=f"llm:{purpose}", error=error or "") for at, purpose, error in llm_errors]
    recent += [RecentError(at=at, source=f"http:{m} {p}", error=f"HTTP {s}") for at, m, p, s in http_errors]
    recent.sort(key=lambda e: e.at, reverse=True)
    return Metrics(
        since=since,
        llm=llm,
        endpoints=endpoints,
        total_estimated_cost_usd=round(sum(u.estimated_cost_usd for u in llm), 6),
        recent_errors=recent[:10],
        pricing_note=(
            f"Estimated at ${price_in}/${price_out} per million input/output tokens "
            "(LLM_INPUT_USD_PER_MILLION / LLM_OUTPUT_USD_PER_MILLION). On the free tier the actual cost is $0."
        ),
    )
