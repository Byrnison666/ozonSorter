from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, 
    Enum, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class DeliveryPoint(str, PyEnum):
    KOMSOMOLSKAYA_4 = "KOMSOMOLSKAYA_4"
    KOLTSEVAYA_16 = "KOLTSEVAYA_16"

class AssignmentStatus(str, PyEnum):
    TO_ASSIGN = "TO_ASSIGN"
    TO_SHIP = "TO_SHIP"
    ON_POINT = "ON_POINT"
    DELIVERED = "DELIVERED"
    EXCLUDED_NOT_OURS = "EXCLUDED_NOT_OURS"
    EXCLUDED_KTY = "EXCLUDED_KTY"

class Client(Base):
    __tablename__ = "clients"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ozon_client_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String)
    phone: Mapped[Optional[str]] = mapped_column(String)
    # Точка выдачи клиента. На уровне БД nullable (безопасная миграция старых строк),
    # на уровне UI/импорта — обязательна: у каждого клиента известна точка.
    fixed_delivery_point: Mapped[Optional[DeliveryPoint]] = mapped_column(
        Enum(DeliveryPoint)
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    shipments: Mapped[List["Shipment"]] = relationship(back_populates="client")

    __table_args__ = (
        Index("idx_clients_is_active", "is_active"),
    )

class ImportSession(Base):
    __tablename__ = "import_sessions"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_file_name: Mapped[str] = mapped_column(String)
    source_file_sha256: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    kty_rows: Mapped[int] = mapped_column(Integer, default=0)
    matched_rows: Mapped[int] = mapped_column(Integer, default=0)
    new_to_ship_rows: Mapped[int] = mapped_column(Integer, default=0)
    already_on_point: Mapped[int] = mapped_column(Integer, default=0)
    not_ours_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors_rows: Mapped[int] = mapped_column(Integer, default=0)
    log_json: Mapped[Optional[str]] = mapped_column(Text)

    shipments: Mapped[List["Shipment"]] = relationship(
        back_populates="import_session", foreign_keys="Shipment.import_session_id"
    )

class Shipment(Base):
    __tablename__ = "shipments"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    posting_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"))
    ozon_client_id_raw: Mapped[str] = mapped_column(String, nullable=False)
    product_label: Mapped[Optional[str]] = mapped_column(String)
    product_name: Mapped[Optional[str]] = mapped_column(String)
    ozon_type: Mapped[Optional[str]] = mapped_column(String)
    ozon_status: Mapped[Optional[str]] = mapped_column(String)
    cell: Mapped[Optional[str]] = mapped_column(String)
    shipment_date_ozon: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_damaged: Mapped[bool] = mapped_column(Boolean, default=False)
    is_kty: Mapped[bool] = mapped_column(Boolean, default=False)
    barcode: Mapped[Optional[str]] = mapped_column(String)
    assignment_status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus), nullable=False
    )
    assigned_point: Mapped[Optional[DeliveryPoint]] = mapped_column(
        Enum(DeliveryPoint)
    )
    import_session_id: Mapped[int] = mapped_column(ForeignKey("import_sessions.id"))
    last_seen_import_session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("import_sessions.id")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    shipped_to_point_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    client: Mapped[Optional["Client"]] = relationship(back_populates="shipments")
    import_session: Mapped["ImportSession"] = relationship(
        back_populates="shipments", foreign_keys=[import_session_id]
    )

    __table_args__ = (
        Index("idx_shipments_assignment_status", "assignment_status"),
        Index("idx_shipments_assigned_point", "assigned_point"),
        Index("idx_shipments_ozon_client_id_raw", "ozon_client_id_raw"),
        Index("idx_shipments_last_seen_import_session_id", "last_seen_import_session_id"),
    )

class ExportSession(Base):
    __tablename__ = "export_sessions"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_session_id: Mapped[int] = mapped_column(ForeignKey("import_sessions.id"))
    delivery_point: Mapped[DeliveryPoint] = mapped_column(Enum(DeliveryPoint), nullable=False)
    export_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    file_path: Mapped[str] = mapped_column(String)
    shipments_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
