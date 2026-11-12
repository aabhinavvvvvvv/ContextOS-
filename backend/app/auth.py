"""Optional API token authentication (single user).

ContextOS is a personal app: one user, one secret. When API_TOKEN is set, every request
except the health checks must send "Authorization: Bearer <token>". Not set → open, which
is fine on localhost; set it before exposing the app on a network.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


async def require_api_token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    if not token or request.url.path.startswith("/health"):
        return
    sent = request.headers.get("authorization", "")
    # compare_digest takes the same time whether the first or last character differs, so the
    # response time can't leak how much of a guessed token was right (a timing attack).
    if not secrets.compare_digest(sent.encode(), f"Bearer {token}".encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"}
        )
