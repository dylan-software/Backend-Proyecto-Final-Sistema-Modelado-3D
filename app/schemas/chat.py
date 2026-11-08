from typing import Literal

from pydantic import Field

from app.schemas.room import CamelModel

MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 2000


class ChatMessage(CamelModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(CamelModel):
    room_id: int = Field(gt=0)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)


class ChatChangeOut(CamelModel):
    """Un cambio guardado por el chat: qué se hizo, con qué objeto y dónde quedó, en palabras."""

    action: Literal["added", "removed", "moved"]
    name: str
    where: str


class ChatReply(CamelModel):
    role: Literal["assistant"] = "assistant"
    content: str
    # Aditivo (CONTRATO-FASE2.md, 2.5): vacío si el mensaje no cambió nada.
    changes: list[ChatChangeOut] = Field(default_factory=list)
