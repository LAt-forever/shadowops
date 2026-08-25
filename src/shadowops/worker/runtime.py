"""Worker process runtime dependencies."""

from functools import lru_cache

from shadowops.application.run_execution import RunExecutionService
from shadowops.config import get_settings
from shadowops.persistence.database import create_control_engine, create_session_factory
from shadowops.persistence.uow import SqlAlchemyUnitOfWork


@lru_cache
def get_execution_service() -> RunExecutionService:
    settings = get_settings()
    engine = create_control_engine(settings.database_url)
    sessions = create_session_factory(engine)
    return RunExecutionService(lambda: SqlAlchemyUnitOfWork(sessions))
