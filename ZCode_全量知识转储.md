# 「案理」项目全量知识转储（供 Qoder 审计）

> 基线：`merge-calibration-layer`；打包版 `dist\案理.exe`（2026-09-19 14:31 构建，含后置否定证据修复；最新提交以 git log 为准）；提交包 `Desktop\ai杭州模拟法庭\`。
> 本文件由 ZCode 于 2026-09-18 会话中导出，内容与当次对话输出一致。
> 先说明一条审计纪律：**逐字全文源码以仓库文件为准**（`anli/` 共 5,987 行 Python，另有 tools 3,000+ 行、tests 6,000+ 行）。聊天里重述全部 15,000 行既超出单条消息的可靠输出长度，也会引入抄写误差——那恰好违反"严禁幻觉"。因此下文：**关键逻辑逐字引用（全部来自实际操作的原文），其余给出精确路径 + 行数 + 完整函数清单**，任何一处都可在仓库/压缩包内逐字节核对。

---

## 1. 全局架构与目录树

### 1.1 目录树（精确到文件，已排除 `__pycache__/.git/build/state/cache/cases/dist`）

```
anli-agent/
├── app.py                     打包入口（from anli.web import main）
├── 启动案理.cmd               源码启动（python -m anli.web）
├── 案理.spec                  PyInstaller 规范（datas=[('web','web'),('data','data')]）
├── pyproject.toml
├── .env / .env.example        ⚠️ .env 含真实密钥，已 gitignore 且 pack.py 强制排除
├── .gitattributes             * text=auto eol=lf（行尾符归一）
├── .gitignore
├── LICENSE
├── PRD.md  APP_FLOW.md  BACKEND_STRUCTURE.md  FRONTEND_GUIDELINE.md
├── CLAUDE.md  PROGRESS.md  README.md  DEMO_SCRIPT.md
├── R3-数据源侦察.md  实证案例录入指南.md  封版检查清单.md  案理-产品介绍.md
├── anli/                      ← 主包（21 个模块）
│   ├── __init__.py
│   ├── models.py              全部数据契约（TaskState/Role/Severity/CaseProfile/
│   │                          CaseRecord/Citation/FeasibilityAssessment/Artifact/TaskRecord）
│   ├── intake.py              规则版案情抽取 + P0 责任方向识别（498 行）
│   ├── llm_intake.py          大模型抽取（脱敏→JSON→受控落回）+ P0 方向交叉校验（320 行）
│   ├── agents.py              五角色智能体（检索/主张/抗辩/法官/复核/修订）+ 工具白名单（930 行）
│   ├── orchestrator.py        编排层：阶段机、预算、规划决策留痕、交付物组装（1324 行）
│   ├── planner.py             自主规划：检索词/轮次/路径排序（278 行，全文见 §2.3）
│   ├── material_text.py       判决书与材料共用的正则抽取层（207 行）
│   ├── materials.py           上传材料正文解析 + 证据线索 + 金额交叉核对（390 行）
│   ├── redact.py              对外发送前脱敏（90 行，全文见 §2.5）
│   ├── cost.py                单任务成本行（160 行，全文见 §2.4）
│   ├── scope.py               范围闸门（刑事/行政拦停）+ 转人工闸门
│   ├── feasibility.py         诉求可行性评估 + 量级闸门 + 证据闸门
│   ├── item_assessment.py     法院式逐项认定（supported/standard/unsupported）
│   ├── evidence_chain.py      证据补强清单 + potential_cap
│   ├── cross_exam.py          质证争点清单
│   ├── strategy.py            策略建议 + 结论摘要（含 P0 责任方向分支）
│   ├── judgment.py            裁判推演（法官视角）
│   ├── documents.py           身份化文书：起诉状/答辩状/裁判推演（224 行）
│   ├── published_cases.py     官方发布案例层（201 行，全文见 §2.6）
│   ├── real_cases.py          实证案例层（MIN_REAL_SAMPLE=3，人类核验才可用）
│   ├── stores.py              法条库/类案库/实证库检索（实证优先、量级不匹配拒绝外推）
│   ├── standards.py           浙江赔偿标准表 + disability_compensation
│   ├── regions.py             31 省级行政区 + DEFAULT_REGION="浙江省"
│   ├── retrieval_flk.py       国家法律法规数据库（flk.npc.gov.cn）回源核对 + 磁盘缓存
│   ├── tools.py               Tool/ToolCall/ToolRegistry/ToolRouter + ROLE_ALLOWLIST
│   ├── tools_builtin.py       工具注册：法条/类案/官方库核对/发布案例/时效/计算
│   ├── exporter.py            文档模型 + Markdown/CSV 渲染 + section_number（213 行）
│   ├── docx_export.py         纯标准库 OOXML Word 导出（minimal zip: 3 部件）
│   ├── web.py                 HTTP 服务、SSE、三格式导出、材料落盘解析（946 行）
│   ├── cli.py                 命令行入口
│   ├── events.py              EventBus / Event
│   ├── state.py               StateStore（检查点落盘）
│   └── paths.py               resource_path / user_root（exe 同目录优先）
├── data/
│   ├── laws.seed.json         23 条法条（20 条 verified + 3 条待核验拒引）· source_url 已换 flk 官方链接
│   ├── cases.seed.json        8 条演示样本（synthetic=true，全程标注）
│   ├── cases.official.json    95 条最高法指导性案例缓存（仅文字参照）
│   ├── cases.published.json   79 条省高院/最高法发布稿（只作旁证，不进区间）
│   ├── real_cases.json        实证库：[]（0 条，录入中）
│   └── standards.json         10 条浙江标准（多年度，含 source_label 与官网核对提示）
├── tools/
│   ├── seal_check.py          封版核对（256 行，契约见 §3.4）
│   ├── selftest.py            53 项端到端自检（332 行，清单见 §3.3）
│   ├── responsibility_matrix.py  30 格责任矩阵自查（162 行，全文见 §2.8）
│   ├── capture_llm_log.py     一次运行落盘全部证据（443 行）
│   ├── build_proposal_docx.py 初赛方案 MD→Word→PDF（279 行）
│   ├── build_web.py           前端自包含打包（48 行）
│   ├── pack.py                提交压缩包（排除 .env/state/cases，95 行）
│   ├── cases_cli.py           实证录入 CLI：template/import/validate/list/stats（155 行）
│   ├── case_extractor.py      判决书半自动提取（177 行）
│   ├── fetch_law_text.py / fetch_cases.py / fetch_published_cases.py  离线抓取
│   └── probe_case_sources.py  来源活性探测（275 行）
├── web/
│   ├── index.src.html         前端源码（三栏布局、导出选择器、材料面板、打印存PDF）
│   ├── index.html             自包含构建产物（由 build_web.py 生成）
│   └── vendor/bootstrap.min.css
├── tests/                     35 个测试文件 / 446 项（含 93 子测试）
└── artifacts/                 证据与素材（gitignored）：验收证据.txt、过程日志、调用明细、
                               工具清单、三格式导出样例、截图清单、2 个旧演示视频
