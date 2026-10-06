import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.language import LanguageProficiency


class UserLanguageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    proficiency: LanguageProficiency


class UserLanguageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    proficiency: LanguageProficiency
