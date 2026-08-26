# ShadowOps MVP 开发计划 v0.1

> 状态：已确认（2026-08-25）｜依据：[PRD](../PRD.md) v0.1、[架构设计](./ARCHITECTURE.md) v0.1｜阶段边界：本文只规划实现，不创建脚手架或业务代码

## 1. 实施策略

采用 **walking skeleton + 纵向切片**：先打通 `CLI → API → Control DB → Outbox → Worker → 状态查询`，再逐步加入仓库发现、规则、Docker 动态验证、Agent、报告和审批。每个里程碑都必须有可运行结果和自动化验收，不按“先写完所有模型、再写所有接口”的横向方式堆积未集成代码。

实施原则：

- 确定性逻辑先行，真实 LLM 后接入；Fake/Recorded Provider 必须先让 CI 可复现。
- 先证明单个安全案例的完整闭环，再扩规则数量和 UI 精度。
- 所有副作用先定义幂等键、超时、清理与失败语义，再实现 happy path。
- 代码只声明已验证的 PostgreSQL 16/Alembic compatibility profile。
- 每个里程碑独立分支/PR，建议使用 `codex/m<N>-<topic>`；文档、迁移和测试与实现同 PR。
- 未真实测量的准确率、成功率、时延和成本只记录为待测指标，不写成绩。

## 2. 里程碑与退出门槛

### M0 — 工程基线与可启动控制面

**目标**：建立可复现但最小的 Python 工程，不实现审计逻辑。

**交付**：`pyproject.toml`/`uv.lock`、Python 3.12、代码质量配置、FastAPI health endpoints、Typer `version/ping`、SQLAlchemy 连接、Control Alembic、Celery app、Compose 的 `api/worker/control-postgres/redis`、结构化日志骨架、CI 快速测试。

**退出门槛**：全新 checkout 可按 README-dev 启动；API readiness 能区分 DB/Redis 不可用；空测试、lint、type check 全通过；没有业务表以外的审计假实现。

### M1 — 可靠任务 walking skeleton

**目标**：创建一个不做实际审计的 run，并可靠地从 `QUEUED` 推进到 `COMPLETED`。

**交付**：`audit_runs/run_steps/outbox_events` 表；Unit of Work；`POST/GET /runs`；outbox dispatcher；Celery 消费；状态迁移器；幂等 key；乐观版本；心跳；SSE；取消请求；Reconciler 最小实现。

**退出门槛**：重复 HTTP 请求、重复 broker 消息不会创建重复 run/step；重启 API/Worker 后 run 可恢复；非法状态跳转被 domain 层拒绝；状态时间线可查询。

### M2 — 不可变快照、Alembic 发现与首批静态规则

**目标**：完成第一个真实价值切片：本地仓库输入可产生证据化静态报告。

**交付**：拆成两个可审阅 PR。M2A 完成允许根目录校验、Git selector、安全快照与 hash、AST revision 解析、线性 revision chain 和持久化；M2B 完成静态 finding schema、4 条聚焦规则（破坏性 DDL、直接新增 NOT NULL、非并发索引、缺失/不可逆 downgrade）、fixture repositories 与 JSON 静态报告。

**退出门槛**：安全加列与高风险 drop 两个 fixture 可端到端运行；路径穿越/symlink 逃逸被拒绝；宿主进程不 import fixture migration；多 head/动态 revision 产生明确的 unsupported/high-risk finding；每个 finding 带文件位置和 evidence id。

### M3 — 计划契约、受限 Tool Gateway 与 Fake Agent

**目标**：先固定 Agent 的边界和可测协议，再接真实模型。

**交付**：`AuditPlanV1` JSON Schema/Pydantic model；capability catalog；Plan Validator；只读 Tool Gateway；Fake Provider；Agent invocation/tool call 持久化；确定性 reference plan；一次 schema repair 机制；prompt/tool schema 版本字段。

**退出门槛**：Agent 不能提交命令字符串、镜像、网络或宿主路径；缺少 mandatory step 的计划被补全或拒绝；无效输出在修复预算耗尽后可靠失败；相同 Fake 输入产生相同计划与 trace。

### M4 — Docker Resource Manager 与 Alembic upgrade

**目标**：在双容器影子环境中跑通 baseline + target upgrade，并无残留资源。

**交付**：Runner 协议/镜像；PostgreSQL 16 shadow container；internal network；资源标签/lease；非 root/只读/capability/resource limits；数据库 readiness；baseline upgrade；target apply；statement timeout；stdout/stderr artifact；finalizer；Sweeper。

