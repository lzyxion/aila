"""로그 소스 연결의 값 정규화 (라우터와 조회 실행 경로가 함께 쓴다).

`expected_services` 는 사람이 텍스트로 입력하는 목록이라 공백·빈 줄·중복이 그대로
들어온다. 저장 시점에 한 번 정리해 두면 조회 실행 경로는 값을 의심하지 않아도 되고,
`ingest_absent` 경고 메시지에 빈 이름이나 같은 이름이 두 번 실리지 않는다.

정규화 규칙은 저장 경로와 사용 경로 **양쪽에서** 같아야 하므로 (revision 0005 이전에
들어간 행, 또는 DB 를 직접 고친 행이 있을 수 있다) 여기 한 곳에 둔다.
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_service_names(values: Iterable[object] | None) -> list[str]:
    """각 항목을 strip → 빈 문자열 제거 → **순서 보존** 중복 제거.

    순서를 지키는 이유는 화면과 경고 메시지가 운영자가 적어 넣은 순서를 그대로
    되돌려줘야 "내가 쓴 목록"으로 읽히기 때문이다 (정렬해 버리면 매번 다르게 보인다).
    """
    seen: set[str] = set()
    result: list[str] = []
    for value in values or ():
        if value is None:
            continue
        name = str(value).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


__all__ = ["normalize_service_names"]
