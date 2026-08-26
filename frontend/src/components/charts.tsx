/**
 * Recharts 차트.
 *
 * 색 사용 규칙:
 * - 시간대별 오류 건수·그룹 추이는 **단일 시리즈**라 범례 없이 한 색(sequential blue)만 쓴다.
 * - 서비스별 건수는 크기 비교라 색이 아니라 축이 정체성을 나른다 — 역시 단일 색.
 * - 토큰 차트만 입력/출력 두 시리즈라 categorical slot 1(blue)·2(orange) 를 쓰고 범례를 붙인다.
 *   (검증: 두 색 인접쌍 CVD ΔE 24.7 / normal ΔE 33.6, 흰 배경 대비 3:1 이상 — 전부 통과)
 *
 * - 사용량 분해(일별·정책별)도 같은 두 슬롯만 쓴다 — 토큰은 입력/출력 두 시리즈,
 *   비용은 단일 시리즈다. **새 색을 도입하지 않는다** (색이 늘어나는 순간 두 화면의
 *   같은 색이 서로 다른 뜻을 갖기 시작한다).
 * - 스파크라인은 축도 범례도 없는 소형 추이라 역시 slot 1 한 색이다.
 *
 * 표기 규칙: 여기 들어오는 건수는 `count_over_time` metric 쿼리 결과이며
 * **로그 라인 수가 아니다.** 호출부가 캡션으로 그 사실을 함께 적는다.
 * 추정 비용이 `null` 인 칸은 **0 으로 그리지 않는다** — 막대를 아예 그리지 않고 `-` 로
 * 적는다 (0 으로 그리면 "그날은 쌌다"로 읽힌다).
 */

import type { ReactNode } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type {
  CountPoint,
  ServiceErrorCount,
  SummarySeriesPoint,
  UsageAggregate,
  UsageBucket,
} from '../api/types';
import { formatEstimatedCost, formatNumber, formatTime } from '../lib/format';
import { EmptyBlock } from './ui';

const COLOR = {
  series1: '#2a78d6',
  series2: '#eb6834',
  grid: '#e2e8f0',
  axis: '#94a3b8',
  tick: '#64748b',
  surface: '#ffffff',
};

const AXIS_TICK = { fill: COLOR.tick, fontSize: 11 };

function TooltipShell({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <p className="font-semibold text-slate-900">{title}</p>
      <div className="mt-1 space-y-0.5 text-slate-600">{children}</div>
    </div>
  );
}

// -------------------------------------------------------- 시간대별 오류 건수

