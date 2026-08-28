"""SQLAlchemy Unit of Work."""

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from shadowops.persistence.repositories import (
    SqlAlchemyAgentPlanningRepository,
    SqlAlchemyOutboxRepository,
    SqlAlchemyRepoSnapshotRepository,
    SqlAlchemyRevisionGraphRepository,
    SqlAlchemyRunRepository,
    SqlAlchemyRunStepRepository,
    SqlAlchemyStaticReportRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._committed = False

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.runs = SqlAlchemyRunRepository(self.session)
        self.steps = SqlAlchemyRunStepRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        self.snapshots = SqlAlchemyRepoSnapshotRepository(self.session)
        self.revision_graphs = SqlAlchemyRevisionGraphRepository(self.session)
        self.static_reports = SqlAlchemyStaticReportRepository(self.session)
        self.agent_planning = SqlAlchemyAgentPlanningRepository(self.session)
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self._committed:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.rollback()
