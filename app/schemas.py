"""Request/response models. All user input is validated here before it touches the database."""
import re
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TITLE_MAX = 120
NOTE_MAX = 500

# Control characters (except tab/newline) are stripped so they can't break the UI or logs.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Color(str, Enum):
    peach = "peach"
    mint = "mint"
    lavender = "lavender"
    butter = "butter"
    sky = "sky"
    blush = "blush"


COLORS = [c.value for c in Color]


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _CONTROL.sub("", value).strip()


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    note: Optional[str] = Field(default=None, max_length=NOTE_MAX)
    priority: Priority = Priority.medium
    due_date: Optional[date] = None
    color: Optional[Color] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("note")
    @classmethod
    def clean_note(cls, v: Optional[str]) -> Optional[str]:
        v = _clean(v)
        return v or None


class TaskUpdate(BaseModel):
    """Partial update: only the fields that are sent are changed."""
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=TITLE_MAX)
    note: Optional[str] = Field(default=None, max_length=NOTE_MAX)
    priority: Optional[Priority] = None
    due_date: Optional[date] = None
    color: Optional[Color] = None
    done: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _clean(v)
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("note")
    @classmethod
    def clean_note(cls, v: Optional[str]) -> Optional[str]:
        v = _clean(v)
        return v or None


class Task(BaseModel):
    id: int
    title: str
    note: Optional[str]
    priority: Priority
    due_date: Optional[date]
    color: Color
    done: bool
    created_at: str
    updated_at: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Loose limits here on purpose: real validation happens in auth.login, and every failure
    # returns the same generic message.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class Me(BaseModel):
    """What the browser learns about the signed-in user. Never includes the password hash."""
    username: str
    role: str
    csrf_token: str
