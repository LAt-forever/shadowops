"""Fixed capability catalog; Agent output cannot redefine these controls."""

from shadowops.agent.contracts import CapabilityName, CapabilitySpecV1

CAPABILITY_CATALOG: tuple[CapabilitySpecV1, ...] = (
    CapabilitySpecV1(
        name=CapabilityName.PROVISION_SHADOW_DB,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=60,
    ),
    CapabilitySpecV1(
        name=CapabilityName.UPGRADE_BASELINE,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=180,
        prerequisites=(CapabilityName.PROVISION_SHADOW_DB,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.APPLY_TARGET_MIGRATIONS,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=180,
        prerequisites=(CapabilityName.UPGRADE_BASELINE,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.LOAD_TEST_DATA,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=120,
        prerequisites=(CapabilityName.APPLY_TARGET_MIGRATIONS,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.RUN_SMOKE_CHECKS,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=120,
        prerequisites=(CapabilityName.LOAD_TEST_DATA,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.VERIFY_ROLLBACK_ROUNDTRIP,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=300,
        prerequisites=(CapabilityName.RUN_SMOKE_CHECKS,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.COLLECT_EVIDENCE,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=60,
        prerequisites=(CapabilityName.VERIFY_ROLLBACK_ROUNDTRIP,),
    ),
    CapabilitySpecV1(
        name=CapabilityName.CLEANUP_SHADOW_ENVIRONMENT,
        version="1.0",
        mandatory=True,
        max_timeout_seconds=60,
        prerequisites=(CapabilityName.COLLECT_EVIDENCE,),
    ),
)

CAPABILITIES_BY_NAME = {item.name: item for item in CAPABILITY_CATALOG}
