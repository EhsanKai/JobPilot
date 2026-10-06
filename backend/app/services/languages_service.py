import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models.language import Language, UserLanguage
from app.models.user import User
from app.schemas.languages import UserLanguageCreate


def _get_or_create_language(db: Session, name: str) -> Language:
    language = db.execute(select(Language).where(Language.name == name)).scalar_one_or_none()
    if language is None:
        language = Language(name=name)
        db.add(language)
        db.flush()
    return language


def list_user_languages(db: Session, user: User) -> list[UserLanguage]:
    return list(
        db.execute(
            select(UserLanguage)
            .where(UserLanguage.user_id == user.id)
            .options(selectinload(UserLanguage.language))
            .join(Language)
            .order_by(Language.name)
        )
        .scalars()
        .all()
    )


def add_language(db: Session, user: User, data: UserLanguageCreate) -> UserLanguage:
    language = _get_or_create_language(db, data.name)
    existing = db.execute(
        select(UserLanguage).where(
            UserLanguage.user_id == user.id, UserLanguage.language_id == language.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"{data.name} has already been added.")

    user_language = UserLanguage(
        user_id=user.id, language_id=language.id, proficiency=data.proficiency
    )
    db.add(user_language)
    db.commit()
    db.refresh(user_language)
    return user_language


def delete_language(db: Session, user: User, user_language_id: uuid.UUID) -> None:
    user_language = db.execute(
        select(UserLanguage).where(
            UserLanguage.id == user_language_id, UserLanguage.user_id == user.id
        )
    ).scalar_one_or_none()
    if user_language is None:
        raise NotFoundError("Language not found.")
    db.delete(user_language)
    db.commit()
