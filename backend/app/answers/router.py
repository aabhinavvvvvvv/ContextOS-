"""HTTP routes for grounded answers."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.answers import service
from app.answers.schemas import AskRequest, AskResponse, CitationRead
from app.db import SessionDep
from app.embeddings import Embedder, get_embedder
from app.llm import LLM, get_llm

router = APIRouter(tags=["answers"])


@router.post("/ask")
async def ask(
    request: AskRequest,
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> AskResponse:
    """Answer a question from your documents, citing the chunks used as [n].

    LLM failures map to: 503 (no API key configured), 429 (provider quota used up),
    502 (provider error or timeout).
    """
    answer = await service.answer_question(
        session, embedder, llm, request.question, top_k=request.top_k
    )
    return AskResponse(
        question=request.question,
        answer=answer.text,
        citations=[
            CitationRead(
                number=c.number,
                chunk_id=c.hit.chunk_id,
                document_id=c.hit.document_id,
                filename=c.hit.filename,
                heading=c.hit.heading,
                page_number=c.hit.page_number,
                text=c.hit.text,
            )
            for c in answer.citations
        ],
        sources_considered=answer.sources_considered,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
    )