```

### 1.2 模块依赖与数据流向

```
用户输入(网页/CLI)
   │  description + 证据勾选 + 上传材料 + 身份 + 省份
   ▼
web._run_task ──► materials.parse_material（逐份解析，未解析如实标注→facts）
   │                     │
   │                     └─► redact.redact（摘录脱敏后才进视图/检查点）
   ▼
llm_intake.build_profile_llm
   ├─ 出口脱敏：redact_with_counts(description) → 送模型（ANLI_SEND_RAW_TEXT=1 才发原文）
   ├─ 模型路径：profile_from_payload（词表越界丢弃 + 方向交叉校验）
   └─ 规则路径：intake.build_profile（parse_responsibility_detail 判方向）
        ▼
   CaseProfile（含 extra：claim_has_no_basis / client_share / client_bears_majority /
               material_facts / responsibility_basis / sufficiency）
        ▼
Orchestrator.run（阶段机：CLARIFYING→CLASSIFIED→RESEARCHING→DEBATING→CRITIQUING→DELIVERING）
   ├─ planner.plan_research ──► 检索词（模型选择+白名单过滤+规则兜底）→ plan_decided 事件
   ├─ ResearcherAgent.act ──► tools: law_lookup / case_search / law_source_check /
   │                                  official_case_search / published_case_search
   │        └─ 引用核验（CitationVerifier：逐字比对，不一致剔除）
   ├─ AdvocacyAgent(claimant/defendant).act ──► user_text_for_llm(原话) + 已核验法条
   ├─ planner.should_continue ──► 轮次决策（高危未解决不接受提前结束）
   ├─ JudgeAgent / RevisionAgent / RedTeamAgent（复核→修订→再复核）
   ├─ scope.human_review_reasons ──► 转人工闸门（住院/伤残/≥50万/时效≤90天）
   ├─ feasibility.build_assessment ──► 量级闸门 + 证据闸门（暂不提供 vs 给数字）
   ├─ strategy.build_strategies / render_summary（P0 方向分支）
   ├─ planner.rank_actions ──► 路径排序 + 依据
   ├─ documents.render_document ──► 起诉状/答辩状/裁判推演（按身份）
   ├─ cost.summarize(llm) ──► 成本行 → 追加进结论摘要
   └─ _build_artifacts ──► 13 项固定交付物（证据不足 +补证清单 = 14）
        ▼
web._export ──► exporter（export_model_from_record / from_view）──► md / csv / docx
        （服务重启后走检查点视图，导出不再 404）
```

---

## 2. 核心代码全量输出（逐文件）

### 2.1 `parse_responsibility` 模块（P0 责任方向识别）——逐字全文

文件：`anli/intake.py` 第 77–232 行（以下为实际源码）：

```python
RESPONSIBILITY_LEVELS: list[tuple[tuple[str, ...], float]] = [
    (("全部责任", "全责"), 1.0),
    (("主要责任", "主责"), 0.7),
    (("同等责任",), 0.5),
    (("次要责任", "次责"), 0.3),
    (("不承担责任", "不负责任", "没有责任", "无责任", "无责"), 0.0),
]

# 说「我负全责」和「对方负全责」是相反的结论，必须看主体。
# 这里取关键词**前面最近**的称呼：越近越可能是这句话的主语。
#
# 但「我」是谁取决于用户选的身份：主张方（原告）说「我」指受害方，
# 抗辩方（被告）说「我」指被索赔方。所以称谓表按身份分两套。
CLAIMANT_SELF = ("我方", "我", "本人", "自己", "原告", "受害人", "伤者", "申请人", "买家")
CLAIMANT_OTHER = ("对方", "被告", "肇事", "侵权人", "车主", "司机", "被申请人", "商家", "平台")
DEFENDANT_SELF = ("我方", "我", "本人", "自己", "被告", "答辩人", "本公司", "本店")
DEFENDANT_OTHER = ("对方", "原告", "受害人", "伤者", "申请人", "买家")


def parse_responsibility(text: str, cause: str = "", role: str | None = None) -> float:
    """被告（对方）应承担的责任比例。保留旧签名，内部走方向识别版。"""

    return parse_responsibility_detail(text, cause, role)[0]


def _subject_before(text: str, position: int, role: str | None = None, window: int = 10) -> str:
    """看关键词前面最近的称呼是谁：`self`（我方）/ `other`（对方）/ `""`（没说）。"""

    is_defendant = str(role) in ("defendant", "Role.DEFENDANT")
    self_hints = DEFENDANT_SELF if is_defendant else CLAIMANT_SELF
    other_hints = DEFENDANT_OTHER if is_defendant else CLAIMANT_OTHER
    segment = text[max(0, position - window) : position]
    subject = ""
    best_index = -1
    for hint in self_hints:
        index = segment.rfind(hint)
        if index > best_index:
            best_index, subject = index, "self"
    for hint in other_hints:
        index = segment.rfind(hint)
        if index > best_index:
            best_index, subject = index, "other"
    return subject


def parse_responsibility_detail(
    text: str, cause: str = "", role: str | None = None
) -> tuple[float, str]:
    """返回 (对方责任比例, 依据说明)。

    这个比例在产品里统一表示「对方（被告）应当承担的比例」，所以要判断主体：

    - 「对方负主要责任」→ 0.7；「我负主要责任」→ 0.3（对方只承担次要责任）；
    - 「我负全部责任 / 对方无责任」→ 0.0，也就是**向对方索赔没有责任基础**；
    - 只写程度不写主体时沿用旧口径（按程度取值）。

    以前只认程度不认主体，会把「我负全部责任」算成对方赔 100%——
    那是把责任判反了，属于会误导用户的严重错误。
    """

    if cause and not responsibility_applies(cause):
        # 该案由不按责任比例分摊，一律按 100% 处理
        return 1.0, "该案由不按责任比例分摊，按全额计算"

    is_defendant = str(role) in ("defendant", "Role.DEFENDANT")

    def defendant_share_of(subject: str, ratio: float) -> float:
        """把「谁承担多大比例」换算成产品统一口径：被告承担多少。"""

        if subject == "self":
            # 我方 = 原告（主张方）/ 被告（抗辩方）
            return ratio if is_defendant else round(1.0 - ratio, 2)
        if subject == "other":
            return round(1.0 - ratio, 2) if is_defendant else ratio
        return ratio  # 没写主体：沿用旧口径（按程度取被告比例）

    findings: list[tuple[str, str, float]] = []
    for keywords, ratio in RESPONSIBILITY_LEVELS:
        for keyword in keywords:
            start = text.find(keyword)
            while start >= 0:
                subject = _subject_before(text, start, role)
                findings.append((subject, keyword, defendant_share_of(subject, ratio)))
                start = text.find(keyword, start + len(keyword))

    if not findings:
        return 1.0, ""

    explicit = [item for item in findings if item[0]]
    subject, keyword, share = (explicit or findings)[0]
    # 说明里用当事人身份写清楚，别让法官视角看到「我方」这种第一人称
    if is_defendant:
        self_label, other_label = "我方（被告）", "对方（原告）"
    elif str(role) in ("judge", "Role.JUDGE"):
        self_label, other_label = "原告侧", "被告侧"
    else:
        self_label, other_label = "我方（原告）", "对方（被告）"
    if subject == "self":
        return share, f"按描述，{keyword}由{self_label}承担 → 被告承担 {share:.0%}"
    if subject == "other":
        return share, f"按描述，{keyword}由{other_label}承担 → 被告承担 {share:.0%}"
    return share, f"按描述提到{keyword}，未写清由谁承担，暂按程度取值（被告 {share:.0%}）"


