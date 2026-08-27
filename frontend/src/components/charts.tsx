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
 * - **유입량(분모 쿼리) 추이는 오류 추이와 눈금이 다르다** — 한 축에 겹치면 오류 곡선이
 *   바닥에 눌린다. 차트를 나누고 slot 2(orange) 를 써서 두 그림이 서로 무엇인지 알아볼 수
 *   있게 한다 (여기서도 새 색은 도입하지 않는다).
 *
 * 표기 규칙: 여기 들어오는 건수는 `count_over_time` metric 쿼리 결과이며
 * **로그 라인 수가 아니다.** 호출부가 캡션으로 그 사실을 함께 적는다.
 * 추정 비용이 `null` 인 칸은 **0 으로 그리지 않는다** — 막대를 아예 그리지 않고 `-` 로
 * 적는다 (0 으로 그리면 "그날은 쌌다"로 읽힌다).
 *
 * 다크 모드: SVG 는 클래스가 아니라 **JS 값**으로 칠해지므로 CSS 토큰을 읽어 온다
 * (`--color-chart-*`, 정의는 `index.css`). 색 값을 여기에 하드코딩해 두면 테마마다
 * 두 벌이 생기고 언젠가 한쪽만 고쳐진다. 슬롯 이름(series1/series2)은 그대로다 —
 * 밝은 테마의 파랑과 어두운 테마의 파랑은 **같은 뜻**이어야 한다.
 */

import { useMemo, type ReactNode } from 'react';
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
import { useResolvedTheme } from '../lib/theme';
import { EmptyBlock } from './ui';

/**
 * 두 테마 모두에서 쓰이는 폴백. CSS 변수를 못 읽는 경우(테스트 환경·초기 페인트)에도
 * 차트가 투명해지지 않게 밝은 테마 값을 그대로 둔다.
 */
const FALLBACK = {
  series1: '#2a78d6',
  series2: '#eb6834',
  grid: '#e2e8f0',
  tick: '#64748b',
  surface: '#ffffff',
};

type ChartColors = typeof FALLBACK & {
  /** 툴팁 커서 선 색 (눈금 글자와 같은 색). */
  axis: string;
  /** 막대 hover 시 깔리는 면 — 밝은 테마는 어둡게, 어두운 테마는 밝게 눌러야 보인다. */
  cursorFill: string;
};

function readVar(styles: CSSStyleDeclaration | null, name: string, fallback: string): string {
  const value = styles?.getPropertyValue(name).trim();
  return value ? value : fallback;
}

/**
 * 차트 색 한 벌. `useResolvedTheme()` 가 바뀔 때만 다시 읽는다 — 테마 전환은 클래스
 * 토글이라 CSS 변수 값이 그 시점에 이미 새 것이다.
 */
function useChartColors(): ChartColors {
  const theme = useResolvedTheme();
  return useMemo(() => {
    const styles =
      typeof window !== 'undefined' && typeof getComputedStyle === 'function'
        ? getComputedStyle(document.documentElement)
        : null;
    const tick = readVar(styles, '--color-chart-tick', FALLBACK.tick);
    return {
      series1: readVar(styles, '--color-chart-series1', FALLBACK.series1),
      series2: readVar(styles, '--color-chart-series2', FALLBACK.series2),
      grid: readVar(styles, '--color-chart-grid', FALLBACK.grid),
      tick,
      surface: readVar(styles, '--color-chart-surface', FALLBACK.surface),
      // 커서 선은 눈금 글자와 같은 색이면 충분하다 — 슬롯을 늘리지 않는다.
      axis: tick,
      cursorFill: theme === 'dark' ? 'rgba(226,232,240,0.08)' : 'rgba(15,23,42,0.04)',
    };
    // theme 이 의존성이다: 값 자체는 DOM 에서 읽지만 언제 다시 읽을지를 이게 정한다.
  }, [theme]);
}

function axisTick(colors: ChartColors, fontSize = 11) {
  return { fill: colors.tick, fontSize };
}

function TooltipShell({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg shadow-slate-900/20">
      <p className="font-semibold text-ink">{title}</p>
      <div className="mt-1 space-y-0.5 text-muted">{children}</div>
    </div>
  );
}

// -------------------------------------------------------- 시간대별 오류 건수

