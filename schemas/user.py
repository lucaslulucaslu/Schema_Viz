from typing import List

from pydantic import BaseModel


class Address(BaseModel):
    street: str
    city: str
    zip_code: str

class User(BaseModel):
    id: int=0
    name: str
    email: str
    addresses: List[Address]
