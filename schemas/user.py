from typing import List, Optional, Union

from pydantic import BaseModel


class Address(BaseModel):
    street: str
    city: str
    zipcode: str = "00000"

class User(BaseModel):
    id: int = 0
    name: str = "John Doe"
    email: str
    addresses: List[Address]
    nickname: Optional[str] = None
    status: Union[str, None] = "active"
