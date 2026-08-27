/**
 * 오류 그룹 상세 — 마스킹된 대표 로그와 AI 분석.
 *
 * 이 화면에는 두 종류의 글이 섞여 있다. 하나는 **계약 문구**(마스킹 규칙 버전, metric 기준,
 * fingerprint 기준)이고 다른 하나는 **LLM 이 생성한 가설**이다. Phase 8 밀도 정리에서
 * 앞쪽은 ⓘ(`InfoTip`) 로 옮겼지만, 뒤쪽 — 가설·근거·한계와 "확정이 아니다"라는 경고 — 는
 * 문구도 자리도 그대로 두고 **타이포·간격만** 손봤다. 환각 경고를 툴팁에 숨기면 그건 밀도
 * 정리가 아니라 위험을 감추는 일이다.
 */

import { useEffect, useState } from 'react';
import { useParams } from 'react-router';

import { analysisJobs as analysisJobsApi } from '../api/endpoints';
import {
  useAnalysisJobWithRefresh,
  useErrorGroup,
  useLlmConnections,
  useStartAnalysis,
} from '../api/queries';
import type { AnalysisJobRead, ErrorGroupDetail, ErrorSampleRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { ErrorTrendChart } from '../components/chartsLazy';
import {
  CopyIcon,
  DashboardIcon,
  EmptyIcon,
  ExportIcon,
  GroupCountIcon,
  ReportIcon,
  RunIcon,
  TimeIcon,
} from '../components/icons';
import {
  AnalysisStatusBadge,
  SeverityBadge,
  TriggeredByBadge,
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
  LoadingBlock,
  LogLine,
  Notice,
  PageHeader,
  PageStack,
  Select,
  SkeletonCard,
  SkeletonStats,
  Spinner,
  Stat,
  TableWrap,
  Td,
  Th,
  cx,
  toneClass,
} from '../components/ui';
import {
  formatDateTime,
  formatDuration,
  formatEstimatedCost,
  formatNumber,
  formatRelative,
  formatTokens,
  jobStatusLabel,
} from '../lib/format';
import { copyToClipboard, downloadMarkdown, lokiSelector, renderReportMarkdown } from '../lib/report';

export function ErrorGroupDetailPage() {
  const params = useParams<{ groupId: string }>();
  const groupId = params.groupId ? Number(params.groupId) : null;

  const write = useWriteAccess();
  const groupQuery = useErrorGroup(groupId);
  const connectionsQuery = useLlmConnections();
  const startAnalysis = useStartAnalysis(groupId ?? 0);

  const [selectedConnectionId, setSelectedConnectionId] = useState<number | ''>('');
  const [activeJobId, setActiveJobId] = useState<number | null>(null);

  const group = groupQuery.data;

  // 기존 분석이 있으면 그 결과를 먼저 보여준다 (fingerprint 기준으로 조인되어 온다).
  useEffect(() => {
    if (activeJobId === null && group?.latest_analysis_job_id) {
      setActiveJobId(group.latest_analysis_job_id);
    }
  }, [activeJobId, group?.latest_analysis_job_id]);

  /**
   * 폴링이 끝나면 그룹 상세도 다시 읽는다.
   *
   * "분석 이력"은 작업 단건이 아니라 **그룹 상세의 `analyses`** 에서 온다. 작업이 끝나도
   * 그 캐시를 아무도 무효화하지 않으면 결과는 표시되는데 이력의 배지만 `분석 중` 스피너로
   * 남는다 — Phase 4 피드백 4번의 무한 로딩이 이것이었다.
   */
  const jobQuery = useAnalysisJobWithRefresh(activeJobId, groupId);

  const connections = (connectionsQuery.data ?? []).filter((connection) => connection.active);
  const defaultConnection = connections.find((connection) => connection.is_default) ?? null;

  if (groupId === null) return <ErrorBlock error="잘못된 그룹 id 입니다." />;
  if (groupQuery.isPending) {
    // 이 화면의 뼈대(요약 4칸 + 카드)는 정해져 있다 — 스켈레톤이 거짓 개수를 말하지 않는다.
    return (
      <PageStack>
        <SkeletonStats count={4} label="오류 그룹을 불러오는 중" />
        <SkeletonCard lines={4} label="대표 로그를 불러오는 중" />
      </PageStack>
    );
  }
  if (groupQuery.isError) return <ErrorBlock error={groupQuery.error} />;
  if (!group) return <EmptyBlock icon={EmptyIcon}>오류 그룹을 찾을 수 없습니다.</EmptyBlock>;

  const trendTotal = group.trend.reduce((acc, point) => acc + point.value, 0);

  return (
    <div>
      <PageHeader
        title={group.error_type ?? '오류 그룹'}
        description={
          <>
            <span className="font-mono text-ink-soft">{group.normalized_message}</span>
            <span className="mt-1 block text-xs text-muted">
              {group.service ?? '(서비스 라벨 없음)'}
              {group.environment ? ` · ${group.environment}` : ''} · fingerprint{' '}
              <Code>{group.fingerprint}</Code>
            </span>
          </>
        }
        info={
          <>
            같은 오류의 판정 기준은 그룹 id 가 아니라 <strong>fingerprint</strong> 입니다 — 조회
            회차가 달라도 같은 fingerprint 면 같은 오류로 봅니다.
            <span className="mt-1.5 block">
              마스킹 전 원본 로그는 <strong>저장하지 않습니다</strong>. 이 화면에 보이는 로그도,
              LLM 으로 나가는 로그도 모두 마스킹된 값입니다.
            </span>
          </>
        }
        actions={
          <ButtonLink to="/">
            <DashboardIcon aria-hidden className="size-4" />
            대시보드
          </ButtonLink>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="발생 수"
          icon={GroupCountIcon}
          value={formatNumber(group.count)}
          sub="이번 조회 기준"
          info={
            <>
              이 그룹으로 묶인 <strong>로그 라인 수</strong>입니다 (조회 회차 기준). 옆의 추이
              합계와 출처가 달라 값이 다를 수 있습니다.
            </>
          }
          tone="accent"
        />
        <Stat
          label="추이 합계"
          icon={ReportIcon}
          value={formatNumber(trendTotal)}
          sub="metric 기준"
          info={
            <>
              <Code>count_over_time</Code> metric 쿼리 결과의 합이라{' '}
              <strong>로그 라인 수가 아닙니다</strong>.
              <span className="mt-1.5 block">
                metric 이 실패한 구간은 0 이 아니라 <strong>없음</strong>이며, 그 경우 아래 차트
                자리에 사유가 대신 나옵니다.
              </span>
            </>
          }
        />
        <Stat
          label="최초 발생"
          icon={TimeIcon}
          value={<span className="text-lg">{formatRelative(group.first_seen)}</span>}
          sub={formatDateTime(group.first_seen)}
        />
        <Stat
          label="마지막 발생"
          icon={TimeIcon}
          value={<span className="text-lg">{formatRelative(group.last_seen)}</span>}
          sub={formatDateTime(group.last_seen)}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <Card
            title="발생 추이"
            description="이 fingerprint 의 시간대별 건수입니다."
            info={
              <>
                <Code>count_over_time</Code> metric 쿼리 기준이라{' '}
                <strong>로그 라인 수가 아닙니다</strong>. 실패한 구간은 0 이 아니라{' '}
                <strong>없음</strong>입니다.
              </>
            }
          >
            {/* 빈 차트만 보여주면 "발생이 없었다"와 "조회하지 못했다"가 구분되지 않는다. */}
            {group.trend.length === 0 && (group.trend_warnings?.length ?? 0) > 0 ? (
              <EmptyBlock>
                추이를 계산하지 못했습니다 —{' '}
                {group.trend_warnings!.map((warning) => warning.message).join(' ')}
              </EmptyBlock>
            ) : (
              <ErrorTrendChart points={group.trend} height={220} />
            )}
          </Card>

          <Card
            title="마스킹된 대표 로그"
            description="화면 표시 전에 마스킹이 적용된 값입니다."
            info={
              <>
                마스킹 규칙 <Code>{group.samples[0]?.masking_rule_version ?? 'v1'}</Code> · 정규화
                규칙 <Code>{group.normalization_rule_version}</Code> 을 적용했습니다.
                <span className="mt-1.5 block">
                  마스킹 전 원본은 <strong>저장하지 않습니다</strong> — 원본이 필요하면 아래{' '}
                  <strong>원본 로그로 돌아가기</strong> 의 셀렉터로 Loki 에서 직접 재조회합니다.
                </span>
              </>
            }
          >
            {group.samples.length === 0 ? (
              <EmptyBlock icon={EmptyIcon}>저장된 대표 로그가 없습니다.</EmptyBlock>
            ) : (
              <div className="space-y-4">
                {group.samples.map((sample) => (
                  <SampleBlock key={sample.id} sample={sample} />
                ))}
              </div>
            )}
          </Card>

          <OriginalLogCard group={group} />
        </div>

        <div className="space-y-6 xl:col-span-2">
          <Card title="라벨">
            {Object.keys(group.labels).length === 0 ? (
              <EmptyBlock icon={EmptyIcon}>라벨이 없습니다.</EmptyBlock>
            ) : (
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                {Object.entries(group.labels).map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="font-mono text-xs text-muted">{key}</dt>
                    <dd className="font-mono text-xs break-all text-ink-soft">{value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {group.top_stack_frame && (
              <div className="mt-4 border-t border-line-soft pt-4">
                <p className="text-xs font-medium text-muted">상위 스택 프레임 (fingerprint 재료)</p>
                <p className="mt-1 font-mono text-xs break-all text-ink-soft">
                  {group.top_stack_frame}
                </p>
              </div>
            )}
          </Card>

          <Card
            title="AI 분석"
            description="수동 트리거만 존재합니다. 비용이 발생하는 요청입니다."
            info={
              <>
                실행하면 <strong>마스킹된 대표 로그·추이·정책 정보만</strong> LLM 으로 나갑니다 —
                마스킹 전 원본이 나가는 경로는 없습니다.
                <span className="mt-1.5 block">
                  같은 fingerprint 로 이미 진행 중인 분석이 있으면 새 작업을 만들지 않고 그 작업을
                  이어서 봅니다. 일일 한도를 넘기면 서버가 <Code>429</Code> 로 거절합니다.
                </span>
              </>
            }
          >
            <div className="space-y-4">
              {!write.allowed && (
                <Notice tone="neutral" title="권한 없음">
                  {write.reason} 아래 분석 결과와 이력은 그대로 볼 수 있습니다.
                </Notice>
              )}

              <Field
                label="사용할 LLM 연결"
                hint={
                  defaultConnection
                    ? `비워 두면 기본 연결(${defaultConnection.name})을 씁니다.`
                    : '기본 연결이 지정되어 있지 않습니다.'
                }
              >
                <Select
                  disabled={!write.allowed}
                  value={selectedConnectionId}
                  onChange={(event) =>
                    setSelectedConnectionId(
                      event.target.value === '' ? '' : Number(event.target.value),
                    )
                  }
                >
                  <option value="">
                    {defaultConnection ? `기본 연결 · ${defaultConnection.name}` : '기본 연결'}
                  </option>
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name} ({connection.model})
                    </option>
                  ))}
                </Select>
              </Field>

              <Button
                variant="primary"
                className="w-full"
                disabled={!write.allowed || startAnalysis.isPending || isJobActive(jobQuery.data)}
                title={
                  write.reason ??
                  '마스킹된 대표 로그만 LLM 으로 나갑니다. 비용이 발생하는 요청입니다.'
                }
                onClick={() =>
                  startAnalysis.mutate(
                    {
                      llm_connection_id:
                        selectedConnectionId === '' ? null : Number(selectedConnectionId),
                    },
                    { onSuccess: (job) => setActiveJobId(job.id) },
                  )
                }
              >
                {startAnalysis.isPending ? (
                  <>
                    <Spinner className="size-4 border-sky-200 border-t-white" />
                    요청 중…
                  </>
                ) : isJobActive(jobQuery.data) ? (
                  jobStatusLabel(jobQuery.data?.status)
                ) : (
                  <>
                    <RunIcon aria-hidden className="size-4" />
                    AI 분석 실행
                  </>
                )}
              </Button>

              {startAnalysis.data?.reused && (
                <Notice tone="info">
                  이미 진행 중인 분석이 있어 새 작업을 만들지 않고 기존 작업 #
                  {startAnalysis.data.id} 을(를) 이어서 보고 있습니다.
                </Notice>
              )}
              {startAnalysis.isError && <ErrorBlock error={startAnalysis.error} />}

              {jobQuery.data && <JobProgress job={jobQuery.data} />}
            </div>
          </Card>

          <PastAnalysesCard
            group={group}
            onSelect={setActiveJobId}
            activeJobId={activeJobId}
            liveJob={jobQuery.data}
            refreshing={groupQuery.isFetching}
          />
        </div>
      </div>

      <div className="mt-6">
        {jobQuery.isError && <ErrorBlock error={jobQuery.error} />}
        {jobQuery.data && <AnalysisResultCard job={jobQuery.data} group={group} />}
        {/*
          비활성 쿼리(선택된 작업 없음)도 status 는 'pending' 이라 isPending 으로 갈라내면
          "결과 없음" 안내가 영영 나오지 않는다. 실제로 요청 중인지는 isLoading 으로 본다.
        */}
        {!jobQuery.data && activeJobId !== null && jobQuery.isLoading && (
          <Card title="LLM 분석 결과">
            <LoadingBlock label="분석 작업을 불러오는 중…" />
          </Card>
        )}
        {!jobQuery.data && !jobQuery.isError && (activeJobId === null || !jobQuery.isLoading) && (
          <Card title="LLM 분석 결과">
            <EmptyBlock icon={ReportIcon}>
              {write.allowed ? (
                <>
                  아직 이 오류 그룹에 대한 분석 결과가 없습니다. 오른쪽에서{' '}
                  <strong>AI 분석 실행</strong>을 누르십시오.
                </>
              ) : (
                <>
                  아직 이 오류 그룹에 대한 분석 결과가 없습니다. 분석 실행은 admin 계정만 할 수
                  있습니다.
                </>
              )}
            </EmptyBlock>
          </Card>
        )}
      </div>
    </div>
  );
}

function isJobActive(job: AnalysisJobRead | undefined): boolean {
  return job?.status === 'pending' || job?.status === 'running';
}

// ------------------------------------------------------------------ 대표 로그

function SampleBlock({ sample }: { sample: ErrorSampleRead }) {
  const [showStack, setShowStack] = useState(false);
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted tabular-nums">{formatDateTime(sample.occurred_at)}</span>
        <div className="flex items-center gap-2">
          <Badge tone="success" title="화면 표시 전에 마스킹이 적용되었습니다.">
            마스킹됨 · {sample.masking_rule_version}
          </Badge>
          {sample.stacktrace && (
            <Button size="sm" variant="ghost" onClick={() => setShowStack((prev) => !prev)}>
              {showStack ? '스택트레이스 접기' : '스택트레이스'}
            </Button>
          )}
        </div>
      </div>
      <LogLine>{sample.masked_log}</LogLine>
      {showStack && sample.stacktrace && (
        <pre className="aila-scroll mt-2 overflow-x-auto rounded-lg border border-line bg-surface-2 px-3.5 py-3 text-xs leading-relaxed whitespace-pre text-ink-soft">
          {sample.stacktrace}
        </pre>
      )}
    </div>
  );
}

// ------------------------------------------------------------ 원본 로그 링크

function OriginalLogCard({ group }: { group: ErrorGroupDetail }) {
  const [copied, setCopied] = useState(false);
  const selector = lokiSelector(group);

  return (
    <Card
      title="원본 로그로 돌아가기"
      description="아래 셀렉터와 시간 범위로 Loki 에서 직접 재조회하십시오."
      info={
        <>
          마스킹 전 원본은 <strong>저장하지 않습니다</strong> — 이 도구는 Loki 를 읽기만 하므로,
          원본이 필요하면 Loki(또는 Grafana)에서 같은 조건으로 다시 조회하는 것이 유일한 방법입니다.
        </>
      }
      actions={
        <Button
          size="sm"
          onClick={() => {
            void copyToClipboard(selector).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            });
          }}
        >
          <CopyIcon aria-hidden className="size-3.5" />
          {copied ? '복사됨' : 'LogQL 복사'}
        </Button>
      }
    >
      <LogLine>{selector}</LogLine>
      <p className="mt-2 text-xs text-muted tabular-nums">
        조회 구간 {formatDateTime(group.first_seen)} ~ {formatDateTime(group.last_seen)}
      </p>
    </Card>
  );
}

