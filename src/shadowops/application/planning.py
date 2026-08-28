"""Reliable planning-stage orchestration and plan query service."""

from collections.abc import Callable
from uuid import UUID

from shadowops.agent.contracts import AuditPlanRecordV1
from shadowops.agent.runtime import AgentPlanner
from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import (
    AuditPlanNotReadyError,
    RepositoryInputError,
    RunNotFoundError,
)
from shadowops.domain.runs import AuditRun


class PlanningStageHandler:
    handler_version = "m3.planning.v1"

    def __init__(self, uow_factory: Callable[[], UnitOfWork], planner: AgentPlanner) -> None:
        self._uow_factory = uow_factory
        self._planner = planner

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            if uow.agent_planning.get_plan_for_run(run.id) is not None:
                return
        result = self._planner.plan(run.id)
        checkpoint()
        with self._uow_factory() as uow:
            uow.agent_planning.save_result(result)
            uow.commit()
        if result.plan is None:
            raise RepositoryInputError("PLAN_INVALID", "Agent plan failed validation after repair")


class AuditPlanQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self, run_id: UUID) -> AuditPlanRecordV1:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            plan = uow.agent_planning.get_plan_for_run(run_id)
        if plan is None:
            raise AuditPlanNotReadyError(run_id)
        return plan
