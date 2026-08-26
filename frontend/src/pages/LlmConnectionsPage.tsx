import { useState } from 'react';

import {
  useCreateLlmConnection,
  useDeactivateLlmConnection,
  useLlmConnections,
  useTestLlmConnection,
  useUpdateLlmConnection,
} from '../api/queries';
import { LLM_PROVIDERS, type LLMConnectionRead, type LLMProviderName } from '../api/types';
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
  PageHeader,
  Select,
  TableWrap,
  Td,
  Th,
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

export function LlmConnectionsPage() {
  const connectionsQuery = useLlmConnections();
  const createConnection = useCreateLlmConnection();
  const updateConnection = useUpdateLlmConnection();
  const deactivateConnection = useDeactivateLlmConnection();
  const testConnection = useTestLlmConnection();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const isEditing = editingId !== null;
  const editing = connectionsQuery.data?.find((connection) => connection.id === editingId) ?? null;
  const saving = createConnection.isPending || updateConnection.isPending;
  const requiresBaseUrl = form.provider === 'openai_compatible';

  function resetForm() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    testConnection.reset();
  }

  function startEdit(connection: LLMConnectionRead) {
    setEditingId(connection.id);
    setForm({
      name: connection.name,
      provider: connection.provider,
      model: connection.model,
      base_url: connection.base_url ?? '',
      // 저장된 키는 평문으로 오지 않는다. 비워 두면 기존 키를 유지한다.
      api_key: '',
      is_default: connection.is_default,
    });
    setFormError(null);
    testConnection.reset();
    window.scrollTo({ top: 0, behavior: 'smooth' });
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
      base_url: form.base_url.trim() || null,
      is_default: form.is_default,
    };

    if (isEditing) {
      updateConnection.mutate(
        {
          id: editingId,
          // 키를 비워 두면 필드를 아예 보내지 않는다 — 기존 키를 지우지 않기 위해서다.
          payload: form.api_key.trim() ? { ...base, api_key: form.api_key.trim() } : base,
        },
        { onSuccess: resetForm },
      );
    } else {
      createConnection.mutate(
        { ...base, active: true, api_key: form.api_key.trim() },
        { onSuccess: resetForm },
      );
    }
  }

  function handleTest() {
    const message = isEditing ? null : validate();
    setFormError(message);
    if (message) return;
    testConnection.mutate({
      // 수정 중이고 새 키를 입력하지 않았으면 저장된 연결로 테스트한다.
      connection_id: isEditing && !form.api_key.trim() ? editingId : null,
      provider: form.provider,
      model: form.model.trim() || null,
      base_url: form.base_url.trim() || null,
      api_key: form.api_key.trim() || null,
    });
  }

  return (
    <div>
      <PageHeader
        title="LLM 연결"
        description={
          <>
            연결은 여러 개 등록하고 <strong>기본 연결 하나</strong>를 지정합니다. 분석 실행 시 다른
            연결을 고를 수 있습니다. 저장된 API 키는 어떤 응답에도 평문으로 나오지 않고 마스킹된
            값만 표시됩니다.
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-2">
          <Card
            title={isEditing ? `연결 수정 · ${editing?.name ?? `#${editingId}`}` : '새 LLM 연결'}
            actions={
              isEditing && (
                <Button size="sm" onClick={resetForm}>
                  새로 만들기
                </Button>
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
                  onChange={(event) =>
                    setForm({ ...form, provider: event.target.value as LLMProviderName })
                  }
                >
                  {LLM_PROVIDERS.map((provider) => (
                    <option key={provider} value={provider}>
                      {providerLabel(provider)}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="모델" required>
                <Input
                  value={form.model}
                  placeholder={MODEL_PLACEHOLDER[form.provider]}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                />
              </Field>

              <Field
                label="base URL"
                required={requiresBaseUrl}
                hint="OpenAI 호환 엔드포인트를 쓸 때만 필요합니다."
              >
                <Input
                  value={form.base_url}
                  placeholder="http://llm-gateway.internal:8080/v1"
                  onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                />
              </Field>

              <Field
                label="API 키"
                required={!isEditing}
                hint={
                  isEditing ? (
                    <>
                      비워 두면 기존 키를 유지합니다. 저장된 키:{' '}
                      <code className="rounded bg-slate-100 px-1">
                        {editing?.api_key_masked ?? '(없음)'}
                      </code>
                    </>
                  ) : (
                    '저장 시 암호화되며 이후에는 마스킹된 값만 표시됩니다.'
                  )
                }
              >
                <Input
                  type="password"
                  autoComplete="off"
                  value={form.api_key}
                  placeholder={isEditing ? '변경할 때만 입력' : 'sk-…'}
                  onChange={(event) => setForm({ ...form, api_key: event.target.value })}
                />
              </Field>

              <Checkbox
                label="기본 연결로 지정"
                hint="기본 연결은 최대 하나입니다. 지정하면 기존 기본 연결이 해제됩니다."
                checked={form.is_default}
                onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
              />
            </div>

            <Notice tone="warning" className="mt-4">
              연결 테스트도 <strong>실제 과금 호출</strong>입니다. 서버가 최소 토큰으로 보냅니다.
            </Notice>

            {formError && (
              <div className="mt-4">
                <Notice tone="danger">{formError}</Notice>
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
            {(createConnection.isError || updateConnection.isError) && (
              <div className="mt-4">
                <ErrorBlock error={createConnection.error ?? updateConnection.error} />
              </div>
            )}

            <div className="mt-5 flex flex-wrap gap-2">
              <Button onClick={handleTest} disabled={testConnection.isPending}>
                {testConnection.isPending ? '테스트 중…' : '연결 테스트'}
              </Button>
              <Button variant="primary" onClick={handleSubmit} disabled={saving}>
                {saving ? '저장 중…' : isEditing ? '연결 수정' : '연결 저장'}
              </Button>
              {isEditing && (
                <Button variant="ghost" onClick={resetForm}>
                  취소
                </Button>
              )}
            </div>
          </Card>
        </div>

        <div className="xl:col-span-3">
          <Card title="등록된 연결" description="비활성화는 실제 삭제가 아닙니다 — 분석 이력이 참조합니다.">
            {connectionsQuery.isPending && <LoadingBlock />}
            {connectionsQuery.isError && <ErrorBlock error={connectionsQuery.error} />}
            {connectionsQuery.data?.length === 0 && (
              <EmptyBlock>등록된 LLM 연결이 없습니다. 먼저 하나를 만드십시오.</EmptyBlock>
            )}
            {connectionsQuery.data && connectionsQuery.data.length > 0 && (
              <TableWrap minWidth="34rem">
                <thead>
                  <tr>
                    <Th>이름</Th>
                    <Th>프로바이더 · 모델 · 키</Th>
                    <Th>상태</Th>
                    <Th align="right">작업</Th>
                  </tr>
                </thead>
                <tbody>
                  {connectionsQuery.data.map((connection) => (
                    <tr key={connection.id} className="hover:bg-slate-50">
                      <Td>
                        <p className="font-medium text-slate-900">{connection.name}</p>
                        {connection.base_url && (
                          <p className="mt-0.5 font-mono text-xs break-all text-slate-500">
                            {connection.base_url}
                          </p>
                        )}
                        <p className="mt-0.5 text-xs text-slate-400">
                          수정 {formatDateTime(connection.updated_at)}
                        </p>
                      </Td>
                      <Td>
                        <p>{providerLabel(connection.provider)}</p>
                        <p className="mt-0.5 font-mono text-xs text-slate-500">
                          {connection.model}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          API 키{' '}
                          <code
                            className="rounded bg-slate-100 px-1.5 py-0.5"
                            title="저장된 키는 평문으로 오지 않습니다."
                          >
                            {connection.api_key_masked ?? '(없음)'}
                          </code>
                        </p>
                      </Td>
                      <Td>
                        <div className="flex flex-col items-start gap-1">
                          {connection.is_default && <Badge tone="accent">기본 연결</Badge>}
                          <Badge tone={connection.active ? 'success' : 'neutral'}>
                            {connection.active ? '활성' : '비활성'}
                          </Badge>
                        </div>
                      </Td>
                      <Td align="right">
                        <div className="flex flex-wrap justify-end gap-1">
                          <Button size="sm" onClick={() => startEdit(connection)}>
                            수정
                          </Button>
                          {!connection.is_default && connection.active && (
                            <Button
                              size="sm"
                              onClick={() =>
                                updateConnection.mutate({
                                  id: connection.id,
                                  payload: { is_default: true },
                                })
                              }
                            >
                              기본 지정
                            </Button>
                          )}
                          {connection.active && (
                            <Button
                              size="sm"
                              variant="danger"
                              disabled={deactivateConnection.isPending}
                              onClick={() => deactivateConnection.mutate(connection.id)}
                            >
                              비활성화
                            </Button>
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
      </div>
    </div>
  );
}
