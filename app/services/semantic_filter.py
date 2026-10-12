"""Semantic filter: asks OmniRoute whether a name typed by the user is a plausible piece of furniture."""

from app.services.omniroute import OmniRouteError, chat_completion  # noqa: F401

__all__ = ["OmniRouteError"]
