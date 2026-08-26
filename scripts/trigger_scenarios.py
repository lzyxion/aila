"""장애 시나리오 6종 트리거 스크립트.

demo-api 의 시나리오 엔드포인트를 순서대로 호출해 Loki 에 오류 로그를 채운다.
표준 라이브러리만 사용하므로 아무 python 으로나 실행할 수 있다.

    python scripts/trigger_scenarios.py                # 기본: 전부 + burst 60 + noise 20
    python scripts/trigger_scenarios.py --repeat 10    # 시나리오별 반복 횟수
    python scripts/trigger_scenarios.py --no-deploy    # 05(배포 급증) 제외 — release 토글을 원치 않을 때
    python scripts/trigger_scenarios.py --burst 0      # burst 생략

주의: 트리거 엔드포인트는 **의도적으로 오류 상태코드를 반환한다** (504/500/401/502).
그 코드가 오면 성공이고, 그 외 코드나 연결 실패가 실패다.

트리거 후에는 Grafana(:3000)에서 유입을 확인하고, 앱에서 정책을 실행하면 된다.
시나리오 03(인증 만료)은 level=WARNING 이므로 정책 LogQL 에 WARNING 이 포함돼야 잡힌다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#: (이름, 메서드, 경로, 기대 상태코드) — 기대 코드가 곧 "시나리오가 재현됐다"는 신호다.
SCENARIOS: list[tuple[str, str, str, int]] = [
    ("01 결제 타임아웃 (payment-api)", "GET", "/payment/charge", 504),
    ("02 DB 연결 실패 (order-api)", "GET", "/orders", 500),
    ("03 인증 만료 (auth-api, WARNING!)", "GET", "/auth/me", 401),
    ("04 Null 참조 (스택트레이스)", "GET", "/profile", 500),
    ("06 비밀값 포함 (마스킹 검증)", "GET", "/debug/dump-context", 502),
]


def call(base: str, method: str, path: str, timeout: float = 10.0) -> int:
    """호출하고 상태코드를 돌려준다. 4xx/5xx 도 정상 흐름이므로 예외로 다루지 않는다."""
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    parser = argparse.ArgumentParser(description="AILA 장애 시나리오 트리거")
    parser.add_argument("--base-url", default="http://localhost:8081", help="demo-api 주소")
    parser.add_argument("--loki-url", default="http://localhost:3100", help="유입 확인용 Loki 주소")
    parser.add_argument("--repeat", type=int, default=5, help="시나리오별 호출 횟수 (그룹 count 확보)")
    parser.add_argument("--burst", type=int, default=60, help="결제 타임아웃 burst 건수 (0=생략)")
    parser.add_argument("--noise", type=int, default=20, help="정상 로그 건수 (0=생략)")
    parser.add_argument("--release", default="v1.5.0", help="05 배포 급증에 쓸 release 라벨")
    parser.add_argument("--spike-seconds", type=int, default=180, help="05 급증 지속 시간")
    parser.add_argument(
        "--no-deploy", action="store_true",
        help="05(배포 급증) 생략 — admin/deploy 는 release 를 토글하므로 원치 않으면 끈다",
    )
    args = parser.parse_args()
    failed = 0

    # demo-api 살아있는지 먼저 확인 — 죽어 있으면 이후 전부 연결 오류라 빨리 끝낸다.
    try:
        call(args.base_url, "GET", "/scenarios", timeout=5.0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"FAIL demo-api({args.base_url}) 에 연결할 수 없습니다: {exc}")
        print("     docker compose up -d 로 스택이 떠 있는지 확인하세요.")
        return 1
    print(f"demo-api OK — {args.base_url}")

    # 시나리오 01~04, 06 — 반복 호출로 그룹 count 를 확보한다.
    for name, method, path, expected in SCENARIOS:
        codes = [call(args.base_url, method, path) for _ in range(args.repeat)]
        ok = all(code == expected for code in codes)
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} {name}: {path} x{args.repeat} -> {codes[0]} (기대 {expected})")
        if not ok:
            failed += 1

    # 05 배포 급증 — release 토글이므로 옵션으로 껐다 켤 수 있다.
    if args.no_deploy:
        print("SKIP 05 배포 급증 (--no-deploy)")
    else:
        query = urllib.parse.urlencode({"release": args.release, "spike_seconds": args.spike_seconds})
        code = call(args.base_url, "POST", f"/admin/deploy?{query}")
        mark = "OK  " if code == 200 else "FAIL"
        print(f"{mark} 05 배포 급증: release={args.release}, {args.spike_seconds}s 동안 ConfigurationError 유입 -> {code}")
        print("     되돌리기: curl.exe -X POST " + args.base_url + "/admin/deploy")
        if code != 200:
            failed += 1

    # burst / noise — 발생량을 채워 추이 차트와 상위 그룹 정렬이 의미를 갖게 한다.
    if args.burst > 0:
        code = call(args.base_url, "POST", f"/scenarios/payment-timeout/burst?count={args.burst}", timeout=30.0)
        print(f"{'OK  ' if code == 200 else 'FAIL'} burst x{args.burst} -> {code}")
        failed += 0 if code == 200 else 1
    if args.noise > 0:
        code = call(args.base_url, "POST", f"/scenarios/noise?count={args.noise}", timeout=30.0)
        print(f"{'OK  ' if code == 200 else 'FAIL'} noise x{args.noise} -> {code}")
        failed += 0 if code == 200 else 1

    # Loki 유입 확인 — 몇 초 기다렸다 최근 5분 서비스별 건수를 metric 쿼리로 센다.
    print("Loki 유입 확인 중 (5초 대기)...")
    time.sleep(5)
    try:
        query = urllib.parse.urlencode(
            {"query": 'sum by (service) (count_over_time({service=~".+"} [5m]))'}
        )
        with urllib.request.urlopen(f"{args.loki_url}/loki/api/v1/query?{query}", timeout=10.0) as response:
            payload = json.load(response)
        results = payload.get("data", {}).get("result", [])
        if results:
            for item in results:
                service = item.get("metric", {}).get("service", "?")
                value = item.get("value", [None, "?"])[1]
                print(f"     {service}: 최근 5분 {value} 건")
        else:
            print("FAIL Loki 에 최근 5분 로그가 없습니다 — alloy 수집을 확인하세요 (Grafana :3000)")
            failed += 1
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"WARN Loki 확인 실패 ({exc}) — Grafana(:3000)에서 직접 확인하세요")

    print()
    if failed:
        print(f"{failed} 개 항목 실패")
        return 1
    print("완료 — 이제 앱에서 정책을 실행하면 그룹이 잡힙니다.")
    print("  · 시나리오 03 은 WARNING 이므로 LogQL 에 level=~\"ERROR|WARNING\" 필요")
    print("  · 넓은 정책 예시: {service=~\"payment-api|order-api|auth-api\"} | json | level=~\"ERROR|WARNING\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
