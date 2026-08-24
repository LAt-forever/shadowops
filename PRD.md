# ShadowOps 一页式 PRD（Draft v0.1）

> 状态：待确认｜首版范围：PostgreSQL + Alembic Migration + 本地 Git 仓库 + Docker 影子环境｜本文件不代表已进入架构或开发阶段

## 1. 产品定位、用户与问题

**ShadowOps** 是面向 Python/PostgreSQL 团队的 Migration 安全审计工具：读取本地 Git 变更中的 Alembic revisions，以受限 Tool Calling 完成静态审计与 Docker 影子执行，输出可追溯的证据报告，并把高风险结论交给人审批。首要用户是提交 Migration 的后端工程师；次要用户是 Reviewer/Tech Lead。它解决三件事：代码审查看不出真实锁表、数据改写和回滚后果；本地验证步骤零散且不可复现；AI 建议缺少执行证据、权限边界和人工门禁。

**用户价值**：在不连接生产库、不修改真实仓库的前提下，于提交评审前得到“变更是什么、哪里危险、影子执行发生了什么、能否回滚、下一步由谁决定”的单次审计结果。**求职展示价值**：集中证明 Python 后端、单 Agent 规划/Tool Calling、可靠异步任务、Docker 隔离、Human-in-the-loop、可观测性与系统化评测，不以多 Agent 数量为卖点。

## 2. 典型流程与核心闭环

用户选择本地仓库及 `base..head`（默认工作区相对 `HEAD`）→ 系统识别 Alembic 配置、revision DAG 与本次新增/修改的 Migration → 静态规则产生初始风险与代码位置 → Agent 基于仓库元数据、规则结果和可用工具生成结构化执行计划 → 编排器校验计划并启动固定版本、受限资源的临时 PostgreSQL Docker 环境 → 升级到基线 revision → 应用目标 Migration → 加载用户 fixture 或生成最小合成数据 → 运行 schema/data 冒烟测试 → 执行 downgrade/upgrade 往返并比较关键 schema/data 证据 → 汇总规则命中、SQL/锁相关观察、耗时、日志、测试和回滚结果 → 输出证据化报告。低/中风险可直接完成；高风险或无法判定项进入 `AWAITING_APPROVAL`，由 Reviewer 批准或拒绝结论（不自动合并、不执行生产变更）。

## 3. MVP 边界与非目标

**MVP 必须有**：本地 Git 只读扫描；Alembic revision 解析；静态规则；一个规划 Agent 与一组窄接口工具；持久化异步任务；Docker PostgreSQL 影子库；fixture/确定性合成数据；upgrade、冒烟与 downgrade 验证；日志/步骤/工具调用/产物关联；风险分级、证据报告与高风险人工审批；失败后的超时、有限重试和容器清理；可复跑的评测集。

**明确不做**：Kubernetes、生产/远程数据库接入、多数据库、多租户、复杂 OAuth、自动提交/合并/修改真实仓库、通用聊天入口、开放 Shell 工具、多个自主 Agent、完整 SQL 性能压测或“绝对安全”承诺。首版只审计 Alembic Python migration；仓库内任意应用测试仅在显式配置且受隔离约束时运行。

## 4. Agent、工具与安全边界

Agent 输入是审计目标、revision 图、静态命中、环境能力和历史步骤；输出必须是版本化 JSON 计划及引用证据的报告，不直接获得 Docker socket、数据库管理员凭据或任意 Shell。建议的首批工具为：`discover_migrations`（定位配置/变更/revision DAG）、`read_revision`（受路径白名单约束读取）、`run_static_rules`、`inspect_schema`、`provision_shadow_db`、`upgrade_to_revision`、`apply_target_migrations`、`load_test_data`、`run_smoke_checks`、`verify_rollback_roundtrip`、`collect_evidence`、`request_human_approval`。每次调用记录输入摘要、输出摘要、耗时、错误与 correlation ID；编排器校验参数、顺序、超时、重试预算和风险策略。所有潜在副作用仅发生在一次性容器/卷内，默认禁外网、限制 CPU/内存/时长，结束时由 finally/finalizer 清理。

## 5. 任务状态机

