from enum import StrEnum

from pydantic import BaseModel

from schemas.post import Post
from schemas.user import User


class Comment(BaseModel):
    id: int
    content: str
    author: User
    post: Post


class Health(StrEnum):
    healthy = "healthy"
    unhealthy = "unhealthy"
    unknown = "unknown"


class HealthCheck(BaseModel):
    status: Health = Health.unknown
