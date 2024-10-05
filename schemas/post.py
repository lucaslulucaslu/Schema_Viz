from typing import List, Optional, Union

from pydantic import BaseModel

from schemas.user import User


class Post(BaseModel):
    id: int
    title: Optional[str]
    content: Union[str, None]
    author: User
    tags: List[str]
