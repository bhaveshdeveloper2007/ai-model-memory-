from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Agent, Project
from .settings import get_settings

bearer = HTTPBearer(auto_error=False)
BLOCKED_KEYS = re.compile(
    r"(?:^|_)(?:prompt|prompt_text|raw_prompt|chain_of_thought|cot|transcript|messages|"
    r"tool_output|authorization|api_key|access_token|refresh_token|password|secret|"
    r"credential|private_key)(?:$|_)",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|secret|credential|api[_-]?key|access[_-]?token)\s*[:=]\s*[^\s,;]{8,}"),
)


@dataclass(frozen=True)
class Actor:
    is_admin: bool
    agent_id: Any | None = None
    agent_name: str = "local-user"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_session),
) -> Actor:
    settings = get_settings()
    if not settings.api_token:
        raise HTTPException(status_code=503, detail="Local API authentication is not configured.")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    supplied = credentials.credentials
    if hmac.compare_digest(supplied, settings.api_token):
        return Actor(is_admin=True)
    agent = db.scalar(select(Agent).where(Agent.credential_digest == token_digest(supplied)))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token.")
    return Actor(is_admin=False, agent_id=agent.id, agent_name=agent.name)


def require_admin(actor: Actor = Depends(require_actor)) -> Actor:
    if not actor.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner credential required.")
    return actor


def require_project_access(db: Session, actor: Actor, project_id, *, write: bool = False) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if actor.is_admin:
        return project
    allowed_agents = {str(value) for value in project.permissions.get("agent_ids", [])}
    if str(actor.agent_id) not in allowed_agents:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is not authorized for this project.")
    return project


def redact_payload(value: Any) -> Any:
    """Remove sensitive fields and redact common credential formats before storage."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if BLOCKED_KEYS.search(str(key)) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        clean = value
        for pattern in SECRET_PATTERNS:
            clean = pattern.sub("[REDACTED]", clean)
        return clean
    return value
