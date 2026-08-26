"""보존 기간 삭제 경로와 `app_settings` 쓰기 경로.

Phase 1 담당 트랙: **정책 API**

설계 문서가 못 박은 두 가지가 오랫동안 "읽기만 있고 쓰기가 없는" 상태였다.

- `error_samples` 에는 보존 기간을 두고 지난 샘플을 **삭제**한다. 마스킹 규칙을
  강화해도 이미 저장된 샘플에는 소급되지 않기 때문이다 — 삭제가 유일한 회수 수단이다.
- 전역 일일 분석 한도·모델 단가표·샘플 보존 기간은 코드가 아니라 `app_settings` 에
  둔다. 배포 없이 바꿀 수 있어야 한다는 결정은 **쓰는 경로가 있어야** 완결된다.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from app.config import get_settings
from app.models import (
    SETTING_DAILY_ANALYSIS_LIMIT,
    SETTING_MODEL_PRICING,
    SETTING_SAMPLE_RETENTION_DAYS,
    AppSetting,
    ErrorSample,
)
from app.policies import service as policy_service
from app.schemas.logrecord import FetchResult
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    FakeLogSource,
    client,
    db,
    engine,
    log_record,
    make_connection,
    make_error_group,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
)


def _sample(db, group, *, days_old: float) -> ErrorSample:
    """`created_at` 을 직접 박은 대표 로그 (purge 는 created_at 기준이다)."""
    from datetime import UTC, datetime

    sample = ErrorSample(
        error_group_id=group.id,
        occurred_at=NOW,
        masked_log="TimeoutError token=<MASKED:API_KEY>",
        labels={},
        stacktrace=None,
        masking_rule_version="v1",
        created_at=datetime.now(UTC) - timedelta(days=days_old),
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return sample


def _group(db):
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    return policy, make_error_group(db, run, fingerprint="fp-timeout")


def _set(db, key: str, value) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value
    db.commit()


# --------------------------------------------------------------- 보존 기간


def test_retention_setting_is_actually_read(db) -> None:
    """`SETTING_SAMPLE_RETENTION_DAYS` 를 읽는 코드가 존재해야 한다."""
    assert policy_service.sample_retention_days(db) == (
        get_settings().default_sample_retention_days
    )
    _set(db, SETTING_SAMPLE_RETENTION_DAYS, 7)
    assert policy_service.sample_retention_days(db) == 7


def test_manual_purge_deletes_only_expired_samples(client, db) -> None:
    _, group = _group(db)
    _set(db, SETTING_SAMPLE_RETENTION_DAYS, 30)
    old = _sample(db, group, days_old=45)
    fresh = _sample(db, group, days_old=1)

    response = client.post("/api/maintenance/purge-samples")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1
    assert body["retention_days"] == 30
    assert body["cutoff"] is not None

    old_id, fresh_id = old.id, fresh.id
    db.expunge_all()
    assert db.get(ErrorSample, old_id) is None
    assert db.get(ErrorSample, fresh_id) is not None


def test_retention_zero_disables_deletion(client, db) -> None:
    """0 은 "전부 지운다"가 아니라 "자동 삭제 끔"이다 — 오타 하나로 다 날아가면 안 된다."""
    _, group = _group(db)
    _set(db, SETTING_SAMPLE_RETENTION_DAYS, 0)
    old = _sample(db, group, days_old=400)

    old_id = old.id
    body = client.post("/api/maintenance/purge-samples").json()

    assert body["deleted"] == 0
    db.expunge_all()
    assert db.get(ErrorSample, old_id) is not None


def test_query_run_triggers_the_daily_purge_once(client, db) -> None:
    """자동 실행은 정책 실행 진입점에 얹혀 하루 1 회만 돈다 (MVP 에 스케줄러는 없다)."""
    policy, group = _group(db)
    _set(db, SETTING_SAMPLE_RETENTION_DAYS, 30)
    old = _sample(db, group, days_old=45)
    provider = FakeLogSource(fetch_result=FetchResult(records=[log_record("x")]))

    with (
        patch("app.policies.integrations.build_provider", return_value=provider),
        patch("app.policies.integrations.group_records", return_value=[]),
    ):
        assert client.post(f"/api/policies/{policy.id}/query-runs", json={}).status_code == 201

    old_id = old.id
    db.expunge_all()
    assert db.get(ErrorSample, old_id) is None
    marker = db.get(AppSetting, policy_service.SETTING_SAMPLE_PURGE_LAST_RUN)
    assert marker is not None and isinstance(marker.value, str)

    # 두 번째 실행은 주기에 걸려 실제로 돌지 않는다.
    second = _sample(db, group, days_old=45)
    with (
        patch("app.policies.integrations.build_provider", return_value=provider),
        patch("app.policies.integrations.group_records", return_value=[]),
    ):
        client.post(f"/api/policies/{policy.id}/query-runs", json={})

    second_id = second.id
    db.expunge_all()
    assert db.get(ErrorSample, second_id) is not None


def test_due_check_runs_again_after_the_interval(db) -> None:
    _, group = _group(db)
    _set(db, SETTING_SAMPLE_RETENTION_DAYS, 30)
    _sample(db, group, days_old=45)

    assert policy_service.purge_expired_samples_if_due(db).deleted == 1
    assert policy_service.purge_expired_samples_if_due(db).executed is False

    # 마지막 실행 시각을 이틀 전으로 되돌리면 다시 돈다.
    marker = db.get(AppSetting, policy_service.SETTING_SAMPLE_PURGE_LAST_RUN)
    from datetime import UTC, datetime

    marker.value = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    db.commit()
    _sample(db, group, days_old=45)
    assert policy_service.purge_expired_samples_if_due(db).deleted == 1


# ------------------------------------------------------------ 설정 API 읽기


def test_list_settings_shows_the_three_reserved_keys(client, db) -> None:
    body = client.get("/api/settings").json()

    keys = [item["key"] for item in body["items"]]
    assert keys == sorted(
        [SETTING_DAILY_ANALYSIS_LIMIT, SETTING_MODEL_PRICING, SETTING_SAMPLE_RETENTION_DAYS]
    )
    # 행이 없어도 실제로 적용되는 값(기본값)을 함께 알려 준다.
    by_key = {item["key"]: item for item in body["items"]}
    assert by_key[SETTING_DAILY_ANALYSIS_LIMIT]["value"] is None
    assert by_key[SETTING_DAILY_ANALYSIS_LIMIT]["effective_value"] == (
        get_settings().default_daily_analysis_limit
    )
    assert by_key[SETTING_MODEL_PRICING]["effective_value"] == {}


def test_get_unknown_setting_key_is_404(client) -> None:
    assert client.get("/api/settings/daily_limit").status_code == 404


# ------------------------------------------------------------ 설정 API 쓰기


def test_put_setting_persists_and_takes_effect(client, db) -> None:
    """여기서 바꾼 한도가 곧바로 분석 시작의 429 판정에 쓰인다."""
    response = client.put(
        f"/api/settings/{SETTING_DAILY_ANALYSIS_LIMIT}", json={"value": 3}
    )

    assert response.status_code == 200, response.text
    assert response.json()["value"] == 3
    assert response.json()["effective_value"] == 3

    db.expire_all()
    assert db.get(AppSetting, SETTING_DAILY_ANALYSIS_LIMIT).value == 3
    assert policy_service.global_daily_analysis_limit(db) == 3


def test_put_model_pricing_accepts_a_well_formed_table(client, db) -> None:
    pricing = {"gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006, "currency": "USD"}}

    response = client.put(f"/api/settings/{SETTING_MODEL_PRICING}", json={"value": pricing})

    assert response.status_code == 200, response.text
    assert response.json()["value"] == pricing


def test_put_retention_days_is_read_back_by_the_purge(client, db) -> None:
    assert (
        client.put(f"/api/settings/{SETTING_SAMPLE_RETENTION_DAYS}", json={"value": 7}).status_code
        == 200
    )
    db.expire_all()
    assert policy_service.sample_retention_days(db) == 7


def test_unknown_key_cannot_be_written(client, db) -> None:
    """오타가 조용히 새 행으로 저장되면 읽는 쪽은 계속 기본값을 쓴다 (비용 사고)."""
    response = client.put("/api/settings/daily_limit", json={"value": 5})

    assert response.status_code == 404, response.text
    assert db.get(AppSetting, "daily_limit") is None


def test_invalid_values_are_rejected(client, db) -> None:
    cases = [
        (SETTING_DAILY_ANALYSIS_LIMIT, "many"),
        (SETTING_DAILY_ANALYSIS_LIMIT, -1),
        (SETTING_DAILY_ANALYSIS_LIMIT, True),
        (SETTING_SAMPLE_RETENTION_DAYS, 1.5),
        (SETTING_MODEL_PRICING, [1, 2]),
        (SETTING_MODEL_PRICING, {"gpt": {"input_per_1k": "cheap"}}),
        (SETTING_MODEL_PRICING, {"gpt": {"input_per_1k": -1}}),
        (SETTING_MODEL_PRICING, {"gpt": {}}),
    ]
    for key, value in cases:
        response = client.put(f"/api/settings/{key}", json={"value": value})
        assert response.status_code == 422, (key, value, response.text)
        db.expire_all()
        assert db.get(AppSetting, key) is None, (key, value)


def test_setting_value_null_falls_back_to_the_server_default(client, db) -> None:
    client.put(f"/api/settings/{SETTING_DAILY_ANALYSIS_LIMIT}", json={"value": 3})
    body = client.put(
        f"/api/settings/{SETTING_DAILY_ANALYSIS_LIMIT}", json={"value": None}
    ).json()

    assert body["value"] is None
    assert body["effective_value"] == get_settings().default_daily_analysis_limit
    db.expire_all()
    assert policy_service.global_daily_analysis_limit(db) == (
        get_settings().default_daily_analysis_limit
    )
