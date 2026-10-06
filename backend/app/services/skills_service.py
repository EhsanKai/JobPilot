import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models.skill import Skill, UserSkill
from app.models.user import User
from app.schemas.skills import SkillCreate, SkillUpdate


def _get_or_create_skill(db: Session, name: str) -> Skill:
    skill = db.execute(select(Skill).where(Skill.name == name)).scalar_one_or_none()
    if skill is None:
        skill = Skill(name=name)
        db.add(skill)
        db.flush()
    return skill


def list_user_skills(db: Session, user: User) -> list[UserSkill]:
    return list(
        db.execute(
            select(UserSkill)
            .where(UserSkill.user_id == user.id)
            .options(selectinload(UserSkill.skill))
            .join(Skill)
            .order_by(Skill.name)
        )
        .scalars()
        .all()
    )


def add_skill(db: Session, user: User, data: SkillCreate) -> UserSkill:
    skill = _get_or_create_skill(db, data.name)
    user_skill = db.execute(
        select(UserSkill).where(UserSkill.user_id == user.id, UserSkill.skill_id == skill.id)
    ).scalar_one_or_none()

    if user_skill is None:
        user_skill = UserSkill(
            user_id=user.id,
            skill_id=skill.id,
            proficiency=data.proficiency,
            years_of_experience=data.years_of_experience,
        )
        db.add(user_skill)
    else:
        user_skill.proficiency = data.proficiency
        user_skill.years_of_experience = data.years_of_experience

    db.commit()
    db.refresh(user_skill)
    return user_skill


def update_skill(db: Session, user: User, user_skill_id: uuid.UUID, data: SkillUpdate) -> UserSkill:
    user_skill = db.execute(
        select(UserSkill).where(UserSkill.id == user_skill_id, UserSkill.user_id == user.id)
    ).scalar_one_or_none()
    if user_skill is None:
        raise NotFoundError("Skill not found.")

    user_skill.proficiency = data.proficiency
    user_skill.years_of_experience = data.years_of_experience
    db.add(user_skill)
    db.commit()
    db.refresh(user_skill)
    return user_skill


def delete_skill(db: Session, user: User, user_skill_id: uuid.UUID) -> None:
    user_skill = db.execute(
        select(UserSkill).where(UserSkill.id == user_skill_id, UserSkill.user_id == user.id)
    ).scalar_one_or_none()
    if user_skill is None:
        raise NotFoundError("Skill not found.")
    db.delete(user_skill)
    db.commit()
