"""This module contains the schema for the post model."""

from typing import List, Optional, Union

from pydantic import BaseModel

from schemas.user import User


class Post(BaseModel):
    """Post schema."""

    id: int
    title: Optional[str]
    content: Union[str, None]
    author: User
    tags: List[str]
