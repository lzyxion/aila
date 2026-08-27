/**
 * 사용자 관리 (`/admin/users`) — 계약 1.
 *
 * 세 가지 보호는 **전부 서버에 있다**: 마지막 남은 활성 admin 의 강등·비활성(409), 자기
 * 자신 비활성(409), 비활성화·비밀번호 변경 시 그 계정 세션 전부 무효화. 화면은 그 규칙을
 * 다시 구현하지 않고 **409 의 `detail` 을 그대로 보여준다** — 문구를 프런트가 따로 쓰면
 * 서버가 규칙을 바꿨을 때 화면만 옛말을 하게 된다.
 *
 * 위험한 버튼에는 미리 사유를 적어 두되(자기 계정·마지막 admin 후보), 그건 **안내이지
 * 판정이 아니다.** 눌러서 409 를 받는 경로가 살아 있어야 규칙이 실제로 도는지 확인된다.
 */

import { useState } from 'react';

import { ApiError, isEndpointMissing, isForbidden } from '../api/client';
import {
  useCreateUser,
  useDeactivateUser,
  useUpdateUser,
  useUsers,
} from '../api/queries';
import type { UserRead, UserRole } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { AddIcon, SaveIcon, UsersIcon } from '../components/icons';
import { RoleBadge } from '../components/StatusBadges';
import {
  Badge,
  Button,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  Notice,
  PageStack,
  Select,
  SkeletonTable,
  TableWrap,
  Td,
  Th,
  cx,
} from '../components/ui';
import { formatDateTime } from '../lib/format';
import { ConfirmButton } from './adminConfirm';

/** 비밀번호·역할 변경이 세션에 미치는 영향 — 화면 여러 곳에서 같은 문구를 쓴다. */
const SESSION_NOTE =
  '비활성화하거나 비밀번호를 바꾸면 그 계정의 세션이 전부 무효화됩니다 — 로그인해 있던 브라우저는 즉시 로그인 화면으로 돌아갑니다.';

