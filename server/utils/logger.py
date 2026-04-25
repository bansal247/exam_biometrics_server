"""Activity logging helper."""

import json
from sqlalchemy.ext.asyncio import AsyncSession
from models import AdminLog, SupervisorLog, OperatorLog, AuthLog


async def log_admin(db: AsyncSession, admin_id: str, action: str, details=None):
    db.add(AdminLog(admin_id=admin_id, action=action,
                    details=json.dumps(details) if isinstance(details, dict) else details))
    await db.flush()


async def log_supervisor(db: AsyncSession, supervisor_id: str, action: str, details=None):
    db.add(SupervisorLog(supervisor_id=supervisor_id, action=action,
                         details=json.dumps(details) if isinstance(details, dict) else details))
    await db.flush()


async def log_operator(db: AsyncSession, operator_id: str, action: str, details=None):
    db.add(OperatorLog(operator_id=operator_id, action=action,
                       details=json.dumps(details) if isinstance(details, dict) else details))
    await db.flush()


async def log_auth(db: AsyncSession, mobile=None, role=None, name=None, photo_id=None):
    db.add(AuthLog(mobile=mobile, role=role, name=name, photo_id=photo_id))
    await db.flush()
