/**
 * 정책 하나의 상세 대시보드 (`/dashboard/:policyId`).
 *
 * 정책 **전체**를 훑는 화면은 홈(`/`)의 카드 그리드다. 여기는 카드에서 하나를 골라
 * 들어오는 자리이고, 그래서 정책 선택은 상태가 아니라 **경로**다 — 링크를 붙여
 * 공유하거나 브라우저 뒤로가기로 되짚을 수 있어야 한다.
 *
 * Phase 8 밀도 정리에서 두 가지를 바꿨다.
 * - 지표 6개를 한 줄에 늘어놓던 것을 **핵심 3개(크게) + 보조 3개(작게)** 로 나눴다.
 *   여섯 칸이 같은 크기면 "지금 봐야 할 숫자"가 어느 것인지 화면이 말해 주지 않는다.
 * - 계약 문구(`count_over_time` 기준·fingerprint 기준·`null ≠ 0`)는 지우지 않고
 *   ⓘ(`InfoTip`) 로 옮겼다. 대신 **분모 미설정 안내처럼 사용자가 할 일이 있는 문장**은
 *   본문 `Notice` 로 남긴다.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';

import {
  useDashboardOverview,
  usePolicies,
  usePolicyQueryRuns,
  useRunPolicy,
} from '../api/queries';
import { policySchedule, type DashboardOverviewParams, type FetchWarning } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { ErrorTrendChart, ServiceBarChart } from '../components/chartsLazy';
import {
  BackIcon,
  ChevronRightIcon,
  ErrorGroupIcon,
  GroupCountIcon,
} from '../components/icons';
import {
  AnalysisStatusBadge,
  IngestAbsentBadge,
  ScheduleBadge,
  SeverityBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Button,
  ButtonLink,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  Field,
  InfoTip,
  LogLine,
  Notice,
  PageHeader,
  PageStack,
  PlayIcon,
  Select,
  SkeletonCard,
  SkeletonStats,
  Spinner,
  Stat,
  TableWrap,
  Td,
  TextLink,
  Th,
} from '../components/ui';
import {
  formatDateTime,
  formatNumber,
  formatRatio,
  formatRelative,
  ingestAbsentWarnings,
  truncate,
  warningCodeLabel,
} from '../lib/format';

/** 기간 프리셋. 서버가 `max_query_range_minutes` 로 상한을 강제하므로 UI 는 편의일 뿐이다. */
const RANGES = [
  { minutes: 60, label: '최근 1시간', step: 300 },
  { minutes: 6 * 60, label: '최근 6시간', step: 900 },
  { minutes: 24 * 60, label: '최근 24시간', step: 3600 },
  { minutes: 3 * 24 * 60, label: '최근 3일', step: 3600 },
];

const METRIC_NOTE = (
  <>
    건수와 추이는 <Code>count_over_time</Code> metric 쿼리 결과입니다 —{' '}
    <strong>로그 라인을 센 값이 아닙니다</strong>.
    <span className="mt-1.5 block">
      metric 이 실패한 값은 0 이 아니라 <strong>없음</strong>이며 화면에는 <Code>-</Code> 로
      나옵니다.
    </span>
  </>
);

const FINGERPRINT_NOTE = (
  <>
    분석 상태는 그룹 id 가 아니라 <strong>fingerprint 기준</strong>입니다 — 이전 조회에서 분석한
    오류도 "분석 완료"로 보입니다.
    <span className="mt-1.5 block">
      덕분에 이미 분석한 오류를 중복 요청(= 중복 과금)하지 않습니다.
    </span>
  </>
);

