# CLAUDE.md · 案理项目协作规则

> 本文件是编码 Agent 在本仓库的长期行为守则。它是软指令，真正的硬约束是文件系统权限、沙箱和审批机制。
> 不要在本文件里复制 PRD 或架构全文，只放指引、命令、目录、红线和流程。

## 1. 项目介绍

「案理」是一个法律维权决策智能体：用户描述民事纠纷并选择身份，系统完成案情理解、法条与类案检索、双方对抗、质证复核（原「红队挑刺」）与多轮修订，最终交付诉求可行性评估与行动方案。

本项目参加杭州智能体大赛「AI+超级智能体赛道」。

## 2. 开工前必读

按顺序只读与本轮任务相关的部分：

1. `PROGRESS.md` —— 当前进度与下一步。
2. `PRD.md` —— 需求与验收标准（只读相关章节）。
3. `BACKEND_STRUCTURE.md` —— 改后端前必读。
4. `APP_FLOW.md` / `FRONTEND_GUIDELINE.md` —— 改流程或前端前必读。

不要一次性加载全部文档。

## 3. 目录说明

```
anli-agent/
├── PRD.md                     产品需求
├── APP_FLOW.md                用户流程与异常分支
├── FRONTEND_GUIDELINE.md      前端规范
├── BACKEND_STRUCTURE.md       后端结构与边界
├── CLAUDE.md                  Agent 协作规则
├── PROGRESS.md                当前进度（唯一进度真相来源）
├── DEMO_SCRIPT.md             演示剧本与录制说明
├── LICENSE                    MIT
├── .env.example               密钥模板（复制为 .env 后填写；交付包不含 .env）
├── app.py                     exe 入口
├── 启动案理.cmd               双击启动（纯 ASCII）
├── anli/                      后端源码
│   ├── orchestrator.py        编排层：阶段、闸门、交付物组装
│   ├── agents.py              六个角色智能体 + 两套 stance playbook
│   ├── web.py                 HTTP + SSE + 导出路由 + 视图组装
│   ├── cli.py                 命令行 demo / ask
│   ├── state.py               状态机 + Budget + Checkpoint
│   ├── events.py              18 类结构化事件 + EventBus
│   ├── models.py              数据模型
│   ├── intake.py              个案信息抽取（规则版）
│   ├── llm_intake.py          大模型案情抽取
│   ├── llm.py                 OpenAI 兼容客户端（标准库）
│   ├── item_assessment.py     逐项三态认定（supported / standard / unsupported）
│   ├── standards.py           法定标准表（浙江省 10 条，带出处）
│   ├── evidence_chain.py      补证清单 + 证据充分度
│   ├── feasibility.py         可行性评估 + 证据闸门
│   ├── strategy.py            策略建议 + 结论摘要
│   ├── cross_exam.py          结构化质证争点
│   ├── judgment.py            模拟判决书
│   ├── exporter.py            报告导出（Markdown）
│   ├── real_cases.py          实证类案库（仅 human_verified 进流程）
│   ├── materials.py           上传材料正文解析（按后缀分派，读不到就如实标注）
│   ├── material_text.py       材料与判决书共用的文本抽取规则
│   ├── redact.py              对外发送前的个人信息打码
│   ├── planner.py             规划决策（检索词 / 轮次 / 路径排序），模型参与 + 规则兜底
│   ├── stores.py              LawStore / CaseStore / CitationVerifier
│   ├── retrieval_flk.py       国家法律法规数据库回查 + TTL 缓存
│   ├── scope.py               刑事行政拦停 + 转人工闸门
│   ├── tools.py               工具注册与路由
│   ├── tools_builtin.py       内置工具注册
│   ├── regions.py             省级行政区归一化
│   ├── scenarios.py           演示案例
│   └── paths.py               资源路径（兼容源码 / 打包）
├── web/                       前端（index.src.html 为源码，index.html 为构建产物）
├── data/                      laws.seed / cases.seed / cases.official / real_cases / standards
├── tools/                     构建、抓取、打包、自测、判决书提取
├── tests/                     27 个测试文件
└── dist/                      打包产物（不含密钥与运行数据）
```

