# 04 · Null 참조

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `null-reference` |
| 예상 오류 | `AttributeError: 'NoneType' object has no attribute 'display_name'`, HTTP 500 |
| 검증 대상 | **스택 프레임 추출** |
| 서비스 / 환경 | `payment-api` / `staging` |
| 트리거 | `GET /profile`, `POST /scenarios/null-reference/burst?count=N` |

## 재현

```powershell
curl.exe -X POST "http://localhost:8081/scenarios/null-reference/burst?count=25"
```

## 로그 모양

`stacktrace` 필드에 **개행이 든 문자열 하나**로 담긴다. 여러 줄로 출력하면 Alloy 가
줄마다 별개 엔트리로 읽어 그룹화가 무의미해지므로, demo-api 는 의도적으로 한 줄로 낸다.

```
Traceback (most recent call last):
  File "/app/app/scenarios.py", line 263, in null_reference
    _load_profile(user_id)
  File "/app/app/scenarios.py", line 241, in _load_profile
    _render_display_name(profile)
  File "/app/app/scenarios.py", line 250, in _render_display_name
    return profile.display_name.strip()
           ^^^^^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'display_name'
```

호출 경로가 3 단계인 **진짜 트레이스백**이다. 스택 프레임 추출 로직을 프레임이 하나뿐인
가짜 스택으로 테스트하면 "상위 프레임만 쓴다"는 설계 결정을 검증할 수 없다.

## 기대하는 그룹화 결과

- 그룹 **1 개**. `user_id=2240` 의 숫자가 지워져야 한다.
- fingerprint 입력은 `service` + `AttributeError` + 정규화 메시지 +
  **상위 스택 프레임** 이다. 여기서 상위 프레임은 예외가 실제로 발생한
  `scenarios.py:_render_display_name` 쪽이다.
- **스택 전체를 해시하면 안 된다.** 호출 경로가 조금만 달라져도 같은 버그가 다른 그룹으로
  쪼개진다. 반대로 메시지만 해시하면 서로 다른 원인이 한 그룹으로 뭉친다.
  이 시나리오는 그 중간을 잡았는지 확인하는 자리다.
- 스택의 파일 경로에 든 **줄 번호는 정규화 대상**이다. 코드가 한 줄만 밀려도
  같은 버그가 새 그룹이 되어서는 안 된다.

## 기대하는 LLM 분석 (원인 가설)

- **요약** — 프로필 조회가 결과 없음(None)을 반환했는데 호출부가 그대로 속성에 접근해 실패한다.
- **severity** — `medium`. 특정 사용자 경로만 깨진다.
- **가설**
  1. `_fetch_profile_row` 가 해당 user_id 에 대해 행을 찾지 못해 `None` 반환 — 근거: 스택 최상단이 속성 접근
  2. 데이터 정합성 문제 — 참조는 있으나 프로필 레코드가 삭제됨
  3. 상류에서 잘못된 user_id 가 전달됨
- **확인 절차**
  1. 실패한 `user_id` 몇 개로 프로필 레코드 존재 여부 직접 조회
  2. `_fetch_profile_row` 의 None 반환 경로에 방어 코드/로그가 있는지 확인
  3. 최근 배포에서 프로필 조회 쿼리나 스키마가 바뀌었는지 확인
- **완화** — 호출부에서 None 검사 후 404 로 응답, 저장소 계층이 None 대신 명시적 예외를 던지도록 변경
- **한계** — 로그에 user_id 는 있으나 그 사용자의 데이터 상태는 알 수 없다.

## 흔한 실패 모드

- 상위 프레임을 못 뽑아 `exception` 만으로 그룹화 → 서로 다른 위치의 `AttributeError` 가 한 그룹.
- 스택 전체(줄 번호 포함) 해시 → burst 25 건이 여러 그룹으로 분리.
- 스택트레이스가 여러 로그 라인으로 쪼개져 들어오는 것 (demo-api 는 그렇지 않지만,
  실제 시스템에서는 흔하다 — 어댑터가 이 상황을 어떻게 다룰지는 별도 과제다).
