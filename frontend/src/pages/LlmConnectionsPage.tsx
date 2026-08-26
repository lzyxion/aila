/**
 * LLM 연결 (`/admin/llm-connections`) — **좌측 목록 + 우측 상세·수정**.
 *
 * 화면 순서가 실제 작업 순서를 따른다: 프로바이더를 고르고 → **API 키를 넣고** → 바로 그
 * 아래에서 **모델 목록을 조회**해 고른다. 예전에는 모델 입력이 키보다 위에 있어서, 키가
 * 없는 상태로 목록을 눌러 보고 실패한 다음에야 키 칸을 찾는 흐름이었다.
 *
 * 모델 목록은 실패해도 화면을 막지 않는다 — 프로바이더가 목록 API 를 주지 않거나 키가
 * 아직 없을 수 있으므로 **자유 입력으로 폴백**한다. 다만 왜 안 되는지는 반드시 적는다
 * (키 없음 / 백엔드에 경로 없음 / 프로바이더 거절은 사용자가 할 일이 서로 다르다).
 *
 * 조회지만 `POST /api/llm-connections/models` 다 — api_key 를 쿼리스트링에 실으면 평문 키가
 * 서버 액세스 로그·프록시 로그·브라우저 히스토리에 남는다.
 */

import { useEffect, useState } from 'react';

import { ApiError, isEndpointMissing } from '../api/client';
import {
  useCreateLlmConnection,
  useDeactivateLlmConnection,
  useLlmConnections,
  useLlmModels,
  useTestLlmConnection,
  useUpdateLlmConnection,
} from '../api/queries';
import {
  LLM_PROVIDERS,
  type LLMConnectionRead,
  type LLMModelListRequest,
  type LLMProviderName,
} from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  LoadingBlock,
  Notice,
  OneLineCode,
  Select,
  Spinner,
  cx,
} from '../components/ui';
import { formatDateTime, providerLabel } from '../lib/format';

interface FormState {
  name: string;
  provider: LLMProviderName;
  model: string;
  base_url: string;
  api_key: string;
  is_default: boolean;
}

const EMPTY_FORM: FormState = {
  name: '',
  provider: 'anthropic',
  model: '',
  base_url: '',
  api_key: '',
  is_default: false,
};

/** 프로바이더별 모델 입력 힌트. 값은 자유 입력이므로 예시일 뿐이다. */
const MODEL_PLACEHOLDER: Record<LLMProviderName, string> = {
  anthropic: 'claude-sonnet-4-6',
  openai: 'gpt-5.2',
  openai_compatible: 'qwen3-32b-instruct',
};

/** base URL 을 쓰는 프로바이더는 하나뿐이다 — 나머지는 필드 자체를 감춘다. */
function usesBaseUrl(provider: LLMProviderName): boolean {
  return provider === 'openai_compatible';
}

/** 드롭다운의 "직접 입력" 항목 값. 모델명과 겹치지 않는 표식을 쓴다. */
const MANUAL_MODEL = '\u0000manual';

/** `null` = 새 연결 작성 중. 숫자면 그 연결의 상세·수정. */
type Selection = number | null;

