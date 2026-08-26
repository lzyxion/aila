import { useEffect, useState } from 'react';

import {
  useCreatePolicy,
  useDeactivatePolicy,
  useLokiConnections,
  useLokiLabels,
  usePolicies,
  usePreviewPolicy,
  useTestLokiConnection,
  useUpdatePolicy,
} from '../api/queries';
import type { PolicyPreviewResponse, PolicyRead } from '../api/types';
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
import { formatDateTime, formatNumber, truncate, warningCodeLabel } from '../lib/format';

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
};

function toForm(policy: PolicyRead): FormState {
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
  };
}

function parseExclusions(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export function PoliciesPage() {
  const policiesQuery = usePolicies();
  const connectionsQuery = useLokiConnections();
  const createPolicy = useCreatePolicy();
  const updatePolicy = useUpdatePolicy();
  const deactivatePolicy = useDeactivatePolicy();
  const preview = usePreviewPolicy();
  const testConnection = useTestLokiConnection();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const connections = connectionsQuery.data ?? [];
  const activeConnections = connections.filter((connection) => connection.active);

  // 연결 목록이 오면 첫 활성 연결을 기본 선택으로 채운다.
  useEffect(() => {
    if (form.loki_connection_id === '' && activeConnections.length > 0) {
      setForm((prev) => ({ ...prev, loki_connection_id: activeConnections[0].id }));
    }
  }, [activeConnections, form.loki_connection_id]);

  const connectionId =
    form.loki_connection_id === '' ? null : Number(form.loki_connection_id);
  const labelsQuery = useLokiLabels(connectionId);

  const isEditing = editingId !== null;
  const saving = createPolicy.isPending || updatePolicy.isPending;

  function resetForm() {
    setEditingId(null);
    setForm({
      ...EMPTY_FORM,
      loki_connection_id: activeConnections[0]?.id ?? '',
    });
    setFormError(null);
    preview.reset();
  }

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
    };

    if (isEditing) {
      updatePolicy.mutate({ id: editingId, payload }, { onSuccess: resetForm });
    } else {
      createPolicy.mutate(payload, { onSuccess: resetForm });
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

  return (
    <div>
      <PageHeader
        title="분석 정책"
        description={
          <>
            정책은 LogQL 한 줄이 아니라 <strong>실행 한도를 포함한 묶음</strong>입니다. 저장 전
            미리보기로 쿼리를 검증하십시오 — 잘못 쓴 LogQL 이 정책으로 굳으면 이후 모든 조회가
            조용히 빈 결과를 냅니다.
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <Card
            title={isEditing ? `정책 수정 · #${editingId}` : '새 정책'}
            description="기간·라인 수 상한은 서버가 다시 강제합니다. 여기 값은 정책의 기본값입니다."
            actions={
              isEditing && (
                <Button size="sm" onClick={resetForm}>
                  새로 만들기
                </Button>
              )
            }
          >
            {connectionsQuery.isError && (
              <div className="mb-4">
                <ErrorBlock error={connectionsQuery.error} hint="Loki 연결 목록을 불러오지 못했습니다." />
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

              <Field
                label="정책별 일일 분석 한도"
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
                  onChange={(event) =>
                    setForm({ ...form, allow_ai_analysis: event.target.checked })
                  }
                />
              </div>
            </div>

            {testConnection.data && (
              <div className="mt-4">
                <Notice tone={testConnection.data.ok ? 'success' : 'danger'}>
                  {testConnection.data.message}
                  {testConnection.data.latency_ms != null && ` (${testConnection.data.latency_ms} ms)`}
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
              {isEditing && (
                <Button variant="ghost" onClick={resetForm}>
                  취소
                </Button>
              )}
            </div>
          </Card>

          <PreviewPanel
            data={preview.data}
            error={preview.isError ? preview.error : null}
            pending={preview.isPending}
          />
        </div>

        <div className="xl:col-span-2">
          <Card title="정책 목록" description="삭제는 실제 삭제가 아니라 비활성화입니다.">
            {policiesQuery.isPending && <LoadingBlock />}
            {policiesQuery.isError && <ErrorBlock error={policiesQuery.error} />}
            {policiesQuery.data && policiesQuery.data.length === 0 && (
              <EmptyBlock>저장된 정책이 없습니다.</EmptyBlock>
            )}
            {policiesQuery.data && policiesQuery.data.length > 0 && (
              <ul className="divide-y divide-slate-100">
                {policiesQuery.data.map((policy) => (
                  <li key={policy.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
                          {policy.name}
                          {!policy.active && <Badge tone="neutral">비활성</Badge>}
                          {!policy.allow_ai_analysis && <Badge tone="warning">AI 분석 불가</Badge>}
                        </p>
                        <p className="mt-1 truncate font-mono text-xs text-slate-500" title={policy.logql}>
                          {truncate(policy.logql, 70)}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          {policy.default_range_minutes}분 · 최대 {formatNumber(policy.max_lines)}{' '}
                          라인 · 대표 로그 {policy.max_samples_per_group}개 ·{' '}
                          {policy.daily_analysis_limit === null
                            ? '전역 한도'
                            : `일 ${policy.daily_analysis_limit}회`}
                        </p>
                        <p className="mt-1 text-xs text-slate-400">
                          수정 {formatDateTime(policy.updated_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-col gap-1">
                        <Button
                          size="sm"
                          onClick={() => {
                            setEditingId(policy.id);
                            setForm(toForm(policy));
                            setFormError(null);
                            preview.reset();
                            window.scrollTo({ top: 0, behavior: 'smooth' });
                          }}
                        >
                          수정
                        </Button>
                        {policy.active && (
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={deactivatePolicy.isPending}
                            onClick={() => deactivatePolicy.mutate(policy.id)}
                          >
                            비활성화
                          </Button>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
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
            <Notice key={`${warning.code}-${index}`} tone="warning" title={warningCodeLabel(warning.code)}>
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
        <TableWrap>
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
