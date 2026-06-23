"""Логика экрана «Контроль посылок»: выборка наших посылок с фильтрами,
ручная смена статуса и сброс базы посылок по точке.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from .models import Shipment, AssignmentStatus, DeliveryPoint
from .parser import ExcelParser

# Статусы, которые видны/управляются в контроле (наши «живые» посылки).
CONTROL_STATUSES = (
    AssignmentStatus.TO_SHIP,
    AssignmentStatus.ON_POINT,
    AssignmentStatus.DELIVERED,
)


class ShipmentControlService:
    def __init__(self, db_session: Session):
        self.session = db_session

    def list_shipments(
        self,
        search: Optional[str] = None,
        point: Optional[DeliveryPoint] = None,
        status: Optional[AssignmentStatus] = None,
    ) -> List[Shipment]:
        """Наши посылки (client_id задан) с опциональными фильтрами.

        search ищется по номеру отправления и Ozon ID (с учётом ведущих нулей).
        """
        stmt = select(Shipment).where(
            Shipment.client_id.isnot(None),
            Shipment.assignment_status.in_(CONTROL_STATUSES),
        )
        if point is not None:
            stmt = stmt.where(Shipment.assigned_point == point)
        if status is not None:
            stmt = stmt.where(Shipment.assignment_status == status)

        shipments = self.session.execute(stmt).scalars().all()

        if search and search.strip():
            q = search.strip()
            qn = ExcelParser.normalize_ozon_id(q) if q.lstrip('0').isdigit() else None
            res = []
            for s in shipments:
                if q.lower() in (s.posting_number or "").lower():
                    res.append(s)
                elif qn and ExcelParser.normalize_ozon_id(s.ozon_client_id_raw or "0") == qn:
                    res.append(s)
            shipments = res

        shipments.sort(key=lambda s: (s.assigned_point.value if s.assigned_point else "",
                                      s.posting_number or ""))
        return shipments

    def set_status(self, shipment_id: int, new_status: AssignmentStatus) -> None:
        """Сменить статус посылки вручную, проставив/сбросив отметки времени."""
        shipment = self.session.get(Shipment, shipment_id)
        if shipment is None:
            return
        now = datetime.now()
        if new_status == AssignmentStatus.ON_POINT:
            shipment.shipped_to_point_at = now
            shipment.delivered_at = None
        elif new_status == AssignmentStatus.DELIVERED:
            if shipment.shipped_to_point_at is None:
                shipment.shipped_to_point_at = now
            shipment.delivered_at = now
        elif new_status == AssignmentStatus.TO_SHIP:
            shipment.shipped_to_point_at = None
            shipment.delivered_at = None
        shipment.assignment_status = new_status
        self.session.commit()

    def count_for_point(self, point: DeliveryPoint) -> int:
        return self.session.execute(
            select(func.count()).select_from(Shipment).where(
                Shipment.assigned_point == point
            )
        ).scalar_one()

    def reset_point(self, point: DeliveryPoint) -> int:
        """Удалить ВСЕ посылки точки из базы. Возвращает число удалённых."""
        n = self.count_for_point(point)
        self.session.execute(delete(Shipment).where(Shipment.assigned_point == point))
        self.session.commit()
        return n
