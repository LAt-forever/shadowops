from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.agent.catalog import CAPABILITY_CATALOG
from shadowops.agent.contracts import (
    AgentInvocationV1,
    AuditPlanRecordV1,
    AuditPlanV1,
    PlanningResultV1,
    PlanStepV1,
    ReadOnlyToolName,
    ToolCallV1,
    ToolObservationV1,
)
from shadowops.domain.runs import AuditRun, RunState
from shadowops.persistence.models import AgentInvocationModel, AgentToolCallModel, AuditPlanModel
from shadowops.persistence.uow import SqlAlchemyUnitOfWork

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE audit_runs CASCADE"))
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _run(now: datetime) -> AuditRun:
    return AuditRun(
        id=uuid4(),
        state=RunState.PLANNING,
        version=4,
        repository_path="projects/demo",
        diff_mode="WORKING_TREE",
        idempotency_key=f"planning-{uuid4()}",
        request_fingerprint="a" * 64,
        created_at=now,
        updated_at=now,
    )


def _result(run: AuditRun, now: datetime) -> PlanningResultV1:
    invocation_id = uuid4()
    input_hash = "b" * 64
    prior: str | None = None
    steps: list[PlanStepV1] = []
    for specification in CAPABILITY_CATALOG:
        step_id = specification.name.value
        steps.append(
            PlanStepV1(
                id=step_id,
                capability=specification.name,
                depends_on=() if prior is None else (prior,),
                timeout_seconds=specification.max_timeout_seconds,
                required=True,
                reason="Persist the validated reference capability.",
                evidence_refs=("evidence:abc",),
            )
        )
        prior = step_id
    invocation = AgentInvocationV1(
        id=invocation_id,
        run_id=run.id,
        provider="fake",
        model="shadowops-reference-planner-v1",
        prompt_version="m3.planner.v1",
        tool_schema_version="m3.read-only-tools.v1",
        input_hash=input_hash,
        output_hash="c" * 64,
        status="SUCCEEDED",
        repair_attempts=0,
        started_at=now,
        completed_at=now,
    )
    observation = ToolObservationV1(
        tool_name=ReadOnlyToolName.DESCRIBE_SHADOW_CAPABILITIES,
        tool_version="1.0",
        data={"count": len(CAPABILITY_CATALOG)},
        evidence_ids=("evidence:abc",),
    )
    tool_call = ToolCallV1(
        id=uuid4(),
        invocation_id=invocation_id,
        run_id=run.id,
        sequence=1,
        tool_name=observation.tool_name,
        tool_version=observation.tool_version,
        arguments_hash="d" * 64,
        result_hash="e" * 64,
        duration_ms=1,
        correlation_id=str(run.id),
        observation=observation,
    )
    plan = AuditPlanRecordV1(
        id=uuid4(),
        run_id=run.id,
        invocation_id=invocation_id,
        input_hash=input_hash,
        plan=AuditPlanV1(objective="Audit migrations", steps=tuple(steps)),
        created_at=now,
    )
    return PlanningResultV1(invocation=invocation, tool_calls=(tool_call,), plan=plan)


def test_agent_plan_and_trace_are_queryable_and_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    run = _run(now)
    result = _result(run, now)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(run)
        uow.agent_planning.save_result(result)
        uow.commit()

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.agent_planning.save_result(result)
        uow.commit()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentInvocationModel)) == 1
        assert session.scalar(select(func.count()).select_from(AgentToolCallModel)) == 1
        assert session.scalar(select(func.count()).select_from(AuditPlanModel)) == 1
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.agent_planning.get_plan_for_run(run.id) == result.plan