def client_share_of(
    responsibility_ratio: float, cause: str, role: str | None
) -> tuple[float | None, bool]:
    """算出「我方承担多少」以及「我方是不是主要责任方」。

    结论摘要要先说哪一头，取决于谁承担主要责任：
    我方只担次要责任时，重点是「能拿多少」；我方担主要责任时，
    重点是「对方只赔多少、其余要自己扛」——这两句是完全不同的引导。
    法官视角没有「我方」，返回 (None, False)。
    """

    text_role = str(role)
    if text_role in ("defendant", "Role.DEFENDANT"):
        share: float | None = responsibility_ratio
    elif text_role in ("claimant", "Role.CLAIMANT"):
        share = round(1.0 - responsibility_ratio, 2)
    else:
        return None, False
    majority = responsibility_applies(cause) and share is not None and share > 0.5
    return share, majority
```

`build_profile` 中的落点（`anli/intake.py` 第 366 行起，签名含 `role`）：

```python
def build_profile(
    description: str, file_names: list[str] | None = None, region: str = "",
    confirmed_evidence: list[str] | None = None, waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None, waive_all: bool = False,
    material_facts: list | None = None, role: str | None = None,
) -> CaseProfile:
    ...
    responsibility_ratio, responsibility_basis = parse_responsibility_detail(text, cause, role)
    # 对方完全不担责（我方全责 / 对方无责任）：索赔缺乏责任基础，
    # 后面的结论、策略与文书都要据此改口径，不能继续按「能拿多少」讲
    claim_has_no_basis = responsibility_applies(cause) and responsibility_ratio <= 0.0
    client_share, client_bears_majority = client_share_of(responsibility_ratio, cause, role)
    ...
    extra={
        ...,
        "responsibility_applies": responsibility_applies(cause),
        "responsibility_basis": responsibility_basis,
        "claim_has_no_basis": claim_has_no_basis,
        "client_share": client_share,
        "client_bears_majority": client_bears_majority,
    }
```

`llm_intake.py` 中的**方向交叉校验**（防模型填反）：

```python
    ratio = payload.get("responsibility_ratio")
    if not responsibility_applies(cause):
        ratio_value = 1.0
    else:
        try:
            ratio_value = float(ratio)
            # 0 是合法值：对方无责任 / 我方全责，就是 0。
            # 以前写成 `not 0 < value`，等于把「对方无责任」判成需要回退的非法值，
            # 回退后再按关键词取值，方向就反了。
            if not 0 <= ratio_value <= 1:
                ratio_value = parse_responsibility(description, cause, role)
        except (TypeError, ValueError):
            ratio_value = parse_responsibility(description, cause, role)
        # 方向交叉校验：描述里写明了「谁承担什么责任」时，以规则解析为准。
        rule_ratio, rule_basis = parse_responsibility_detail(description, cause, role)
        if "由" in rule_basis and abs(rule_ratio - ratio_value) > 0.01:
            ratio_value = rule_ratio
```

调用链透传：`web._run_task → build_profile_llm(..., role=role) → profile_from_payload(..., role=role) → parse_responsibility_detail`。

**实测四项**（真机复现）：
`我负全部责任 → 被告 0%，no_basis=True`；`对方负全部责任 → 100%`；`我承担主要责任 → 30%`；被告身份下 `对方负全部责任 → 被告 0%，no_basis=True`。

### 2.2 `documents.py`（P1a 身份化文书）——逐字核心

文件：`anli/documents.py`（224 行）。以下为核心实现（队友线逐字移植，未改写）：

```python
"""文书草稿：按身份出具当事人自己的文书，不给用户"法院会怎么判"的错觉。

为什么不是判决书：判决书是人民法院的专属文书，智能体出具判决书形式的文本，
即使加了免责声明，复制出去也容易被当成裁判结果；而当事人真正要写、也真正用得上的是
起诉状（主张方）、答辩状（抗辩方）。法官视角需要的是中立推演，见 judgment.py。

三条纪律：
1. 金额只取自可行性评估的确定性结果，不另编；
2. 不编造法院名称、案号、当事人姓名——需要用户填写的一律留占位符；
3. 文书首尾必须标注「草稿 + 需人工复核」，不得出现胜诉承诺。
"""

DRAFT_HEAD = (
    "（本文书由「案理」智能体根据你提供的材料自动生成，属草稿，"
    "需经执业律师或你本人核对后使用；不构成法律意见，不替代律师。）"
)

ROLE_NOTE = {
    Role.CLAIMANT: "（提示：本文书为参考草稿，是否起诉、何时起诉请结合时效与成本判断。）",
    Role.DEFENDANT: "（提示：本文书为参考草稿，具体答辩策略请结合对方证据与开庭情况调整。）",
}


def _amount(value: float | None) -> str:
    return "待定" if value is None else f"{value:,.0f} 元"


def _support_sentence(assessment, blocked: list[str]) -> str:
    """「可支持金额」这句话在评估缺失时必须换个说法，不能写成「约为 待定」。"""

    tail = ("未提供凭证的项目（" + "、".join(blocked) + "）待补充证据后另行主张。") if blocked else (
        "暂未发现完全无凭证的项目，仍以原件质证为准。"
    )
    if assessment is None:
        return "2. 可支持金额需在补齐材料后评估；" + tail
    return f"2. 按现有材料，可支持金额约为 {_amount(assessment.legal_cap)}；" + tail


def _no_basis_notice(profile: CaseProfile, role: Role) -> list[str]:
    """责任认定显示被告不担责时，文书开头必须先把这个说清楚。

    主张方最容易被误导：他会以为「材料补齐就能拿到钱」，实际上金额不是障碍、责任才是。
    """

    if not (profile.extra or {}).get("claim_has_no_basis"):
        return []
    basis = (profile.extra or {}).get("responsibility_basis") or "责任认定显示你方不承担责任"
    if role == Role.DEFENDANT:
        return [
            "",
            f"（重要提示：{basis}。按描述你方不承担赔偿责任，"
            "下面这份答辩状的重点是固定「责任不在你方」这一抗辩，而不是逐项压价。）",
        ]
    return [
        "",
        f"（重要提示：{basis}。本文书所载赔偿请求**缺乏责任基础**，"
        "即使证据齐全通常也不会得到支持；除非事故认定本身被复核纠正，"
        "否则不建议据此起诉。请先按「策略建议」核对认定书。）",
    ]