export function ErrorTrendChart({
  points,
  height = 260,
  label = '오류 건수',
}: {
  points: CountPoint[];
  height?: number;
  label?: string;
}) {
  if (points.length === 0) {
    return <EmptyBlock>표시할 추이 데이터가 없습니다.</EmptyBlock>;
  }

  const data = points.map((point) => ({
    timestamp: point.timestamp,
    value: point.value,
  }));

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <defs>
            <linearGradient id="aila-trend-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLOR.series1} stopOpacity={0.28} />
              <stop offset="100%" stopColor={COLOR.series1} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={COLOR.grid} strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatTime}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: COLOR.grid }}
            minTickGap={40}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ stroke: COLOR.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as { timestamp: string; value: number };
              return (
                <TooltipShell title={formatTime(row.timestamp)}>
                  <p>
                    {label} <span className="font-semibold text-slate-900">{formatNumber(row.value)}</span> 건
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={COLOR.series1}
            strokeWidth={2}
            fill="url(#aila-trend-fill)"
            activeDot={{ r: 4, strokeWidth: 2, stroke: COLOR.surface }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------- 서비스별 오류 건수

export function ServiceBarChart({ data }: { data: ServiceErrorCount[] }) {
  if (data.length === 0) {
    return <EmptyBlock>서비스별 집계가 없습니다.</EmptyBlock>;
  }

  const rows = data.map((item) => ({
    service: item.service ?? '(라벨 없음)',
    count: item.count,
  }));

  return (
    <div style={{ height: Math.max(160, rows.length * 42) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 56, bottom: 4, left: 4 }}
          barCategoryGap={10}
        >
          <CartesianGrid stroke={COLOR.grid} horizontal={false} />
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="service"
            tick={{ ...AXIS_TICK, fontSize: 12 }}
            tickLine={false}
            axisLine={{ stroke: COLOR.grid }}
            width={132}
          />
          <Tooltip
            cursor={{ fill: 'rgba(15,23,42,0.04)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as { service: string; count: number };
              return (
                <TooltipShell title={row.service}>
                  <p>
                    <span className="font-semibold text-slate-900">{formatNumber(row.count)}</span> 건
                    (metric 기준)
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false} maxBarSize={18}>
            {rows.map((row) => (
              <Cell key={row.service} fill={COLOR.series1} />
            ))}
            <LabelList
              dataKey="count"
              position="right"
              formatter={(value) => formatNumber(Number(value))}
              style={{ fill: COLOR.tick, fontSize: 11 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ------------------------------------------------------------- 스파크라인

/**
 * 정책 카드 안의 24h 소형 추이. **축도 격자도 범례도 없다** — 카드에서 이 그림이 답하는
 * 질문은 "몇 건인가"가 아니라 "지금 오르는 중인가"뿐이고, 정확한 값은 옆의 숫자가 준다.
 *
 * 포인트가 없으면(= metric 실패) 아무것도 그리지 않고 호출부가 사유를 적는다 — 평평한
 * 선을 그리면 "오류가 없었다"로 읽힌다.
 */
export function Sparkline({
  points,
  height = 40,
  label = '오류 건수',
}: {
  points: SummarySeriesPoint[];
  height?: number;
  label?: string;
}) {
  if (points.length === 0) return null;

  return (
    <div style={{ height }} aria-label={`최근 24시간 ${label} 추이`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="aila-spark-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLOR.series1} stopOpacity={0.3} />
              <stop offset="100%" stopColor={COLOR.series1} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis dataKey="timestamp" hide />
          <YAxis hide domain={[0, 'dataMax']} />
          <Tooltip
            cursor={{ stroke: COLOR.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as SummarySeriesPoint;
              return (
                <TooltipShell title={formatTime(row.timestamp)}>
                  <p>
                    {label}{' '}
                    <span className="font-semibold text-slate-900">{formatNumber(row.value)}</span> 건
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={COLOR.series1}
            strokeWidth={1.5}
            fill="url(#aila-spark-fill)"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 1, stroke: COLOR.surface }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ------------------------------------------------- 사용량 분해 (일별 · 정책별)

/**
 * `GET /api/usage?group_by=` 의 `buckets` 를 그린다.
 *
 * - `metric="tokens"`: 입력/출력 스택 (categorical slot 1·2, 범례 있음)
 * - `metric="cost"`: 추정 비용 단일 시리즈. **null 인 칸은 막대가 없다** — 0 과 구분된다.
 * - `layout="horizontal"`: 라벨이 긴 정책명용 (가로 막대). 날짜는 `vertical` 이 자연스럽다.
 */
export function UsageBucketChart({
  buckets,
  metric,
  layout = 'vertical',
  height,
}: {
  buckets: UsageBucket[];
  metric: 'tokens' | 'cost';
  layout?: 'vertical' | 'horizontal';
  height?: number;
}) {
  if (buckets.length === 0) {
    return <EmptyBlock>이 기간에 분해할 사용량 기록이 없습니다.</EmptyBlock>;
  }

  const rows = buckets.map((bucket) => ({
    key: bucket.key,
    label: bucket.label,
    입력: bucket.input_tokens,
    출력: bucket.output_tokens,
    // 계산되지 않은 비용은 0 이 아니라 **없음**이다. undefined 를 주면 막대가 그려지지 않는다.
    비용: bucket.estimated_cost === null ? undefined : Number(bucket.estimated_cost),
    job_count: bucket.job_count,
    failure_count: bucket.failure_count,
    estimated_cost: bucket.estimated_cost,
  }));

  const chartHeight =
    height ??
    (layout === 'horizontal' ? Math.max(160, rows.length * 46) : Math.max(200, 220));

  const tooltip = (
    <Tooltip
      cursor={{ fill: 'rgba(15,23,42,0.04)' }}
      content={({ active, payload }) => {
        if (!active || !payload?.length) return null;
        const row = payload[0].payload as (typeof rows)[number];
        return (
          <TooltipShell title={row.label}>
            <p>
              분석 {formatNumber(row.job_count)}회
              {row.failure_count > 0 ? ` · 실패 ${formatNumber(row.failure_count)}회` : ''}
            </p>
            <p>
              입력 <span className="font-semibold text-slate-900">{formatNumber(row.입력)}</span> ·
              출력 <span className="font-semibold text-slate-900">{formatNumber(row.출력)}</span> tok
            </p>
            <p>
              추정 비용{' '}
              <span className="font-semibold text-slate-900">
                {formatEstimatedCost(row.estimated_cost)}
              </span>
              {row.estimated_cost === null && ' (단가 미등록 — 0 이 아님)'}
            </p>
          </TooltipShell>
        );
      }}
    />
  );

  return (
    <div style={{ height: chartHeight }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          layout={layout === 'horizontal' ? 'vertical' : 'horizontal'}
          margin={{ top: 4, right: 16, bottom: 4, left: layout === 'horizontal' ? 4 : -8 }}
          barCategoryGap={layout === 'horizontal' ? 12 : 16}
        >
          <CartesianGrid
            stroke={COLOR.grid}
            horizontal={layout !== 'horizontal'}
            vertical={layout === 'horizontal'}
          />
          {layout === 'horizontal' ? (
            <>
              <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <YAxis
                type="category"
                dataKey="label"
                tick={{ ...AXIS_TICK, fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: COLOR.grid }}
                width={168}
              />
            </>
          ) : (
            <>
              <XAxis
                dataKey="label"
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={{ stroke: COLOR.grid }}
                minTickGap={8}
              />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={56} />
            </>
          )}
          {tooltip}
          {metric === 'tokens' ? (
            <>
              <Legend
                verticalAlign="top"
                align="left"
                height={28}
                iconType="circle"
                iconSize={8}
                wrapperStyle={{ fontSize: 12, color: COLOR.tick }}
              />
              <Bar
                dataKey="입력"
                stackId="tokens"
                fill={COLOR.series1}
                stroke={COLOR.surface}
                strokeWidth={2}
                isAnimationActive={false}
                maxBarSize={30}
              />
              <Bar
                dataKey="출력"
                stackId="tokens"
                fill={COLOR.series2}
                stroke={COLOR.surface}
                strokeWidth={2}
                isAnimationActive={false}
                maxBarSize={30}
              />
            </>
          ) : (
            <Bar
              dataKey="비용"
              fill={COLOR.series1}
              isAnimationActive={false}
              maxBarSize={30}
              radius={layout === 'horizontal' ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ------------------------------------------------------------ 모델별 토큰

export function TokenByModelChart({ items }: { items: UsageAggregate[] }) {
  if (items.length === 0) {
    return <EmptyBlock>집계할 사용량 기록이 없습니다.</EmptyBlock>;
  }

  const rows = items.map((item) => ({
    model: item.model,
    입력: item.input_tokens,
    출력: item.output_tokens,
  }));

  return (
    <div style={{ height: Math.max(180, rows.length * 56) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 24, bottom: 4, left: 4 }}
          barCategoryGap={14}
        >
          <CartesianGrid stroke={COLOR.grid} horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} />
          <YAxis
            type="category"
            dataKey="model"
            tick={{ ...AXIS_TICK, fontSize: 12 }}
            tickLine={false}
            axisLine={{ stroke: COLOR.grid }}
            width={168}
          />
          <Tooltip
            cursor={{ fill: 'rgba(15,23,42,0.04)' }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <TooltipShell title={String(label)}>
                  {payload.map((entry) => (
                    <p key={String(entry.dataKey)} className="flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="inline-block size-2 rounded-full"
                        style={{ background: entry.color }}
                      />
                      {String(entry.dataKey)} 토큰{' '}
                      <span className="font-semibold text-slate-900">
                        {formatNumber(Number(entry.value))}
                      </span>
                    </p>
                  ))}
                </TooltipShell>
              );
            }}
          />
          <Legend
            verticalAlign="top"
            align="left"
            height={28}
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 12, color: COLOR.tick }}
          />
          {/* 스택 사이 2px 간격은 표면색 stroke 로 만든다. */}
          <Bar
            dataKey="입력"
            stackId="tokens"
            fill={COLOR.series1}
            stroke={COLOR.surface}
            strokeWidth={2}
            isAnimationActive={false}
            maxBarSize={22}
          />
          <Bar
            dataKey="출력"
            stackId="tokens"
            fill={COLOR.series2}
            stroke={COLOR.surface}
            strokeWidth={2}
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
            maxBarSize={22}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
