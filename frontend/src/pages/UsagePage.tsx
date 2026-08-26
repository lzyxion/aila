import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { useAnalysisJobs, useModelPricing, useUpsertModelPricing, useUsage } from '../api/queries';
import type { ModelPricingEntry, ModelPricingTable, UsageParams } from '../api/types';
import { asModelPricingTable } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { TokenByModelChart } from '../components/chartsLazy';
import {
  AnalysisStatusBadge,
  SeverityBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  Field,
  InfoIcon,
  Input,
  LoadingBlock,
  Notice,
  PageHeader,
  Select,
  Stat,
  TableWrap,
  Td,
  Th,
} from '../components/ui';
import {
  formatDateTime,
  formatDuration,
  formatEstimatedCost,
  formatNumber,
  formatTokens,
  providerLabel,
} from '../lib/format';

/**
 * 단가는 프로바이더 API 가 주지 않는다 — 모델 페이지의 공시 단가를 사람이 옮겨 적는 것이
 * 유일한 방법이고, 그래서 이 화면에 인라인 입력이 붙어 있다.
 */
const PRICING_TOOLTIP =
  '프로바이더 API 는 단가를 제공하지 않습니다. 공시 단가를 1K 토큰 기준으로 직접 입력하는 것이 정답입니다. ' +
  '표에 없는 모델의 추정 비용은 0 이 아니라 "-" 로 남습니다.';

const RANGES = [
  { days: 1, label: '최근 24시간' },
  { days: 7, label: '최근 7일' },
  { days: 30, label: '최근 30일' },
];

