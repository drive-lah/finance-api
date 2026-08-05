"""Minimal mapping of the shared `users` table (owned by the admin console).

finance-api runs against the same DB, and `hr_employees.user_id` has a FK to `users.id`.
Without this table in SQLAlchemy's metadata the FK target can't be resolved at flush time,
which broke employee create. We map only the columns HR reads/writes; the console owns the
full row. `extend_existing` keeps this a thin view over the real table.
"""
from datetime import date, datetime

from sqlalchemy import String, Integer, Boolean, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    is_employee: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    employee_type: Mapped[str | None] = mapped_column(String, nullable=True)
    manager_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    bank_code: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_joining: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