`QUEUED → DISCOVERING → STATIC_ANALYSIS → PLANNING → PROVISIONING → BASELINE_READY → APPLYING → SEEDING → SMOKE_TESTING → ROLLBACK_VERIFYING → REPORTING → (AWAITING_APPROVAL → APPROVED | REJECTED) | COMPLETED`。任一步可进入 `FAILED` 或 `CANCELLED`；终态前统一经过资源清理并记录 `cleanup_status`。步骤具备幂等键、心跳、超时与有限重试；不可安全重试的 Migration apply 不原地重放，而是销毁影子环境后从已知基线重建。人工审批只接受基于报告版本的决策，报告变化后旧审批失效。

## 6. 首批风险规则（静态命中 + 动态证据）

1. `DROP TABLE/COLUMN/INDEX`、不可逆 `downgrade` 或 downgrade 缺失（高）；2. 大表上新增 `NOT NULL` 且无分阶段回填/默认策略（高/待动态确认）；3. 带非空默认值的新增列可能触发表重写，结合 PostgreSQL 版本判定（中/高）；4. 非并发创建索引可能长时间阻塞写入（中/高）；5. `CREATE INDEX CONCURRENTLY` 被放入事务或 Alembic 用法不合法（高）；6. 列类型变更、`USING` 表达式或全表数据转换（高）；7. 新增唯一约束/唯一索引遇到脏数据（动态失败即高）；8. 外键/检查约束直接验证，缺少 `NOT VALID` + 后续 `VALIDATE` 策略（中）；9. 原始 SQL、批量 `UPDATE/DELETE` 无边界或无回滚策略（高）；10. revision 链断裂、多 head、重复/错误 `down_revision`（高）。规则报告必须包含代码位置、触发条件、严重度、置信度、修复建议和可复核证据；表大小未知时标为“需确认”，不伪装成已知生产事实。

## 7. 评测集、指标与验收

建立版本化、可在 CI 本地复跑的 fixture 仓库：每个案例含基线 schema/数据、Alembic 变更、期望规则、期望风险级别、动态结果与是否需要审批。首批覆盖安全加列、危险 drop、NOT NULL 分阶段/非分阶段、索引并发正反例、类型转换、唯一冲突、外键验证、数据回填、不可逆回滚、断链/多 head，以及工具超时、容器失败、重复投递、审批过期等可靠性案例；同时保留若干组合案例检验 Agent 计划，而非只测单条规则。

**测量项**：Migration 识别准确率；规则 precision/recall/F1（按规则和严重度）；高风险漏报率；Agent 计划 schema-valid 率、工具选择/顺序正确率、无依据断言率；upgrade/rollback 判定准确率；任务成功率、重试恢复率、取消/超时后的资源清理率；报告证据可追溯率；审批门禁绕过次数；单任务端到端时延与 LLM/容器成本。所有结果记录模型、提示词、规则集、PostgreSQL 镜像和评测集版本。

**MVP 验收门槛（提案值，尚未测量）**：固定评测集全部可重复运行；所有标注为高风险的核心案例零漏报；审批门禁零绕过；终态任务均能查询完整步骤与证据；故障注入案例不重复产生不可控副作用且能完成资源清理；同一输入和固定配置的风险级别、规则命中及关键动态结论保持一致；Demo 可在全新机器按 README 以单条命令启动，并完整演示一个安全案例与一个需审批的高风险案例。precision/F1、时延和成本的具体数值门槛在首轮基线测量后设定，不预填成绩。

## 8. 待确认决策

1. 首版交互面是否定为 **CLI + 本地 Web 报告/审批页**（推荐），而不是先做完整 Web IDE？  
2. 默认审计范围是否采用 **工作区/暂存区相对 HEAD，并允许显式 `base..head`**？  
3. 测试数据策略是否采用 **仓库 fixture 优先、确定性合成数据兜底**，并明确首版不复制生产数据？  
4. 高风险审批的含义是否定为 **批准审计结论/允许团队继续人工流程**，而非 ShadowOps 执行合并或上线？  
5. MVP 支持的 PostgreSQL 主版本先固定一个（建议开发时依据目标岗位与本机环境选择），还是从一开始做 2 个版本矩阵？

