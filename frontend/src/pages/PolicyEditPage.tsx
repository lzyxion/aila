/**
 * 정책 추가·수정 전용 페이지 (`/policies/new`, `/policies/:policyId/edit`).
 *
 * 목록 화면에서 폼을 분리한 이유는 하나다 — 정책은 LogQL 한 줄이 아니라 **실행 한도를
 * 포함한 묶음**이라 입력 항목이 열 개가 넘는다. 목록 옆에 붙여 두면 "지금 무엇을 고치는
 * 중인가"가 스크롤 밖으로 밀리고, 미리보기 결과가 들어갈 자리도 없다.
 *
 * 저장하면 목록으로 돌아간다. 편집 중 이탈은 막지 않는다(저장 전 값은 서버에 없다).
 *
 * 미리보기는 여기 있다 — **저장 전에** 쿼리가 실제로 무엇을 가져오는지 보는 장치이므로
 * 조회 전용 화면에 둘 이유가 없다. 잘못 쓴 LogQL 이 정책으로 굳으면 이후 모든 조회가
 * 조용히 빈 결과를 낸다.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { Link } from 'react-router';

import {
  useCreatePolicy,
  useLokiConnections,
  useLokiLabels,
  usePolicies,
  usePreviewPolicy,
  useTestLokiConnection,
  useUpdatePolicy,
} from '../api/queries';
import { policySchedule, type PolicyPreviewResponse, type PolicyRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  LoadingBlock,
  Notice,
  PageHeader,
  Select,
  TableWrap,
  Td,
  Textarea,
  Th,
} from '../components/ui';
import { formatIntervalMinutes, formatNumber, warningCodeLabel } from '../lib/format';

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
  loki_connection_id: number | '';
  name: string;
  description: string;
  logql: string;
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
  loki_connection_id: '',
  name: '',
  description: '',
  logql: '{service="", environment="staging"} | json | level="ERROR"',
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
    loki_connection_id: policy.loki_connection_id,
    name: policy.name,
    description: policy.description ?? '',
    logql: policy.logql,
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
  const connectionsQuery = useLokiConnections();
  const createPolicy = useCreatePolicy();
  const updatePolicy = useUpdatePolicy();
  const preview = usePreviewPolicy();
  const testConnection = useTestLokiConnection();

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
    if (form.loki_connection_id === '' && activeConnections.length > 0) {
      setForm((prev) => ({ ...prev, loki_connection_id: activeConnections[0].id }));
    }
  }, [activeConnections, form.loki_connection_id, isEditing]);

  const connectionId = form.loki_connection_id === '' ? null : Number(form.loki_connection_id);
  const labelsQuery = useLokiLabels(connectionId);
  const saving = createPolicy.isPending || updatePolicy.isPending;

  function validate(): string | null {
    if (form.loki_connection_id === '') return 'Loki 연결을 선택하십시오.';
    if (!form.name.trim()) return '정책 이름을 입력하십시오.';
    if (!form.logql.trim()) return 'LogQL 을 입력하십시오.';
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
      loki_connection_id: Number(form.loki_connection_id),
      name: form.name.trim(),
      description: form.description.trim() || null,
      logql: form.logql.trim(),
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
      loki_connection_id: Number(form.loki_connection_id),
      logql: form.logql.trim(),
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
            {write.reason} 정책의 LogQL·한도·스케줄은{' '}
            <Link to="/policies" className="font-medium text-sky-800 underline">
              분석 정책
            </Link>{' '}
            목록에서 볼 수 있습니다.
          </Notice>
        </Card>
      </div>
    );
  }

  if (isEditing && policiesQuery.isPending) {
    return <LoadingBlock label="정책을 불러오는 중…" />;
  }
  if (isEditing && policiesQuery.data && !target) {
    return (
      <div>
        <PageHeader title="정책 수정" />
        <ErrorBlock error={`정책 #${editingId} 을(를) 찾을 수 없습니다.`} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={isEditing ? `정책 수정 · ${target?.name ?? `#${editingId}`}` : '새 정책'}
        description={
          <>
            기간·라인 수 상한은 서버가 다시 강제합니다 — 여기 값은 정책의 기본값이자{' '}
            <strong>실행 상한</strong>입니다. 저장 전 <strong>미리보기</strong>로 쿼리가 실제로
            무엇을 가져오는지 확인하십시오.
          </>
        }
        actions={
          <Link
            to="/policies"
            className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
          >
            목록으로
          </Link>
        }
      />

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="정책 설정">
          {connectionsQuery.isError && (
            <div className="mb-4">
              <ErrorBlock
                error={connectionsQuery.error}
                hint="Loki 연결 목록을 불러오지 못했습니다."
              />
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Loki 연결" required>
              <div className="flex gap-2">
                <Select
                  value={form.loki_connection_id}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      loki_connection_id:
                        event.target.value === '' ? '' : Number(event.target.value),
                    })
                  }
                >
                  <option value="">선택하십시오</option>
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name}
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
              label="LogQL"
              required
              className="sm:col-span-2"
              hint={
                <>
                  소스 고유 문법 그대로 저장됩니다. <code>| json</code> 은 파싱 실패 라인을 조용히
                  흘려보내므로 <code>| __error__=&quot;&quot;</code> 처리를 검토하십시오.
                </>
              }
            >
              <Textarea
                rows={3}
                value={form.logql}
                onChange={(event) => setForm({ ...form, logql: event.target.value })}
              />
            </Field>

            {labelsQuery.data && labelsQuery.data.supports_label_discovery && (
              <div className="sm:col-span-2 -mt-2">
                <p className="text-xs text-slate-500">
                  사용 가능한 라벨:{' '}
                  {labelsQuery.data.labels.map((label) => (
                    <code key={label} className="mr-1 rounded bg-slate-100 px-1">
                      {label}
                    </code>
                  ))}
                </p>
              </div>
            )}

            <Field label="기본 기간 (분)" required hint="기본값이자 실행 상한입니다.">
              <Input
                type="number"
                min={1}
                value={form.default_range_minutes}
                onChange={(event) =>
                  setForm({ ...form, default_range_minutes: Number(event.target.value) })
                }
              />
            </Field>

            <Field label="최대 조회 수 (라인)" required hint="서버 상한을 넘으면 서버가 자릅니다.">
              <Input
                type="number"
                min={1}
                value={form.max_lines}
                onChange={(event) => setForm({ ...form, max_lines: Number(event.target.value) })}
              />
            </Field>

            <Field
              label="그룹당 대표 로그 수"
              required
              hint="LLM 프롬프트에 들어가는 로그 수를 직접 좌우합니다."
            >
              <Input
                type="number"
                min={1}
                value={form.max_samples_per_group}
                onChange={(event) =>
                  setForm({ ...form, max_samples_per_group: Number(event.target.value) })
                }
              />
            </Field>

            <Field label="정책별 일일 분석 한도" hint="비우면 전역 한도만 적용됩니다.">
              <Input
                type="number"
                min={0}
                placeholder="전역 한도 사용"
                value={form.daily_analysis_limit}
                onChange={(event) => setForm({ ...form, daily_analysis_limit: event.target.value })}
              />
            </Field>

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

            <div className="sm:col-span-2">
              <Checkbox
                label="AI 분석 허용"
                hint="끄면 이 정책의 오류 그룹에서 LLM 분석을 실행할 수 없습니다. 비용이 나가는 경로입니다."
                checked={form.allow_ai_analysis}
                onChange={(event) => setForm({ ...form, allow_ai_analysis: event.target.checked })}
              />
            </div>

            {/* ------------------------------------------------------ 스케줄 */}
            <div className="sm:col-span-2 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3">
              <Checkbox
                label="스케줄 조회"
                hint="켜면 사람이 누르지 않아도 주기마다 이 정책으로 Loki 를 조회합니다. 조회 자체는 LLM 비용이 들지 않습니다."
                checked={form.schedule_enabled}
                onChange={(event) => setForm({ ...form, schedule_enabled: event.target.checked })}
              />

              {form.schedule_enabled && (
                <div className="mt-3 space-y-3 border-t border-slate-200 pt-3">
                  <Field
                    label="실행 주기 (분)"
                    required
                    className="max-w-48"
                    hint={
                      form.schedule_interval_minutes.trim() === ''
                        ? '비워 두면 저장할 수 없습니다.'
                        : `${formatIntervalMinutes(Number(form.schedule_interval_minutes))}마다 실행`
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
                        <strong>{AUTO_ANALYZE_COST_NOTE}</strong> 이미 분석 이력이 있는 fingerprint
                        는 다시 돌지 않으며, 전역·정책별 일일 분석 한도를 그대로 받습니다.
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
                    <p className="text-xs text-slate-500">
                      이 정책은 <strong>AI 분석 허용</strong>이 꺼져 있어 자동 분석을 켤 수 없습니다.
                    </p>
                  )}
                </div>
              )}
            </div>

            {/*
              비활성화는 삭제가 아니다 — 되돌릴 수 있어야 한다. 여기가 재활성 경로다.
              생성 API 에는 active 필드가 없어 수정할 때만 보인다.
            */}
            {isEditing && (
              <div className="sm:col-span-2 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3">
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

          {formError && (
            <div className="mt-4">
              <Notice tone="danger">{formError}</Notice>
            </div>
          )}
          {(createPolicy.isError || updatePolicy.isError) && (
            <div className="mt-4">
              <ErrorBlock error={createPolicy.error ?? updatePolicy.error} />
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <Button onClick={handlePreview} disabled={preview.isPending}>
              {preview.isPending ? '미리보기 실행 중…' : '저장 전 미리보기'}
            </Button>
            <Button variant="primary" onClick={handleSubmit} disabled={saving}>
              {saving ? '저장 중…' : isEditing ? '정책 수정' : '정책 저장'}
            </Button>
            <Button variant="ghost" onClick={() => navigate('/policies')}>
              취소
            </Button>
          </div>
        </Card>

        <PreviewPanel
          data={preview.data}
          error={preview.isError ? preview.error : null}
          pending={preview.isPending}
        />
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
        <LoadingBlock label="쿼리를 실행하는 중…" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="미리보기">
        <ErrorBlock error={error} hint="LogQL 문법과 셀렉터 라벨을 확인하십시오." />
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="미리보기">
        <EmptyBlock>
          저장하기 전에 <strong>미리보기</strong>로 쿼리가 실제로 무엇을 가져오는지 확인하십시오.
        </EmptyBlock>
      </Card>
    );
  }

  return (
    <Card
      title="미리보기 결과"
      description="표시되는 로그는 마스킹을 거친 값입니다. 마스킹 전 원본은 저장하지 않습니다."
    >
      <div className="mb-4 flex flex-wrap gap-2 text-xs">
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
          결과가 비어 있습니다. 셀렉터 라벨이 실제로 존재하는지, <code>| json</code> 파싱이
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
                <Td className="text-xs text-slate-400 tabular-nums">{index + 1}</Td>
                <Td>
                  <pre className="aila-scroll max-w-full overflow-x-auto font-mono text-xs whitespace-pre text-slate-700">
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
