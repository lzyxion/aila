/**
 * 정책 추가·수정 전용 페이지 (`/policies/new`, `/policies/:policyId/edit`).
 *
 * 목록 화면에서 폼을 분리한 이유는 하나다 — 정책은 쿼리 한 줄이 아니라 **실행 한도를
 * 포함한 묶음**이라 입력 항목이 열 개가 넘는다. 목록 옆에 붙여 두면 "지금 무엇을 고치는
 * 중인가"가 스크롤 밖으로 밀리고, 미리보기 결과가 들어갈 자리도 없다.
 *
 * 저장하면 목록으로 돌아간다. 편집 중 이탈은 막지 않는다(저장 전 값은 서버에 없다).
 *
 * 미리보기는 여기 있다 — **저장 전에** 쿼리가 실제로 무엇을 가져오는지 보는 장치이므로
 * 조회 전용 화면에 둘 이유가 없다. 잘못 쓴 쿼리가 정책으로 굳으면 이후 모든 조회가
 * 조용히 빈 결과를 낸다.
 *
 * 폼은 네 덩어리다 (Phase 8): **기본 정보**(무엇을 어디서 잡나) · **조회 한도**(한 번에
 * 얼마나 가져오나) · **분모 쿼리**(비율의 분모) · **분석·스케줄**(비용이 나가는 설정).
 * 비용·상한처럼 잘못 두면 돈이 나가는 항목만 마지막 덩어리에 모아 두었다 — 긴 폼에서
 * 위험한 스위치가 이름 입력란 옆에 섞여 있으면 실수로 켜진다.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import {
  useCreatePolicy,
  useLogSourceConnections,
  useLogSourceLabels,
  usePolicies,
  usePreviewPolicy,
  useTestLogSourceConnection,
  useUpdatePolicy,
} from '../api/queries';
import { policySchedule, type PolicyPreviewResponse, type PolicyRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { BackIcon, RunIcon, SaveIcon } from '../components/icons';
import {
  Badge,
  Button,
  ButtonLink,
  Card,
  Checkbox,
  Code,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  LoadingBlock,
  Notice,
  PageHeader,
  PageStack,
  Select,
  SkeletonCard,
  TableWrap,
  Td,
  Textarea,
  TextLink,
  Th,
} from '../components/ui';
import { formatIntervalMinutes, formatNumber, warningCodeLabel } from '../lib/format';
import { queryLanguageOf } from '../lib/sources';

/**
 * 자동 분석의 비용 문구 — **계약**이다.
 *
 * 자동 분석은 비용이 나가는 유일한 자동 경로다. 두 가지 제한을 항상 함께 적는다:
 * (1) 처음 보는 오류(fingerprint 이력 없음)에만 돈다, (2) 일일 분석 한도를 그대로 받는다.
 * 둘 중 하나만 적으면 "자동으로 계속 돈다"로 읽힌다.
 */
export const AUTO_ANALYZE_COST_NOTE =
  '자동 분석은 처음 보는 오류에만 실행되고 일일 한도의 제한을 받습니다.';

interface FormState {
  log_source_connection_id: number | '';
  name: string;
  description: string;
  query: string;
  /** 빈 문자열이면 저장 시 `null` 로 보낸다 — "지운다"와 "안 건드린다"를 구분해야 한다. */
  baseline_query: string;
  default_range_minutes: number;
  max_lines: number;
  exclusionsText: string;
  max_samples_per_group: number;
  allow_ai_analysis: boolean;
  daily_analysis_limit: string;
  /** 수정할 때만 의미가 있다 — 생성 API 에는 active 필드가 없다(항상 활성으로 생긴다). */
  active: boolean;
  schedule_enabled: boolean;
  /** 문자열로 들고 있다가 저장 시점에 숫자로 바꾼다 (빈 값 = 미설정). */
  schedule_interval_minutes: string;
  auto_analyze_new: boolean;
}

const EMPTY_FORM: FormState = {
  log_source_connection_id: '',
  name: '',
  description: '',
  query: '{service="", environment="staging"} | json | level="ERROR"',
  baseline_query: '',
  default_range_minutes: 60,
  max_lines: 1000,
  exclusionsText: '',
  max_samples_per_group: 3,
  allow_ai_analysis: true,
  daily_analysis_limit: '',
  active: true,
  schedule_enabled: false,
  schedule_interval_minutes: '60',
  auto_analyze_new: false,
};