export function UsersPage() {
  const { user: me } = useAuth();
  const usersQuery = useUsers();
  const updateUser = useUpdateUser();
  const deactivateUser = useDeactivateUser();

  /** 인라인 폼을 열어 둔 계정. 한 번에 하나만 연다. */
  const [passwordFor, setPasswordFor] = useState<number | null>(null);
  /** 마지막으로 서버가 거절한 동작 — 409 는 화면이 미리 못 막는 규칙이라 크게 띄운다. */
  const [conflict, setConflict] = useState<string | null>(null);

  const rows = usersQuery.data?.items ?? [];
  const activeAdmins = rows.filter((row) => row.role === 'admin' && row.active).length;

  function runUpdate(id: number, payload: Parameters<typeof updateUser.mutate>[0]['payload']) {
    setConflict(null);
    updateUser.mutate(
      { id, payload },
      {
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) setConflict(error.detail);
        },
      },
    );
  }

  return (
    <PageStack>
      <CreateUserCard onConflict={setConflict} />

      <Card
        title="계정 목록"
        description="비활성화는 삭제가 아닙니다 — 계정 행은 남고 로그인만 막힙니다."
        info={
          <>
            계정 행을 지우지 않는 이유는 <strong>분석 이력이 계정을 참조</strong>하기
            때문입니다.
            <span className="mt-1.5 block">{SESSION_NOTE}</span>
          </>
        }
        actions={
          rows.length > 0 && (
            <span className="text-xs text-muted tabular-nums">
              전체 {usersQuery.data?.total ?? rows.length}개 · 활성 admin {activeAdmins}명
            </span>
          )
        }
      >
        {conflict && (
          <div className="mb-4">
            <Notice tone="danger" title="서버가 거절했습니다 (409)">
              {conflict}
            </Notice>
          </div>
        )}

        {usersQuery.isPending && (
          <SkeletonTable rows={4} cols={5} label="계정 목록을 불러오는 중" />
        )}

        {usersQuery.isError &&
          (isEndpointMissing(usersQuery.error) ? (
            <Notice tone="warning" title="계정 관리 API 를 아직 쓸 수 없습니다">
              <Code>GET /api/auth/users</Code> 가 응답하지 않습니다. 백엔드에 이 경로가 올라오면
              계정 목록·역할 변경·비밀번호 재설정이 여기에 표시됩니다. 그 전에는{' '}
              <Code>POST /api/auth/users</Code> 로 계정을 만드는 것까지만 가능합니다.
            </Notice>
          ) : isForbidden(usersQuery.error) ? (
            <Notice tone="neutral" title="admin 계정만 볼 수 있습니다">
              계정 목록은 조회도 admin 전용입니다. 서버가 403 으로 거절했습니다.
            </Notice>
          ) : (
            <ErrorBlock error={usersQuery.error} />
          ))}

        {usersQuery.data && rows.length === 0 && (
          <EmptyBlock icon={UsersIcon}>계정이 없습니다.</EmptyBlock>
        )}

        {rows.length > 0 && (
          <TableWrap minWidth="52rem">
            <thead>
              <tr>
                <Th>계정</Th>
                <Th>역할</Th>
                <Th>상태</Th>
                <Th>생성</Th>
                <Th align="right">작업</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isMe = me?.username === row.username;
                // 화면이 미리 알려 줄 수 있는 것만 알린다 — 판정은 서버다.
                const lastAdmin = row.role === 'admin' && row.active && activeAdmins <= 1;
                const open = passwordFor === row.id;
                return [
                  <tr
                    key={row.id}
                    className={cx('hover:bg-surface-2', !row.active && 'opacity-70')}
                  >
                    <Td>
                      <p className="font-medium text-ink">
                        {row.username}
                        {isMe && (
                          <span className="ml-2 text-xs font-normal text-muted">
                            (지금 로그인한 계정)
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs text-faint tabular-nums">#{row.id}</p>
                    </Td>
                    <Td>
                      <div className="flex flex-col items-start gap-1">
                        <RoleBadge role={row.role} />
                        {/* 색이 아니라 글자로 알린다 — 서버가 왜 거절할지를 미리 적는다. */}
                        {lastAdmin && (
                          <Badge tone="warning">마지막 활성 admin · 강등·비활성 불가</Badge>
                        )}
                      </div>
                    </Td>
                    <Td>
                      <Badge tone={row.active ? 'success' : 'neutral'}>
                        {row.active ? '활성' : '비활성'}
                      </Badge>
                    </Td>
                    <Td className="whitespace-nowrap text-xs text-muted tabular-nums">
                      {formatDateTime(row.created_at)}
                    </Td>
                    <Td align="right">
                      <div className="flex flex-wrap justify-end gap-1">
                        <RoleSelect
                          value={row.role}
                          disabled={updateUser.isPending}
                          title={
                            lastAdmin
                              ? '마지막 활성 admin 은 viewer 로 바꿀 수 없습니다 (서버가 409 로 거절합니다).'
                              : '역할을 바꾸면 즉시 적용됩니다.'
                          }
                          onChange={(role) => {
                            if (role !== row.role) runUpdate(row.id, { role });
                          }}
                        />
                        <Button
                          size="sm"
                          variant={open ? 'ghost' : 'secondary'}
                          title={SESSION_NOTE}
                          onClick={() => setPasswordFor(open ? null : row.id)}
                        >
                          {open ? '닫기' : '비밀번호 재설정'}
                        </Button>
                        {row.active ? (
                          /*
                            비활성화는 그 계정의 세션을 전부 끊는다 — 되돌릴 수는 있지만 누른
                            순간 상대가 로그아웃되므로 한 번 더 묻는다. 자기 자신·마지막
                            admin 은 서버가 409 로 막지만, 그 사유는 확인 문구에 미리 적는다.
                          */
                          <ConfirmButton
                            variant="danger"
                            pending={deactivateUser.isPending}
                            question={
                              isMe
                                ? '자기 자신은 비활성화할 수 없습니다 (서버가 409).'
                                : lastAdmin
                                  ? '마지막 활성 admin 입니다 (서버가 409 로 거절합니다).'
                                  : `${row.username} 의 세션이 전부 끊깁니다.`
                            }
                            onConfirm={() => {
                              setConflict(null);
                              deactivateUser.mutate(row.id, {
                                onError: (error) => {
                                  if (error instanceof ApiError && error.status === 409) {
                                    setConflict(error.detail);
                                  }
                                },
                              });
                            }}
                          >
                            비활성화
                          </ConfirmButton>
                        ) : (
                          <Button
                            size="sm"
                            disabled={updateUser.isPending}
                            title="비활성화는 삭제가 아닙니다 — 다시 켤 수 있습니다."
                            onClick={() => runUpdate(row.id, { active: true })}
                          >
                            재활성화
                          </Button>
                        )}
                      </div>
                    </Td>
                  </tr>,
                  open ? (
                    <tr key={`${row.id}-password`}>
                      <Td colSpan={5} className="bg-surface-2">
                        <PasswordForm
                          user={row}
                          isMe={isMe}
                          onDone={() => setPasswordFor(null)}
                          onConflict={setConflict}
                        />
                      </Td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </TableWrap>
        )}

        {(updateUser.isError || deactivateUser.isError) && !conflict && (
          <div className="mt-4">
            <ErrorBlock error={updateUser.error ?? deactivateUser.error} />
          </div>
        )}
      </Card>
    </PageStack>
  );
}

// ------------------------------------------------------------------ 역할 변경

function RoleSelect({
  value,
  disabled,
  title,
  onChange,
}: {
  value: UserRole;
  disabled: boolean;
  title: string;
  onChange: (role: UserRole) => void;
}) {
  // 폭은 **감싸는 div** 가 정한다 — `Select` 자신이 `w-full` 을 들고 있어서, 같은 성질의
  // 유틸리티를 className 으로 덧붙이면 어느 쪽이 이길지 생성된 CSS 순서에 달린다.
  return (
    <div className="w-28">
      <Select
        className="py-1.5 text-xs"
        value={value}
        disabled={disabled}
        title={title}
        aria-label="역할"
        onChange={(event) => onChange(event.target.value as UserRole)}
      >
        <option value="admin">admin</option>
        <option value="viewer">viewer</option>
      </Select>
    </div>
  );
}

// -------------------------------------------------------------- 비밀번호 재설정

/**
 * 비밀번호 재설정.
 *
 * 기존 비밀번호를 묻지 않는다 — admin 이 남의 계정을 되살리는 경로이기 때문이다. 대신
 * **세션이 전부 끊긴다는 사실**을 저장 버튼 옆에 적는다. 자기 계정이면 저장 직후 자기가
 * 로그아웃되므로 문구를 따로 강조한다.
 */
function PasswordForm({
  user,
  isMe,
  onDone,
  onConflict,
}: {
  user: UserRead;
  isMe: boolean;
  onDone: () => void;
  onConflict: (message: string) => void;
}) {
  const updateUser = useUpdateUser();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  function submit() {
    if (password.trim().length < 4) {
      setError('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    if (password !== confirm) {
      setError('두 입력이 다릅니다.');
      return;
    }
    setError(null);
    updateUser.mutate(
      { id: user.id, payload: { password } },
      {
        onSuccess: () => {
          setPassword('');
          setConfirm('');
          onDone();
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) onConflict(err.detail);
        },
      },
    );
  }

  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <p className="text-sm font-semibold text-ink">
        <span className="font-mono">{user.username}</span> 비밀번호 재설정
      </p>
      {/*
        세션이 끊긴다는 사실은 **위험 고지**라 ⓘ 로 접지 않는다 — 누르기 전에 보여야 한다.
      */}
      <p className="mt-1 text-xs text-muted">{SESSION_NOTE}</p>
      {isMe && (
        <div className="mt-2">
          <Notice tone="warning">
            <strong>지금 로그인한 계정입니다</strong> — 저장하면 이 화면도 로그인 화면으로
            돌아갑니다.
          </Notice>
        </div>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <Field label="새 비밀번호">
          <Input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Field label="새 비밀번호 확인">
          <Input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>
        <div className="flex items-end gap-2">
          <Button variant="primary" disabled={updateUser.isPending} onClick={submit}>
            <SaveIcon aria-hidden className="size-4" />
            {updateUser.isPending ? '저장 중…' : '비밀번호 저장'}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            취소
          </Button>
        </div>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="danger">{error}</Notice>
        </div>
      )}
      {updateUser.isError && (
        <div className="mt-3">
          <ErrorBlock error={updateUser.error} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 계정 생성

function CreateUserCard({ onConflict }: { onConflict: (message: string) => void }) {
  const createUser = useCreateUser();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('viewer');
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  function submit() {
    if (!username.trim()) {
      setError('사용자명을 입력하십시오.');
      return;
    }
    if (password.trim().length < 4) {
      setError('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    setError(null);
    createUser.mutate(
      { username: username.trim(), password, role },
      {
        onSuccess: () => {
          setCreated(username.trim());
          setUsername('');
          setPassword('');
          setRole('viewer');
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) onConflict(err.detail);
        },
      },
    );
  }

  return (
    <Card
      title="계정 만들기"
      description="기본은 viewer 입니다 — 조회만 필요한 사람에게 admin 을 주지 마십시오."
      info={
        <>
          admin 은 정책 실행·AI 분석·설정 변경처럼 <strong>비용이 나가는 동작</strong>을 할 수
          있습니다. viewer 는 GET 만 가능합니다.
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="사용자명" required>
          <Input
            value={username}
            placeholder="oncall-watcher"
            autoComplete="off"
            onChange={(event) => setUsername(event.target.value)}
          />
        </Field>
        <Field label="비밀번호" required hint="저장 시 해시로만 보관됩니다.">
          <Input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Field label="역할" required>
          <Select value={role} onChange={(event) => setRole(event.target.value as UserRole)}>
            <option value="viewer">viewer — 조회만</option>
            <option value="admin">admin — 실행·저장·설정</option>
          </Select>
        </Field>
        <div className="flex items-end">
          <Button variant="primary" disabled={createUser.isPending} onClick={submit}>
            <AddIcon aria-hidden className="size-4" />
            {createUser.isPending ? '만드는 중…' : '계정 만들기'}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="danger">{error}</Notice>
        </div>
      )}
      {createUser.isError && (
        <div className="mt-3">
          <ErrorBlock error={createUser.error} />
        </div>
      )}
      {created && !createUser.isError && (
        <div className="mt-3">
          <Notice tone="success" title={`'${created}' 계정을 만들었습니다`}>
            비밀번호는 전달 즉시 본인이 바꾸게 하십시오 — 이 화면의{' '}
            <strong>비밀번호 재설정</strong>은 admin 이 언제든 다시 할 수 있습니다.
          </Notice>
        </div>
      )}
    </Card>
  );
}
