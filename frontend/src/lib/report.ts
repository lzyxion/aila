/**
 * Markdown 보고서 렌더러.
 *
 * 실제 계약상 보고서는 `GET /api/analysis-jobs/{id}/report` 가 **요청 시점에** 렌더링해
 * 반환한다. 여기 있는 렌더러는 mock 모드에서 같은 모양의 결과를 만들기 위한 것이고,
 * 라이브 모드에서는 서버가 준 Markdown 을 그대로 쓴다.
 *
 * 보고서에는 "LLM 이 생성한 원인 가설" 표기와 원본 로그로 돌아갈 링크를 반드시 포함한다 —
 * 화면 밖으로 나가도 사실 확정으로 읽히지 않게 하기 위해서다.
 */

import type { AnalysisJobRead, ErrorGroupDetail } from '../api/types';
import { formatDateTime, formatEstimatedCost, severityLabel } from './format';

export function renderReportMarkdown(job: AnalysisJobRead, group: ErrorGroupDetail): string {
  const lines: string[] = [];
  const push = (line = '') => lines.push(line);

  push(`# 오류 분석 보고서 — ${group.service ?? '알 수 없는 서비스'}`);
  push();
  push(
    '> **이 문서의 분석 부분은 LLM 이 생성한 원인 가설입니다.** 사실 확정이 아니며, ' +
      '확인 절차를 거쳐 검증해야 합니다.',
  );
  push();

  push('## 오류 그룹');
  push();
  push('| 항목 | 값 |');
  push('| --- | --- |');
  push(`| 서비스 | ${group.service ?? '-'} |`);
  push(`| 환경 | ${group.environment ?? '-'} |`);
  push(`| 예외 타입 | ${group.error_type ?? '-'} |`);
  push(`| 정규화 메시지 | \`${group.normalized_message.replace(/\|/g, '\\|')}\` |`);
  push(`| 발생 수 | ${group.count.toLocaleString('ko-KR')} 건 |`);
  push(`| 최초 발생 | ${formatDateTime(group.first_seen)} |`);
  push(`| 마지막 발생 | ${formatDateTime(group.last_seen)} |`);
  push(`| fingerprint | \`${group.fingerprint}\` |`);
  push(`| 정규화 규칙 버전 | ${group.normalization_rule_version} |`);
  if (group.top_stack_frame) push(`| 상위 스택 프레임 | \`${group.top_stack_frame}\` |`);
  push();

  if (Object.keys(group.labels).length > 0) {
    push('### 라벨');
    push();
    for (const [key, value] of Object.entries(group.labels)) {
      push(`- \`${key}\` = \`${value}\``);
    }
    push();
  }

  if (group.trend.length > 0) {
    const total = group.trend.reduce((acc, point) => acc + point.value, 0);
    const peak = group.trend.reduce((a, b) => (b.value > a.value ? b : a));
    push('### 발생 추이');
    push();
    push(
      `- metric 쿼리(\`count_over_time\`) 기준 합계 **${Math.round(total).toLocaleString('ko-KR')} 건**` +
        ' (로그 라인 수가 아닙니다)',
    );
    push(`- 최대 구간: ${formatDateTime(peak.timestamp)} — ${Math.round(peak.value)} 건`);
    push();
  }

  push('## 분석 실행 정보');
  push();
  push('| 항목 | 값 |');
  push('| --- | --- |');
  push(`| 분석 작업 ID | ${job.id} |`);
  push(`| 상태 | ${job.status} |`);
  push(`| 프로바이더 / 모델 | ${job.provider} / ${job.model} |`);
  push(`| 프롬프트 버전 | ${job.prompt_version} |`);
  push(`| 요청 시각 | ${formatDateTime(job.requested_at)} |`);
  push(`| 완료 시각 | ${job.completed_at ? formatDateTime(job.completed_at) : '-'} |`);
  if (job.usage) {
    push(
      `| 토큰 | 입력 ${job.usage.input_tokens.toLocaleString('ko-KR')} / 출력 ${job.usage.output_tokens.toLocaleString('ko-KR')} |`,
    );
    push(`| 추정 비용 | ${formatEstimatedCost(job.usage.estimated_cost)} (추정) |`);
    push(`| 응답 시간 | ${job.usage.latency_ms ? `${job.usage.latency_ms} ms` : '-'} |`);
  }
  push();

  const result = job.result;
  if (!result) {
    push('## 분석 결과');
    push();
    push(job.error_message ? `분석이 실패했습니다: ${job.error_message}` : '분석 결과가 없습니다.');
    push();
  } else {
    push('## LLM 이 생성한 원인 가설');
    push();
    push(`**요약(LLM 생성)** — ${result.summary}`);
    push();
    push(
      `**LLM 추정 심각도**: ${severityLabel(result.severity)} — 대표 로그 몇 건으로 추정한 값이며, ` +
        '발생량 기반 지표와는 다릅니다.',
    );
    push();

    push('### 원인 가설');
    push();
    result.hypotheses.forEach((hypothesis, index) => {
      push(`${index + 1}. **${hypothesis.cause}**`);
      push(`   - 가설 순위 힌트: ${hypothesis.confidence.toFixed(2)} (확률이 아닙니다)`);
      if (hypothesis.evidence.length > 0) {
        push(`   - 근거(마스킹된 로그 조각): ${hypothesis.evidence.map((e) => `\`${e}\``).join(', ')}`);
      }
    });
    push();

    if (result.investigation_steps.length > 0) {
      push('### 확인 절차');
      push();
      result.investigation_steps.forEach((step, index) => push(`${index + 1}. ${step}`));
      push();
    }

    if (result.mitigation.length > 0) {
      push('### 완화·대응 초안');
      push();
      result.mitigation.forEach((item) => push(`- ${item}`));
      push();
    }

    push('### 한계');
    push();
    result.limitations.forEach((item) => push(`- ${item}`));
    push();
  }

  if (group.samples.length > 0) {
    push('## 마스킹된 대표 로그');
    push();
    push(
      `민감정보는 마스킹 규칙 \`${group.samples[0].masking_rule_version}\` 로 치환된 상태입니다. ` +
        '마스킹 전 원본은 저장하지 않습니다.',
    );
    push();
    group.samples.forEach((sample) => {
      push(`- **${formatDateTime(sample.occurred_at)}**`);
      push();
      push('  ```');
      sample.masked_log.split('\n').forEach((line) => push(`  ${line}`));
      push('  ```');
      push();
    });
  }

  push('## 원본 로그로 돌아가기');
  push();
  push(
    '원본(마스킹 전) 로그는 저장하지 않습니다. 확인이 필요하면 아래 라벨과 시간 범위로 ' +
      'Loki 에서 직접 재조회하십시오.',
  );
  push();
  push('```logql');
  push(lokiSelector(group));
  push('```');
  push();
  push(`- 조회 구간: ${formatDateTime(group.first_seen)} ~ ${formatDateTime(group.last_seen)}`);
  push(`- 앱 내 링크: \`/error-groups/${group.id}\``);
  push();
  push('---');
  push();
  push(
    `생성 시각 ${formatDateTime(new Date().toISOString())} · AILA · ` +
      '비용은 계산 시점 단가표 기준 **추정**값이며 정산 근거가 아닙니다.',
  );

  return lines.join('\n');
}

/** 그룹 라벨로 원본 재조회용 LogQL selector 를 만든다. */
export function lokiSelector(group: ErrorGroupDetail): string {
  const keys = ['service', 'environment', 'level', 'release', 'pod'];
  const parts = keys
    .filter((key) => group.labels[key])
    .map((key) => `${key}="${group.labels[key]}"`);
  if (parts.length === 0) {
    return `{service="${group.service ?? 'unknown'}"}`;
  }
  return `{${parts.join(', ')}}`;
}

export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // 보안 컨텍스트가 아닌 경우의 폴백.
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}