function toForm(policy: PolicyRead): FormState {
  const schedule = policySchedule(policy);
  return {
    log_source_connection_id: policy.log_source_connection_id,
    name: policy.name,
    description: policy.description ?? '',
    query: policy.query,
    baseline_query: policy.baseline_query ?? '',
    default_range_minutes: policy.default_range_minutes,
    max_lines: policy.max_lines,
    exclusionsText: policy.exclusions.join('\n'),
    max_samples_per_group: policy.max_samples_per_group,
    allow_ai_analysis: policy.allow_ai_analysis,
    daily_analysis_limit:
      policy.daily_analysis_limit === null ? '' : String(policy.daily_analysis_limit),
    active: policy.active,
    schedule_enabled: schedule.enabled,
    schedule_interval_minutes:
      schedule.intervalMinutes === null ? '' : String(schedule.intervalMinutes),
    auto_analyze_new: schedule.autoAnalyze,
  };
}

function parseExclusions(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export function PolicyEditPage() {
  const params = useParams<{ policyId?: string }>();
  const navigate = useNavigate();
  const write = useWriteAccess();

  const parsed = params.policyId ? Number(params.policyId) : NaN;
  const editingId = Number.isFinite(parsed) ? parsed : null;
  const isEditing = editingId !== null;

  const policiesQuery = usePolicies();
  const connectionsQuery = useLogSourceConnections();
  const createPolicy = useCreatePolicy();
  const updatePolicy = useUpdatePolicy();
  const preview = usePreviewPolicy();
  const testConnection = useTestLogSourceConnection();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  /** 편집 대상 정책을 폼에 한 번만 채운다 — 매 렌더 덮어쓰면 입력이 되돌아간다. */
  const [loadedId, setLoadedId] = useState<number | null>(null);

  const connections = connectionsQuery.data ?? [];
  const activeConnections = connections.filter((connection) => connection.active);
  const target = isEditing
    ? (policiesQuery.data?.find((policy) => policy.id === editingId) ?? null)
    : null;

  useEffect(() => {
    if (!isEditing || !target || loadedId === target.id) return;
    setForm(toForm(target));
    setLoadedId(target.id);
  }, [isEditing, loadedId, target]);

  // 새 정책이면 첫 활성 연결을 기본 선택으로 채운다.
  useEffect(() => {
    if (isEditing) return;
    if (form.log_source_connection_id === '' && activeConnections.length > 0) {
      setForm((prev) => ({ ...prev, log_source_connection_id: activeConnections[0].id }));
    }
  }, [activeConnections, form.log_source_connection_id, isEditing]);

  const connectionId = form.log_source_connection_id === '' ? null : Number(form.log_source_connection_id);
  const labelsQuery = useLogSourceLabels(connectionId);
  // 선택한 연결의 소스가 쿼리 언어를 정한다 — 라벨의 "(LogQL)" 병기는 여기서 나온다.
  const selectedConnection = connections.find((connection) => connection.id === connectionId) ?? null;
  const queryLanguage = queryLanguageOf(selectedConnection?.source_type);
  const queryLabel = queryLanguage ? `쿼리 (${queryLanguage})` : '쿼리';
  const saving = createPolicy.isPending || updatePolicy.isPending;

  function validate(): string | null {
    if (form.log_source_connection_id === '') return '로그 소스 연결을 선택하십시오.';
    if (!form.name.trim()) return '정책 이름을 입력하십시오.';
    if (!form.query.trim()) return '쿼리를 입력하십시오.';
    if (form.default_range_minutes <= 0) return '기본 기간은 1분 이상이어야 합니다.';
    if (form.max_lines <= 0) return '최대 조회 수는 1 이상이어야 합니다.';
    if (form.max_samples_per_group <= 0) return '대표 로그 수는 1 이상이어야 합니다.';
    for (const pattern of parseExclusions(form.exclusionsText)) {
      try {
        new RegExp(pattern);
      } catch {
        return `제외 정규식이 잘못되었습니다: ${pattern}`;
      }
    }
    if (form.schedule_enabled) {
      const interval = Number(form.schedule_interval_minutes);
      if (form.schedule_interval_minutes.trim() === '' || !Number.isFinite(interval)) {
        return '스케줄을 켰으면 실행 주기(분)를 입력하십시오.';
      }
      if (!Number.isInteger(interval) || interval < 1) {
        return '실행 주기는 1분 이상의 정수여야 합니다.';
      }
    }
    return null;
  }

  function handleSubmit() {
    const message = validate();
    setFormError(message);
    if (message) return;

    const payload = {
      log_source_connection_id: Number(form.log_source_connection_id),
      name: form.name.trim(),
      description: form.description.trim() || null,
      query: form.query.trim(),
      // 빈 입력은 **null** 이다 — 빈 문자열을 보내면 "쿼리를 설정했는데 아무것도 세지
      // 못하는" 상태가 되고, 화면은 그걸 "분모 쿼리 실패"로 읽는다.
      baseline_query: form.baseline_query.trim() || null,
      default_range_minutes: form.default_range_minutes,
      max_lines: form.max_lines,
      exclusions: parseExclusions(form.exclusionsText),
      max_samples_per_group: form.max_samples_per_group,
      allow_ai_analysis: form.allow_ai_analysis,
      daily_analysis_limit:
        form.daily_analysis_limit.trim() === '' ? null : Number(form.daily_analysis_limit),
      schedule_enabled: form.schedule_enabled,
      // 스케줄이 꺼져 있으면 주기를 비운다 — 꺼진 정책에 주기만 남아 있으면 다음에 켰을 때
      // 예전 값으로 조용히 돌기 시작한다.
      schedule_interval_minutes:
        form.schedule_enabled && form.schedule_interval_minutes.trim() !== ''
          ? Number(form.schedule_interval_minutes)
          : null,
      // 자동 분석은 스케줄 조회에 붙는 동작이다. 스케줄이 꺼져 있으면 켤 자리가 없다.
      auto_analyze_new: form.schedule_enabled && form.auto_analyze_new,
    };

    if (isEditing) {
      // active 는 수정에서만 보낸다 — 여기가 비활성 정책을 되살리는 유일한 경로다.
      updatePolicy.mutate(
        { id: editingId, payload: { ...payload, active: form.active } },
        { onSuccess: () => navigate(`/policies?policy=${editingId}`) },
      );
    } else {
      createPolicy.mutate(payload, {
        onSuccess: (created) => navigate(`/policies?policy=${created.id}`),
      });
    }
  }

  function handlePreview() {
    const message = validate();
    setFormError(message);
    if (message) return;
    preview.mutate({
      log_source_connection_id: Number(form.log_source_connection_id),
      query: form.query.trim(),
      range_minutes: form.default_range_minutes,
      limit: Math.min(form.max_lines, 50),
      exclusions: parseExclusions(form.exclusionsText),
    });
  }

  // viewer 는 정책을 만들거나 고칠 수 없다. 폼을 비활성 상태로 남겨 두면 "왜 저장이 안
  // 되지"로 끝나므로, 화면 자체를 사유가 적힌 안내로 바꾼다.
  if (!write.allowed) {
    return (
      <div>
        <PageHeader title={isEditing ? '정책 수정' : '새 정책'} />
        <Card title="권한 없음">
          <Notice tone="neutral" title="읽기 전용 계정입니다">
            {write.reason} 정책의 쿼리·한도·스케줄은 <TextLink to="/policies">분석 정책</TextLink>{' '}
            목록에서 볼 수 있습니다.
          </Notice>
        </Card>
      </div>
    );
  }

  if (isEditing && policiesQuery.isPending) {
    // 폼 카드는 뼈대가 정해진 자리라 스켈레톤이 맞는다 (몇 건인지 세는 목록이 아니다).
    return (
      <div>
        <PageHeader title="정책 수정" />
        <SkeletonCard lines={6} label="정책을 불러오는 중" />
      </div>
    );
  }
  if (isEditing && policiesQuery.data && !target) {
    return (
      <div>
        <PageHeader title="정책 수정" />
        <ErrorBlock error={`정책 #${editingId} 을(를) 찾을 수 없습니다.`} />
      </div>
    );
  }

  const scheduleSummary =
    form.schedule_enabled && form.schedule_interval_minutes.trim() !== ''
      ? `${formatIntervalMinutes(Number(form.schedule_interval_minutes))}마다 실행`
      : null;

  return (
    <div>
      <PageHeader
        title={isEditing ? `정책 수정 · ${target?.name ?? `#${editingId}`}` : '새 정책'}
        description="저장 전 미리보기로 이 쿼리가 실제로 무엇을 가져오는지 확인하십시오."
        info={
          <>
            잘못 쓴 쿼리가 정책으로 굳으면 이후 모든 조회가 <strong>조용히 빈 결과</strong>를
            냅니다 — 미리보기는 그걸 저장 전에 잡는 장치입니다.
            <span className="mt-1.5 block">
              미리보기에 나오는 로그도 <strong>마스킹된 값</strong>이며, 마스킹 전 원본은
              저장하지 않습니다.
            </span>
          </>
        }
        actions={
          <ButtonLink to="/policies">
            <BackIcon aria-hidden className="size-4" />
            목록으로
          </ButtonLink>
        }
      />

      {/* items-start 가 있어야 오른쪽 미리보기의 sticky 가 늘어난 칸에 갇히지 않는다. */}
      <div className="grid gap-6 xl:grid-cols-2 xl:items-start">
        <PageStack>
          {/* ------------------------------------------------------ 기본 정보 */}
          <Card title="기본 정보" description="무엇을, 어느 연결에서 잡을지 정합니다.">
            {connectionsQuery.isError && (
              <div className="mb-4">
                <ErrorBlock
                  error={connectionsQuery.error}
                  hint="로그 소스 연결 목록을 불러오지 못했습니다."
                />
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="로그 소스 연결" required>
                <div className="flex gap-2">
                  <Select
                    value={form.log_source_connection_id}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        log_source_connection_id:
                          event.target.value === '' ? '' : Number(event.target.value),
                      })
                    }
                  >
                    <option value="">선택하십시오</option>
                    {connections.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name} · {connection.source_type}
                        {connection.active ? '' : ' (비활성)'}
                      </option>
                    ))}
                  </Select>
                  <Button
                    className="shrink-0"
                    disabled={connectionId === null || testConnection.isPending}
                    onClick={() => testConnection.mutate({ connection_id: connectionId })}
                  >
                    {testConnection.isPending ? '테스트 중…' : '연결 테스트'}
                  </Button>
                </div>
              </Field>

              <Field label="정책 이름" required>
                <Input
                  value={form.name}
                  placeholder="payment-api 오류 (staging)"
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>

              <Field label="설명" className="sm:col-span-2">
                <Input
                  value={form.description}
                  placeholder="이 정책이 무엇을 잡으려는지"
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>

              <Field
                label={queryLabel}
                required
                className="sm:col-span-2"
                hint="소스 고유 문법 그대로 저장됩니다."
                info={
                  <>
                    <Code>| json</Code> 은 파싱에 실패한 라인을 <strong>조용히 흘려보냅니다</strong>{' '}
                    — 빠진 줄이 있는지 확인하려면 <Code>| __error__=&quot;&quot;</Code> 처리를
                    검토하십시오.
                    <span className="mt-1.5 block">
                      셀렉터 라벨이 하나만 틀려도 결과가 <strong>0 건</strong>이 되고, 화면에서는
                      "오류가 없는 상태"와 구분되지 않습니다.
                    </span>
                  </>
                }
              >
                <Textarea
                  rows={3}
                  value={form.query}
                  onChange={(event) => setForm({ ...form, query: event.target.value })}
                />
              </Field>

              {labelsQuery.data && labelsQuery.data.supports_label_discovery && (
                <div className="-mt-2 sm:col-span-2">
                  <p className="flex flex-wrap items-center gap-1 text-xs text-muted">
                    사용 가능한 라벨:{' '}
                    {labelsQuery.data.labels.map((label) => (
                      <Code key={label}>{label}</Code>
                    ))}
                  </p>
                </div>
              )}

              <Field
                label="제외 정규식"
                className="sm:col-span-2"
                hint="한 줄에 하나씩. 매칭되는 라인은 그룹화 전에 제외됩니다."
              >
                <Textarea
                  rows={3}
                  placeholder={'healthcheck\nGET /metrics'}
                  value={form.exclusionsText}
                  onChange={(event) => setForm({ ...form, exclusionsText: event.target.value })}
                />
              </Field>

              {/*
                비활성화는 삭제가 아니다 — 되돌릴 수 있어야 한다. 여기가 재활성 경로다.
                생성 API 에는 active 필드가 없어 수정할 때만 보인다.
              */}
              {isEditing && (
                <div className="rounded-lg border border-line bg-surface-2 px-3.5 py-3 sm:col-span-2">
                  <Checkbox
                    label="정책 활성"
                    hint={
                      form.active
                        ? '대시보드 정책 선택과 실행에 나옵니다.'
                        : '비활성 정책은 대시보드 선택 목록에서 빠지지만 목록·실행 이력에는 그대로 남습니다. 체크하면 다시 활성화됩니다.'
                    }
                    checked={form.active}
                    onChange={(event) => setForm({ ...form, active: event.target.checked })}
                  />
                </div>
              )}
            </div>

            {/* 연결 테스트 결과는 누른 자리 옆에 둔다 — 페이지 맨 아래로 밀지 않는다. */}
            {testConnection.data && (
              <div className="mt-4">
                <Notice tone={testConnection.data.ok ? 'success' : 'danger'}>
                  {testConnection.data.message}
                  {testConnection.data.latency_ms != null &&
                    ` (${testConnection.data.latency_ms} ms)`}
                </Notice>
              </div>
            )}
            {testConnection.isError && (
              <div className="mt-4">
                <ErrorBlock error={testConnection.error} />
              </div>
            )}
          </Card>

          {/* ------------------------------------------------------ 조회 한도 */}
          <Card
            title="조회 한도"
            description="한 번의 조회가 얼마나 가져올지 — 기본값이자 실행 상한입니다."
            info={
              <>
                상한은 <strong>서버가 다시 강제합니다</strong> — 대시보드에서 더 넓은 기간을
                골라도 이 값으로 잘리고, 잘렸다는 사실은 조회 회차의 경고로 남습니다.
                <span className="mt-1.5 block">
                  대표 로그 수는 LLM 프롬프트에 들어가는 로그 수를 직접 좌우합니다 — 비용이
                  여기서 갈립니다.
                </span>
              </>
            }
          >
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="기본 기간 (분)" required>
                <Input
                  type="number"
                  min={1}
                  value={form.default_range_minutes}
                  onChange={(event) =>
                    setForm({ ...form, default_range_minutes: Number(event.target.value) })
                  }
                />
              </Field>

              <Field label="최대 조회 수 (라인)" required>
                <Input
                  type="number"
                  min={1}
                  value={form.max_lines}
                  onChange={(event) => setForm({ ...form, max_lines: Number(event.target.value) })}
                />
              </Field>

              <Field label="그룹당 대표 로그 수" required>
                <Input
                  type="number"
                  min={1}
                  value={form.max_samples_per_group}
                  onChange={(event) =>
                    setForm({ ...form, max_samples_per_group: Number(event.target.value) })
                  }
                />
              </Field>
            </div>
          </Card>

          {/* ------------------------------------------------------ 분모 쿼리 */}
          {/*
            분모 쿼리 — **선택 항목**이다. 없으면 대시보드가 유입량·오류 비율을 그리지
            않을 뿐 조회·그룹화·분석은 그대로 동작한다. 그래서 필수 표시를 붙이지 않고,
            "무엇을 세는 쿼리인가"를 정확히 적는다 (오류 쿼리를 그대로 붙여 넣으면
            비율이 항상 100% 가 된다).
          */}
          <Card
            title="분모 쿼리 (유입량 기준)"
            description="선택 항목 — 유입량·오류 비율의 분모를 세는 쿼리입니다."
            info={
              <>
                오류 셀렉터와 <strong>같은 라벨 범위</strong>의 전체 로그를 세야 합니다. 오류
                쿼리를 그대로 붙여 넣으면 비율이 항상 100% 가 됩니다.
                <span className="mt-1.5 block">
                  비워 두면 대시보드의 유입량·비율은 <strong>0 이 아니라</strong> <Code>-</Code> 로
                  표시됩니다 — "유입이 없었다"가 아니라 "계산하지 않았다"는 뜻입니다.
                </span>
              </>
            }
          >
            <Field
              label={queryLanguage ? `분모 쿼리 (${queryLanguage})` : '분모 쿼리'}
              hint="비워 두면 대시보드가 유입량과 비율을 계산하지 않습니다."
            >
              <Textarea
                rows={2}
                placeholder={'{service="payment-api", environment="staging"}'}
                value={form.baseline_query}
                onChange={(event) => setForm({ ...form, baseline_query: event.target.value })}
              />
            </Field>
          </Card>

          {/* -------------------------------------------------- 분석 · 스케줄 */}
          {/*
            비용이 나가는 설정만 모은 덩어리다. 자동 분석은 이 앱에서 **사람이 누르지 않아도
            LLM 을 호출하는 유일한 경로**라, 켜져 있는 동안에는 경고를 툴팁이 아니라 본문에
            남긴다 (계약: 위험·비용 경고는 접지 않는다).
          */}
          <Card
            title="분석 · 스케줄"
            description="비용이 나가는 설정은 여기 모여 있습니다."
            info={
              <>
                조회 자체는 LLM 비용이 들지 않습니다 — 돈이 나가는 것은{' '}
                <strong>LLM 분석</strong>뿐입니다.
                <span className="mt-1.5 block">
                  일일 한도는 <strong>로컬 자정</strong>을 경계로 리셋되고, 한도를 넘긴 요청은
                  429 로 거절됩니다.
                </span>
              </>
            }
          >
            <div className="space-y-4">
              <Checkbox
                label="AI 분석 허용"
                hint="끄면 이 정책의 오류 그룹에서 LLM 분석을 실행할 수 없습니다. 비용이 나가는 경로입니다."
                checked={form.allow_ai_analysis}
                onChange={(event) => setForm({ ...form, allow_ai_analysis: event.target.checked })}
              />

              <Field
                label="정책별 일일 분석 한도"
                className="max-w-56"
                hint="비우면 전역 한도만 적용됩니다."
              >
                <Input
                  type="number"
                  min={0}
                  placeholder="전역 한도 사용"
                  value={form.daily_analysis_limit}
                  onChange={(event) =>
                    setForm({ ...form, daily_analysis_limit: event.target.value })
                  }
                />
              </Field>

              <div className="rounded-lg border border-line bg-surface-2 px-3.5 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <Checkbox
                    label="스케줄 조회"
                    hint="켜면 사람이 누르지 않아도 주기마다 이 정책으로 로그 소스를 조회합니다. 조회 자체는 LLM 비용이 들지 않습니다."
                    checked={form.schedule_enabled}
                    onChange={(event) =>
                      setForm({ ...form, schedule_enabled: event.target.checked })
                    }
                  />
                  {scheduleSummary && (
                    <Badge tone="info" className="mt-0.5">
                      {scheduleSummary}
                    </Badge>
                  )}
                </div>

                {form.schedule_enabled && (
                  <div className="mt-3 space-y-3 border-t border-line pt-3">
                    <Field
                      label="실행 주기 (분)"
                      required
                      className="max-w-48"
                      hint={
                        form.schedule_interval_minutes.trim() === ''
                          ? '비워 두면 저장할 수 없습니다.'
                          : (scheduleSummary ?? undefined)
                      }
                    >
                      <Input
                        type="number"
                        min={1}
                        value={form.schedule_interval_minutes}
                        onChange={(event) =>
                          setForm({ ...form, schedule_interval_minutes: event.target.value })
                        }
                      />
                    </Field>

                    <Checkbox
                      label="신규 그룹 자동 분석"
                      hint={
                        <>
                          <strong>{AUTO_ANALYZE_COST_NOTE}</strong> 이미 분석 이력이 있는
                          fingerprint 는 다시 돌지 않으며, 전역·정책별 일일 분석 한도를 그대로
                          받습니다.
                        </>
                      }
                      checked={form.auto_analyze_new}
                      disabled={!form.allow_ai_analysis}
                      onChange={(event) =>
                        setForm({ ...form, auto_analyze_new: event.target.checked })
                      }
                    />

                    {form.auto_analyze_new && form.allow_ai_analysis && (
                      <Notice tone="warning" title="비용이 나가는 자동 경로입니다">
                        {AUTO_ANALYZE_COST_NOTE} 한도는{' '}
                        {form.daily_analysis_limit.trim() === ''
                          ? '전역 일일 한도'
                          : `이 정책의 일 ${form.daily_analysis_limit}회`}
                        가 적용됩니다. 실행된 분석은 <strong>관리 → 분석 이력</strong> 화면에서{' '}
                        <strong>자동</strong> 배지로 구분됩니다.
                      </Notice>
                    )}
                    {!form.allow_ai_analysis && (
                      <p className="text-xs text-muted">
                        이 정책은 <strong>AI 분석 허용</strong>이 꺼져 있어 자동 분석을 켤 수
                        없습니다.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* ------------------------------------------------------ 저장 동작 */}
          <div className="space-y-4">
            {formError && <Notice tone="danger">{formError}</Notice>}
            {(createPolicy.isError || updatePolicy.isError) && (
              <ErrorBlock error={createPolicy.error ?? updatePolicy.error} />
            )}

            <div className="flex flex-wrap gap-2">
              <Button onClick={handlePreview} disabled={preview.isPending}>
                <RunIcon aria-hidden className="size-4" />
                {preview.isPending ? '미리보기 실행 중…' : '저장 전 미리보기'}
              </Button>
              <Button variant="primary" onClick={handleSubmit} disabled={saving}>
                <SaveIcon aria-hidden className="size-4" />
                {saving ? '저장 중…' : isEditing ? '정책 수정' : '정책 저장'}
              </Button>
              <Button variant="ghost" onClick={() => navigate('/policies')}>
                취소
              </Button>
            </div>
          </div>
        </PageStack>

        {/* 폼이 길다 — 미리보기가 스크롤을 따라와야 "고친 값 → 결과"를 나란히 볼 수 있다. */}
        <div className="xl:sticky xl:top-6">
          <PreviewPanel
            data={preview.data}
            error={preview.isError ? preview.error : null}
            pending={preview.isPending}
          />
        </div>
      </div>
    </div>
  );
}

function PreviewPanel({
  data,
  error,
  pending,
}: {
  data: PolicyPreviewResponse | undefined;
  error: unknown;
  pending: boolean;
}) {
  if (pending) {
    return (
      <Card title="미리보기">
        {/* 결과가 몇 줄일지 모르는 자리다 — 스켈레톤은 "몇 건 있다"는 거짓 신호가 된다. */}
        <LoadingBlock label="쿼리를 실행하는 중…" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="미리보기">
        <ErrorBlock error={error} hint="쿼리 문법과 셀렉터 라벨을 확인하십시오." />
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="미리보기">
        <EmptyBlock icon={RunIcon}>
          저장하기 전에 <strong>미리보기</strong>로 쿼리가 실제로 무엇을 가져오는지 확인하십시오.
        </EmptyBlock>
      </Card>
    );
  }

  return (
    <Card
      title="미리보기 결과"
      description="표시되는 로그는 마스킹을 거친 값입니다."
      info={
        <>
          마스킹 전 원본은 <strong>저장하지 않습니다</strong> — 미리보기도 예외가 아닙니다.
          <span className="mt-1.5 block">
            여기 수는 <strong>최대 50 라인</strong>까지만 가져온 표본이라, 실제 조회량이 아닙니다.
          </span>
        </>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <Badge tone="info">조회 {formatNumber(data.fetched)} 라인</Badge>
        <Badge tone={data.dropped > 0 ? 'warning' : 'neutral'}>
          제외 {formatNumber(data.dropped)} 라인
        </Badge>
        {data.truncated && <Badge tone="warning">한도에서 잘림 — 집계에 쓰지 마십시오</Badge>}
      </div>

      {data.warnings.length > 0 && (
        <div className="mb-4 space-y-2">
          {data.warnings.map((warning, index) => (
            <Notice
              key={`${warning.code}-${index}`}
              tone="warning"
              title={warningCodeLabel(warning.code)}
            >
              {warning.message}
              {warning.count != null && ` (${formatNumber(warning.count)}건)`}
            </Notice>
          ))}
        </div>
      )}

      {data.sample_lines.length === 0 ? (
        <EmptyBlock>
          결과가 비어 있습니다. 셀렉터 라벨이 실제로 존재하는지, <Code>| json</Code> 파싱이
          실패하고 있지는 않은지 확인하십시오.
        </EmptyBlock>
      ) : (
        <TableWrap minWidth="24rem">
          <thead>
            <tr>
              <Th className="w-10">#</Th>
              <Th>마스킹된 로그 라인</Th>
            </tr>
          </thead>
          <tbody>
            {data.sample_lines.map((line, index) => (
              <tr key={index}>
                <Td className="text-xs text-faint tabular-nums">{index + 1}</Td>
                <Td>
                  <pre className="aila-scroll max-w-full overflow-x-auto font-mono text-xs whitespace-pre text-ink-soft">
                    {line}
                  </pre>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </Card>
  );
}
