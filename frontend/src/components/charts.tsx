/**
 * Recharts 차트.
 *
 * 색 사용 규칙:
 * - 시간대별 오류 건수·그룹 추이는 **단일 시리즈**라 범례 없이 한 색(sequential blue)만 쓴다.
 * - 서비스별 건수는 크기 비교라 색이 아니라 축이 정체성을 나른다 — 역시 단일 색.
 * - 토큰 차트만 입력/출력 두 시리즈라 categorical slot 1(blue)·2(orange) 를 쓰고 범례를 붙인다.
 *   (검증: 두 색 인접쌍 CVD ΔE 24.7 / normal ΔE 33.6, 흰 배경 대비 3:1 이상 — 전부 통과)
 *
 * 표기 규칙: 여기 들어오는 건수는 `count_over_time` metric 쿼리 결과이며
 * **로그 라인 수가 아니다.** 호출부가 캡션으로 그 사실을 함께 적는다.
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

import type { CountPoint, ServiceErrorCount, UsageAggregate } from '../api/types';
import { formatNumber, formatTime } from '../lib/format';
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