**退出门槛**：安全 fixture upgrade 成功；错误 Migration 产生结构化失败；Runner 无 Docker socket、控制库 DSN 或 LLM secret；成功/失败/取消后容器、网络和卷均被验证清理；相同数据库 generation 上不重放 apply。

### M5 — 数据、Smoke Checks 与 Rollback Roundtrip

**目标**：补齐动态安全审计闭环。

**交付**：fixture manifest；固定 seed 合成生成器；coverage gap；schema fingerprint；row/constraint smoke checks；downgrade/upgrade roundtrip；关键 data/schema diff；artifact store 原子写入与 hash；证据脱敏。

**退出门槛**：唯一冲突、不可逆 downgrade、类型转换失败和安全 roundtrip fixture 均产生正确动态证据；不支持的数据类型不会被误报为已覆盖；报告能区分 shadow observation 与生产未知。

### M6 — 真实 Agent、风险报告与确定性 Policy Engine

**目标**：让模型参与计划和证据解释，但不能改变安全底线。

**交付**：`LLMProvider` 接口与首个真实 provider adapter；Planner/Reporter prompts；read-only evidence tools；`RiskReportV1`；静态/动态严重度聚合；无依据断言检查；token/latency/error 元数据；Fake/Recorded/live 三种运行模式。

**退出门槛**：高风险规则或 rollback 失败时，模型输出无法降低最终风险；事实型报告条目必须引用 evidence id；LLM 超时、限流、畸形输出可诊断并按预算恢复；CI 不依赖真实 API key。Provider 和模型的最终选择在 M6 开始前单独确认，不写死在 domain 层。

### M7 — 本地 Web 报告与 Human-in-the-loop

**目标**：完成用户可演示闭环。

**交付**：任务列表/详情、SSE 时间线、findings/工具调用/动态证据页面、报告 HTML、`AWAITING_APPROVAL`、approve/reject 表单、report hash、actor/comment、CSRF/local session secret、CLI `open/cancel`。

**退出门槛**：低/中风险 run 自动完成；高风险 run 必须暂停；过期 report hash 无法审批；批准/拒绝不会触发 Git 或生产副作用；安全与高风险案例可从 CLI 创建并在 Web 完整查看。

### M8 — 可靠性、安全、可观测性与系统化评测

**目标**：把“能跑”提升为可用于面试证明的工程闭环。

**交付**：故障注入；orphan recovery；queue lag/step latency/retry/cleanup metrics；trace spans/OTLP；评测 runner；规则标注集；Agent grader；结果版本元数据；资源与成本采集；安全测试；基线结果文件。

**退出门槛**：固定评测集可复跑；核心高风险案例零漏报；门禁零绕过；Worker kill、Redis 暂时不可用、重复投递、Migration hang、LLM malformed、清理首次失败等案例均有已验证结论。其余 precision/F1/时延/成本目标只在得到基线数据后设定。

### M9 — README、Demo 与求职包装

**目标**：把真实实现和真实测量结果变成可复核作品集。

**交付**：一键启动 README；架构/威胁模型；安全+高风险 Demo script；截图或短视频；评测报告；故障恢复演示；API 示例；简历项目描述与面试讲解提纲。

**退出门槛**：新环境按文档能启动；Demo 不依赖手工改数据库；README 中所有指标可追溯到评测产物；简历不宣称未支持数据库、生产接入或未测性能。

## 3. 目标目录结构

```text
shadowops/
├── pyproject.toml
├── uv.lock
├── compose.yaml
├── .env.example
├── src/shadowops/
│   ├── api/                 # FastAPI routes, schemas, SSE, templates/static
│   ├── cli/                 # Typer commands; calls local API
│   ├── application/         # Commands, queries, use-case services, UoW ports
│   ├── domain/              # Run state, policy, errors, events; no framework imports
│   ├── persistence/         # SQLAlchemy models/repos/UoW/outbox
│   ├── worker/              # Celery tasks, orchestrator, dispatcher/reconciler/sweeper
│   ├── repository/          # Git selectors, snapshot, Alembic AST discovery
│   ├── rules/               # Registry, rule implementations, finding models
│   ├── agent/               # Runtime, contracts, prompts, tools, provider adapters
│   ├── sandbox/             # Docker manager, lease/finalizer, runner client
│   ├── evidence/            # Artifact store, collectors, hashing, redaction
│   ├── reporting/           # RiskReport builder and HTML rendering
│   ├── observability/       # Logging, metrics, tracing
│   └── config.py
├── runner/
│   ├── Dockerfile
│   └── src/shadowops_runner/ # Fixed entrypoint and versioned request/result protocol
├── migrations/control/       # ShadowOps control-plane Alembic migrations
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   ├── fault_injection/
│   └── fixtures/repos/       # Versioned miniature Alembic repositories
├── evals/
│   ├── cases/
│   ├── labels/
│   ├── graders/
│   └── results/              # Only measured, versioned baselines
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT_PLAN.md
│   ├── adr/
│   └── threat-model.md
└── scripts/                  # Small deterministic dev/demo helpers only
```

