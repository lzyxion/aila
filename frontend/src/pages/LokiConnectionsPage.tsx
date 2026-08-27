/**
 * Loki 연결 (`/admin/loki-connections`) — **좌측 목록 + 우측 상세·수정**.
 *
 * 이 화면이 생긴 이유는 Phase 7 의 `expected_services` 다. 연결마다 "이 서비스들은 로그를
 * 내고 있어야 정상"이라고 적어 두면, 조회 실행이 그 목록과 실제 관측을 대조해 **수집 중단
 * 의심**(`ingest_absent`)을 회차 경고로 남긴다. 그전까지 Loki 연결은 정책 편집 화면의
 * 드롭다운에서만 고를 수 있었고, 만들거나 고칠 자리는 없었다.
 *
 * 표기 규칙(계약):
 * - 저장된 secret 은 어떤 응답에도 평문으로 오지 않는다 — `has_secret`(있음/없음)만 온다.
 * - 비활성화는 **삭제가 아니다.** 정책과 조회 이력이 이 연결을 참조한다.
 * - 수집 중단 확인은 **경고 기록일 뿐**이다 — 여기 서비스를 적어도 알림이 가거나 무언가가
 *   자동으로 실행되지 않는다 (자동 트리거는 정책의 `auto_analyze_new` 하나뿐이다).
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router';

import {
  useCreateLokiConnection,
  useDeactivateLokiConnection,
  useLokiConnections,
  useTestLokiConnection,
  useUpdateLokiConnection,
} from '../api/queries';
import { AUTH_TYPES, type AuthType, type LokiConnectionRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import {
  Badge,
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  LoadingBlock,
  Notice,
  Select,
  Textarea,
  cx,
} from '../components/ui';
import { authTypeLabel, formatDateTime } from '../lib/format';

interface FormState {
  name: string;
  base_url: string;
  auth_type: AuthType;
  /** 평문 입력 전용. 비워 두면 기존 값을 유지한다 (응답에는 절대 오지 않는다). */
  secret: string;
  /** `소스라벨=표준필드` 한 줄에 하나. */
  labelMappingText: string;
  /** 쉼표(또는 줄바꿈) 구분. 표준 필드 `service` 기준이다. */
  expectedServicesText: string;
}

const EMPTY_FORM: FormState = {
  name: '',
  base_url: '',
  auth_type: 'none',
  secret: '',
  labelMappingText: 'app=service\nenv=environment',
  expectedServicesText: '',
};

function toForm(connection: LokiConnectionRead): FormState {
  return {
    name: connection.name,
    base_url: connection.base_url,
    auth_type: connection.auth_type,
    secret: '',
    labelMappingText: Object.entries(connection.label_mapping ?? {})
      .map(([source, standard]) => `${source}=${standard}`)
      .join('\n'),
    expectedServicesText: (connection.expected_services ?? []).join(', '),
  };
}

/** `a=b` 줄들을 매핑으로. 형식이 어긋난 줄은 저장 전에 오류로 알린다. */
function parseLabelMapping(text: string): Record<string, string> | string {
  const mapping: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const index = trimmed.indexOf('=');
    if (index <= 0 || index === trimmed.length - 1) {
      return `라벨 매핑은 "소스라벨=표준필드" 형식이어야 합니다: ${trimmed}`;
    }
    mapping[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1).trim();
  }
  return mapping;
}

/**
 * 쉼표·줄바꿈 구분 입력을 목록으로. 중복은 접고 순서는 입력 순서를 지킨다.
 *
 * 빈 입력은 **빈 배열**이다 — 계약상 빈 배열이 "수집 중단 확인을 끈다"는 뜻이고,
 * 그 상태를 화면에서 만들 수 없으면 한 번 켠 확인을 끌 방법이 없어진다.
 */
function parseServices(text: string): string[] {
  const seen = new Set<string>();
  const rows: string[] = [];
  for (const token of text.split(/[,\n]/)) {
    const value = token.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    rows.push(value);
  }
  return rows;
}