// ------------------------------------------------------------------ 진행 상태

function JobProgress({ job }: { job: AnalysisJobRead }) {
  const active = isJobActive(job);
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <AnalysisStatusBadge status={job.status} />
        <span className="text-xs text-faint tabular-nums">작업 #{job.id}</span>
      </div>
      <dl className="mt-2 space-y-1 text-xs text-muted">
        <div className="flex justify-between gap-2">
          <dt>모델</dt>
          <dd className="font-mono text-ink-soft">{job.model}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>프롬프트 버전</dt>
          <dd className="font-mono text-ink-soft">{job.prompt_version}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>요청</dt>
          <dd className="tabular-nums">{formatDateTime(job.requested_at)}</dd>
        </div>
      </dl>
      {active && (
        <p className="mt-2 text-xs text-muted">
          2 초 간격으로 상태를 확인하고 있습니다. LLM 호출은 수 초에서 수십 초가 걸립니다.
        </p>
      )}
      {job.status === 'failed' && job.error_message && (
        <p className="mt-2 text-xs text-rose-700 dark:text-rose-300">{job.error_message}</p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 과거 이력

function PastAnalysesCard({
  group,
  onSelect,
  activeJobId,
  liveJob,
  refreshing,
}: {
  group: ErrorGroupDetail;
  onSelect: (id: number) => void;
  activeJobId: number | null;
  /** 폴링 중인 작업. 이력 항목과 같은 작업이면 **폴링 값이 이긴다**. */
  liveJob: AnalysisJobRead | undefined;
  refreshing: boolean;
}) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          같은 오류의 분석 이력
          {/* 갱신 중이라는 사실은 배지가 아니라 여기서만 알린다 — 이력 항목의 스피너는
              "그 작업이 아직 진행 중"이라는 뜻이어야 한다. */}
          {refreshing && <Spinner className="size-3.5" />}
        </span>
      }
      description="조회 회차를 넘어 fingerprint 로 조인된 이력입니다."
      info={
        <>
          다른 조회 회차에서 실행한 분석도 같은 fingerprint 면 여기 나옵니다. 항목을 누르면 아래
          결과 카드가 그 작업으로 바뀝니다.
          <span className="mt-1.5 block">
            <strong>실패한 분석도 "이력 있음"</strong>입니다 — 그래서 미분석 지표에서는 빠집니다.
          </span>
        </>
      }
    >
      {group.analyses.length === 0 ? (
        <EmptyBlock icon={ReportIcon}>이 fingerprint 로 실행된 분석이 아직 없습니다.</EmptyBlock>
      ) : (
        <ul className="divide-y divide-line-soft">
          {group.analyses.map((analysis) => {
            // 그룹 상세는 작업보다 늦게 갱신될 수 있다. 같은 작업이면 폴링으로 받은
            // 최신 상태를 쓴다 — 완료된 작업이 이력에서 "분석 중"으로 남지 않게.
            const live = liveJob && liveJob.id === analysis.id ? liveJob : null;
            const status = live?.status ?? analysis.status;
            const severity = live?.result?.severity ?? analysis.severity;
            const summary = live?.result?.summary ?? analysis.summary;
            const selected = activeJobId === analysis.id;
            return (
              <li key={analysis.id} className="py-2.5 first:pt-0 last:pb-0">
                <button
                  type="button"
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => onSelect(analysis.id)}
                  className={cx(
                    'w-full rounded-lg px-2 py-1.5 text-left transition-colors',
                    selected
                      ? 'bg-accent-soft ring-1 ring-inset ring-line-strong'
                      : 'hover:bg-surface-2',
                  )}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <AnalysisStatusBadge status={status} />
                    <SeverityBadge severity={severity} />
                    {/* 스케줄의 자동 분석과 사람이 누른 분석을 이력에서 구분한다. */}
                    <TriggeredByBadge value={live?.triggered_by ?? analysis.triggered_by} />
                  </div>
                  <p className="mt-1.5 text-xs text-muted">
                    {analysis.provider} · <span className="font-mono">{analysis.model}</span> ·{' '}
                    <span className="tabular-nums">{formatDateTime(analysis.requested_at)}</span>
                  </p>
                  {summary && (
                    <p className="mt-1 text-xs leading-relaxed text-ink-soft">{summary}</p>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ 분석 결과

/**
 * LLM 분석 결과.
 *
 * **여기 있는 문구는 Phase 8 에서 한 글자도 바꾸지 않았다.** 가설이 여럿이라는 사실,
 * confidence 가 확률이 아니라는 사실, 한계 목록 — 전부 단정을 막으려고 있는 글이라
 * 요약하거나 툴팁으로 접으면 그 목적이 사라진다. 바꾼 것은 위계(제목·간격·행간)뿐이다.
 */
function AnalysisResultCard({ job, group }: { job: AnalysisJobRead; group: ErrorGroupDetail }) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [exportError, setExportError] = useState<string | null>(null);

  /**
   * 보고서는 서버가 요청 시점에 렌더링해 준다. 서버가 아직 구현하지 않았으면(501)
   * 화면이 가진 것과 같은 데이터로 클라이언트에서 조립한다 — 내용은 동일하게 마스킹된 값뿐이다.
   */
  async function buildMarkdown(): Promise<string> {
    try {
      return await analysisJobsApi.report(job.id);
    } catch {
      return renderReportMarkdown(job, group);
    }
  }

  if (isJobActive(job)) {
    return (
      <Card title="LLM 분석 결과">
        <LoadingBlock label={`${jobStatusLabel(job.status)} — 완료되면 여기에 표시됩니다.`} />
      </Card>
    );
  }

  if (job.status === 'failed') {
    return (
      <Card title="LLM 분석 결과">
        <Notice tone="danger" title="분석이 실패했습니다">
          {job.error_message ?? '실패 사유가 기록되지 않았습니다.'}
        </Notice>
        {job.usage && <UsageRow job={job} />}
      </Card>
    );
  }

  const result = job.result;
  if (!result) {
    return (
      <Card title="LLM 분석 결과">
        <EmptyBlock icon={EmptyIcon}>결과가 비어 있습니다.</EmptyBlock>
      </Card>
    );
  }

  return (
    <Card
      title="LLM 분석 결과"
      description="아래 내용은 사실 확정이 아니라 LLM 이 생성한 원인 가설입니다. 반드시 확인 절차로 검증하십시오."
      actions={
        <>
          <Button
            size="sm"
            onClick={() => {
              setExportError(null);
              void buildMarkdown()
                .then((markdown) =>
                  downloadMarkdown(`aila-report-${job.id}-${group.fingerprint}.md`, markdown),
                )
                .catch((error: unknown) =>
                  setExportError(error instanceof Error ? error.message : String(error)),
                );
            }}
          >
            <ExportIcon aria-hidden className="size-3.5" />
            Markdown 다운로드
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setExportError(null);
              void buildMarkdown()
                .then((markdown) => copyToClipboard(markdown))
                .then(() => {
                  setCopyState('copied');
                  setTimeout(() => setCopyState('idle'), 2000);
                })
                .catch(() => setCopyState('failed'));
            }}
          >
            <CopyIcon aria-hidden className="size-3.5" />
            {copyState === 'copied'
              ? '복사됨'
              : copyState === 'failed'
                ? '복사 실패'
                : '클립보드 복사'}
          </Button>
        </>
      }
    >
      {exportError && (
        <div className="mb-4">
          <Notice tone="danger">보고서를 만들지 못했습니다: {exportError}</Notice>
        </div>
      )}

      {/* 요약 블록 — 색조는 `toneClass` 를 통해서만 칠한다(다크 짝이 한 곳에 있다). */}
      <div className={cx('rounded-lg px-4 py-3 ring-1 ring-inset', toneClass('accent'))}>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="accent">LLM 생성</Badge>
          <SeverityBadge severity={result.severity} />
        </div>
        <p className="mt-2.5 text-sm leading-relaxed font-medium">{result.summary}</p>
        <p className="mt-2 text-xs leading-relaxed opacity-90">
          심각도는 대표 로그 몇 건으로 추정한 값이며, 발생 수({formatNumber(group.count)}건) 같은
          발생량 기반 지표와는 다릅니다.
        </p>
      </div>

      <section className="mt-6">
        <h3 className="text-sm font-semibold text-ink">원인 가설</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          여러 가설이 나오는 것이 정상입니다 — 스키마가 단정을 막습니다. 아래 숫자는 정렬용
          힌트이며 확률이 아닙니다.
        </p>
        <ol className="mt-3 space-y-3">
          {result.hypotheses.map((hypothesis, index) => (
            <li key={index} className="rounded-lg border border-line bg-surface-2 px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-sm leading-relaxed font-medium text-ink">
                  <span className="mr-1.5 text-muted tabular-nums">{index + 1}.</span>
                  {hypothesis.cause}
                </p>
                <Badge tone="neutral" title="정렬용 힌트입니다. 확률로 읽지 마십시오.">
                  가설 순위 힌트 {hypothesis.confidence.toFixed(2)}
                </Badge>
              </div>
              {/* 칩 크기는 컨테이너가 정한다 — `Code` 는 부모의 0.9em 이라 칩마다 글자
                  크기를 또 주면 두 지정이 싸운다. */}
              {hypothesis.evidence.length > 0 && (
                <div className="mt-2.5 flex flex-wrap gap-1.5 text-sm">
                  {hypothesis.evidence.map((evidence, evidenceIndex) => (
                    <Code key={evidenceIndex}>{evidence}</Code>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <ListSection title="확인 절차" items={result.investigation_steps} ordered />
        <ListSection title="완화·대응 초안" items={result.mitigation} />
      </div>

      <section className="mt-6">
        <h3 className="text-sm font-semibold text-ink">한계</h3>
        <Notice tone="warning" className="mt-2">
          <ul className="list-disc space-y-1.5 pl-4 leading-relaxed">
            {result.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </Notice>
      </section>

      {job.usage && <UsageRow job={job} />}
    </Card>
  );
}

function ListSection({
  title,
  items,
  ordered = false,
}: {
  title: string;
  items: string[];
  ordered?: boolean;
}) {
  if (items.length === 0) return null;
  const ListTag = ordered ? 'ol' : 'ul';
  return (
    <section>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <ListTag
        className={cx(
          'mt-2 space-y-2 pl-5 text-sm leading-relaxed text-ink-soft',
          ordered ? 'list-decimal' : 'list-disc',
        )}
      >
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ListTag>
    </section>
  );
}

function UsageRow({ job }: { job: AnalysisJobRead }) {
  if (!job.usage) return null;
  return (
    <div className="mt-6 border-t border-line-soft pt-4">
      {/* 비용 관련 고지는 툴팁에 숨기지 않는다 — 본문에 그대로 둔다. */}
      <p className="mb-2 text-xs text-muted">
        비용은 계산 시점 단가표 기준 <strong>추정</strong>값입니다. 정산 근거가 아닙니다.
      </p>
      <TableWrap>
        <thead>
          <tr>
            <Th>프로바이더 · 모델</Th>
            <Th align="right">입력 토큰</Th>
            <Th align="right">출력 토큰</Th>
            <Th align="right">추정 비용</Th>
            <Th align="right">응답 시간</Th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <Td>
              {job.usage.provider} · <span className="font-mono text-xs">{job.usage.model}</span>
            </Td>
            <Td align="right">{formatTokens(job.usage.input_tokens)}</Td>
            <Td align="right">{formatTokens(job.usage.output_tokens)}</Td>
            <Td align="right">{formatEstimatedCost(job.usage.estimated_cost)} (추정)</Td>
            <Td align="right">{formatDuration(job.usage.latency_ms)}</Td>
          </tr>
        </tbody>
      </TableWrap>
    </div>
  );
}
