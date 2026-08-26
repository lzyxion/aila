/**
 * 사용량 (`/admin/usage`).
 *
 * 기존 모델별 집계에 **분해 두 종**(일별·정책별)이 붙었다 — 계약 3 의 `group_by` 다.
 * 분해는 additive 라 `buckets` 가 없는 응답도 정상이고, 그 경우 화면은 실패가 아니라
 * "아직 분해를 못 준다"로 안내한다.
 *
 * 비용 표기 규칙은 여기서도 같다 — 단가표에 없는 모델의 추정 비용은 **0 이 아니라 null**
 * 이고, 막대를 0 으로 그리지 않고 `-` 로 적는다. 0 으로 그리면 "그날은 쌌다"로 읽힌다.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { isEndpointMissing } from '../api/client';
import { useModelPricing, useUpsertModelPricing, useUsage } from '../api/queries';
import type {
  ModelPricingEntry,
  ModelPricingTable,
  UsageBucket,
  UsageParams,
} from '../api/types';
import { asModelPricingTable } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { TokenByModelChart, UsageBucketChart } from '../components/chartsLazy';
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
  // 분해는 같은 기간에 축만 달리한 조회다 — 서버가 집계하므로 화면에서 재계산하지 않는다.
  const dayQuery = useUsage(useMemo(() => ({ ...params, group_by: 'day' as const }), [params]));
  const policyQuery = useUsage(
    useMemo(() => ({ ...params, group_by: 'policy' as const }), [params]),
  );
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
      <Card
        title="사용량"
        description={
          <>
            비용은 계산 시점 단가표 기준 <strong>추정</strong>값입니다 — 캐시 적중·배치 할인에 따라
            실제 청구액과 벌어지므로 정산 근거로 쓰지 마십시오. 비용 차단은 이 화면이 아니라 일일
            분석 한도가 담당합니다. 개별 실행은{' '}
            <Link to="/admin/analysis-jobs" className="font-medium text-sky-800 underline">
              분석 이력
            </Link>{' '}
            탭에서 검색합니다.
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
        className="mb-6"
      >
        <p className="text-sm text-slate-600">
          집계 구간 {formatDateTime(usageQuery.data?.range_start)} ~{' '}
          {formatDateTime(usageQuery.data?.range_end)}
        </p>
      </Card>

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

          <BucketCard
            title="일별 사용량"
            description={
              <>
                <code className="rounded bg-slate-200 px-1">app_settings.timezone</code> 기준 로컬
                날짜로 묶은 값입니다 (기본 <code className="rounded bg-slate-200 px-1">Asia/Seoul</code>)
                — 일일 분석 한도의 리셋 기준과 같은 날짜라 두 화면의 "오늘"이 어긋나지 않습니다.
              </>
            }
            groupBy="day"
            buckets={dayQuery.data?.buckets}
            pending={dayQuery.isPending}
            error={dayQuery.isError ? dayQuery.error : null}
          />

          <BucketCard
            title="정책별 사용량"
            description="어느 정책이 비용을 쓰는지 봅니다. 정책 연결이 끊긴 오래된 이력은 '정책 연결 없음' 으로 모입니다 — 버리면 합계가 어긋납니다."
            groupBy="policy"
            buckets={policyQuery.data?.buckets}
            pending={policyQuery.isPending}
            error={policyQuery.isError ? policyQuery.error : null}
          />

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

    </div>
  );
}

// ------------------------------------------------------- group_by 분해 카드

/**
 * 분해 하나(일별 또는 정책별)의 카드.
 *
 * 토큰과 비용을 **두 개의 차트**로 나눈다 — 한 축에 겹치면 단위가 다른 두 값(토큰 수 vs
 * 달러)이 같은 눈금을 공유하게 되고, 그러면 둘 중 하나는 언제나 바닥에 눌린다. 색은
 * 기존 팔레트의 두 슬롯만 쓴다(새 색 없음).
 *
 * 비용이 하나도 계산되지 않은 분해는 비용 차트를 **아예 그리지 않는다** — 빈 축을 그려
 * 두면 "0 달러였다"로 읽힌다.
 */
