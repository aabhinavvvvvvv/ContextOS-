"""HTTP routes for grounded answers."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.answers import service
from app.answers.schemas import AskRequest, AskResponse, CitationRead
from app.conversations import service as conversations
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

    The question and answer are saved to a conversation: a new one, or the one given by
    `conversation_id` (404 if it doesn't exist). Nothing is saved if answering fails.

    LLM failures map to: 503 (no API key configured), 429 (provider quota used up),
    502 (provider error or timeout).
    """
    # Check before answering: a typo'd id shouldn't cost an LLM call.
    if (
        request.conversation_id is not None
        and await conversations.get_conversation(session, request.conversation_id) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    answer = await service.answer_question(
        session, embedder, llm, request.question, top_k=request.top_k
    )
    citations = [
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
    ]
    try:
        conversation_id = await conversations.record_turn(
            session,
            conversation_id=request.conversation_id,
            question=request.question,
            answer=answer.text,
            # mode="json": UUIDs become strings, so the list can be stored as JSONB.
            citations=[c.model_dump(mode="json") for c in citations],
        )
    except conversations.ConversationNotFoundError:
        # Deleted between the check above and now.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    return AskResponse(
        conversation_id=conversation_id,
        question=request.question,
        answer=answer.text,
        citations=citations,
        sources_considered=answer.sources_considered,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
    )