def render_document(
    profile: CaseProfile, assessment: FeasibilityAssessment | None, role: Role,
    real_citations: list[Citation], judgment_renderer=None,
) -> tuple[str, str]:
    """按身份返回 (标题, 正文)。法官视角交给模拟推演渲染器。"""

    if role == Role.JUDGE:
        renderer = judgment_renderer
        if renderer is None:  # pragma: no cover - 正常路径一定会传入
            from .judgment import render_judgment
            renderer = render_judgment
        return "裁判推演（模拟，仅供学习参考）", renderer(profile, assessment, real_citations)
    if role == Role.DEFENDANT:
        return "民事答辩状（草稿）", render_defense(profile, assessment)
    return "民事起诉状（草稿）", render_complaint(profile, assessment)
```

`render_complaint`（主张方）关键结构：抬头 DRAFT_HEAD → `_no_basis_notice` → 原被告**留占位符**（"请补全姓名／名称、性别…"）→ 一、诉讼请求（合计 = `assessment.expectation`；第 2 条 = `_support_sentence`）→ 二、事实与理由（逐项 findings + `责任认定：被告应承担 {ratio:.0%}`）→ 三、证据清单（`_evidence_lines`）→ 四、此致 **`____人民法院`（请按被告住所地或侵权行为地确定管辖后填写；{region}为本次分析采用的裁判尺度参照地）** → 具状人占位符 + 日期 → ROLE_NOTE。

`render_defense`（抗辩方）关键结构：复用 `cross_exam.build_challenges` 逐条质证（"关于{target_item}（涉及约 {impact:,.0f} 元）：{argument}"）→ 金额句（`可能被支持的金额约为 X 元，超出部分请求不予支持`）→ 三、对方举证情况 → 四、答辩请求。

`render_judgment`（`anli/judgment.py`，法官视角）已同时改写为"裁判推演"口径：一、诉讼请求 / 二、查明的基本事实 / 三、**推演意见**（含 `no_liability` 分支："关于责任：认定书显示被告不承担责任…应走复核程序"）/ 四、**类案参照** / 五、**推演结论**（不担责时："一、被告不承担赔偿责任…二、…应另行申请复核"）/ 落款"出具方：案理智能体（模拟推演，非审判组织、非裁判文书）"。

### 2.3 `planner.py`（自主规划）——全文与要点

文件：`anli/planner.py`（278 行）。关键常量：

```python
ACTION_TEMPLATES = [
    {"key": "negotiate", "text": "协商：成本最低，需准备证据清单与书面主张。"},
    {"key": "complaint", "text": "投诉/调解：向主管部门或调解组织申请，周期较短。"},
    {"key": "litigation", "text": "仲裁/诉讼：成本与周期上升，需确认时效与管辖。"},
]
ACTION_DISCLAIMER = "提示：本方案为参考性质，不构成法律意见，重大金额或时效临近请咨询执业律师。"

@dataclass
class PlanningDecision:
    kind: str      # research_keywords / continue_round / action_order
    source: str    # llm 或 rules；回退时必须是 rules
    detail: str
    payload: dict = field(default_factory=dict)
```

三个入口（每个都"模型参与 + 白名单过滤 + 规则兜底 + 留痕"）：
- `plan_research(profile, llm)`：白名单 = 案由 ∪ 争议焦点 ∪ 证据词表 ∪ 赔偿项目别名（`allowed_keywords`）；模型返回中 `item in allowed` 才保留，越界进 `dropped` 并在 detail 里写"越界丢弃 N 个"；结果 `dict.fromkeys(kept + fallback)[:6]`。
- `should_continue(findings, round_no, max_rounds, llm)`：**两条硬边界不交给模型**（无未解决中/高危 → False；到上限 → False）；模型说 False 但仍有 `Severity.HIGH` 未解决 → 规则强制 `continue=True`。
- `rank_actions(profile, assessment, llm)`：模型只能排序，`kept = [k for k in raw if k in ACTION_KEYS]` 后补全成完整排列；`render_actions` 渲染回固定文案，非法输入回退 `ACTION_KEYS`。
- `decision_line(d)` → `f"[{d.source}] {d.detail}"`，报告与过程流共用同一句话。

三个 Prompt 全文：

```python
RESEARCH_SYSTEM = """你是法律检索规划助手，负责决定这次要检索哪些关键词。
只输出 JSON，不要任何解释：
{"keywords": ["关键词1", "关键词2"], "reason": "一句话说明为什么先查这些"}

硬约束：
1. 关键词只能从用户给出的候选词里选，不得自造新词；
2. 最多 6 个，按检索优先级排序；
3. 至少包含案由本身。"""

ROUND_SYSTEM = """你是复核轮次决策助手，判断是否还需要再走一轮「修订 → 对方反驳 → 再复核」。
只输出 JSON：{"continue": true 或 false, "reason": "一句话理由"}
判断依据：未解决的问题是否会影响结论数字或法律依据。已经解决、或只涉及措辞的问题不必再来一轮。"""

ORDER_SYSTEM = """你是维权路径规划助手，根据案情给三条路径排序。
只输出 JSON：{"order": ["negotiate", "complaint", "litigation"], "reason": "一句话理由"}
三条路径的 key 固定为 negotiate（协商）、complaint（投诉/调解）、litigation（仲裁/诉讼）。
只能排序，不得新增、删除或改名。"""
```

### 2.4 `cost.py`（成本行）——逐字全文

```python
"""单任务成本估算。一条纪律：**没有单价就不猜价格**。"""

@dataclass
class CostSummary:
    calls: int = 0                # 成功的调用次数
    attempts: int = 0             # 实际发出的请求次数（含失败）
    failures: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None     # 未配置单价时为 None，而不是 0
    currency: str = DEFAULT_CURRENCY
    pricing_source: str = "未配置"
    text: str = ""


def summarize(llm=None, pricing=None, pricing_source="") -> CostSummary:
    calls = int(getattr(llm, "calls", 0) or 0)
    attempts = int(getattr(llm, "attempts", 0) or 0) or calls
    ...
    if calls == 0:
        cost = 0.0
    elif pricing is not None and (prompt_tokens or completion_tokens):
        cost = _cost_from(pricing, prompt_tokens, completion_tokens)
    ...


