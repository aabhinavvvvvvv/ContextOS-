"""HTTP routes for grounded answers."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.answers import service
from app.answers.graph import answer_question
from app.answers.schemas import AskRequest, AskResponse, CitationRead, MemoryCitationRead
from app.conversations import service as conversations
from app.db import SessionDep
from app.embeddings import Embedder, get_embedder
from app.jobs import JobQueue, get_job_queue
from app.llm import LLM, get_llm

logger = logging.getLogger(__name__)

router = APIRouter(tags=["answers"])


@router.post("/ask")
async def ask(
    request: AskRequest,
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue)],
) -> AskResponse:
    """Answer a question from your documents, citing the chunks used as [n].

    The question and answer are saved to a conversation: a new one, or the one given by
    `conversation_id` (404 if it doesn't exist). Nothing is saved if answering fails.

    LLM failures map to: 503 (no API key configured), 429 (provider quota used up),
    502 (provider error or timeout).
    """
    history: list[tuple[str, str]] = []
    if request.conversation_id is not None:
        # Check before answering: a typo'd id shouldn't cost an LLM call.
        if await conversations.get_conversation(session, request.conversation_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        recent = await conversations.recent_messages(
            session, request.conversation_id, limit=2 * service.MAX_HISTORY_TURNS
        )
        history = [(m.role, m.content) for m in recent]

    answer = await answer_question(
        session, embedder, llm, request.question, top_k=request.top_k, history=history
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
    memory_citations = [
        MemoryCitationRead(
            number=c.number,
            memory_id=c.memory.id,
            kind=c.memory.kind,
            content=c.memory.content,
            created_at=c.memory.created_at,
        )
        for c in answer.memory_citations
    ]
    try:
        turn = await conversations.record_turn(
            session,
            conversation_id=request.conversation_id,
            question=request.question,
            answer=answer.text,
            # mode="json": UUIDs and datetimes become strings, so the lists can be stored as JSONB.
            citations=[c.model_dump(mode="json") for c in citations],
            memory_citations=[c.model_dump(mode="json") for c in memory_citations],
        )
    except conversations.ConversationNotFoundError:
        # Deleted between the check above and now.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    # Learn from this turn in the background: the user doesn't wait for it.
    try:
        await job_queue.enqueue_memory_extraction(turn.user_message_id)
    except Exception:
        # The answer is already saved; the worker's sweep will queue extraction later.
        logger.exception("Could not enqueue memory extraction for message %s", turn.user_message_id)

    return AskResponse(
        conversation_id=turn.conversation_id,
        question=request.question,
        standalone_question=answer.standalone_question,
        sub_questions=answer.sub_questions,
        answer=answer.text,
        citations=citations,
        memory_citations=memory_citations,
        sources_considered=answer.sources_considered,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        timeline=answer.timeline,
    )