export function DashboardPage() {
  const [rangeIndex, setRangeIndex] = useState(0);
  const params = useParams<{ policyId?: string }>();
  const navigate = useNavigate();
  const write = useWriteAccess();

  // 정책 선택은 상태가 아니라 경로다. `/dashboard` (id 없음)는 전체 조회 결과를 본다.
  const parsed = params.policyId ? Number(params.policyId) : NaN;
  const policyId = Number.isFinite(parsed) ? parsed : null;

  const policiesQuery = usePolicies();
  const runPolicy = useRunPolicy();

  const range = RANGES[rangeIndex];

  // 파라미터를 rangeIndex/policyId 에서만 파생시켜야 캐시 키가 매 렌더 바뀌지 않는다.
  const overviewParams = useMemo<DashboardOverviewParams>(() => {
    const end = new Date();
    const start = new Date(end.getTime() - range.minutes * 60_000);
    return {
      ...(policyId !== null ? { policy_id: policyId } : {}),
      range_start: start.toISOString(),
      range_end: end.toISOString(),
      step_seconds: range.step,
      top: 10,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeIndex, policyId]);

  const overview = useDashboardOverview(overviewParams);
  /*
    수집 중단 경고는 **조회 회차**에 남는다 (`query_runs.warnings`). 대시보드가 보는
    overview 는 metric 쿼리 결과라 그 경고를 반드시 싣지는 않으므로, 최근 회차 한 건을
    따로 읽어 배지의 근거로 쓴다. 경로가 없는 백엔드에서는 훅이 조용히 실패하고
    (retry 없음) 배지만 사라진다.
  */
  const recentRuns = usePolicyQueryRuns(policyId, 1);

  const allPolicies = policiesQuery.data ?? [];
  const activePolicies = allPolicies.filter((policy) => policy.active);
  // 비활성 정책의 대시보드로 직접 들어올 수 있다 — 목록에 없다고 빈 화면을 주지 않는다.
  const selectedPolicy = allPolicies.find((policy) => policy.id === policyId) ?? null;
  const schedule = selectedPolicy ? policySchedule(selectedPolicy) : null;

  /*
    수집 중단 경고. 세 출처를 합치고 메시지로 중복을 접는다 — 방금 누른 실행 결과, 최근
    회차, overview. 어느 하나만 보면 "실행한 직후에만 보이는 배지"나 "새로고침해야 사라지는
    배지"가 된다.

    이건 **기록**이다. 배지가 떠도 아무것도 자동으로 실행되지 않는다 (계약: 자동 트리거는
    정책의 auto_analyze_new 하나뿐).
  */
  const latestRun = recentRuns.data?.items?.[0] ?? null;
  const absentWarnings = ingestAbsentWarnings(
    runPolicy.isSuccess ? runPolicy.data.warnings : undefined,
    latestRun?.warnings,
    overview.data?.warnings,
  );

  /*
    지표 두 개는 **회차 전체 COUNT** 가 정답이다 (`group_count`·`unanalyzed_group_count`).
    필드를 아직 안 내려주는 백엔드에서만 상위 N 으로 폴백하고, 그 사실을 부제에 적는다 —
    두 상태를 같은 문구로 적으면 "그룹이 10개뿐"이라는 오해가 굳는다.
  */
  const data = overview.data;
  const groupCountIsExact = typeof data?.group_count === 'number';
  const groupCount = groupCountIsExact ? (data?.group_count ?? 0) : (data?.top_groups.length ?? 0);
  const unanalyzedIsExact = typeof data?.unanalyzed_group_count === 'number';
  const unanalyzedCount = unanalyzedIsExact
    ? (data?.unanalyzed_group_count ?? 0)
    : (data?.top_groups.filter((group) => !group.analysis_status).length ?? 0);

  /*
    유입량·오류 비율은 정책의 분모 쿼리(`baseline_query`)가 있어야 계산된다. **null 은 0 이
    아니다** — 화면은 `-` 로 쓰고, 왜 비었는지를 값 옆에 적는다 (미설정 / 실패 / 정책 미선택은
    사용자가 할 일이 서로 다르다).
  */
  const baselineMissing =
    selectedPolicy !== null && !(selectedPolicy.baseline_query ?? '').trim();
  const baselineHint =
    selectedPolicy === null
      ? '정책 미선택'
      : baselineMissing
        ? '분모 쿼리 미설정'
        : '분모 쿼리 실패 · 0 아님';
  const showIngestChart = (data?.ingest_series?.length ?? 0) > 0;

  return (
    <div>
      <PageHeader
        title={selectedPolicy ? `대시보드 · ${selectedPolicy.name}` : '대시보드 · 전체'}
        description={`${range.label} 기준 지표와 상위 오류 그룹입니다.`}
        info={
          <>
            {METRIC_NOTE}
            <span className="mt-1.5 block">{FINGERPRINT_NOTE}</span>
          </>
        }
        actions={
          <ButtonLink to="/">
            <BackIcon aria-hidden className="size-4" />
            통합 대시보드
          </ButtonLink>
        }
      />

      {selectedPolicy && (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          <Badge tone={selectedPolicy.active ? 'success' : 'neutral'}>
            {selectedPolicy.active ? '활성' : '비활성'}
          </Badge>
          {schedule && (
            <ScheduleBadge
              enabled={schedule.enabled}
              intervalMinutes={schedule.intervalMinutes}
              autoAnalyze={schedule.autoAnalyze}
            />
          )}
          <TextLink to={`/policies?policy=${selectedPolicy.id}`} className="ml-1 text-xs">
            정책 설정 →
          </TextLink>
        </div>
      )}

      {/*
        수집 중단 의심 — 오류가 0 건인 것과 로그 자체가 끊긴 것은 정반대의 사건인데
        화면에서는 똑같이 "조용한 정책"으로 보인다. 그래서 지표보다 위에 둔다.
        위험 경고는 ⓘ 로 숨기지 않는다.
      */}
      {absentWarnings.length > 0 && (
        <div className="mb-4">
          <Notice tone="danger" title="수집 중단 의심">
            <ul className="mt-1 space-y-1">
              {absentWarnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`} className="flex flex-wrap items-center gap-2">
                  <IngestAbsentBadge warning={warning} />
                  <span>{warning.message}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs leading-relaxed">
              연결에 등록한 <strong>수집 확인 대상 서비스</strong>가 조회 기간에 로그를 한 줄도
              내지 않았습니다. 오류가 0 건인 것과 로그가 끊긴 것은 다릅니다 — 수집 파이프라인
              (Alloy·Loki)을 먼저 확인하십시오. 이 경고는 <strong>기록일 뿐</strong>이며 아무것도
              자동으로 실행하지 않습니다.
            </p>
          </Notice>
        </div>
      )}

      {/*
        실행 카드. 예전에는 카드 안에 하늘색 상자를 하나 더 두어 정책 선택과 실행 버튼을
        묶었는데, 카드-in-카드가 되어 배경이 두 겹으로 쌓였다. 지금은 카드 하나 안에서
        **행 배치**로 묶는다 — 정책·기간·실행이 한 줄에 있으면 상자가 없어도 한 동작으로 읽힌다.
      */}
      <Card
        title="정책 실행"
        description="선택한 정책의 쿼리로 로그 소스를 지금 조회하고 결과를 그룹으로 묶습니다."
        info={
          <>
            분석(LLM 호출)은 여기서 실행되지 않습니다 — <strong>그룹 상세</strong>에서 따로
            실행합니다.
            <span className="mt-1.5 block">
              기간·라인 수 상한은 <strong>서버가</strong> 강제합니다. 화면의 기간 선택은 편의일
              뿐이고, 정책의 <Code>default_range_minutes</Code> 를 넘는 요청은 조용히 clamp 되어
              경고로 남습니다.
            </span>
          </>
        }
        className="mb-6"
      >
        <div className="flex flex-wrap items-end gap-3">
          <Field
            label="정책"
            className="min-w-56 flex-1"
            hint={
              selectedPolicy
                ? `상한 ${formatNumber(selectedPolicy.max_lines)} 라인 · 대표 로그 ${selectedPolicy.max_samples_per_group} 개`
                : '선택하지 않으면 전체 조회 결과를 보여줍니다.'
            }
          >
            <Select
              value={policyId ?? ''}
              onChange={(event) =>
                navigate(
                  event.target.value === '' ? '/dashboard' : `/dashboard/${event.target.value}`,
                )
              }
            >
              <option value="">전체</option>
              {activePolicies.map((policy) => (
                <option key={policy.id} value={policy.id}>
                  {policy.name}
                </option>
              ))}
              {/* 비활성 정책으로 직접 들어온 경우에도 선택 상태가 보여야 한다. */}
              {selectedPolicy && !selectedPolicy.active && (
                <option value={selectedPolicy.id}>{selectedPolicy.name} (비활성)</option>
              )}
            </Select>
          </Field>

          <Field label="기간" className="w-48">
            <Select
              value={rangeIndex}
              onChange={(event) => setRangeIndex(Number(event.target.value))}
            >
              {RANGES.map((item, index) => (
                <option key={item.label} value={index}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>

          <Button
            variant="primary"
            size="lg"
            className="mb-1"
            disabled={!write.allowed || !selectedPolicy || runPolicy.isPending}
            title={
              write.reason ??
              (selectedPolicy
                ? `${selectedPolicy.name} 정책으로 로그 소스를 조회합니다.`
                : '실행할 정책을 먼저 고르십시오.')
            }
            onClick={() => {
              if (!selectedPolicy) return;
              runPolicy.mutate({ id: selectedPolicy.id, payload: {} });
            }}
          >
            {runPolicy.isPending ? (
              <>
                <Spinner className="size-4 border-sky-200 border-t-white" />
                조회 중…
              </>
            ) : (
              <>
                <PlayIcon />
                정책 실행
              </>
            )}
          </Button>
        </div>

        {/* 권한·선택 없음은 버튼이 왜 눌리지 않는지에 대한 답이라 본문에 남긴다. */}
        {(!write.allowed || !selectedPolicy) && (
          <p className="mt-2 text-xs text-muted">
            {!write.allowed ? write.reason : '정책을 골라야 실행할 수 있습니다.'}
          </p>
        )}

        {/* 쿼리는 길고 늘 볼 필요는 없다 — 접어 두되 한 번의 클릭으로 펼쳐진다. */}
        {selectedPolicy && (
          <details className="group mt-3">
            {/* `list-none` 만으로는 Safari 의 기본 삼각형이 남는다 — 웹킷 마커까지 지운다. */}
            <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-xs font-medium text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
              <ChevronRightIcon
                aria-hidden
                className="size-3.5 transition-transform group-open:rotate-90"
              />
              쿼리 보기
            </summary>
            <div className="mt-2">
              <LogLine>{selectedPolicy.query}</LogLine>
            </div>
          </details>
        )}

        {runPolicy.isPending && (
          <div className="mt-4">
            <Notice tone="info">
              <span className="inline-flex items-center gap-2">
                <Spinner />
                정책 <strong>{selectedPolicy?.name}</strong> 으로 조회하는 중입니다. 기간·라인 수
                상한은 서버가 강제합니다.
              </span>
            </Notice>
          </div>
        )}
        {runPolicy.isError && (
          <div className="mt-4">
            <ErrorBlock error={runPolicy.error} />
          </div>
        )}
        {runPolicy.isSuccess && !runPolicy.isPending && (
          <div className="mt-4">
            <Notice tone="success" title={`조회 #${runPolicy.data.id} 완료`}>
              {formatNumber(runPolicy.data.fetched_count)} 라인 조회 ·{' '}
              {formatNumber(runPolicy.data.dropped_count)} 라인 제외 ·{' '}
              {runPolicy.data.group_count} 개 그룹{' '}
              <TextLink to={`/query-runs/${runPolicy.data.id}`}>이 조회의 오류 그룹 보기 →</TextLink>
              {runPolicy.data.warnings.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
                  {runPolicy.data.warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>
                      {warningCodeLabel(warning.code)} — {warning.message}
                    </li>
                  ))}
                </ul>
              )}
            </Notice>
          </div>
        )}
      </Card>

      {overview.isPending && (
        <PageStack>
          <SkeletonStats count={3} label="대시보드 지표를 불러오는 중" />
          <SkeletonCard lines={4} label="차트를 불러오는 중" />
        </PageStack>
      )}
      {overview.isError && <ErrorBlock error={overview.error} />}

      {overview.data && (
        <PageStack>
          {/* 수집 중단은 위에서 이미 전용 블록으로 말했다 — 여기서 또 적지 않는다. */}
          <WarningList
            warnings={overview.data.warnings.filter(
              (warning) => warning.code !== 'ingest_absent',
            )}
          />

          {/*
            지표 위계. 핵심 셋(오류 건수·그룹 수·미분석)은 크게, 파생 지표 셋(유입량·비율·
            영향 서비스)은 한 줄짜리 보조 타일로 내린다. 여섯 칸이 같은 크기면 화면이
            "무엇을 먼저 보라"고 말하지 못한다.

            `group_count`·`unanalyzed_group_count` 는 **회차 전체 COUNT** 다 —
            `top_groups.length`(상위 N)를 지표 자리에 쓰면 정책이 커질수록 항상 "10"이 되어
            숫자가 상한에 붙어 버린다. 필드가 없는 옛 백엔드에서만 옛 계산으로 폴백하고,
            그 경우 부제에 "상위 N 기준"이라고 적어 두 상태를 구분한다.
          */}
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat
              label="총 오류 건수"
              icon={ErrorGroupIcon}
              value={formatNumber(overview.data.total_errors)}
              sub={range.label}
              info={METRIC_NOTE}
              tone="accent"
            />
            <Stat
              label="오류 그룹"
              icon={GroupCountIcon}
              value={formatNumber(groupCount)}
              sub={groupCountIsExact ? '이 회차 전체' : `상위 ${overview.data.top_groups.length}개 기준`}
              info={
                groupCountIsExact ? (
                  <>
                    조회 회차 <strong>전체</strong>의 그룹 수(DB COUNT)입니다 — 아래 표의{' '}
                    <strong>상위 오류 그룹</strong> 개수와 다릅니다.
                  </>
                ) : (
                  <>
                    백엔드가 회차 전체 COUNT 를 아직 내려주지 않아 <strong>상위 N 개</strong>로
                    폴백한 값입니다 — 실제 그룹 수는 이보다 많을 수 있습니다.
                  </>
                )
              }
            />
            <Stat
              label="미분석 그룹"
              icon={GroupCountIcon}
              value={formatNumber(unanalyzedCount)}
              sub={unanalyzedIsExact ? '이 회차 전체' : `상위 ${overview.data.top_groups.length}개 기준`}
              info={
                <>
                  {FINGERPRINT_NOTE}
                  {!unanalyzedIsExact && (
                    <span className="mt-1.5 block">
                      백엔드가 회차 전체 COUNT 를 아직 내려주지 않아 <strong>상위 N 개</strong>
                      만으로 센 값입니다.
                    </span>
                  )}
                </>
              }
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <SubStat
              label="유입량 (분모)"
              value={
                overview.data.ingest_total == null ? (
                  <span className="text-faint">-</span>
                ) : (
                  formatNumber(overview.data.ingest_total)
                )
              }
              note={overview.data.ingest_total == null ? baselineHint : '같은 기간 전체 로그'}
              info={
                <>
                  정책의 <strong>분모 쿼리</strong>(오류 셀렉터와 같은 라벨 범위의 전체 로그를 세는
                  쿼리)가 있을 때만 계산합니다. 오류 쿼리에서 역산하지 않습니다.
                  <span className="mt-1.5 block">
                    미설정·실패면 값은 0 이 아니라 <strong>없음</strong>(<Code>-</Code>)입니다.
                  </span>
                </>
              }
            />
            <SubStat
              label="오류 비율"
              value={
                overview.data.error_ratio == null ? (
                  <span className="text-faint">-</span>
                ) : (
                  formatRatio(overview.data.error_ratio)
                )
              }
              note={overview.data.error_ratio == null ? baselineHint : '오류 ÷ 유입량'}
              info={
                <>
                  같은 기간·같은 step 의 <strong>오류 ÷ 유입량</strong>입니다. 분모가 없으면{' '}
                  <Code>-</Code> 이며 <strong>0 이 아닙니다</strong>.
                </>
              }
            />
            <SubStat
              label="영향 서비스"
              value={formatNumber(overview.data.by_service.length)}
              note="라벨 기준"
              info={
                <>
                  표준 라벨 <Code>service</Code> 기준으로 이 기간에 오류가 하나라도 잡힌 서비스
                  수입니다.
                </>
              }
            />
          </div>

          {/* 분모 쿼리가 없으면 유입량·비율 칸이 왜 비어 있는지 같은 자리에 적는다. */}
          {baselineMissing && selectedPolicy && (
            <Notice tone="neutral" title="분모 쿼리가 설정되지 않았습니다">
              유입량과 오류 비율은 <strong>분모 쿼리</strong>(오류 셀렉터와 같은 라벨 범위의 전체
              로그를 세는 쿼리)가 있어야 계산합니다. 값이 <Code>-</Code> 인 것은{' '}
              <strong>0 이라는 뜻이 아닙니다</strong>.{' '}
              <TextLink to={`/policies/${selectedPolicy.id}/edit`}>정책 수정</TextLink> 에서{' '}
              <strong>분모 쿼리</strong>를 채우면 이 자리에 표시됩니다.
            </Notice>
          )}

          <div className="grid gap-6 lg:grid-cols-3">
            <Card
              title="시간대별 오류 건수"
              description={`${formatDateTime(overview.data.range_start)} ~ ${formatDateTime(
                overview.data.range_end,
              )} · ${overview.data.step_seconds}초 간격`}
              info={METRIC_NOTE}
              className={showIngestChart ? 'lg:col-span-2' : 'lg:col-span-3'}
            >
              <ErrorTrendChart points={overview.data.series} />
            </Card>

            {/*
              유입량은 오류와 **눈금이 다르다** (보통 두 자릿수 크다). 한 축에 겹치면 오류
              곡선이 바닥에 눌려 모양이 사라지므로 차트를 나누고 색으로만 묶는다.
            */}
            {showIngestChart && (
              <Card
                title="유입량 추이 (분모)"
                description="같은 기간·같은 간격입니다."
                info={
                  <>
                    유입량은 오류와 <strong>눈금이 다릅니다</strong> (보통 두 자릿수 큽니다). 한
                    축에 겹치면 오류 곡선이 바닥에 눌려 모양이 사라지므로 축을 나눴습니다.
                  </>
                }
              >
                <ErrorTrendChart
                  points={overview.data.ingest_series}
                  height={260}
                  label="유입 건수"
                  tone="series2"
                />
              </Card>
            )}
          </div>

          <div className="grid gap-6 lg:grid-cols-5">
            <Card
              title="서비스별 오류 건수"
              description="라벨 기준 분해입니다."
              info={
                <>
                  라인 수가 아니라 <Code>count_over_time</Code> <strong>metric 집계</strong>입니다.
                  같은 시각의 포인트는 시간대별 차트에서 합산되지만, 여기서는 서비스별로
                  나뉩니다.
                </>
              }
              className="lg:col-span-2"
            >
              <ServiceBarChart data={overview.data.by_service} />
            </Card>

            <Card
              title={`상위 오류 그룹 (${formatNumber(overview.data.top_groups.length)}개)`}
              description="발생 수 상위 그룹입니다. 누르면 대표 로그와 AI 분석으로 갑니다."
              info={
                <>
                  이 표는 <strong>상위 몇 개</strong>만 보여줍니다 — 회차 전체 그룹 수는 위의{' '}
                  <strong>오류 그룹</strong> 지표입니다.
                  <span className="mt-1.5 block">{FINGERPRINT_NOTE}</span>
                </>
              }
              className="lg:col-span-3"
            >
              {overview.data.top_groups.length === 0 ? (
                <EmptyBlock icon={ErrorGroupIcon}>
                  이 기간에 묶인 오류 그룹이 없습니다.
                </EmptyBlock>
              ) : (
                <TableWrap minWidth="36rem">
                  <thead>
                    <tr>
                      <Th>메시지 · 서비스</Th>
                      <Th align="right" className="whitespace-nowrap">
                        발생 수
                      </Th>
                      <Th className="whitespace-nowrap">마지막 발생</Th>
                      <Th className="whitespace-nowrap">분석 상태</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.data.top_groups.map((group) => (
                      <tr key={group.id} className="transition-colors hover:bg-surface-2">
                        <Td>
                          <TextLink
                            to={`/error-groups/${group.id}`}
                            title={group.normalized_message}
                          >
                            {truncate(group.normalized_message, 90)}
                          </TextLink>
                          <p className="mt-0.5 text-xs text-muted">
                            {group.service ?? '(서비스 라벨 없음)'}
                            {group.environment ? ` · ${group.environment}` : ''}
                            {group.error_type ? ` · ${group.error_type}` : ''}
                          </p>
                        </Td>
                        <Td align="right" className="font-semibold text-ink">
                          {formatNumber(group.count)}
                        </Td>
                        <Td className="whitespace-nowrap text-muted">
                          <span title={formatDateTime(group.last_seen)}>
                            {formatRelative(group.last_seen)}
                          </span>
                        </Td>
                        <Td className="whitespace-nowrap">
                          <div className="flex flex-col items-start gap-1">
                            <AnalysisStatusBadge status={group.analysis_status} />
                            {group.latest_severity && (
                              <SeverityBadge severity={group.latest_severity} />
                            )}
                          </div>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Card>
          </div>
        </PageStack>
      )}
    </div>
  );
}

/**
 * 보조 지표 타일.
 *
 * `Stat` 보다 한 단계 작다 — 파생 지표(유입량·비율·서비스 수)를 핵심 지표와 같은 크기로
 * 두면 화면에 큰 숫자가 여섯이 되고, 그 순간 위계가 사라진다. 색·간격 토큰은 `Stat` 과
 * 같은 것을 쓴다(새 슬롯을 만들지 않는다).
 */
function SubStat({
  label,
  value,
  note,
  info,
}: {
  label: string;
  value: ReactNode;
  /** 값의 출처를 알리는 **짧은** 한 줄. 긴 설명은 `info` 로 보낸다. */
  note?: string;
  info?: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-2.5">
      <span className="flex min-w-0 items-center gap-1 text-xs font-medium text-muted">
        <span className="min-w-0 truncate">{label}</span>
        {info && (
          <InfoTip label={`${label} 설명 보기`} title={label}>
            {info}
          </InfoTip>
        )}
      </span>
      <span className="flex shrink-0 items-baseline gap-2">
        {note && <span className="text-xs text-faint">{note}</span>}
        <span className="text-lg font-semibold text-ink tabular-nums">{value}</span>
      </span>
    </div>
  );
}

export function WarningList({ warnings }: { warnings: FetchWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <Notice tone="warning" title="조회 경고">
      <ul className="mt-1 space-y-1">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`}>
            <strong>{warningCodeLabel(warning.code)}</strong>
            {warning.count != null && ` (${formatNumber(warning.count)}건)`} — {warning.message}
          </li>
        ))}
      </ul>
    </Notice>
  );
}