## 4. 工作流程

1. 先读需求与现有代码，用自己的话说明将要改动什么、为什么。
2. 涉及架构或接口的改动，先更新 `BACKEND_STRUCTURE.md`，再动代码。
3. 改动保持最小范围，不顺手动无关文件。
4. 改完必须运行对应测试；无法运行时说明原因。
5. 每完成一个阶段，更新 `PROGRESS.md` 并做结果摘要。

## 5. 红线（不得违反）

- 不得编造法条、案号、判例、裁判要旨或金额。
- 不得让未经引用核验的内容进入交付物。
- 不得把「法律依据」与「类案实践」合并成单一结论。
- 不得输出「胜诉」「一定能赢」等确定性承诺。
- 不得为刑事案件、行政案件提供分析结论。
- 不得在未经用户确认的情况下对外发送文书或提交材料。
- 不得把用户上传的敏感材料写入对外产物或第三方服务。
- 不得绕过 Tool Allowlist、Sandbox 和审批机制。
- 不得把 API Key 写进代码、文档或提交到版本库。
- 模型生成的审查意见必须标注「需核验」，不得直接当作法律依据。

## 6. 安全与权限

- Subagent 权限不得高于 Orchestrator，后者不得高于用户授权。
- 高风险动作必须走人工确认，Prompt 约束不作为安全边界。
- 所有工具调用记录 Trace，便于复现与追责。

## 7. 测试与验证

- 引用核验、无类案、金额区间、异常路径、端到端五类测试为必跑项。
- 交付物中的每条结论都必须可回溯到来源。
- 演示主线案例必须每次可复现。

## 8. 常用命令

运行环境：Python 3.12（本机路径 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`），仅使用标准库。

```powershell
# 运行主线演示案例
python -m anli.cli demo

# 用一段真实案情描述跑完整流程（默认调用大模型）
python -m anli.cli ask --desc "你的案情描述" --expect 12000

# 只用规则版，不调用大模型（离线可用）
python -m anli.cli demo --no-llm

# 打印事件流（阶段、工具调用、核验结果）
python -m anli.cli demo --verbose

# 输出机器可读结果
python -m anli.cli demo --json

# 运行全部测试
python -m unittest discover -s tests -v

# 重新打包 exe（产物在 dist\案理.exe）
python -m PyInstaller --noconfirm --onefile --console --name 案理 `
  --add-data "web;web" --add-data "data;data" app.py

# 端到端自检（真实 HTTP + 无头浏览器口径，34 项检查）
python tools/selftest.py

# 改了前端源码后必须重新构建自包含页面
python tools/build_web.py

# 实证类案录入：判决书半自动提取 → 人工核对 → 入库
python tools/case_extractor.py --file 判决书.txt
python tools/cases_cli.py list
```

工作目录为 `anli-agent/`。

注意：**交付包不带 `.env`**。首次运行请把 `.env.example` 复制为 `.env`（或 `dist\.env`），
填入自己的密钥；没有密钥时流程自动退回规则版，功能可用但理解能力下降。

隐私相关开关：

- 案情描述发给大模型前**默认自动脱敏**（手机号、身份证、银行卡、详细住址）；
  只有显式设置 `ANLI_SEND_RAW_TEXT=1` 才发送原文，设置后视图会标注「已发送原文」。
- 上传材料的正文解析结果会随任务写入检查点；`excerpt` 是脱敏后的片段，原文不进视图。

## 9. 大模型配置

- 密钥放在项目根目录 `.env`（已在 `.gitignore` 中排除），键名 `ANLI_LLM_API_KEY`。
- 其他可配置项：`ANLI_LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`ANLI_LLM_MODEL`（默认 `deepseek-chat`）。
- 环境变量优先于 `.env`，便于临时切换模型。
- 未配置密钥或调用失败时自动退回规则版，主流程不会中断。
