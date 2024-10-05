from pydantic import BaseModel

from schemas.post import Post
from schemas.user import User


class Comment(BaseModel):
    id: int
    content: str
    author: User
    post: Post