def render_line(summary: CostSummary) -> str:
    if summary.calls == 0 and summary.attempts == 0:
        return "本次分析：未调用大模型（规则版），模型成本 0 元。"
    parts = [f"模型调用 {summary.calls} 次"]
    if summary.failures:
        parts.append(f"其中失败 {summary.failures} 次")
    parts.append(f"耗时 {summary.seconds:.1f} 秒")
    if summary.cost is None:
        detail = ("未配置单价，故不估算金额（要估算请在 pricing.json 或环境变量里填单价）"
                  if summary.pricing_source == "未配置" else "接口未返回 token 用量，无法估算金额")
    else:
        tokens = summary.prompt_tokens + summary.completion_tokens
        detail = f"约 {tokens:,} tokens，估算成本 {summary.cost:.4f} {summary.currency}"
    return "本次分析：" + "、".join(parts) + f"；{detail}。"
```

单价查找顺序（`load_pricing`）：环境变量 `ANLI_PRICE_INPUT_PER_MILLION / ANLI_PRICE_OUTPUT_PER_MILLION / ANLI_PRICE_CURRENCY` → `user_root()/pricing.json` → `cwd/pricing.json` → 包内 `data/pricing.json`；全部缺失返回 `(None, "未配置")`。
成本行注入点：`orchestrator.run` 里 `record.extra["summary"] += "\n\n" + cost.text`（保证报告/导出/页面同源）。

### 2.5 `redact.py`（发送前脱敏）——逐字全文

```python
# 顺序有意义：18 位身份证要先处理，否则会被银行卡规则先吃掉。
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("身份证号", re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)"), r"\1********\3"),
    ("手机号",   re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"), r"\1****\3"),
    ("银行卡号", re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)"), r"\1********\2"),
    ("邮箱",     re.compile(r"([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"), r"\1***@\2"),
    ("详细住址",
     re.compile(
         r"((?:[\u4e00-\u9fa5]{2,10}?(?:省|自治区|市|区|县|旗)){1,3})"
         r"([\u4e00-\u9fa5]{1,12}(?:路|街|巷|大道|小区|村|组)[\u4e00-\u9fa5\d]{0,12})"
     ), r"\1***"),
]
LABELED_NUMBER = re.compile(
    r"((?:账号|帐号|卡号|账户|流水号|保单号|证件号|统一社会信用代码)[：:\s]{0,4})([A-Za-z0-9]{6,})"
)
```

`redact(text)` = `redact_with_counts(text)[0]`；`describe(counts)` 生成给用户看的一句话（"已打码：身份证号 1 处、手机号 1 处"）。

**出口覆盖**：① `llm_intake.build_profile_llm` 发送前；② `materials` 的摘录（`excerpt=redact(text[:200]...)`）；③ 合并后新增的 `agents.user_text_for_llm()`——主张/抗辩/法官/修订四个 agent 的 prompt 里所有"用户原话"统一走它（回归测试 `test_redact.py::test_agent_prompt_exit_is_masked`）。

### 2.6 `published_cases.py`（79 条发布案例层）——逐字核心

```python
TIER_LABEL = "官方发布案例（典型案例稿，非判决书全文）"
CRIMINAL_MARKS = ("有期徒刑", "缓刑", "判处", "被告人", "公诉机关", "刑事拘留")


@dataclass
class PublishedCase:
    case_id: str; title: str; case_name: str = ""; cause: str = ""; region: str = ""
    court: str = ""; injury_level: str = ""; responsibility: str = ""
    amounts: list[str] = field(default_factory=list); case_no: str = ""
    facts: str = ""; holding: str = ""; meaning: str = ""
    source_url: str = ""; source_name: str = ""; article_title: str = ""
    published_date: str = ""; fetched_at: str = ""

    @property
    def is_clean_civil(self) -> bool:
        """防御性校验：正文里出现刑事表述的，不放进参照。"""
        blob = f"{self.title}{self.facts}{self.holding}"
        return not any(mark in blob for mark in CRIMINAL_MARKS)


class PublishedCaseStore:
    def __init__(self, records=None):
        self.records = [item for item in (records or []) if item.is_clean_civil]   # 二次防御

    @classmethod
    def load(cls, path):        # 读不到/格式不对 → 空库，绝不让主流程挂掉
    def stats(self) -> dict:    # {"total": 79, "with_amount": 53, "causes": {...}}
    def search(self, keywords, case_type="", limit=4, min_score=3.0):
        # 同案由 +3；关键词原样命中 +2；二元切片命中每个 +0.4；带金额/带责任认定各 +0.5
        # 门槛 3.0：措辞与判决书不同，门槛要比官方案例缓存低一档，否则会「一条都参照不到」
    def render_reference_lines(self, cases): ...
```

金额标注（渲染层强制）：`f"文中金额：{case.amount_text}（旁证，不参与区间计算）"`；来源行强制带 `source_name` 与 `source_url` + 抓取日期。

**隔离性证据**（集成测试断言）：`tests/test_published_integration.py::test_published_amounts_do_not_appear_in_money_artifacts` 与 `ReportIntegrationTest::test_every_role_gets_the_reference_layer` —— 即"有/无这份缓存时金额区间完全相等"。

### 2.7 `scenarios.py`（三组新样例）——逐字全文

```python
def car_full_description() -> str:
    return (
        "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，认定对方负全部责任。"
        "我门诊治疗花了医疗费3200元，有医疗费发票；误工费1800元，单位开了误工证明，也有工资流水；"
        "请护工花了护理费2100元，有护理费发票；打车去医院的交通费300元，有交通费票据。"
        "我想要对方一共赔我5500元。"
    )


def car_full_files() -> list[str]:
    return ["事故认定书.jpg", "医疗费发票.jpg", "误工证明.jpg", "工资流水.pdf",
            "护理费发票.jpg", "交通费票据.jpg"]


def car_full_expectation() -> float:
    return 5500.0


def consumption_gap_description() -> str:
    return (
        "8月1号我在网上买了一部手机，商家客服说保证是全新原装，8月4号收货后发现是翻新机。"
        "我有订单截图和客服聊天记录，但下单时没保存商品宣传页面。"
        "8月5号申请退款被商家拒绝。手机花了2999元，我想要退一赔三，一共赔我11996元。"
    )


def consumption_gap_files() -> list[str]:
    return ["订单截图.png", "客服聊天记录.png"]


def at_fault_description() -> str:
    return (
        "6月10号我骑电动车撞了一辆小轿车，交警出了事故认定书，认定我负全部责任，对方无责任。"
        "我门诊治疗花了医疗费3200元，因为养伤误工费大概1800元，我想要对方一共赔我12000元。"
    )


def at_fault_files() -> list[str]:
    return ["事故认定书.jpg", "医疗费发票.jpg"]


def at_fault_expectation() -> float:
    return 12000.0
