"""Схема данных (ТЗ 1.0, п. 4.3, Д-1…Д-9)."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):  # Д-9
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    must_change_password: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)


class MachineRow(Base):  # Д-5
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    model: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(128))
    is_cnc: Mapped[bool] = mapped_column(Boolean)
    work_zone: Mapped[list | None] = mapped_column(JSON, nullable=True)
    technologies: Mapped[list] = mapped_column(JSON, default=list)
    controller: Mapped[str] = mapped_column(String(128), default="")
    accuracy: Mapped[str] = mapped_column(String(128), default="")
    power: Mapped[str] = mapped_column(String(128), default="")
    hours_per_day: Mapped[float] = mapped_column(Float, default=6)
    maintenance: Mapped[list] = mapped_column(JSON, default=list)  # график ТО: в MVP не используется
    note: Mapped[str] = mapped_column(Text, default="")
    busy_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Дополнительные характеристики (в расчёте не используются, для справки технолога)
    axes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spindle_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_size: Mapped[str | None] = mapped_column(String(128), nullable=True)
    inventory_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Операции маршрута, которые выполняет оборудование (Stage.value): связь операций и станков
    operations: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Parameter(Base):  # Д-6
    __tablename__ = "parameters"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | float | str] = mapped_column(JSON)


class JournalRow(Base):  # Д-4
    __tablename__ = "journal"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_code: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    machine_code: Mapped[str] = mapped_column(String(32), default="")
    material: Mapped[str] = mapped_column(String(128), default="")
    dims: Mapped[list] = mapped_column(JSON)
    tolerance_grade: Mapped[int] = mapped_column(Integer)
    complexity: Mapped[int] = mapped_column(Integer)
    technology: Mapped[str] = mapped_column(String(32), default="milling")
    stages: Mapped[list] = mapped_column(JSON, default=list)
    measurements: Mapped[list] = mapped_column(JSON, default=list)
    step_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    step_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class OrderRow(Base):  # Д-1
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(64))
    # Шапка списка деталей заказчика (ФТ-32)
    customer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    customer_order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_list: Mapped[str | None] = mapped_column(String(256), nullable=True)
    positions: Mapped[list["PositionRow"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PositionRow.id")


class PositionRow(Base):  # Д-2, Д-3
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    order: Mapped[OrderRow] = relationship(back_populates="positions")
    designation: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    step_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    step_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    read_data: Mapped[dict] = mapped_column(JSON, default=dict)  # как считано из файлов
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # текущие значения (с правками)
    complexity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cooperation: Mapped[bool] = mapped_column(Boolean, default=False)
    analog_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_analog: Mapped[bool] = mapped_column(Boolean, default=False)
    route: Mapped[list] = mapped_column(JSON, default=list)
    machine_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    approved_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_calc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_calc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    baseline_finish: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def needs_link(self) -> bool:
        return not (self.step_path and self.pdf_path)


class FactRow(Base):  # Д-7
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str] = mapped_column(String(32))
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    entered_by: Mapped[str] = mapped_column(String(64))
    entered_at: Mapped[datetime] = mapped_column(DateTime)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    calc_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class HistoryRow(Base):  # Д-8
    __tablename__ = "history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    username: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(64))
    old: Mapped[object] = mapped_column(JSON, nullable=True)
    new: Mapped[object] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class OperationRow(Base):
    """Операция утверждённой позиции на оборудовании: учёт «Начал» / «Закончил»."""
    __tablename__ = "operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str | None] = mapped_column(String(32), nullable=True)
    planned_hours: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)  # кому; None — мастеру
    key: Mapped[str] = mapped_column(String(64), unique=True)
    position_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    planned_at: Mapped[datetime] = mapped_column(DateTime)
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
