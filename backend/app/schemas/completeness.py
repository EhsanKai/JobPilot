from pydantic import BaseModel, Field


class CompletenessCategory(BaseModel):
    label: str
    weight: float = Field(description="This category's share of the total 100%.")
    score: float = Field(description="Fraction (0.0-1.0) of this category's weight earned.")


class ProfileCompleteness(BaseModel):
    percentage: int
    categories: list[CompletenessCategory]
    message: str