function BucketCard({
  title,
  description,
  groupBy,
  buckets,
  pending,
  error,
}: {
  title: string;
  description: React.ReactNode;
  groupBy: 'day' | 'policy';
  /** `null` = 분해를 못 받음(요청 미지원). `[]` = 분해했더니 비었음. 둘은 다른 문구다. */
  buckets: UsageBucket[] | null | undefined;
  pending: boolean;
  error: unknown;
}) {
  const rows = buckets ?? [];
  const pricedCount = rows.filter((bucket) => bucket.estimated_cost !== null).length;
  const unpriced = rows.length - pricedCount;

  return (
    <Card title={title} description={description}>
      {pending && <LoadingBlock label="분해를 불러오는 중…" />}

      {/* 아직 group_by 를 모르는 백엔드는 실패가 아니다 — 안내로 물러난다. */}
      {error != null &&
        (isEndpointMissing(error) ? (
          <Notice tone="warning" title="사용량 분해를 아직 쓸 수 없습니다">
            <code className="rounded bg-white/60 px-1">GET /api/usage?group_by={groupBy}</code> 가
            응답하지 않습니다. 백엔드에 이 파라미터가 올라오면 여기에 분해가 표시됩니다. 그 전에도
            아래 <strong>모델별 집계</strong>는 그대로 동작합니다.
          </Notice>
        ) : (
          <ErrorBlock error={error} />
        ))}

      {/* null(요청 미지원)과 빈 배열(기록 없음)을 같은 문구로 접지 않는다. */}
      {!pending && error == null && buckets == null && (
        <Notice tone="warning" title="응답에 buckets 가 없습니다">
          <code className="rounded bg-white/60 px-1">group_by</code> 는 추가 파라미터라 모르는
          백엔드는 무시하고 기존 응답만 줍니다(오류가 아닙니다).
        </Notice>
      )}

      {buckets != null && rows.length === 0 && (
        <EmptyBlock>이 기간에 분해할 사용량 기록이 없습니다.</EmptyBlock>
      )}

      {rows.length > 0 && (
        <div className="space-y-5">
          <div>
            <h3 className="mb-2 text-sm font-semibold text-slate-900">토큰</h3>
            <UsageBucketChart
              buckets={rows}
              metric="tokens"
              layout={groupBy === 'policy' ? 'horizontal' : 'vertical'}
            />
          </div>

          <div>
            <h3 className="mb-2 flex flex-wrap items-baseline gap-2 text-sm font-semibold text-slate-900">
              추정 비용
              <span className="text-xs font-normal text-slate-500">
                추정 — 정산 근거가 아닙니다.
              </span>
            </h3>
            {pricedCount === 0 ? (
              <EmptyBlock>
                이 기간의 모델이 전부 단가표에 없어 비용을 계산하지 못했습니다.{' '}
                <strong>0 원이라는 뜻이 아닙니다</strong> — 아래 표에서 단가를 등록하십시오.
              </EmptyBlock>
            ) : (
              <>
                <UsageBucketChart
                  buckets={rows}
                  metric="cost"
                  layout={groupBy === 'policy' ? 'horizontal' : 'vertical'}
                />
                {unpriced > 0 && (
                  <p className="mt-2 text-xs text-amber-700">
                    {unpriced}개 칸은 단가가 등록되지 않아 막대가 없습니다 — 0 이 아니라{' '}
                    <strong>계산되지 않음</strong>입니다.
                  </p>
                )}
              </>
            )}
          </div>

          <TableWrap minWidth="40rem">
            <thead>
              <tr>
                <Th>{groupBy === 'day' ? '날짜' : '정책'}</Th>
                <Th align="right">실행</Th>
                <Th align="right">실패</Th>
                <Th align="right">입력 토큰</Th>
                <Th align="right">출력 토큰</Th>
                <Th align="right">추정 비용</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((bucket) => (
                <tr key={bucket.key} className="hover:bg-slate-50">
                  <Td>
                    <p className="font-medium text-slate-900">{bucket.label}</p>
                    {groupBy === 'policy' && (
                      <p className="mt-0.5 font-mono text-xs text-slate-400">
                        {bucket.key === 'unknown' ? 'unknown' : `#${bucket.key}`}
                      </p>
                    )}
                  </Td>
                  <Td align="right">{formatNumber(bucket.job_count)}</Td>
                  <Td align="right">
                    {bucket.failure_count > 0 ? (
                      <Badge tone="danger">{formatNumber(bucket.failure_count)}</Badge>
                    ) : (
                      <span className="text-slate-400">0</span>
                    )}
                  </Td>
                  <Td align="right">{formatTokens(bucket.input_tokens)}</Td>
                  <Td align="right">{formatTokens(bucket.output_tokens)}</Td>
                  <Td align="right">
                    {formatEstimatedCost(bucket.estimated_cost)}
                    <span className="ml-1 text-xs text-slate-400">
                      {bucket.estimated_cost === null ? '(단가 없음)' : '(추정)'}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </div>
      )}
    </Card>
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