export function ErrorTrendChart({
  points,
  height = 260,
  label = '오류 건수',
  tone = 'series1',
  emptyLabel,
}: {
  points: CountPoint[];
  height?: number;
  label?: string;
  /**
   * 색 슬롯. 오류는 slot 1(blue), **유입량(분모)** 은 slot 2(orange) 다.
   *
   * 두 값은 눈금이 다르므로(유입량이 오류보다 두 자릿수 크다) 한 축에 겹치지 않고 차트를
   * 나눈다 — 겹치면 오류 곡선이 바닥에 눌려 모양이 사라진다. 대신 색을 고정해 두 차트가
   * 서로 무엇인지 알아볼 수 있게 한다.
   */
  tone?: 'series1' | 'series2';
  emptyLabel?: ReactNode;
}) {
  // 훅은 조기 반환보다 먼저다 — 빈 데이터일 때만 훅이 빠지면 호출 순서가 흔들린다.
  const colors = useChartColors();

  if (points.length === 0) {
    return <EmptyBlock>{emptyLabel ?? '표시할 추이 데이터가 없습니다.'}</EmptyBlock>;
  }

  const data = points.map((point) => ({
    timestamp: point.timestamp,
    value: point.value,
  }));
  const stroke = tone === 'series2' ? colors.series2 : colors.series1;
  // 그라디언트 id 는 문서 전역이다 — 같은 페이지에 두 차트가 있으면 id 가 겹쳐 뒤 차트가
  // 앞 차트의 색으로 칠해진다. 슬롯별로 다른 id 를 쓴다.
  const gradientId = `aila-trend-fill-${tone}`;

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={colors.grid} strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatTime}
            tick={axisTick(colors)}
            tickLine={false}
            axisLine={{ stroke: colors.grid }}
            minTickGap={40}
          />
          <YAxis
            tick={axisTick(colors)}
            tickLine={false}
            axisLine={false}
            width={48}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ stroke: colors.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as { timestamp: string; value: number };
              return (
                <TooltipShell title={formatTime(row.timestamp)}>
                  <p>
                    {label} <span className="font-semibold text-ink">{formatNumber(row.value)}</span> 건
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={stroke}
            strokeWidth={2}
            fill={`url(#${gradientId})`}
            activeDot={{ r: 4, strokeWidth: 2, stroke: colors.surface }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------- 서비스별 오류 건수

export function ServiceBarChart({ data }: { data: ServiceErrorCount[] }) {
  const colors = useChartColors();

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
          <CartesianGrid stroke={colors.grid} horizontal={false} />
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="service"
            tick={axisTick(colors, 12)}
            tickLine={false}
            axisLine={{ stroke: colors.grid }}
            width={132}
          />
          <Tooltip
            cursor={{ fill: colors.cursorFill }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as { service: string; count: number };
              return (
                <TooltipShell title={row.service}>
                  <p>
                    <span className="font-semibold text-ink">{formatNumber(row.count)}</span> 건
                    (metric 기준)
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false} maxBarSize={18}>
            {rows.map((row) => (
              <Cell key={row.service} fill={colors.series1} />
            ))}
            <LabelList
              dataKey="count"
              position="right"
              formatter={(value) => formatNumber(Number(value))}
              style={{ fill: colors.tick, fontSize: 11 }}
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
  const colors = useChartColors();

  if (points.length === 0) return null;

  return (
    <div style={{ height }} aria-label={`최근 24시간 ${label} 추이`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="aila-spark-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.series1} stopOpacity={0.3} />
              <stop offset="100%" stopColor={colors.series1} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis dataKey="timestamp" hide />
          <YAxis hide domain={[0, 'dataMax']} />
          <Tooltip
            cursor={{ stroke: colors.axis, strokeWidth: 1 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as SummarySeriesPoint;
              return (
                <TooltipShell title={formatTime(row.timestamp)}>
                  <p>
                    {label}{' '}
                    <span className="font-semibold text-ink">{formatNumber(row.value)}</span> 건
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={colors.series1}
            strokeWidth={1.5}
            fill="url(#aila-spark-fill)"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 1, stroke: colors.surface }}
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
  const colors = useChartColors();

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
      cursor={{ fill: colors.cursorFill }}
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
              입력 <span className="font-semibold text-ink">{formatNumber(row.입력)}</span> ·
              출력 <span className="font-semibold text-ink">{formatNumber(row.출력)}</span> tok
            </p>
            <p>
              추정 비용{' '}
              <span className="font-semibold text-ink">
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
            stroke={colors.grid}
            horizontal={layout !== 'horizontal'}
            vertical={layout === 'horizontal'}
          />
          {layout === 'horizontal' ? (
            <>
              <XAxis type="number" tick={axisTick(colors)} tickLine={false} axisLine={false} />
              <YAxis
                type="category"
                dataKey="label"
                tick={axisTick(colors, 12)}
                tickLine={false}
                axisLine={{ stroke: colors.grid }}
                width={168}
              />
            </>
          ) : (
            <>
              <XAxis
                dataKey="label"
                tick={axisTick(colors)}
                tickLine={false}
                axisLine={{ stroke: colors.grid }}
                minTickGap={8}
              />
              <YAxis tick={axisTick(colors)} tickLine={false} axisLine={false} width={56} />
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
                wrapperStyle={{ fontSize: 12, color: colors.tick }}
              />
              <Bar
                dataKey="입력"
                stackId="tokens"
                fill={colors.series1}
                stroke={colors.surface}
                strokeWidth={2}
                isAnimationActive={false}
                maxBarSize={30}
              />
              <Bar
                dataKey="출력"
                stackId="tokens"
                fill={colors.series2}
                stroke={colors.surface}
                strokeWidth={2}
                isAnimationActive={false}
                maxBarSize={30}
              />
            </>
          ) : (
            <Bar
              dataKey="비용"
              fill={colors.series1}
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
  const colors = useChartColors();

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
          <CartesianGrid stroke={colors.grid} horizontal={false} />
          <XAxis type="number" tick={axisTick(colors)} tickLine={false} axisLine={false} />
          <YAxis
            type="category"
            dataKey="model"
            tick={axisTick(colors, 12)}
            tickLine={false}
            axisLine={{ stroke: colors.grid }}
            width={168}
          />
          <Tooltip
            cursor={{ fill: colors.cursorFill }}
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
                      <span className="font-semibold text-ink">
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
            wrapperStyle={{ fontSize: 12, color: colors.tick }}
          />
          {/* 스택 사이 2px 간격은 표면색 stroke 로 만든다. */}
          <Bar
            dataKey="입력"
            stackId="tokens"
            fill={colors.series1}
            stroke={colors.surface}
            strokeWidth={2}
            isAnimationActive={false}
            maxBarSize={22}
          />
          <Bar
            dataKey="출력"
            stackId="tokens"
            fill={colors.series2}
            stroke={colors.surface}
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