export function LlmConnectionsPage() {
  const write = useWriteAccess();
  const connectionsQuery = useLlmConnections();
  const createConnection = useCreateLlmConnection();
  const updateConnection = useUpdateLlmConnection();
  const deactivateConnection = useDeactivateLlmConnection();
  const testConnection = useTestLlmConnection();

  const connections = connectionsQuery.data ?? [];
  const [selected, setSelected] = useState<Selection>(null);
  /** 목록이 처음 오면 기본 연결(없으면 첫 연결)을 열어 둔다 — 빈 오른쪽 칸을 만들지 않는다. */
  const [initialised, setInitialised] = useState(false);

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  /**
   * 모델 목록을 조회할 조건. **폼 상태와 분리해 둔다** — API 키를 한 글자 칠 때마다
   * 프로바이더를 두드리면 안 되므로, 프로바이더 변경·조회 버튼에서만 바뀐다.
   */
  const [modelQuery, setModelQuery] = useState<LLMModelListRequest | null>(null);
  /** 목록이 있어도 사용자가 "직접 입력"을 고르면 자유 입력이 이긴다. */
  const [manualModel, setManualModel] = useState(false);

  const editing = connections.find((connection) => connection.id === selected) ?? null;
  const isEditing = editing !== null;
  const saving = createConnection.isPending || updateConnection.isPending;
  const requiresBaseUrl = usesBaseUrl(form.provider);

  // 목록이 처음 도착하면 기본 연결(없으면 첫 연결)을 열어 둔다. 한 번만 한다 — 매번
  // 맞추면 사용자가 고른 연결이 목록 갱신 때마다 되돌아간다.
  useEffect(() => {
    if (initialised || connections.length === 0) return;
    setInitialised(true);
    openConnection(connections.find((c) => c.is_default && c.active) ?? connections[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connections, initialised]);

  const modelsQuery = useLlmModels(modelQuery ?? { provider: form.provider }, modelQuery !== null);
  // 프로바이더는 자기 순서대로 준다(OpenAI 는 100 개가 넘는다) — 정렬은 표시 계층의 몫이다.
  const models = [...(modelsQuery.data?.models ?? [])].sort((a, b) => a.localeCompare(b));
  // 목록을 못 받았으면(미지원·키 없음·프로바이더 오류) 조용히 자유 입력으로 돌아간다.
  const useModelDropdown = !manualModel && models.length > 0;

  /**
   * 모델 목록을 조회할 수 있는 상태인가.
   *
   * 이 판정이 hint 문구와 버튼 비활성을 **동시에** 결정한다 — 둘이 갈리면 "왜 안 눌리는지"가
   * 화면 어디에도 없는 버튼이 생긴다.
   */
  const canListModels = requiresBaseUrl
    ? form.base_url.trim() !== ''
    : form.api_key.trim() !== '' || (isEditing && Boolean(editing?.api_key_masked));

  const modelBlockedReason = requiresBaseUrl
    ? 'OpenAI 호환 엔드포인트는 base URL 을 먼저 입력해야 모델을 물어볼 수 있습니다.'
    : 'API 키를 먼저 입력하십시오 — 모델 목록은 프로바이더에게 키로 물어보는 조회입니다.';

  /** 지금 폼 값으로 모델 목록을 다시 조회한다. */
  function refreshModels(next: FormState, connectionId: number | null = selected) {
    setModelQuery({
      provider: next.provider,
      // 새 키를 입력했으면 그 키로, 아니면 저장된 연결로 조회한다.
      connection_id: next.api_key.trim() ? null : connectionId,
      api_key: next.api_key.trim() || null,
      base_url: usesBaseUrl(next.provider) ? next.base_url.trim() || null : null,
    });
  }

  function startCreate() {
    setSelected(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setModelQuery(null);
    setManualModel(false);
    testConnection.reset();
  }

  function openConnection(connection: LLMConnectionRead) {
    setSelected(connection.id);
    setForm({
      name: connection.name,
      provider: connection.provider,
      model: connection.model,
      base_url: connection.base_url ?? '',
      // 저장된 키는 평문으로 오지 않는다. 비워 두면 기존 키를 유지한다.
      api_key: '',
      is_default: connection.is_default,
    });
    setManualModel(false);
    // 목록은 자동으로 부르지 않는다 — 연결을 훑기만 해도 프로바이더를 두드리게 된다.
    setModelQuery(null);
    setFormError(null);
    testConnection.reset();
  }

  function validate(): string | null {
    if (!form.name.trim()) return '연결 이름을 입력하십시오.';
    if (!form.model.trim()) return '모델을 입력하십시오.';
    if (requiresBaseUrl && !form.base_url.trim()) {
      return 'OpenAI 호환 프로바이더는 base URL 이 필요합니다.';
    }
    if (!isEditing && !form.api_key.trim()) return 'API 키를 입력하십시오.';
    return null;
  }

  function handleSubmit() {
    const message = validate();
    setFormError(message);
    if (message) return;

    const base = {
      name: form.name.trim(),
      provider: form.provider,
      model: form.model.trim(),
      // 감춘 필드의 값은 보내지 않는다 — provider 를 바꿨는데 예전 base URL 이 남으면
      // 화면에는 없는 설정으로 호출이 나간다.
      base_url: requiresBaseUrl ? form.base_url.trim() || null : null,
      is_default: form.is_default,
    };

    if (isEditing) {
      updateConnection.mutate(
        {
          id: editing.id,
          // 키를 비워 두면 필드를 아예 보내지 않는다 — 기존 키를 지우지 않기 위해서다.
          payload: form.api_key.trim() ? { ...base, api_key: form.api_key.trim() } : base,
        },
        { onSuccess: (updated) => openConnection(updated) },
      );
    } else {
      createConnection.mutate(
        { ...base, active: true, api_key: form.api_key.trim() },
        { onSuccess: (created) => openConnection(created) },
      );
    }
  }

  function handleTest() {
    const message = isEditing ? null : validate();
    setFormError(message);
    if (message) return;
    testConnection.mutate({
      // 수정 중이고 새 키를 입력하지 않았으면 저장된 연결로 테스트한다.
      connection_id: isEditing && !form.api_key.trim() ? editing.id : null,
      provider: form.provider,
      model: form.model.trim() || null,
      base_url: requiresBaseUrl ? form.base_url.trim() || null : null,
      api_key: form.api_key.trim() || null,
    });
  }

  return (
    <div className="grid gap-6 xl:grid-cols-5">
      {/* ------------------------------------------------------------ 목록 */}
      <div className="xl:col-span-2">
        <Card
          title="LLM 연결"
          description="하나를 눌러 오른쪽에서 상세를 보고 고칩니다. 비활성화는 실제 삭제가 아닙니다 — 분석 이력이 참조합니다."
          actions={
            write.allowed && (
              <Button size="sm" variant={selected === null ? 'primary' : 'secondary'} onClick={startCreate}>
                새 연결
              </Button>
            )
          }
        >
          {connectionsQuery.isPending && <LoadingBlock />}
          {connectionsQuery.isError && <ErrorBlock error={connectionsQuery.error} />}
          {connectionsQuery.data?.length === 0 && (
            <EmptyBlock>등록된 LLM 연결이 없습니다. 먼저 하나를 만드십시오.</EmptyBlock>
          )}

          {connections.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {connections.map((connection) => {
                const active = selected === connection.id;
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
                        {connection.is_default && <Badge tone="accent">기본 연결</Badge>}
                        {!connection.active && <Badge tone="neutral">비활성</Badge>}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {providerLabel(connection.provider)} ·{' '}
                        <span className="font-mono">{connection.model}</span>
                      </p>
                      {connection.base_url && (
                        <p
                          className="mt-0.5 truncate font-mono text-xs text-slate-400"
                          title={connection.base_url}
                        >
                          {connection.base_url}
                        </p>
                      )}
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
          <ReadOnlyDetail connection={editing} reason={write.reason} />
        ) : (
          <div className="space-y-6">
            <Card
              title={isEditing ? `연결 수정 · ${editing.name}` : '새 LLM 연결'}
              description={
                isEditing
                  ? '저장된 API 키는 어떤 응답에도 평문으로 나오지 않습니다 — 마스킹된 값만 표시됩니다.'
                  : '분석 실행 시 기본 연결이 쓰이고, 그룹 상세에서 다른 연결을 고를 수 있습니다.'
              }
              actions={
                isEditing && (
                  <span className="text-xs text-slate-400">
                    #{editing.id} · 수정 {formatDateTime(editing.updated_at)}
                  </span>
                )
              }
            >
              <div className="grid gap-4">
                <Field label="연결 이름" required>
                  <Input
                    value={form.name}
                    placeholder="Claude (기본)"
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                  />
                </Field>

                <Field label="프로바이더" required>
                  <Select
                    value={form.provider}
                    onChange={(event) => {
                      const provider = event.target.value as LLMProviderName;
                      // provider 를 바꾸면 모델도 base URL 도 의미가 달라진다.
                      const next: FormState = {
                        ...form,
                        provider,
                        model: '',
                        base_url: usesBaseUrl(provider) ? form.base_url : '',
                      };
                      setForm(next);
                      setManualModel(false);
                      setModelQuery(null);
                    }}
                  >
                    {LLM_PROVIDERS.map((provider) => (
                      <option key={provider} value={provider}>
                        {providerLabel(provider)}
                      </option>
                    ))}
                  </Select>
                </Field>

                {/* base URL 은 OpenAI 호환 엔드포인트에만 있는 개념이라 아예 감춘다. */}
                {requiresBaseUrl && (
                  <Field
                    label="base URL"
                    required
                    hint="OpenAI 호환 엔드포인트의 주소입니다. 백엔드가 컨테이너 안이면 localhost 가 아니라 서비스명을 씁니다."
                  >
                    <Input
                      value={form.base_url}
                      placeholder="http://llm-mock:8000/v1"
                      onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                    />
                  </Field>
                )}

                {/* -------------------------------- API 키 → 바로 아래 모델 조회 */}
                <div className="rounded-lg border border-sky-200 bg-sky-50/60 px-3.5 py-3">
                  <Field
                    label="API 키"
                    required={!isEditing}
                    hint={
                      isEditing ? (
                        <span className="flex min-w-0 flex-wrap items-center gap-1">
                          비워 두면 기존 키를 유지합니다. 저장된 키:
                          <OneLineCode
                            className="inline-block max-w-40"
                            title="저장된 키는 어떤 응답에도 평문으로 오지 않습니다."
                          >
                            {editing?.api_key_masked ?? '(없음)'}
                          </OneLineCode>
                        </span>
                      ) : (
                        '저장 시 암호화되며 이후에는 마스킹된 값만 표시됩니다.'
                      )
                    }
                  >
                    {/*
                      키는 길다. 한 줄에 고정하고 넘치는 부분은 말줄임으로 둔다 — 줄바꿈되면
                      폼이 밀려 스크롤이 생긴다.
                    */}
                    <Input
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      className="truncate"
                      value={form.api_key}
                      placeholder={isEditing ? '변경할 때만 입력' : 'sk-…'}
                      onChange={(event) => setForm({ ...form, api_key: event.target.value })}
                    />
                  </Field>

                  {/*
                    모델 조회는 키 바로 아래에 있다 — 키를 넣은 손이 그대로 다음 버튼으로
                    이어지는 순서다. 키가 없으면 버튼을 잠그되 **왜 잠겼는지**를 같은 자리에
                    적는다 (누르고 실패해서 알게 되는 것보다 낫다).
                  */}
                  <div className="mt-3 border-t border-sky-200 pt-3">
                    <div className="flex flex-wrap items-end gap-2">
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={!canListModels || modelsQuery.isFetching}
                        title={canListModels ? '프로바이더에게 모델 목록을 물어봅니다.' : modelBlockedReason}
                        onClick={() => {
                          setManualModel(false);
                          refreshModels(form);
                          void modelsQuery.refetch();
                        }}
                      >
                        {modelsQuery.isFetching ? (
                          <>
                            <Spinner className="size-3.5 border-sky-200 border-t-white" />
                            조회 중…
                          </>
                        ) : (
                          '모델 목록 조회'
                        )}
                      </Button>
                      <span className="text-xs text-slate-600">
                        {canListModels
                          ? '조회 후 아래 드롭다운에서 고르십시오. 목록에 없으면 직접 입력해도 됩니다.'
                          : modelBlockedReason}
                      </span>
                    </div>

                    <Field
                      label="모델"
                      required
                      className="mt-3"
                      hint={
                        modelsQuery.isFetching ? (
                          <span className="inline-flex items-center gap-1.5">
                            <Spinner className="size-3" />
                            모델 목록을 불러오는 중…
                          </span>
                        ) : useModelDropdown ? (
                          <>
                            {providerLabel(form.provider)} 이(가) 제공하는 {models.length}개 · 목록에
                            없으면 <strong>직접 입력</strong>을 고르십시오.
                          </>
                        ) : modelsQuery.isError ? (
                          <span className="text-amber-700">
                            모델 목록을 불러오지 못해 직접 입력으로 전환했습니다 —{' '}
                            {/*
                              경로 자체가 없을 때(백엔드 미구현)와 프로바이더가 거절했을 때는
                              사용자가 할 일이 다르다. 앞의 경우 FastAPI 의 경로 파싱 오류
                              문구를 그대로 보여주면 아무 도움이 되지 않는다.
                            */}
                            {isEndpointMissing(modelsQuery.error)
                              ? '백엔드에 모델 목록 API 가 아직 없습니다. 모델명을 직접 입력하십시오.'
                              : modelsQuery.error instanceof ApiError
                                ? modelsQuery.error.detail
                                : '프로바이더가 목록을 제공하지 않습니다.'}
                          </span>
                        ) : (
                          '위 버튼으로 목록을 불러오거나, 아는 모델명을 그대로 입력하십시오.'
                        )
                      }
                    >
                      {useModelDropdown ? (
                        <Select
                          value={models.includes(form.model) ? form.model : ''}
                          onChange={(event) => {
                            if (event.target.value === MANUAL_MODEL) {
                              setManualModel(true);
                              return;
                            }
                            setForm({ ...form, model: event.target.value });
                          }}
                        >
                          <option value="">선택하십시오</option>
                          {models.map((model) => (
                            <option key={model} value={model}>
                              {model}
                            </option>
                          ))}
                          {/* 저장된 값이 목록에 없을 수 있다 (모델이 내려갔거나 별칭). */}
                          {form.model && !models.includes(form.model) && (
                            <option value={form.model}>{form.model} (현재 값)</option>
                          )}
                          <option value={MANUAL_MODEL}>직접 입력…</option>
                        </Select>
                      ) : (
                        <Input
                          value={form.model}
                          placeholder={MODEL_PLACEHOLDER[form.provider]}
                          onChange={(event) => setForm({ ...form, model: event.target.value })}
                        />
                      )}
                    </Field>
                  </div>
                </div>

                <Checkbox
                  label="기본 연결로 지정"
                  hint="기본 연결은 최대 하나입니다. 지정하면 기존 기본 연결이 해제됩니다."
                  checked={form.is_default}
                  onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
                />
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

              <div className="mt-5 flex flex-wrap gap-2">
                <Button variant="primary" onClick={handleSubmit} disabled={saving}>
                  {saving ? '저장 중…' : isEditing ? '연결 수정' : '연결 저장'}
                </Button>
                {isEditing && (
                  <Button variant="ghost" onClick={() => openConnection(editing)}>
                    되돌리기
                  </Button>
                )}
              </div>
            </Card>

            {/* ------------------------------------ 연결 테스트 · 기본 지정 (상세) */}
            <Card
              title="연결 테스트 · 기본 지정"
              description="이 연결이 실제로 동작하는지 확인하고, 분석에서 기본으로 쓸지 정합니다."
            >
              <Notice tone="warning">
                연결 테스트도 <strong>실제 과금 호출</strong>입니다. 서버가 최소 토큰으로 보냅니다.
              </Notice>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={handleTest} disabled={testConnection.isPending}>
                  {testConnection.isPending ? '테스트 중…' : '연결 테스트'}
                </Button>
                {isEditing && !editing.is_default && editing.active && (
                  <Button
                    disabled={updateConnection.isPending}
                    title="기본 연결은 최대 하나입니다 — 지정하면 기존 기본 연결이 해제됩니다."
                    onClick={() =>
                      updateConnection.mutate({ id: editing.id, payload: { is_default: true } })
                    }
                  >
                    기본 연결로 지정
                  </Button>
                )}
                {isEditing && editing.active && (
                  <Button
                    variant="danger"
                    disabled={deactivateConnection.isPending}
                    title="실제 삭제가 아닙니다 — 분석 이력이 이 연결을 참조합니다."
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
              </div>

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

              {isEditing && (
                <dl className="mt-4 grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
                  <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
                    <dt className="text-slate-500">상태</dt>
                    <dd>
                      <Badge tone={editing.active ? 'success' : 'neutral'}>
                        {editing.active ? '활성' : '비활성'}
                      </Badge>
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
                    <dt className="text-slate-500">기본 연결</dt>
                    <dd className="text-slate-800">{editing.is_default ? '예' : '아니오'}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-slate-100 py-1">
                    <dt className="text-slate-500">저장된 API 키</dt>
                    <dd>
                      <OneLineCode className="inline-block max-w-32">
                        {editing.api_key_masked ?? '(없음)'}
                      </OneLineCode>
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
                    <dt className="text-slate-500">등록</dt>
                    <dd className="text-slate-800">{formatDateTime(editing.created_at)}</dd>
                  </div>
                </dl>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

/** viewer 용 상세 — 쓰기 폼 자리를 사유가 적힌 안내로 바꾼다. */
function ReadOnlyDetail({
  connection,
  reason,
}: {
  connection: LLMConnectionRead | null;
  reason: string | null;
}) {
  return (
    <Card title={connection ? `연결 상세 · ${connection.name}` : '연결 상세'}>
      <Notice tone="neutral" title="권한 없음">
        {reason} 연결 등록·수정·테스트는 전부 쓰기 동작입니다 (테스트도 실제 과금 호출을 합니다).
      </Notice>
      {connection && (
        <dl className="mt-4 grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">프로바이더</dt>
            <dd className="text-slate-800">{providerLabel(connection.provider)}</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">모델</dt>
            <dd className="font-mono text-slate-800">{connection.model}</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">기본 연결</dt>
            <dd className="text-slate-800">{connection.is_default ? '예' : '아니오'}</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">상태</dt>
            <dd className="text-slate-800">{connection.active ? '활성' : '비활성'}</dd>
          </div>
        </dl>
      )}
    </Card>
  );
}
