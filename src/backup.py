import os
import shutil
from datetime import datetime
from src.database import APP_DATA_DIR, DEFAULT_DB_PATH

def backup_database():
    backup_dir = os.path.join(APP_DATA_DIR, 'backups')
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
        
    if not os.path.exists(DEFAULT_DB_PATH):
        return
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"ozon_sorter_{timestamp}.db.bak"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    shutil.copy2(DEFAULT_DB_PATH, backup_path)
    
    # Keep last 30 backups
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.bak')])
    if len(backups) > 30:
        for old_backup in backups[:-30]:
            os.remove(os.path.join(backup_dir, old_backup))
