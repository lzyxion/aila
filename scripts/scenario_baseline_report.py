r"""시나리오 6종을 실 LLM 으로 분석하고 기준선 비교 리포트를 만든다.

설계 문서의 검증 절차 그대로다 — LLM 결과를 자동 채점하지 않는다. 이 스크립트는
분석 실행과 자료 정리(결과 ↔ expected-analysis.md 나란히 배치)까지만 하고,
비교 판정은 리포트의 체크리스트에 사람이 기입한다.

    backend\.venv\Scripts\python.exe scripts\scenario_baseline_report.py
    ... --policy-id 3 --llm-connection-id 2 --out docs/reports/...

주의: 시나리오당 실 LLM 1회 호출(과금)이며 일일 분석 한도를 6건 소모한다.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

#: error_type -> 시나리오 디렉터리. 그룹과 기준선을 잇는 유일한 매핑이다.
SCENARIO_MAP = {
    "PaymentGatewayTimeout": ("01", "infra/scenarios/01-payment-timeout"),
    "DatabaseConnectionError": ("02", "infra/scenarios/02-db-connection"),
    "TokenExpiredError": ("03", "infra/scenarios/03-auth-expired"),
    "AttributeError": ("04", "infra/scenarios/04-null-reference"),
    "ConfigurationError": ("05", "infra/scenarios/05-post-deploy-spike"),
    "PaymentRetryError": ("06", "infra/scenarios/06-secret-leak"),
}

CHECKLIST = """### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:
"""


def poll_job(client: httpx.Client, job_id: int, timeout_s: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/analysis-jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(2)
    raise TimeoutError(f"분석 작업 {job_id} 이 {timeout_s}s 안에 끝나지 않았습니다.")


def items_of(payload) -> list[dict]:
    """{total, items} 봉투와 맨 배열 양쪽을 수용한다."""
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    return payload


def render_result(result: dict) -> list[str]:
    lines: list[str] = []
    lines.append(f"- **요약**: {result['summary']}")
    lines.append(f"- **심각도 (LLM 추정)**: {result['severity']}")
    lines.append("- **가설**:")
    for i, h in enumerate(result.get("hypotheses", []), 1):
        evidence = ", ".join(h.get("evidence", [])) or "-"
        lines.append(f"  {i}. {h['cause']} (confidence {h.get('confidence')}, 근거: {evidence})")
    lines.append("- **확인 절차**: " + " → ".join(result.get("investigation_steps", [])))
    lines.append("- **완화 조치**: " + " / ".join(result.get("mitigation", [])))
    lines.append("- **한계 (limitations)**:")
    for item in result.get("limitations", []):
        lines.append(f"  - {item}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="시나리오 기준선 비교 리포트 생성")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--policy-id", type=int, default=3, help="시나리오 전체를 잡는 넓은 정책")
    parser.add_argument("--llm-connection-id", type=int, default=None, help="미지정 시 기본 연결")
    parser.add_argument("--out", default=None, help="리포트 경로 (기본 docs/reports/<날짜>-scenario-baseline.md)")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else REPO_ROOT / "docs" / "reports" / f"{today}-scenario-baseline.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(base_url=args.base_url, timeout=30.0)
    login = client.post("/api/auth/login", json={"username": args.username, "password": args.password})
    if login.status_code != 200:
        print(f"FAIL 로그인 실패: {login.status_code} {login.text[:200]}")
        return 1
    print(f"로그인: {login.json()['username']} ({login.json()['role']})")

    # 정책 실행 — 신선한 그룹으로 분석한다 (조회·그룹화는 무료).
    run = client.post(f"/api/policies/{args.policy_id}/query-runs", json={}).json()
    print(f"query-run #{run['id']} {run['status']} fetched={run['fetched_count']} groups={run['group_count']}")
    if run["status"] != "succeeded":
        print(f"FAIL 정책 실행 실패: {run.get('error_message')}")
        return 1

    groups = items_of(client.get(f"/api/query-runs/{run['id']}/error-groups", params={"limit": 50}).json())
    by_error_type = {g["error_type"]: g for g in groups if g.get("error_type") in SCENARIO_MAP}
    missing = [k for k in SCENARIO_MAP if k not in by_error_type]
    if missing:
        print(f"WARN 이번 조회에 없는 시나리오: {missing} — scripts/trigger_scenarios.py 를 먼저 실행하세요.")

    # LLM 연결 정보 (리포트 메타용)
    connections = client.get("/api/llm-connections").json()
    conn = None
    if args.llm_connection_id is not None:
        conn = next((c for c in connections if c["id"] == args.llm_connection_id), None)
    else:
        conn = next((c for c in connections if c.get("is_default") and c.get("active")), None)
    if conn is None:
        print("FAIL 사용할 LLM 연결을 찾지 못했습니다.")
        return 1
    print(f"LLM 연결: #{conn['id']} {conn['name']} ({conn['provider']}/{conn['model']})")

    sections: list[str] = []
    total_in = total_out = 0
    total_cost = 0.0
    cost_known = False
    failures = 0

    for error_type, (num, scen_dir) in sorted(SCENARIO_MAP.items(), key=lambda kv: kv[1][0]):
        title = f"{num} · {error_type}"
        group = by_error_type.get(error_type)
        if group is None:
            sections.append(f"## {title}\n\n이번 조회에 해당 그룹이 없음 — 시나리오 트리거 후 재실행 필요.\n")
            failures += 1
            continue

        print(f"[{num}] 그룹 #{group['id']} ({error_type}, count={group['count']}) 분석 요청...")
        body = {}
        if args.llm_connection_id is not None:
            body["llm_connection_id"] = args.llm_connection_id
        created = client.post(f"/api/error-groups/{group['id']}/analysis-jobs", json=body)
        if created.status_code not in (200, 201, 202):
            print(f"     FAIL 분석 생성 {created.status_code}: {created.text[:200]}")
            sections.append(f"## {title}\n\n분석 생성 실패: {created.status_code} {created.text[:300]}\n")
            failures += 1
            continue
        payload = created.json()
        job_stub = payload.get("job", payload)
        job = poll_job(client, job_stub["id"])
        if job["status"] != "succeeded":
            print(f"     FAIL 분석 실패: {job.get('error_message')}")
            sections.append(f"## {title}\n\n분석 실패: {job.get('error_message')}\n")
            failures += 1
            continue

        usage = job.get("usage") or {}
        in_tok = usage.get("input_tokens") or 0
        out_tok = usage.get("output_tokens") or 0
        cost = usage.get("estimated_cost")
        total_in += in_tok
        total_out += out_tok
        if cost is not None:
            total_cost += float(cost)
            cost_known = True
        cost_text = f"${float(cost):.6f} (추정)" if cost is not None else "- (단가 미등록)"
        print(f"     OK  job #{job['id']} tokens {in_tok}/{out_tok}, cost {cost_text}")

        expected_path = REPO_ROOT / scen_dir / "expected-analysis.md"
        expected = expected_path.read_text(encoding="utf-8") if expected_path.exists() else "(기준선 파일 없음)"

        lines = [f"## {title}", ""]
        lines.append(
            f"그룹 #{group['id']} · 발생 {group['count']}건 · 작업 #{job['id']} · "
            f"토큰 {in_tok}/{out_tok} · 비용 {cost_text}"
        )
        lines.append("")
        lines.append(f"### LLM 분석 결과 ({conn['provider']}/{conn['model']})")
        lines.append("")
        lines.extend(render_result(job["result"]))
        lines.append("")
        lines.append(f"### 기대 기준선 (`{scen_dir}/expected-analysis.md`)")
        lines.append("")
        lines.append("<details><summary>기준선 전문 펼치기</summary>")
        lines.append("")
        lines.append(expected.strip())
        lines.append("")
        lines.append("</details>")
        lines.append("")
        lines.append(CHECKLIST)
        sections.append("\n".join(lines))

    cost_total_text = f"${total_cost:.6f} (추정)" if cost_known else "- (단가 미등록)"
    header = "\n".join(
        [
            f"# 시나리오 기준선 비교 리포트 — {today}",
            "",
            "실 LLM 분석 결과와 `infra/scenarios/*/expected-analysis.md` 기준선을 나란히 둔다.",
            "**비교 판정은 자동이 아니다** — 각 시나리오의 체크리스트에 사람이 기입한다 (설계 문서의 검증 절차).",
            "",
            f"- LLM: `{conn['provider']}/{conn['model']}` (연결 #{conn['id']} {conn['name']})",
            f"- query-run: #{run['id']} (정책 #{args.policy_id}, fetched {run['fetched_count']}, 그룹 {run['group_count']})",
            f"- 총 토큰: 입력 {total_in:,} / 출력 {total_out:,} · 총 비용: {cost_total_text}",
            f"- 결과의 심각도·confidence 는 **LLM 추정값**이며 발생량 지표와 무관하다.",
            "",
        ]
    )
    out_path.write_text(header + "\n" + "\n\n---\n\n".join(sections) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    if failures:
        print(f"{failures}개 시나리오 미완 — 리포트에 사유 기재")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
