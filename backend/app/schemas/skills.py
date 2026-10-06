import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.skill import SkillProficiency


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    proficiency: SkillProficiency | None = None
    years_of_experience: int | None = Field(default=None, ge=0, le=80)


class SkillUpdate(BaseModel):
    proficiency: SkillProficiency | None = None
    years_of_experience: int | None = Field(default=None, ge=0, le=80)


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    proficiency: SkillProficiency | None
    years_of_experience: int | None
