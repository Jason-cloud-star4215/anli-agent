"""端到端自测脚本：对着运行中的服务把主要功能跑一遍。

用法：
    python tools/selftest.py --base http://127.0.0.1:8790

覆盖：健康检查、三种身份、证据链追问、勾选补交、上传文件、放弃补证、
参数校验、超范围案情、报告结构。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" :: {detail}" if detail else ""))
    return bool(condition)


def request(base: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


CAR_DESC = (
    "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，对方主要责任。"
    "门诊治疗花了医疗费3200元，误工费1800元，护理费900元，交通费300元。"
    "我想要对方赔我12000元。"
)


def create_task(
    base: str, role: str, desc: str = CAR_DESC, expectation: float = 12000
) -> str:
    _, data = request(
        base,
        "/api/tasks",
        {
            "description": desc,
            "role": role,
            "expectation": expectation,
            "file_names": [],
            "files": [],
            "confirmed": [],
        },
    )
    return data.get("task_id", "")


def wait_done(base: str, task_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        _, last = request(base, f"/api/tasks/{task_id}")
        if last.get("state") not in {"RUNNING", None}:
            return last
        time.sleep(1)
    return last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="案理端到端自测")
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    args = parser.parse_args(argv)
    base = args.base

    status, health = request(base, "/api/health")
    check("健康检查可访问", status == 200 and health.get("code") == 0, f"llm={health.get('llm')}")

    task = create_task(base, "claimant")
    check("创建任务返回 task_id", bool(task), task)
    claimant = wait_done(base, task)
    check("主张方任务完成", claimant.get("state") == "NEEDS_EVIDENCE", f"state={claimant.get('state')}")
    check("有结论摘要", len(claimant.get("summary", "")) > 40)
    check("有策略建议", len(claimant.get("strategies", [])) >= 2, f"{len(claimant.get('strategies', []))} 条")
    check("有证据补强清单", len(claimant.get("evidence_request", [])) >= 1)
    check("有引用核验结果", len(claimant.get("citations", [])) >= 1, f"{len(claimant.get('citations', []))} 条")
    check("有风险清单", len(claimant.get("findings", [])) >= 1)
    check("报告包含立场分析", "我方主张要点" in claimant.get("report", ""))
    check("报告包含补证章节", "需要你补充的证据" in claimant.get("report", ""))
    check("证据充分度有值", isinstance(claimant.get("evidence_sufficiency"), (int, float)))

    before_cap = (claimant.get("assessment") or {}).get("legal_cap", 0)
    status, _ = request(
        base,
        f"/api/tasks/{task}/evidence",
        {"note": "", "confirmed": ["医疗费票据", "误工证明"], "files": [], "file_names": []},
    )
    check("补交接口返回成功", status == 200)
    after = wait_done(base, task)
    after_cap = (after.get("assessment") or {}).get("legal_cap", 0)
    check("勾选确认后金额上升", after_cap > before_cap, f"{before_cap} → {after_cap}")
    check(
        "充分度上升",
        (after.get("evidence_sufficiency") or 0) >= (claimant.get("evidence_sufficiency") or 0),
    )

    # 3.5 补充材料细节后，计入比例应继续上升
    status, _ = request(
        base,
        f"/api/tasks/{task}/evidence",
        {
            "note": "",
            "confirmed": [],
            "files": [],
            "file_names": [],
            "quality": {
                "医疗费票据": ["formal", "amount", "related"],
                "误工证明": ["written", "period", "amount", "flow"],
            },
        },
    )
    check("补充细节接口返回成功", status == 200)
    detailed = wait_done(base, task)
    detailed_cap = (detailed.get("assessment") or {}).get("legal_cap", 0)
    check("补充细节后金额继续上升", detailed_cap > after_cap, f"{after_cap} → {detailed_cap}")

    status, schema = request(base, "/api/evidence-schema")
    check(
        "材料细节问题接口可用",
        status == 200 and "误工证明" in (schema.get("schema") or {}),
    )

    blob = base64.b64encode(b"demo-file").decode("ascii")
    status, _ = request(
        base,
        f"/api/tasks/{task}/evidence",
        {
            "note": "",
            "confirmed": [],
            "file_names": ["交通费发票.jpg"],
            "files": [{"name": "交通费发票.jpg", "data": blob}],
        },
    )
    check("上传文件接口返回成功", status == 200)
    uploaded = wait_done(base, task)
    check(
        "上传票据后该项转为可支持",
        any(
            item.get("item") == "交通费" and item.get("status") == "supported"
            for item in uploaded.get("item_findings", [])
        ),
        f"充分度={uploaded.get('evidence_sufficiency')}",
    )

    task2 = create_task(base, "claimant")
    wait_done(base, task2)
    status, _ = request(
        base, f"/api/tasks/{task2}/evidence", {"waive_all": True, "files": [], "file_names": []}
    )
    check("放弃补证接口返回成功", status == 200)
    waived = wait_done(base, task2)
    check("放弃补证后直接交付", waived.get("state") == "DELIVERED", f"state={waived.get('state')}")
    check("结论说明材料拿不出来", "拿不出来" in waived.get("summary", ""))
    check("结论给出两种口径", "两种口径" in waived.get("summary", ""))
    check("放弃补证后不再要求补材料", not waived.get("evidence_request"))

    defendant = wait_done(base, create_task(base, "defendant"))
    judge = wait_done(base, create_task(base, "judge"))
    check("抗辩方报告有抗辩要点", "我方抗辩要点" in defendant.get("report", ""))
    check("法官报告有中立评估", "中立评估" in judge.get("report", ""))
    check("抗辩方结论口径不同", "对方主张" in defendant.get("summary", ""))
    check(
        "三种身份报告互不相同",
        len({claimant.get("report"), defendant.get("report"), judge.get("report")}) == 3,
    )
    check(
        "抗辩方策略提到质证或要求对方",
        any(
            ("质证" in item["action"] or "要求对方" in item["action"])
            for item in defendant.get("strategies", [])
        ),
    )
    check(
        "主张方策略提到保险理赔",
        any("保险" in item["action"] for item in claimant.get("strategies", [])),
    )
    check(
        "抗辩方策略不出现「多主张」",
        not any("多主张" in item.get("expected", "") for item in defendant.get("strategies", [])),
    )

    status, data = request(base, "/api/tasks", {"description": "", "role": "claimant"})
    check("空描述被拒绝", status == 400, f"status={status}")
    status, data = request(base, "/api/tasks", {"description": "你好", "role": "claimant"})
    if check("无法识别时不报错", status == 200, f"status={status}"):
        need_info = wait_done(base, data.get("task_id", ""))
        check(
            "超范围案情返回待补信息",
            need_info.get("state") == "NEEDS_INFO",
            f"state={need_info.get('state')}",
        )

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
