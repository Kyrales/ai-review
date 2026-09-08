from pydantic import BaseModel


class DiffFileSchema(BaseModel):
    file: str
    diff: str
    added_lines: set[int]