type Selection = number | null;

export function LokiConnectionsPage() {
  const write = useWriteAccess();
  const connectionsQuery = useLokiConnections();
  const createConnection = useCreateLokiConnection();
  const updateConnection = useUpdateLokiConnection();
  const deactivateConnection = useDeactivateLokiConnection();
  const testConnection = useTestLokiConnection();

  const connections = connectionsQuery.data ?? [];
  const [selected, setSelected] = useState<Selection>(null);
  const [initialised, setInitialised] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const editing = connections.find((connection) => connection.id === selected) ?? null;
  const isEditing = editing !== null;
  const saving = createConnection.isPending || updateConnection.isPending;
  const needsSecret = form.auth_type !== 'none';

  // 목록이 처음 오면 첫 활성 연결을 열어 둔다 — 한 번만 한다(사용자 선택을 되돌리지 않게).
  useEffect(() => {
    if (initialised || connections.length === 0) return;
    setInitialised(true);
    openConnection(connections.find((connection) => connection.active) ?? connections[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connections, initialised]);

  function startCreate() {
    setSelected(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    testConnection.reset();
  }

  function openConnection(connection: LokiConnectionRead) {
    setSelected(connection.id);
    setForm(toForm(connection));
    setFormError(null);
    testConnection.reset();
  }

  function handleSubmit() {
    if (!form.name.trim()) {
      setFormError('연결 이름을 입력하십시오.');
      return;
    }
    if (!/^https?:\/\//.test(form.base_url.trim())) {
      setFormError('base URL 은 http(s):// 로 시작해야 합니다.');
      return;
    }
    const mapping = parseLabelMapping(form.labelMappingText);
    if (typeof mapping === 'string') {
      setFormError(mapping);
      return;
    }
    if (!isEditing && needsSecret && !form.secret.trim()) {
      setFormError('선택한 인증 방식에는 secret 이 필요합니다.');
      return;
    }
    setFormError(null);

    const expected = parseServices(form.expectedServicesText);

    if (isEditing) {
      updateConnection.mutate(
        {
          id: editing.id,
          payload: {
            name: form.name.trim(),
            base_url: form.base_url.trim(),
            auth_type: form.auth_type,
            label_mapping: mapping,
            // 빈 배열은 "확인을 끈다"는 명시적 값이라 그대로 보낸다.
            expected_services: expected,
            // secret 을 비워 두면 필드를 아예 보내지 않는다 — 기존 값을 지우지 않기 위해서다.
            ...(form.secret.trim() ? { secret: form.secret.trim() } : {}),
          },
        },
        { onSuccess: (updated) => openConnection(updated) },
      );
    } else {
      createConnection.mutate(
        {
          name: form.name.trim(),
          source_type: 'loki',
          base_url: form.base_url.trim(),
          auth_type: form.auth_type,
          label_mapping: mapping,
          active: true,
          expected_services: expected,
          secret: form.secret.trim() || null,
        },
        { onSuccess: (created) => openConnection(created) },
      );
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-5">
      {/* ------------------------------------------------------------ 목록 */}
      <div className="xl:col-span-2">
        <Card
          title="Loki 연결"
          description="하나를 눌러 오른쪽에서 상세를 보고 고칩니다. 비활성화는 실제 삭제가 아닙니다 — 정책과 조회 이력이 참조합니다."
          actions={
            write.allowed && (
              <Button
                size="sm"
                variant={selected === null ? 'primary' : 'secondary'}
                onClick={startCreate}
              >
                새 연결
              </Button>
            )
          }
        >
          {connectionsQuery.isPending && <LoadingBlock />}
          {connectionsQuery.isError && <ErrorBlock error={connectionsQuery.error} />}
          {connectionsQuery.data?.length === 0 && (
            <EmptyBlock>등록된 Loki 연결이 없습니다. 먼저 하나를 만드십시오.</EmptyBlock>
          )}

          {connections.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {connections.map((connection) => {
                const active = selected === connection.id;
                const expected = connection.expected_services ?? [];
                return (
                  <li key={connection.id} className="py-2 first:pt-0 last:pb-0">
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => openConnection(connection)}
                      className={cx(
                        'w-full cursor-pointer rounded-lg px-2.5 py-2 text-left transition-colors',
                        active ? 'bg-sky-50 ring-1 ring-sky-200 ring-inset' : 'hover:bg-slate-50',
                        !connection.active && 'opacity-75',
                      )}
                    >
                      <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
                        {connection.name}
                        {!connection.active && <Badge tone="neutral">비활성</Badge>}
                        {connection.has_secret && <Badge tone="info">secret 있음</Badge>}
                      </p>
                      <p
                        className="mt-0.5 truncate font-mono text-xs text-slate-400"
                        title={connection.base_url}
                      >
                        {connection.base_url}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {expected.length === 0
                          ? '수집 확인 대상 없음'
                          : `수집 확인 ${expected.length}개 · ${expected.join(', ')}`}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      {/* -------------------------------------------------------- 상세·수정 */}
      <div className="xl:col-span-3">
        {!write.allowed ? (
          <Card title={editing ? `연결 상세 · ${editing.name}` : '연결 상세'}>
            <Notice tone="neutral" title="읽기 전용 계정입니다">
              {write.reason} 연결 등록·수정·테스트는 전부 쓰기 동작입니다.
            </Notice>
            {editing && (
              <dl className="mt-4 grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
                <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
                  <dt className="text-slate-500">base URL</dt>
                  <dd className="font-mono text-slate-800">{editing.base_url}</dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
                  <dt className="text-slate-500">인증</dt>
                  <dd className="text-slate-800">{authTypeLabel(editing.auth_type)}</dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-slate-100 py-1 sm:col-span-2">
                  <dt className="text-slate-500">수집 확인 대상</dt>
                  <dd className="text-right font-mono text-slate-800">
                    {(editing.expected_services ?? []).join(', ') || '없음'}
                  </dd>
                </div>
              </dl>
            )}
          </Card>
        ) : (
          <Card
            title={isEditing ? `연결 수정 · ${editing.name}` : '새 Loki 연결'}
            description={
              isEditing
                ? '저장된 secret 은 어떤 응답에도 평문으로 오지 않습니다 — 있음/없음만 표시됩니다.'
                : '백엔드가 컨테이너 안이면 localhost 가 아니라 서비스명을 씁니다 (예: http://loki:3100).'
            }
            actions={
              isEditing && (
                <span className="text-xs text-slate-400">
                  #{editing.id} · 수정 {formatDateTime(editing.updated_at)}
                </span>
              )
            }
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="연결 이름" required>
                <Input
                  value={form.name}
                  placeholder="local-loki"
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>

              <Field label="base URL" required>
                <Input
                  value={form.base_url}
                  placeholder="http://loki:3100"
                  onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                />
              </Field>

              <Field label="인증 방식">
                <Select
                  value={form.auth_type}
                  onChange={(event) =>
                    setForm({ ...form, auth_type: event.target.value as AuthType })
                  }
                >
                  {AUTH_TYPES.map((authType) => (
                    <option key={authType} value={authType}>
                      {authTypeLabel(authType)}
                    </option>
                  ))}
                </Select>
              </Field>

              {needsSecret && (
                <Field
                  label="secret"
                  required={!isEditing}
                  hint={
                    isEditing
                      ? `비워 두면 기존 값을 유지합니다. 저장된 secret: ${
                          editing.has_secret ? '있음' : '없음'
                        }`
                      : '저장 시 암호화되며 이후 어떤 응답에도 평문으로 나오지 않습니다.'
                  }
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    className="truncate"
                    value={form.secret}
                    placeholder={isEditing ? '변경할 때만 입력' : ''}
                    onChange={(event) => setForm({ ...form, secret: event.target.value })}
                  />
                </Field>
              )}

              <Field
                label="라벨 매핑"
                className="sm:col-span-2"
                hint={
                  <>
                    소스 라벨을 표준 필드로 옮깁니다. 한 줄에 하나씩{' '}
                    <code>소스라벨=표준필드</code> (예: <code>app=service</code>).
                  </>
                }
              >
                <Textarea
                  rows={3}
                  value={form.labelMappingText}
                  onChange={(event) => setForm({ ...form, labelMappingText: event.target.value })}
                />
              </Field>

              {/*
                수집 중단 확인 대상. 비용도 자동 실행도 없는 값이지만 오해를 부르기 쉬워
                문구를 정확히 적는다 — "경고가 남는다"까지가 전부다.
              */}
              <div className="sm:col-span-2 rounded-lg border border-amber-200 bg-amber-50/60 px-3.5 py-3">
                <Field
                  label="수집 확인 대상 서비스"
                  hint={
                    <>
                      여기 적힌 서비스가 조회 기간에 로그를 <strong>한 줄도 안 내면</strong>{' '}
                      <strong>수집 중단 경고</strong>가 회차에 남습니다. 표준 필드{' '}
                      <code>service</code> 기준이고 쉼표로 구분합니다. 비워 두면 확인을 하지
                      않습니다.
                    </>
                  }
                >
                  <Input
                    value={form.expectedServicesText}
                    placeholder="payment-api, order-api, auth-api"
                    onChange={(event) =>
                      setForm({ ...form, expectedServicesText: event.target.value })
                    }
                  />
                </Field>
                <p className="mt-2 text-xs text-amber-900">
                  경고는 <strong>기록일 뿐</strong>입니다 — 알림을 보내거나 조회·분석을 자동으로
                  실행하지 않습니다. 경고는{' '}
                  <Link to="/" className="font-medium underline">
                    통합 대시보드
                  </Link>{' '}
                  카드와 정책 상세에 배지로 표시됩니다.
                </p>
              </div>
            </div>

            {formError && (
              <div className="mt-4">
                <Notice tone="danger">{formError}</Notice>
              </div>
            )}
            {(createConnection.isError || updateConnection.isError) && (
              <div className="mt-4">
                <ErrorBlock error={createConnection.error ?? updateConnection.error} />
              </div>
            )}
            {testConnection.data && (
              <div className="mt-4">
                <Notice tone={testConnection.data.ok ? 'success' : 'danger'}>
                  {testConnection.data.message}
                  {testConnection.data.latency_ms != null &&
                    ` (${testConnection.data.latency_ms} ms)`}
                </Notice>
              </div>
            )}
            {testConnection.isError && (
              <div className="mt-4">
                <ErrorBlock error={testConnection.error} />
              </div>
            )}

            <div className="mt-5 flex flex-wrap gap-2">
              <Button variant="primary" onClick={handleSubmit} disabled={saving}>
                {saving ? '저장 중…' : isEditing ? '연결 수정' : '연결 저장'}
              </Button>
              <Button
                disabled={testConnection.isPending}
                onClick={() =>
                  testConnection.mutate({
                    connection_id: isEditing && !form.secret.trim() ? editing.id : null,
                    base_url: form.base_url.trim() || null,
                    auth_type: form.auth_type,
                    secret: form.secret.trim() || null,
                  })
                }
              >
                {testConnection.isPending ? '테스트 중…' : '연결 테스트'}
              </Button>
              {isEditing && editing.active && (
                <Button
                  variant="danger"
                  disabled={deactivateConnection.isPending}
                  title="실제 삭제가 아닙니다 — 정책과 조회 이력이 이 연결을 참조합니다."
                  onClick={() => deactivateConnection.mutate(editing.id)}
                >
                  비활성화
                </Button>
              )}
              {isEditing && !editing.active && (
                <Button
                  disabled={updateConnection.isPending}
                  onClick={() =>
                    updateConnection.mutate({ id: editing.id, payload: { active: true } })
                  }
                >
                  재활성화
                </Button>
              )}
              {isEditing && (
                <Button variant="ghost" onClick={() => openConnection(editing)}>
                  되돌리기
                </Button>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
