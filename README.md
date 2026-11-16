# 岚桥实验放量台 · LIFT LAB

把用户级随机实验从假设登记、异步统计分析连接到放量会议结论，保留可复算的证据。

## 场景与工作流

模拟客户为岚桥内容科技的 32 人增长团队。原流程从埋点导出 CSV，在表格里手算显著性，再把结论贴到会议纪要。前置收入差异、p 值误读和无法追溯的复算是三个主要痛点。上游为用户级随机分桶观测，下游为产品放量会议；本项目替代中间的分析、证据导出和结论归档。

演示数据为固定随机种子 42 生成的合成数据，2400 位独立用户，1:1 分组，前置和后置各 7 日收入。仅供演示，不是实际客户资料。三个演示假设复用同一数据以便体验，不能视作三个独立实验。

| 角色 | 页面 | 权限 | 典型动作 |
|---|---|---|---|
| admin | 全部六页 | 读、登记、运行、归档结论 | 实验负责人作会议结论 |
| analyst | 全部六页 | 读、登记、运行 | 分析师准备区间与导出 |
| viewer | 全部六页 | 只读与导出 | 产品观察员阅读证据 |

## 快速启动

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18200)；[后端接口文档](http://localhost:19200/docs)。Nginx 将 `/api/` 去掉后代理到后端，直接访问后端不加 `/api` 前缀。

| 账号 | 密码 | 角色 |
|---|---|---|
| admin@lanqiao.demo | Lanqiao2026! | admin |
| analyst@lanqiao.demo | Lanqiao2026! | analyst |
| viewer@lanqiao.demo | Lanqiao2026! | viewer |

首次启动自动 seed（`SEED_DEMO=true`），失败记日志而不阻断服务。再次执行根据用户邮箱、数据集名称、实验名查重，不覆盖已经存在的行。数据库通过 ORM metadata 首次建表；`backend/migrations/001_initial.sql` 提供索引迁移示例，后续结构修改需审查迁移，启动不会自动升级已存在表。

## 一天的研究剧本

1. 09:00 林序以 admin 登录，检查 `lanqiao-paywall-2026-11.csv` 的 2400 条观测和校验和。
2. 09:10 许禾以 analyst 登录，在实验配置登记“短文付费入口 v4”。
3. 选择已冻结的付费入口数据，写明可检验的业务假设。
4. 固定收入指标、α=.05，打开 CUPED 校正，提交分析。
5. 后台 worker 从 Redis 队列取任务，使用 Python 计算 Welch 与 CUPED 结果。
6. 09:15 在分析结果检查 1200/1200 样本量、p 值、区间和方差降低，并导出 CSV。
7. 周澄以 viewer 阅读同一证据；登记接口和归档接口均会拒绝该角色。
8. 10:00 林序记录“同意放量/继续观察/暂缓上线”及至少 8 字依据，在操作留痕追溯动作。

## 计算口径

- **Welch t 检验**：输入用户级后置收入与分组；差异为候选均值减对照均值。标准误 `sqrt(sA²/nA+sB²/nB)`，Welch–Satterthwaite 自由度，使用 SciPy Student t 双侧尾概率和 `1-alpha` 区间。输出两组均值、样本量、差异、自由度、标准误、p 值和上下界。
- **CUPED**：输入分桶前协变量 X 与后置收入 Y；合并样本估计 `theta=cov(Y,X)/var(X)`，计算 `Y_adj=Y-theta*(X-mean(X))` 后再做 Welch。输出原始与校正结果，方差降低口径为 `1-SE_adj²/SE_raw²`（可能为负），不把它解释为业务增长。
- **身份口径**：分析前先按登记时固定的身份版本把匿名标识并成自然人。同一人的同组收入（前置与后置）相加后只保留一条自然人观测；同一人跨对照组与候选组时整人隔离，不参与合并，并在结果页与导出中记录原因。样本量 n 为自然人数量，不是匿名标识数量。
- 至少每组 3 人；非有限值、空集、未知分组、组内零方差拒绝；启用 CUPED 时前置指标常量拒绝。固定种子、持久化观测与 SHA256 使结果可复算。算法见 `backend/src/services/statistics_service.py`。
- 假设用户独立随机分桶、协变量在分桶前取得；只支持单个预注册指标，未做序贯检验、多重比较、离群值稳健估计或聚类相关性校正。95% 区间仅在 α=.05 时成立，结果页按实际 alpha 标注。

## 匿名身份关联

观测页提供跨设备身份维护。关联表达“两个匿名标识属于同一人”，按连通性传递；撤销保留历史而不删除行。

- **合并与隔离**：同一人的同组收入相加后分析；跨组身份整人隔离并在页面、结果与导出中写明原因。观测预览与摘要始终标注所用身份版本。
- **可传递、可撤销**：`S0-S1` 与 `S1-S2` 生效时三人同组。撤销环内一条边（如再有的 `S2-S0`）若仍连通，归属不变且不写新分配；撤销桥边后按剩余连通性拆开并恢复各自样本。
- **只用受影响人群**：撤销先读取该边所在连通分量的链接，再只重算该分量；不扫描其余观测、关联或分配。
- **版本分批发布**：每次关联变更新建身份版本，改写的匿名标识按 `batch_size` 切批持久化。构建期间读取方继续使用上一完整版本；中断后 worker 启动时或页面“继续构建”会从首个未完成批次恢复；最后一批提交后在同一事务内切换状态。构建失败会清除本次已写入的分配并回滚链接图。
- **实验固定版本**：登记实验时锁定当时最新**已发布**版本。构建中登记不会混入半份新数据，之后发布的新版本也不改写既有实验证据；重跑沿用同一版本。
- 权限与入口：`POST /datasets/{id}/identity/links`、`POST .../links/{link_id}/revoke`、`POST .../versions/{id}/resume` 需 admin/analyst；版本、分组与观测状态只读接口对所有登录角色开放。审计记录关联、撤销、版本构建/发布/恢复。
- 并发保护由唯一索引承担：每个数据集最多一个构建中版本，同一有序匿名对最多一条生效关联。迁移见 `backend/migrations/002_identity_versions.sql`。

身份相关测试位于 `backend/tests/test_identity_service.py` 与 `backend/tests/test_identity_api.py`，覆盖传递合并、跨组隔离、环/桥撤销、只重算受影响人群、分批中断与恢复、构建失败不泄漏、构建期间登记、导出溯源。

## 功能与工程结构

六页：实验概览、观测数据（前100条预览、身份关联与版本）、实验配置（登记时锁定身份版本）、分析结果（真实 SVG 区间图、身份口径溯源）、决策档案、操作留痕。实体 Dataset / Observation / Experiment / AnalysisRun / Decision 及 IdentityLink / IdentityVersion / IdentityAssignment / IdentityWorkItem / ExperimentIdentity 各有数据模型与 API；API、前端 store、组件职责分开。

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、SQLAlchemy、SciPy / NumPy |
| 前端 | React 18、TypeScript、Vite、Lucide、Noto Serif SC + Noto Sans SC |
| 持久化 | PostgreSQL 16、Redis 7 AOF |
| 交付 | 恰好 5 个 Compose 服务，Nginx SPA 回落 |

```
backend/src/{models,schemas,services,controllers,routes,middlewares,utils,constants,config}
backend/src/{main.py,worker.py}
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql
frontend/src/{api,stores,types,components,hooks,pages,router,utils,constants}
```

后端 JWT 有效期 8 小时，PBKDF2-SHA256 存储密码；角色每次由数据库读取，不信任前端。审计覆盖登录、登记、运行完成、决策以及身份关联/撤销/版本构建与发布。两个独立中间件处理全局错误与登录频率限制（单进程内每 IP 每分钟30次）。文件导出需要认证。

共享枚举：角色位于 `backend/src/constants/roles.py` / `frontend/src/constants/enums.ts`，任务状态位于 `backend/src/constants/status.py` / `frontend/src/constants/enums.ts`，身份版本与批次状态位于 `backend/src/constants/identity.py`；后端服务持久化与接口使用同名值。`ExperimentSummary` 在概览、结果与决策页共用，`DataTable`、`StatusBadge`、`Notice` 提供一致交互。

## 环境变量与 Docker

| 变量 | 默认值 | 说明 |
|---|---|---|
| COMPOSE_PROJECT_NAME | lift-lab | 容器和网络命名空间 |
| FRONTEND_PORT | 18200 | Nginx 前端 |
| BACKEND_PORT | 19200 | FastAPI |
| DB_PORT | 17700 | PostgreSQL 宿主映射 |
| JWT_SECRET | local-demo-lift-lab-change-this-key | 本地演示签名密钥，部署前更换 |
| SEED_DEMO | true | 后端启动时 seed |

命名卷 `db-data`、`redis-data`、`artifacts`；backend/worker 共享产物卷。worker 使用2秒阻塞轮询并持续写心跳；身份重建按批处理并在批次间穿插分析任务，启动时重排未完成构建。5个容器均有健康检查。后端等待 db/redis healthy，前端等待 backend healthy。单 worker 运行；进程意外退出时已标为 running 的任务需要人工重新提交，本地演示未提供自动重试。停止并清空本项目数据：

```bash
docker compose down -v --remove-orphans
```

## 本地开发与验证

安装 Python 3.11，`pip install -r backend/requirements.txt`；将 `DATABASE_URL` / `REDIS_URL` 指向本机依赖，`cd backend && uvicorn src.main:app --reload`。前端 `cd frontend && npm install && npm run dev`；开发时需为 `/api` 配置反向代理到本地后端。标准启动以 Compose 为准。

身份测试不依赖 PostgreSQL / Redis：`cd backend && PYTHONPATH=. python3 -m pytest tests/ -q`。测试脚本、截图和真实结果在项目外 `../../test-artifacts/gb-0001/`。日志与 `.env` 不提交。该应用适合本地合成数据研究；不包含真实文件上传、邮件外发或生产级多实例限流；身份构建可跨 worker 重启恢复，但分析任务仍不自动重试。

## License

MIT，详见 LICENSE。