```

前端入口：`?demo=carFull` / `?demo=consumptionGap` / `?demo=atFault`（`web/index.src.html` 的 `SAMPLES` + 三个按钮 + 监听器，已 build 进 `index.html`）。
实测交付物项数（规则版）：car=14（13+补证）、carFull=13、consumption=13、atFault=13。

### 2.8 `tools/responsibility_matrix.py`（30 格矩阵）——逐字核心

```python
ALL_EVIDENCE = ["事故认定书", "医疗费票据", "误工证明", "工资流水", "护理费票据", "交通费票据"]

RESPONSIBILITY_WORDS = {
    "claimant": {"对方全责": "…认定对方负全部责任", "对方主要责任": …, "同等责任": …,
                 "我方主要责任": …, "我方全责": "…认定我负全部责任，对方无责任"},
    "defendant": {"对方全责": "…认定对方（原告）负全部责任，我无责任", …},
    "judge":     {"原告全责": …, "被告全责": …},
}
```

`run_cell` 断言口径：`ratio`（被告承担）、`client_share`、`no_basis`、`majority`、`state`、结论首句、`has_warning`（当事人文书"重要提示"或法官推演"被告不承担责任"）、`asked_evidence`（责任不成立时必须为 False）、`first_strategy`。终端输出 30 行表格，末行自检判据：**ratio=0 必须说「缺乏责任基础」或「对你有利」；我方>50% 必须首句讲责任分摊；ratio=100% 必须讲对方全额承担**。实测 30/30 符合。

---

## 3. 配置、Prompt 与测试资产

### 3.1 环境变量模板（脱敏状态，逐字）

```
# 复制本文件为 .env，填入你自己的密钥后再运行
# 不要把 .env 发给别人，也不要把密钥写进代码或文档
ANLI_LLM_BASE_URL=https://api.deepseek.com
ANLI_LLM_MODEL=deepseek-chat
ANLI_LLM_API_KEY=在这里填你自己的密钥

# 可选：模型单价（元 / 百万 token）。不填时报告只写调用次数与耗时，不估算金额。
# 也可以写在项目根目录的 pricing.json 里，字段同名。
# ANLI_PRICE_INPUT_PER_MILLION=2
# ANLI_PRICE_OUTPUT_PER_MILLION=8
# ANLI_PRICE_CURRENCY=CNY

# 可选：案情描述对外发送前默认脱敏；确需发送原文时再打开这个开关
# ANLI_SEND_RAW_TEXT=1
```

其他运行开关：`ANLI_DISABLE_WEB=1`（不注册官方库核对工具）、`ANLI_NO_BROWSER=1`、`ANLI_PORT`（默认 8765，占用则顺延 10 个端口）、`ANLI_FFMPEG`（录屏编码器路径）。

### 3.2 系统 Prompt / 角色 Prompt（逐字）

**① 案情抽取（`llm_intake.SYSTEM_PROMPT`）**：

```
你是法律案情抽取助手，把用户的口语描述整理成结构化 JSON。
只输出 JSON，不要任何解释文字。

案由只能从下列取值中选择：
{causes}

每个案由可用的赔偿项目：
{items}

证据名称只能从下列取值中选择：
{evidence}

输出 JSON 结构：
{{
  "cause": "案由",
  "summary": "一句话概括纠纷",
  "responsibility_ratio": 0到1之间的小数,
  "claim_items": {{"赔偿项目名": 金额数字}},
  "evidence": [{{"name": "证据名", "have_material": true}}],
  "unquantified_items": ["用户提到但没说具体金额的赔偿项目"],
  "expectation": 期望赔偿总额数字,
  "timeline": ["按时间顺序的事件"],
  "disputed_points": ["争议焦点"]
}}

规则：
1. 金额只填用户明确说出的数字，没说的不要编造。
2. have_material 为 true 表示用户手上有原件、截图或票据；只是口头提到填 false。
3. 用户上传的文件名视为有材料。
4. 用户主张「退一赔三」但没说具体金额时，claim_items 里只填商品价款。
5. 认不出案由时 cause 填「一般民事纠纷」。
```

**② 检索书记员（ResearcherAgent）**：`"你只负责检索与核对来源，不得编造任何法条或案号。"`

**③ 主张方（STANCE_PLAYBOOK[CLAIMANT]）**：
`system`："你是原告方的代理律师，负责把委托人的损失做实、把能主张的都要到位。只能引用给定法条，不得编造法条、案号或金额；不得承诺胜诉或具体赔偿结果。"
`goal`："目标：把损失做实、把可主张的项目找全、把责任比例与赔偿基数顶到法律允许的上限，并规划举证顺序与谈判锚点。"
`sections`: ["可主张项目", "举证顺序", "责任比例", "谈判锚点"]
`must` 5 条：逐项列可主张项目（条号+计算口径+现有材料能支撑多少）；缺证据写成"补什么材料就能主张、大概能拿多少"；按影响金额给举证顺序；预判对方拿责任比例/必要性/因果关系做文章并给应对；给协商与诉讼锚点、让步空间与最低可接受线。
`forbidden`：不替对方论证、不主动否定自己可主张的项目；不把"对方可能抗辩的点"写成结论，要写成预案。

**④ 抗辩方（STANCE_PLAYBOOK[DEFENDANT]）**：
`system`："你是被告方的代理律师，负责逐项打掉对方主张中站不住的部分。只能引用给定法条，不得编造法条、案号或金额；不得承诺判决结果。"
`goal`："目标：逐项质证对方证据与法律适用，压低责任比例与赔偿基数，说清自己最坏大概赔多少、以及和解区间在哪。"
`sections`: ["质证要点", "举证责任", "责任比例", "和解区间"]
`must` 4 条：逐项质证（真实性/合法性/关联性，不认可要说明理由）；指出举证责任在对方及举证不能的后果；核对责任比例，指出可减轻责任的事实（认定书可复核、对方也有过错）；给出可接受赔付上限与和解方案及理由。
`forbidden`：不替对方补强证据、不默认认可未证明部分；不能只说"对方没证据"，还要说明自己该准备什么材料。

**⑤ 法官视角（JudgeAgent）**：`"你保持中立，只做评估与风险提示，不给出胜诉败诉的确定结论。"`；用户 prompt 亦为"你是中立的评估者，只做证据与论证质量的评估…"。

**⑥ 质证复核（RedTeamAgent）**：`"你的唯一职责是复核并挑出漏洞，每条必须挂上依据。"`；用户 prompt："你是质证复核员，只指出用户主张中的漏洞，每条必须给出依据，不得编造法条或案号，不得重复已有的发现。"

**⑦ 修订（RevisionAgent）**：`"你负责按复核意见修订方案，逐条说明已修订、坚持与无法处理的理由，不得编造法条、案号或金额。"`

**⑧ 规划三件套**：`RESEARCH_SYSTEM` / `ROUND_SYSTEM` / `ORDER_SYSTEM`（全文见 §2.3）。

### 3.3 端到端自检（53 项）——实测通过清单（逐条）

```
健康检查可访问 | 创建任务返回 task_id | 主张方任务完成 | 有结论摘要 | 有策略建议 | 有证据补强清单
有引用核验结果 | 有风险清单 | 报告包含立场分析 | 报告包含补证章节 | 证据充分度有值
规划决策留痕透出 | 包含检索与路径两类决策 | 决策标注了来源 | 报告含检索决策留痕 | 报告含路径排序依据
成本信息透出 | 成本行进入结论摘要 | 调用了模型就有成本口径 | 导出 Markdown 报告 | 导出金额认定表
导出 Word 报告 | 不支持的导出格式被拒绝 | 补交接口返回成功 | 勾选确认后金额上升
充分度上升 | 补充细节接口返回成功 | 补充细节后金额继续上升 | 材料细节问题接口可用
上传文件接口返回成功 | 上传票据后该项转为可支持 | 放弃补证接口返回成功 | 放弃补证后直接交付
结论说明材料拿不出来 | 结论给出两种口径 | 放弃补证后不再要求补材料 | 抗辩方报告有抗辩要点
法官报告有中立评估 | 抗辩方结论口径不同 | 三种身份报告互不相同 | 抗辩方策略提到质证或要求对方
主张方策略提到保险理赔 | 抗辩方策略不出现「多主张」 | 材料解析结果透出 | 文本材料被真正读到
从材料正文抽出金额 | 从材料正文抽出日期 | 图片如实标注未解析 | 未解析材料不产生证据
材料解析有汇总说明 | 空描述被拒绝 | 无法识别时不报错 | 超范围案情返回待补信息
```

（其中"成本信息透出""规划决策留痕""三格式导出""材料解析"为合并后新增；最后 4 项为边界用例。）

### 3.4 `tools/seal_check.py`——核心契约

```python
REPORT_MATERIALS = [artifacts/验收证据.txt, artifacts/截图清单.md,
                    artifacts/过程日志_LLM链路.txt, artifacts/导出报告_LLM链路.md]