边界规则：`domain` 不 import FastAPI/Celery/SQLAlchemy/Docker/LLM SDK；`api` 不 import Docker 实现；`agent` 不 import Docker SDK 或 approval repository；`runner` 是独立构建上下文，不能 import control-plane package；测试 fixture 不进入生产包。

## 4. 版本化接口契约

实现前先固定以下 Pydantic/JSON Schema；数据库模型不能直接充当公开 DTO。

| 契约 | 生产者 → 消费者 | 必备内容 |
|---|---|---|
| `CreateAuditRunRequestV1` | CLI/Web → API | repo relative path、diff mode、base/head、idempotency key |
| `AuditRunViewV1` | API → CLI/Web | state/version、current step、risk summary、timestamps、links |
| `RevisionGraphV1` | Discovery → Rules/Agent | resolved refs、revision nodes、target chain、unsupported reasons |
| `StaticFindingV1` | Rule → Policy/Report | rule/version、severity/confidence、location、message、evidence refs |
| `AuditPlanV1` | Agent → Plan Validator | allowlisted capabilities、DAG、timeouts、reasons、coverage gaps |
| `RunnerRequestV1` | Orchestrator → Runner | action enum、revision refs、DB alias、budgets；禁止 command string |
| `RunnerResultV1` | Runner → Orchestrator | status/error code、timings、current revision、artifact manifest |
| `EvidenceItemV1` | Any producer → Store/Report | kind、source、observation scope、sha256、redaction status |
| `RiskReportV1` | Reporter/Policy → UI | findings、dynamic results、unknowns、citations、risk、report hash |
| `ApprovalDecisionV1` | UI → API | report id/hash、decision、actor、comment、expected run version |

### 关键端口

- `RunRepository` / `UnitOfWork`：状态领取和原子迁移。
- `EventPublisher`：仅接受 outbox event，不允许业务代码直接 publish broker。
- `ArtifactStore.put/get`：内容寻址、原子写、hash 校验。
- `Snapshotter.create`：允许根目录内输入 → immutable snapshot。
- `Rule.evaluate(context) -> list[StaticFindingV1]`：无 I/O 或显式只读依赖。
- `AgentProvider.invoke(request) -> AgentResponse`：domain 不依赖具体 SDK。
- `ToolGateway.call(read_only_tool, args)`：路径和结果大小受限。
- `SandboxManager.provision/execute/finalize`：只有 Worker adapter 可调用。
- `PolicyEngine.decide(evidence) -> PolicyDecision`：纯函数、严重度单调不降。

错误使用稳定 `error_code`，至少区分 `INVALID_INPUT`、`UNSUPPORTED_REPOSITORY`、`PLAN_INVALID`、`LLM_TRANSIENT`、`SANDBOX_UNAVAILABLE`、`MIGRATION_FAILED`、`CHECK_TIMEOUT`、`ROLLBACK_FAILED`、`CLEANUP_FAILED` 与 `STALE_APPROVAL`。原始异常仅写入受控日志，不直接暴露密钥或宿主路径。

## 5. 测试与评测矩阵

| 层级 | 主要对象 | 必测行为 | 默认 CI |
|---|---|---|---:|
| Unit | state machine、policy、plan validator、rules、redaction | 边界、非法状态、严重度单调性、确定性 | 是 |
| Contract | API/Agent/Runner/Report schemas | backward compatibility、unknown fields、错误码 | 是 |
| Persistence integration | PostgreSQL/outbox/UoW | 原子提交、并发领取、重复消息、乐观锁 | 是，容器 lane |
| Broker/Worker integration | Redis/Celery | 投递、retry、heartbeat、cancel、reconcile | 是，容器 lane |
| Repository security | snapshot/Git/AST | traversal、symlink、oversize、dynamic revision、秘密排除 | 是 |
| Sandbox integration | Docker/Runner/PostgreSQL 16 | limits、network、upgrade、timeout、cleanup | 是，Docker lane |
| E2E | 安全与高风险 fixture | CLI/API/Worker/Runner/Report/Approval 全链路 | 是，Fake Agent |
| Fault injection | Worker/Redis/LLM/Docker/Migration | kill、断连、重复、hang、malformed、orphan | 定期/合并前 |
| Agent eval | Planner/Reporter | schema-valid、工具顺序、coverage、无依据断言 | Recorded；live 手动 |
| Rule eval | 标注 fixture | per-rule precision/recall/F1、高风险漏报 | 是 |

