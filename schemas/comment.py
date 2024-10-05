from post import Post
from pydantic import BaseModel
from user import User


class Comment(BaseModel):
    id: int
    content: str
    author: User
    post: Post