export function UsagePage() {
  const write = useWriteAccess();
  const [rangeIndex, setRangeIndex] = useState(1);

  const params = useMemo<UsageParams>(() => {
    const end = new Date();
    const start = new Date(end.getTime() - RANGES[rangeIndex].days * 24 * 60 * 60_000);
    return { range_start: start.toISOString(), range_end: end.toISOString() };
  }, [rangeIndex]);

  const usageQuery = useUsage(params);
  const jobsQuery = useAnalysisJobs();
  const pricingQuery = useModelPricing();

  /** 인라인 단가 입력을 열어 둔 모델. 한 번에 하나만 연다. */
  const [pricingModel, setPricingModel] = useState<string | null>(null);
  /** 방금 단가를 등록한 모델 — "이후 분석부터" 안내를 어디에 붙일지 정한다. */
  const [pricedModel, setPricedModel] = useState<string | null>(null);

  const pricingTable: ModelPricingTable = asModelPricingTable(
    pricingQuery.data?.value ?? pricingQuery.data?.effective_value,
  );

  const totalFailures = (usageQuery.data?.items ?? []).reduce(
    (acc, item) => acc + item.failure_count,
    0,
  );

  return (
    <div>
      <PageHeader
        title="분석 이력·사용량"
        description={
          <>
            비용은 계산 시점 단가표 기준 <strong>추정</strong>값입니다 — 캐시 적중·배치 할인에 따라
            실제 청구액과 벌어지므로 정산 근거로 쓰지 마십시오. 비용 차단은 이 화면이 아니라 일일
            분석 한도가 담당합니다.
          </>
        }
        actions={
          <Field label="기간" className="w-44">
            <Select
              value={rangeIndex}
              onChange={(event) => setRangeIndex(Number(event.target.value))}
            >
              {RANGES.map((range, index) => (
                <option key={range.label} value={index}>
                  {range.label}
                </option>
              ))}
            </Select>
          </Field>
        }
      />

      {usageQuery.isPending && <LoadingBlock />}
      {usageQuery.isError && <ErrorBlock error={usageQuery.error} />}

      {usageQuery.data && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="총 추정 비용"
              value={formatEstimatedCost(usageQuery.data.total_estimated_cost)}
              sub={
                usageQuery.data.total_estimated_cost === null
                  ? '단가표에 모델이 없어 계산하지 않음'
                  : '추정 — 정산 근거 아님'
              }
              tone="accent"
            />
            <Stat
              label="분석 실행"
              value={formatNumber(usageQuery.data.total_jobs)}
              sub={`실패 ${formatNumber(totalFailures)}건`}
            />
            <Stat
              label="입력 토큰"
              value={formatNumber(usageQuery.data.total_input_tokens)}
              sub="프롬프트"
            />
            <Stat
              label="출력 토큰"
              value={formatNumber(usageQuery.data.total_output_tokens)}
              sub="구조화 응답"
            />
          </div>

          <Card
            title="모델별 토큰"
            description={`${formatDateTime(usageQuery.data.range_start)} ~ ${formatDateTime(
              usageQuery.data.range_end,
            )}`}
          >
            <TokenByModelChart items={usageQuery.data.items} />
          </Card>

          <Card
            title="모델별 집계"
            description="평균 응답 시간은 성공·실패를 모두 포함합니다. 단가가 등록되지 않은 모델은 비용이 0 이 아니라 계산되지 않은 상태입니다."
          >
            {pricingQuery.isError && (
              <div className="mb-4">
                <ErrorBlock
                  error={pricingQuery.error}
                  hint="단가표를 읽지 못했습니다 — 등록 UI 는 열리지만 기존 값을 병합하지 못할 수 있습니다."
                />
              </div>
            )}
            {usageQuery.data.items.length === 0 ? (
              <EmptyBlock>이 기간에 기록된 사용량이 없습니다.</EmptyBlock>
            ) : (
              <TableWrap>
                <thead>
                  <tr>
                    <Th>프로바이더 · 모델</Th>
                    <Th align="right">실행</Th>
                    <Th align="right">실패</Th>
                    <Th align="right">입력 토큰</Th>
                    <Th align="right">출력 토큰</Th>
                    <Th align="right">
                      <span className="inline-flex items-center gap-1">
                        추정 비용
                        <span title={PRICING_TOOLTIP} className="cursor-help text-slate-400">
                          <InfoIcon />
                        </span>
                      </span>
                    </Th>
                    <Th align="right">평균 응답</Th>
                  </tr>
                </thead>
                <tbody>
                  {usageQuery.data.items.map((item) => {
                    const registered = pricingTable[item.model];
                    const open = pricingModel === item.model;
                    return [
                      <tr key={`${item.provider}-${item.model}`} className="hover:bg-slate-50">
                        <Td>
                          <p className="font-medium text-slate-900">
                            {providerLabel(item.provider)}
                          </p>
                          <p className="mt-0.5 font-mono text-xs text-slate-500">{item.model}</p>
                        </Td>
                        <Td align="right">{formatNumber(item.job_count)}</Td>
                        <Td align="right">
                          {item.failure_count > 0 ? (
                            <Badge tone="danger">{formatNumber(item.failure_count)}</Badge>
                          ) : (
                            <span className="text-slate-400">0</span>
                          )}
                        </Td>
                        <Td align="right">{formatTokens(item.input_tokens)}</Td>
                        <Td align="right">{formatTokens(item.output_tokens)}</Td>
                        <Td align="right">
                          {formatEstimatedCost(item.estimated_cost)}
                          {/* 단가표에 없어 계산하지 못한 값은 0 이 아니라 "-" 다. */}
                          <span className="ml-1 text-xs text-slate-400">
                            {item.estimated_cost === null ? '(단가 없음)' : '(추정)'}
                          </span>
                          {item.estimated_cost === null && (
                            <div className="mt-1 flex flex-col items-end gap-1">
                              {/* 단가 등록은 PUT 이다 — viewer 는 누를 수 없다. */}
                              <Button
                                size="sm"
                                variant={open ? 'ghost' : 'secondary'}
                                disabled={!write.allowed}
                                title={write.reason ?? PRICING_TOOLTIP}
                                onClick={() => setPricingModel(open ? null : item.model)}
                              >
                                {open ? '닫기' : registered ? '단가 수정' : '단가 등록'}
                              </Button>
                              {registered && (
                                <span className="text-xs text-emerald-700">
                                  단가 등록됨 · 이후 분석부터 적용
                                </span>
                              )}
                            </div>
                          )}
                        </Td>
                        <Td align="right">{formatDuration(item.avg_latency_ms)}</Td>
                      </tr>,
                      open && write.allowed ? (
                        <tr key={`${item.provider}-${item.model}-pricing`}>
                          <Td colSpan={7} className="bg-slate-50">
                            <PricingForm
                              model={item.model}
                              current={registered}
                              onDone={() => {
                                setPricedModel(item.model);
                                setPricingModel(null);
                              }}
                            />
                          </Td>
                        </tr>
                      ) : null,
                    ];
                  })}
                </tbody>
              </TableWrap>
            )}

            {pricedModel && (
              <div className="mt-4">
                <Notice tone="success" title={`${pricedModel} 단가를 등록했습니다`}>
                  <strong>이후 분석부터 계산됩니다 — 기존 기록은 소급 계산하지 않습니다.</strong>{' '}
                  이미 기록된 실행의 추정 비용은 그때의 단가표로 고정되어 있으므로 계속 `-` 로
                  남습니다.
                </Notice>
              </div>
            )}
          </Card>
        </div>
      )}

      <div className="mt-6">
        <Card
          title="분석 실행 목록"
          description="같은 오류를 다시 분석하기 전에 fingerprint 기준 이력을 먼저 확인하십시오."
        >
          {jobsQuery.isPending && <LoadingBlock />}
          {jobsQuery.isError && <ErrorBlock error={jobsQuery.error} />}
          {jobsQuery.data && jobsQuery.data.length === 0 && (
            <EmptyBlock>실행된 분석이 없습니다.</EmptyBlock>
          )}
          {jobsQuery.data && jobsQuery.data.length > 0 && (
            <TableWrap>
              <thead>
                <tr>
                  <Th>요청 시각</Th>
                  <Th>실행 주체</Th>
                  <Th>오류 그룹 · fingerprint</Th>
                  <Th>서비스 · 오류</Th>
                  <Th>모델</Th>
                  <Th>상태</Th>
                  <Th>요약</Th>
                </tr>
              </thead>
              <tbody>
                {jobsQuery.data.map((job) => (
                  <tr key={job.id} className="hover:bg-slate-50">
                    <Td>
                      <p>{formatDateTime(job.requested_at)}</p>
                      <p className="mt-0.5 text-xs text-slate-400">작업 #{job.id}</p>
                    </Td>
                    <Td>
                      <TriggeredByBadge value={job.triggered_by} />
                      {!job.triggered_by && <span className="text-xs text-slate-400">-</span>}
                    </Td>
                    <Td>
                      <Link
                        to={`/error-groups/${job.error_group_id}`}
                        className="font-medium text-sky-800 hover:underline"
                      >
                        그룹 #{job.error_group_id}
                      </Link>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{job.fingerprint}</p>
                    </Td>
                    <Td>
                      <p>{job.service ?? '-'}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">
                        {job.error_type ?? '-'}
                        {job.environment ? ` · ${job.environment}` : ''}
                      </p>
                    </Td>
                    <Td>
                      <p>{providerLabel(job.provider)}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{job.model}</p>
                    </Td>
                    <Td>
                      <div className="flex flex-col items-start gap-1">
                        <AnalysisStatusBadge status={job.status} />
                        {job.severity && <SeverityBadge severity={job.severity} />}
                        {job.status === 'failed' && job.error_message && (
                          <span className="text-xs text-rose-700">{job.error_message}</span>
                        )}
                      </div>
                    </Td>
                    <Td>
                      {/* 목록 응답에는 토큰·비용이 없다 — 위쪽 모델별 집계에서 본다. */}
                      <p className="max-w-md text-xs text-slate-600">
                        {job.summary ?? job.normalized_message ?? '-'}
                      </p>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          )}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------- 모델 단가 인라인 등록

/**
 * 모델 하나의 단가 입력.
 *
 * 단가는 **1K 토큰당** 값이다 (`app/analysis/pricing.py` 와 같은 단위). 저장은
 * `PUT /api/settings/model_pricing` 이고 키를 통째로 교체하므로, 훅이 기존 표를 읽어
 * 병합한 뒤 보낸다 — 여기서 한 모델을 등록하다 다른 모델 단가를 지우면 그쪽 비용이
 * 조용히 `-` 로 돌아간다.
 */
function PricingForm({
  model,
  current,
  onDone,
}: {
  model: string;
  current: ModelPricingEntry | undefined;
  onDone: () => void;
}) {
  const upsert = useUpsertModelPricing();
  const [input, setInput] = useState(
    current?.input_per_1k != null ? String(current.input_per_1k) : '',
  );
  const [output, setOutput] = useState(
    current?.output_per_1k != null ? String(current.output_per_1k) : '',
  );
  const [currency, setCurrency] = useState(current?.currency ?? 'USD');
  const [error, setError] = useState<string | null>(null);

  function submit() {
    const inputRate = input.trim() === '' ? null : Number(input);
    const outputRate = output.trim() === '' ? null : Number(output);
    if (inputRate === null && outputRate === null) {
      setError('입력·출력 단가 중 최소 하나는 있어야 합니다.');
      return;
    }
    for (const rate of [inputRate, outputRate]) {
      if (rate !== null && (Number.isNaN(rate) || rate < 0)) {
        setError('단가는 0 이상의 숫자여야 합니다.');
        return;
      }
    }
    setError(null);
    upsert.mutate(
      {
        model,
        entry: {
          input_per_1k: inputRate,
          output_per_1k: outputRate,
          currency: currency.trim() || 'USD',
        },
      },
      { onSuccess: onDone },
    );
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900">
          <span className="font-mono">{model}</span> 단가 등록
        </p>
        <span
          className="inline-flex cursor-help items-center gap-1 text-xs text-slate-500"
          title={PRICING_TOOLTIP}
        >
          <InfoIcon />
          단가는 왜 수동인가?
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        <strong>1K 토큰당</strong> 단가를 입력하십시오 (예: 1M 토큰 $3.00 이면{' '}
        <code className="rounded bg-slate-100 px-1">0.003</code>). 프로바이더 API 는 단가를
        제공하지 않으므로 공시 단가를 직접 옮겨 적는 것이 정답입니다.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Field label="입력 단가 / 1K">
          <Input
            type="number"
            min={0}
            step="0.000001"
            inputMode="decimal"
            placeholder="0.003"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
        </Field>
        <Field label="출력 단가 / 1K">
          <Input
            type="number"
            min={0}
            step="0.000001"
            inputMode="decimal"
            placeholder="0.015"
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </Field>
        <Field label="통화">
          <Input value={currency} onChange={(event) => setCurrency(event.target.value)} />
        </Field>
        <div className="flex items-end gap-2">
          <Button variant="primary" disabled={upsert.isPending} onClick={submit}>
            {upsert.isPending ? '저장 중…' : '단가 저장'}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            취소
          </Button>
        </div>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="danger">{error}</Notice>
        </div>
      )}
      {upsert.isError && (
        <div className="mt-3">
          <ErrorBlock error={upsert.error} />
        </div>
      )}

      <p className="mt-3 text-xs text-slate-500">
        저장하면 <strong>이후 분석부터</strong> 추정 비용이 계산됩니다. 이미 기록된 실행은
        그때의 단가로 고정되어 있어 소급 계산하지 않습니다.
      </p>
    </div>
  );
}