```

判定三类：`[OK]` 一致 / `[改]` 材料数字需跟着改（工作树不干净）/ `[重跑]` 产物落后于**代码与数据的最近一次提交**（`git log -1 --format=%ct -- anli web data app.py 案理.spec`，刻意用"代码/数据提交"而非 HEAD，纯文档提交不会误报）；覆盖项：测试数（unittest discover，纯标准库，顺带判定是否全绿；pytest 不作依赖）、法条库/官方案例/演示样本/实证库条数、两个演示案例的可支持金额与区间与样本构成、冻结口径回显（读自 artifacts/模型调用明细，与 sync_frozen_numbers 同源）、`dist/案理.exe` 完整性与 `web/index.html` 与四份素材的新鲜度（后两类仅仓库环境做；解压目录无 .git，时间戳类核对自动跳过并如实说明，避免解压改时间戳造成的误报）。退出码 0/1 供脚本调用；实测仓库与解压目录输出均为 `全部一致`。

---

## 4. 口径与数字冻结清单

### 4.1 冻结数字（**以本包为准**）与映射位置

| 数字 | 值 | 代码/文档映射位置 |
| --- | --- | --- |
| 单元测试 | **446 项通过**（含 93 子测试） | `tests/`（35 文件）；`03_验收证据/验收证据_446项测试_53项自检.txt §一`；初赛方案 1.1/1.3；截图清单图注 8 |
| 端到端自检 | **53/53**（对打包版 exe） | `tools/selftest.py`；同上 §二；DEMO_SCRIPT 第三节 |
| 主线运行 | **run `bbb2d3f4e575`**：14 次调用 / **47.8 秒** / 6,924+8,859 tokens / 9 条引用 / 3 条规划决策 / 14 项交付物 | `03_验收证据/模型调用明细_LLM链路.md`、`过程日志_LLM链路.txt`；初赛方案 4.3 |
| 可支持金额 | **约 2,240 元**；补齐后上限 **4,620 元**；期望 12,000 元 | 导出报告样例 §一/§八；初赛方案 3.1/4.2/4.3 |
| 类案区间 | **5,125–5,675 元**（中位数 5,450；n=4，全部演示数据） | 导出报告 §八/§十；初赛方案 4.2；`seal_check` 输出 |
| 证据充分度 | **62%**（0.621→62%），约 3,400 元缺证 → 触发证据闸门 | 导出报告；初赛方案 4.3（占已列明损失 6,600 元的 52%） |
| 交付物 | **13 项固定**（证据不足 +补证清单 = **14 项**） | 初赛方案 5.3；实测四样例见 §2.7 |
| 法条库 | 23 条 = 20 verified + 3 待核验（拒引） | `data/laws.seed.json`；初赛方案 3.3；`seal_check` |
| 类案三层 | 官方案例 95 / 发布案例 79 / 演示样本 8 / **实证 0** | `data/*.json`；初赛方案 六；导出报告 §十 |
| 打包产物 | `dist\案理.exe` **2026-09-19 14:31**（9.6 MB） | `案理.spec`；`seal_check` 新鲜度判定 |

> **口径变更记录（审计要点）**：文档历史版本里出现过的"343 项测试 / 12 次调用 / 42.3 秒"是**上一轮冻结值**（P0–P2 移植前的构建）。移植后重新实测并全量替换为 441 项 / 12 次 / 43.7 秒（观测区间：调用 11–14 次、耗时 35.8–56.5 秒）；2026-09-19 后置否定证据修复后再次冻结为 446 项 / 14 次 / 47.8 秒（冻结口径见 4.1 表）；旧值残留在旧文档副本中，已随清理删除，现全部材料统一为 4.1 表内数值。

### 4.2 法条来源官方库化的实现方式

1. **静态来源**：`data/laws.seed.json` 中每条 `source_url` 已从新华网转载页**统一替换为国家法律法规数据库官方链接**（`https://flk.npc.gov.cn/detail2.html?<bbbs base64>`），`verified=true` + `note:"已从权威公开来源抓取并核验"`；3 条 `verified=false` 占位条目（`text:"（正文待核验，未核验条文不得引用）"`）由核验器一律拒绝引用——库里自带"反面样本"。
2. **运行时回源**：`anli/retrieval_flk.py` — `FLK_BASE="https://flk.npc.gov.cn"`；`search_law(title)`（POST 检索）→ `pick_best_match` → `law_detail(bbbs)`（GET 详情，取公布/施行日期）；`LawSourceChecker(cache_path=ROOT/"cache"/"law_sources.json")` 带磁盘缓存，产出 `LawSourceCheck`（`summary` 供报告"官方库核对"小节，注明"仅证明该法存在，不代替条文正文核验"）。
3. **注册与开关**：`tools_builtin.build_default_registry(..., law_checker=...)` 注册 `law_source_check`；`web._build_orchestrator` 中 `if os.environ.get("ANLI_DISABLE_WEB") != "1": checker = LawSourceChecker(...)`——离线环境整体摘除该工具，过程流不出现空跑检索。
4. **留痕**：`ResearcherAgent._check_official_sources` 最多核对 2 部法律，逐次 `retrieval_query`/`retrieval_hit` 事件（附件《工具调用清单》里 2/2 收录）。

---

## 5. 移植状态与已知风险

### 5.1 Cherry-pick 记录（全部为**逐块移植**，非分支合并）

| 轮次 | 来源 | 落点（逐条） | 提交 |
| --- | --- | --- | --- |
| 前序 | `codebuddy-v3` = 队友 CodeBuddy 工作区快照（基线 d3bf652） | `anli/planner.py`、`anli/materials.py`、`anli/material_text.py`、`anli/redact.py`、`anli/cost.py`（新文件原样）；`orchestrator` 三处调用点与 `_record_decision/_decision_lines`；`llm.py` 成本统计并入我方韧性循环（attempts/seconds/usage 逐次累加，重命名循环变量 `http_try` 避免覆盖 `attempt` 参数）；`exporter.section_number` + CSV；`intake/llm_intake/web` 的 material_facts 透传（并与我方字符串容错版合并）；前端材料面板 + 三格式导出选择器（保留我方打印按钮与量级闸门显示） | `b42d18c`（六处冲突全部解决） |
| P0 | 队友 0918 线 | `intake.py`：`RESPONSIBILITY_LEVELS`（含"不承担责任→0"）、四张主语表、`_subject_before`、`parse_responsibility_detail`、`client_share_of`、`build_profile(+role)` 与 extra 五键；`llm_intake.py`：`0` 合法化 + 规则优先的方向交叉校验 + role 全链透传；`web.py`：`_run_task` 传 `role=`；`strategy.py`：`build_strategies` 两条 P0 分支（责任不成立 / 我方主责）+ `render_summary` 的 `no_basis`/`client_majority`/`evidence_tail`/gap 分支；`orchestrator.py`：`claim_has_no_basis` 时清空补证请求与摘要措辞；`judgment.py`：`no_liability` 分支与"推演"口径 | `cda6116` |
| P1a | 同上 | `anli/documents.py`（新文件逐字）；`orchestrator._build_artifacts` 在策略建议后插入 `kind="document"`（`render_document(..., judgment_renderer=render_judgment)`），删除末尾旧判决书块；`judgment.py` 全文改写为"裁判推演"（保留我方"另予认定"逻辑） | `cda6116` |
| P1b | 同上 | `anli/published_cases.py` + `data/cases.published.json`（79 条）逐字；`tools_builtin` 注册 `published_case_search`（仅当缓存非空）；`tools.py` 白名单为 RESEARCHER 增加该项（**移植时必须补的一处**，否则检索被静默吞掉）；`agents._search_published_cases` + payload/交付物小节；`orchestrator` 构造参数与 extra；`web`/`cli` 装载 store；`tools/fetch_published_cases.py`、`tools/probe_case_sources.py` | `cda6116` |
| P2 | 同上 | `anli/scenarios.py` 三组样例（纯增量，逐字）；前端三按钮 + SAMPLES + 监听；`tools/check_conclusion_matrix.py` → 改名 `tools/responsibility_matrix.py`；`tests/`：`test_documents.py`、`test_responsibility_direction.py`、`test_conclusion_matrix.py`、`test_published_*.py`(3)、`test_sample_tier.py`、`test_full_evidence_sample.py`、`test_probe_sources.py`、`test_pack.py` | `cda6116` |
| 移植后修复 | 本会话发现 | ① `llm_intake.profile_from_payload` 的 `extra` 漏 `material_facts/material_summary` → 大模型路径材料面板为空（`4f0e9bb`）；② `agents.py` 四个"用户原话"出口未脱敏 → 新增 `user_text_for_llm` 并加回归测试（`cda6116`） | `4f0e9bb` / `cda6116` |
| 未移植（有意） | 队友线 | `tools/pack.py`/`tools/selftest.py`/文档正文一律用我方版本（我方版本更严：无密钥可跑绿、口径自检、法条来源官方库化）；队友 `test_*` 中依赖其特有实现的用例未收 | — |

### 5.2 已知风险与技术债务（含小的点）

1. **实证案例库 0 条**（最高优先级内容缺口）：量级闸门因此对多数案情输出"暂不提供"；报告已带"数据等级与升级路径"（`sample_tier_line`），但 🟢 实证区间在演示中亮不出来。缓解：`cases/draft_从文书提取_verified.json`（豫08民再17号，14,612.28 元，2万以下档）待按铁律核验入库。
2. **发布案例层的数据时效**：79 条为 2026-09-17 抓取；`fetched_at` 已写入每条，报告标注"非判决书全文"。若来源页改版，需 `tools/probe_case_sources.py` 复检活性。
3. **PDF 解析为文本层**（无 OCR）：扫描件/图片一律如实标注"未解析"且不产生证据——设计边界；用户若只传扫描件会感到"系统没用"。
4. **标准表仅浙江**：`data/standards.json` 来源为第三方汇编转载（`source_label` 已写明并附"引用前请在省统计局官网核对原文"）；其他省份无表。
5. **离线/无密钥降级**：规则版功能完整但论证文字变简；报告首行如实标注。已实测：干净机器双击 exe（无 .env）→ `llm: false` → 规则版跑通 14 项交付物 + 三格式导出。
6. **耗时波动**：同一主线案例实测 35.8–56.5 秒，调用次数 11–14 次（模型 JSON 校验重试与轮次决策的差异）；冻结口径取本次 capture run 的 47.8 秒 / 14 次，材料里注明"统一采用 run bbb2d3f4e575"。
7. **图 12（材料解析面板）无实拍截图**：浏览器自动化不支持文件选择器；能力由 31 项 `test_materials.py` 覆盖。提交包该图位已留说明，需人工补一张。
8. **图片拼接说明**：初赛方案图 4 为 9 条引用面板的 3×3 拼接版式（内容未改动、未美化），图注已注明"拼接展示"。
9. **`tools/` 内有历史一次性脚本**（`_night_*` 类已清理；现存 `fetch_*`/`probe_*` 为可复跑的数据管线工具）。
10. **微瑕（2026-09-19 已修复其一）**：`seal_check` 汇总提示的目录名已更新为《ai杭州模拟法庭》，且脚本已适配解压环境（unittest 计数、跳过时间戳核对）；`PROGRESS.md` 未逐轮记录移植（以 git log 为准）。
11. **前端 `index.html` 为构建产物**：改 `index.src.html` 后必须 `python tools/build_web.py`，否则页面与源码不一致（`seal_check` 已加该项检测）。
12. **`case_from_dict` 忽略未知字段**：未来扩展 `PublishedCase` 字段需同步抓取工具，否则静默丢弃。

---

[ZCode 全量转储完毕，等待 Qoder 审计]
