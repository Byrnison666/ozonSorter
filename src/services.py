import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import (
    Client, Shipment, ImportSession, AssignmentStatus, 
    DeliveryPointPolicy, DeliveryPoint
)
from .parser import ExcelParser

class ImportService:
    def __init__(self, db_session: Session):
        self.session = db_session
        self.parser = ExcelParser()

    def process_import(self, file_path: str) -> ImportSession:
        file_hash = self.parser.get_file_sha256(file_path)
        
        # Check if already imported
        existing_session = self.session.execute(
            select(ImportSession).where(ImportSession.source_file_sha256 == file_hash)
        ).scalar_one_or_none()
        
        # Note: Caller should handle warning if existing_session is found
        
        import_session = ImportSession(
            source_file_name=file_path.split('/')[-1],
            source_file_sha256=file_hash,
            started_at=datetime.now()
        )
        self.session.add(import_session)
        self.session.flush() # Get ID
        
        rows = self.parser.parse_file(file_path)
        import_session.total_rows = len(rows)
        
        logs = []
        
        for row in rows:
            import_session.total_rows += 1 # This is already len(rows), maybe count in parser?
            # Re-calculating correctly:
            pass
        
        # Reset counters for actual count
        import_session.total_rows = len(rows)
        import_session.kty_rows = 0
        import_session.matched_rows = 0
        import_session.new_to_ship_rows = 0
        import_session.already_on_point = 0
        import_session.not_ours_rows = 0
        import_session.errors_rows = 0

        for row_data in rows:
            posting_number = row_data['posting_number']
            ozon_client_id = row_data['ozon_client_id']
            
            if row_data['is_kty']:
                import_session.kty_rows += 1
                self._create_shipment(row_data, import_session.id, AssignmentStatus.EXCLUDED_KTY)
                continue
                
            # Find client
            client = self.session.execute(
                select(Client).where(Client.ozon_client_id == ozon_client_id, Client.is_active == True)
            ).scalar_one_or_none()
            
            if not client:
                import_session.not_ours_rows += 1
                self._create_shipment(row_data, import_session.id, AssignmentStatus.EXCLUDED_NOT_OURS)
                continue
            
            import_session.matched_rows += 1
            
            # Check existing shipment
            existing_shipment = self.session.execute(
                select(Shipment).where(Shipment.posting_number == posting_number)
            ).scalar_one_or_none()
            
            if existing_shipment:
                existing_shipment.last_seen_at = datetime.now()
                if existing_shipment.assignment_status == AssignmentStatus.ON_POINT:
                    import_session.already_on_point += 1
                    logs.append(f"Shipment {posting_number} already on point.")
                elif existing_shipment.assignment_status == AssignmentStatus.DELIVERED:
                    logs.append(f"WARNING: Shipment {posting_number} marked as DELIVERED but seen again in import.")
                else:
                    # Keep existing assignment status and point
                    pass
            else:
                # Create new shipment
                status = AssignmentStatus.TO_SHIP
                assigned_point = None
                
                if client.delivery_point_policy == DeliveryPointPolicy.FIXED:
                    status = AssignmentStatus.TO_SHIP
                    assigned_point = client.fixed_delivery_point
                else:
                    status = AssignmentStatus.TO_ASSIGN
                    assigned_point = None
                
                self._create_shipment(
                    row_data, 
                    import_session.id, 
                    status, 
                    client_id=client.id, 
                    assigned_point=assigned_point
                )
                if status == AssignmentStatus.TO_SHIP:
                    import_session.new_to_ship_rows += 1
                
        import_session.finished_at = datetime.now()
        import_session.log_json = json.dumps(logs)
        self.session.commit()
        return import_session

    def _create_shipment(self, row_data: Dict[str, Any], session_id: int, status: AssignmentStatus, client_id: Optional[int] = None, assigned_point: Optional[DeliveryPoint] = None):
        shipment = Shipment(
            posting_number=row_data['posting_number'],
            client_id=client_id,
            ozon_client_id_raw=row_data['ozon_client_id'] or "",
            product_label=row_data.get('product_label'),
            product_name=row_data.get('product_name'),
            ozon_type=row_data.get('type'),
            ozon_status=row_data.get('status'),
            cell=row_data.get('cell'),
            shipment_date_ozon=row_data.get('shipment_date_ozon'),
            is_damaged=row_data.get('is_damaged', False),
            is_kty=row_data.get('is_kty', False),
            assignment_status=status,
            assigned_point=assigned_point,
            import_session_id=session_id,
            first_seen_at=datetime.now(),
            last_seen_at=datetime.now()
        )
        self.session.add(shipment)
