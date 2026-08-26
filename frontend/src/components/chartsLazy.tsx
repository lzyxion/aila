/**
 * 차트의 지연 로딩 래퍼.
 *
 * Recharts 는 이 앱에서 가장 큰 의존성이라 초기 번들에 그대로 들어가면 첫 화면이
 * 느려진다. 페이지는 여기서 import 하고, 실제 차트 코드는 필요한 순간에 별도 청크로
 * 내려온다. 컴포넌트 이름·props 는 `./charts` 와 동일하다.
 */

import { lazy, Suspense, type ComponentProps } from 'react';

import type {
  ErrorTrendChart as ErrorTrendChartImpl,
  ServiceBarChart as ServiceBarChartImpl,
  Sparkline as SparklineImpl,
  TokenByModelChart as TokenByModelChartImpl,
  UsageBucketChart as UsageBucketChartImpl,
} from './charts';
import { Spinner } from './ui';

const LazyErrorTrend = lazy(() =>
  import('./charts').then((module) => ({ default: module.ErrorTrendChart })),
);
const LazyServiceBar = lazy(() =>
  import('./charts').then((module) => ({ default: module.ServiceBarChart })),
);
const LazyTokenByModel = lazy(() =>
  import('./charts').then((module) => ({ default: module.TokenByModelChart })),
);
const LazyUsageBucket = lazy(() =>
  import('./charts').then((module) => ({ default: module.UsageBucketChart })),
);
const LazySparkline = lazy(() =>
  import('./charts').then((module) => ({ default: module.Sparkline })),
);

function ChartFallback({ height = 220 }: { height?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded-lg bg-slate-50 text-sm text-slate-400"
      style={{ height }}
    >
      <Spinner />
    </div>
  );
}

export function ErrorTrendChart(props: ComponentProps<typeof ErrorTrendChartImpl>) {
  return (
    <Suspense fallback={<ChartFallback height={props.height ?? 260} />}>
      <LazyErrorTrend {...props} />
    </Suspense>
  );
}

export function ServiceBarChart(props: ComponentProps<typeof ServiceBarChartImpl>) {
  return (
    <Suspense fallback={<ChartFallback height={Math.max(160, props.data.length * 42)} />}>
      <LazyServiceBar {...props} />
    </Suspense>
  );
}

export function TokenByModelChart(props: ComponentProps<typeof TokenByModelChartImpl>) {
  return (
    <Suspense fallback={<ChartFallback height={Math.max(180, props.items.length * 56)} />}>
      <LazyTokenByModel {...props} />
    </Suspense>
  );
}

export function UsageBucketChart(props: ComponentProps<typeof UsageBucketChartImpl>) {
  const fallbackHeight =
    props.height ??
    (props.layout === 'horizontal' ? Math.max(160, props.buckets.length * 46) : 220);
  return (
    <Suspense fallback={<ChartFallback height={fallbackHeight} />}>
      <LazyUsageBucket {...props} />
    </Suspense>
  );
}

/**
 * 카드 안의 소형 추이. 로딩 자리는 **높이만 차지하는 빈 칸**이다 — 카드마다 스피너가
 * 하나씩 돌면 첫 화면이 공사장처럼 보인다.
 */
export function Sparkline(props: ComponentProps<typeof SparklineImpl>) {
  if (props.points.length === 0) return null;
  return (
    <Suspense fallback={<div style={{ height: props.height ?? 40 }} />}>
      <LazySparkline {...props} />
    </Suspense>
  );
}