测试数据原则：一个 case 一个明确风险事实；组合 case 只用于测试规划与聚合。每个 case 固定 PostgreSQL image digest、seed、rule set、prompt/tool schema、provider/model 或 recorded response 版本。

### 首批 E2E 案例

1. 安全 nullable column：自动完成，无审批。
2. `DROP COLUMN`：静态高风险，动态可执行仍必须审批。
3. 唯一索引与重复数据：动态失败，高风险。
4. 非并发索引/错误 concurrent transaction：规则命中。
5. NOT NULL 无回填与分阶段安全版本：正反对照。
6. 类型转换失败：保留 SQL/step 证据。
7. downgrade 缺失或不可逆：rollback failed。
8. revision 断链/多 head：不进入不安全动态执行。
9. Migration hang：statement timeout、取消、资源清理。
10. 过期 report hash：审批拒绝且无状态越权。

## 6. CI、分支与完成定义

每个 PR 至少经过：format/lint、type check、unit、contract、security snapshot tests。需要 PostgreSQL/Redis/Docker 的测试放入明确的 integration lane；live LLM 测试不作为普通 PR 的硬依赖，使用 Fake/Recorded Provider 保证可复现。

一个里程碑只有同时满足以下条件才算完成：

- 代码、control migration、测试、日志/指标和文档同时更新。
- 正常、失败、重试、取消、清理路径都有证据。
- `git diff --check`、静态检查和对应测试通过。
- 没有跳过测试、硬编码秘密、未清理容器或延期补做的核心事项。
- Demo 行为与 README/架构声明一致。
- 所有完成声明附实际命令和结果；未运行的测试明确标注。

## 7. Superpowers 与 Codex skills 使用决策

Superpowers **不是 ShadowOps 的代码依赖，也不是正式开发的前置条件**。Codex skills 是给开发 Agent 使用的可复用工作流；只有当前会话列出的 skill 才视为可用。用户已于 2026-08-25 从插件市场安装 Superpowers，第四阶段开始按任务触发适用 skill。

如果正式开发前启用 Superpowers，建议按任务精确使用：

| Skill | 建议时机 | 是否必要 |
|---|---|---:|
| `test-driven-development` | state machine、policy、rule、plan validator | 可选但推荐 |
| `systematic-debugging` | 可复现失败或集成故障 | 可选 |
| `verification-before-completion` | 每个里程碑交付前 | 可选但推荐 |
| `requesting-code-review` / `receiving-code-review` | 里程碑 PR | 可选 |
| `writing-plans` | 本计划后续发生重大改版时 | 当前不需要重复使用 |
| `subagent-driven-development` / `dispatching-parallel-agents` | 只有用户明确要求委派/并行 Agent 时 | 默认不用 |

若某个 skill 被启用并触发，先读取其完整 `SKILL.md`，并让用户知道它如何影响当前动作。不要把 Codex skill 的存在写入 ShadowOps README 的运行要求；招聘方应能在没有这些 skills 的环境中构建和验证项目。

## 8. 正式开发前的启动清单

1. 用户确认本开发计划和里程碑顺序。
2. 提交并推送 PRD、架构与开发计划文档。
3. 启动 Docker Desktop，验证 Docker server 可连接。
4. 创建 `codex/m0-foundation` 分支。
5. M0 初始化时再生成 Python 3.12/uv 工程与 Compose，不提前建空目录。
6. 到 M6 前确认真实 LLM provider、模型、API key 管理和可接受成本；此前统一使用 Fake/Recorded Provider。
7. Superpowers 已启用；使用 `writing-plans`、`using-git-worktrees`、`test-driven-development`、`verification-before-completion` 等匹配当前任务的流程，但不把它加入项目依赖。

本计划已于 2026-08-25 确认。第四阶段从 M0 开始实现；每完成一个里程碑先报告实际验证结果，再进入下一个。
