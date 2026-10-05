import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.completeness import ProfileCompleteness
from app.schemas.languages import UserLanguageCreate, UserLanguageRead
from app.schemas.profile import ProfileWithUserRead, UserProfileUpdate
from app.schemas.skills import SkillCreate, SkillRead, SkillUpdate
from app.services import completeness_service, languages_service, profile_service, skills_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileWithUserRead)
def get_profile(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProfileWithUserRead:
    return profile_service.build_profile_response(db, current_user)


@router.put("", response_model=ProfileWithUserRead)
def update_profile(
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileWithUserRead:
    return profile_service.update_profile(db, current_user, data)


@router.get("/completeness", response_model=ProfileCompleteness)
def get_profile_completeness(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProfileCompleteness:
    return completeness_service.calculate_completeness(db, current_user)


@router.get("/skills", response_model=list[SkillRead])
def list_skills(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SkillRead]:
    user_skills = skills_service.list_user_skills(db, current_user)
    return [
        SkillRead(
            id=us.id,
            name=us.skill.name,
            proficiency=us.proficiency,
            years_of_experience=us.years_of_experience,
        )
        for us in user_skills
    ]


@router.post("/skills", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
def add_skill(
    data: SkillCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillRead:
    user_skill = skills_service.add_skill(db, current_user, data)
    return SkillRead(
        id=user_skill.id,
        name=user_skill.skill.name,
        proficiency=user_skill.proficiency,
        years_of_experience=user_skill.years_of_experience,
    )


@router.put("/skills/{skill_id}", response_model=SkillRead)
def update_skill(
    skill_id: uuid.UUID,
    data: SkillUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillRead:
    user_skill = skills_service.update_skill(db, current_user, skill_id, data)
    return SkillRead(
        id=user_skill.id,
        name=user_skill.skill.name,
        proficiency=user_skill.proficiency,
        years_of_experience=user_skill.years_of_experience,
    )


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    skills_service.delete_skill(db, current_user, skill_id)


@router.get("/languages", response_model=list[UserLanguageRead])
def list_languages(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[UserLanguageRead]:
    user_languages = languages_service.list_user_languages(db, current_user)
    return [
        UserLanguageRead(id=ul.id, name=ul.language.name, proficiency=ul.proficiency)
        for ul in user_languages
    ]


@router.post("/languages", response_model=UserLanguageRead, status_code=status.HTTP_201_CREATED)
def add_language(
    data: UserLanguageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserLanguageRead:
    user_language = languages_service.add_language(db, current_user, data)
    return UserLanguageRead(
        id=user_language.id, name=user_language.language.name, proficiency=user_language.proficiency
    )


@router.delete("/languages/{language_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_language(
    language_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    languages_service.delete_language(db, current_user, language_id)
