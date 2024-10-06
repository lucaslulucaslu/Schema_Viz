"""Comment schemas."""

from enum import StrEnum

from pydantic import BaseModel

from schemas.post import Post
from schemas.user import User


class Comment(BaseModel):
    """Comment schema."""

    id: int
    content: str
    author: User
    post: Post


class Health(StrEnum):
    """Health status enum."""

    healthy = "healthy"
    unhealthy = "unhealthy"
    unknown = "unknown"


class HealthCheck(BaseModel):
    """Health check schema."""

    status: Health = Health.unknown
