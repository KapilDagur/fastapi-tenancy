"""ORM models for Projectr — used in both shared (RLS) and per-tenant schemas."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey,
    String, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ProjectStatus(str, Enum):
    ACTIVE    = "active"
    ARCHIVED  = "archived"
    DELETED   = "deleted"


class TaskStatus(str, Enum):
    TODO        = "todo"
    IN_PROGRESS = "in_progress"
    DONE        = "done"
    BLOCKED     = "blocked"


class Project(Base):
    __tablename__ = "projects"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id   = Column(String(255), nullable=False, index=True)
    name        = Column(String(500), nullable=False)
    description = Column(Text, default="")
    status      = Column(String(20), default=ProjectStatus.ACTIVE, nullable=False)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tasks = relationship("Task", back_populates="project", lazy="select", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    tenant_id  = Column(String(255), nullable=False, index=True)
    title      = Column(String(500), nullable=False)
    status     = Column(String(20), default=TaskStatus.TODO, nullable=False)
    assignee   = Column(String(255))
    due_date   = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    project = relationship("Project", back_populates="tasks")
