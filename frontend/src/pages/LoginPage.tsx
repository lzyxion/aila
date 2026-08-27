/**
 * 로그인.
 *
 * 성공하면 **원래 가려던 경로**로 돌아간다 (`location.state.from`). 항상 홈으로 보내면
 * 세션이 만료된 사용자가 보고 있던 그룹 상세·조회 회차를 손으로 다시 찾아 들어가야 한다.
 */

import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

import { ApiError, USE_MOCK } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ThemeToggle } from '../components/ThemeToggle';
import { Button, Code, Field, InfoTip, Input, Notice, Spinner } from '../components/ui';

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { status, user, login, loginPending, loginError } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const from = (location.state as LocationState | null)?.from ?? '/';

  /**
   * 이미 세션이 있거나 백엔드에 인증이 없으면 로그인 화면에 머물 이유가 없다.
   * (인증 미배포 백엔드에서 /login 을 직접 열면 영영 못 들어가는 상태가 된다.)
   */
  useEffect(() => {
    if (status === 'authenticated' || status === 'disabled') {
      navigate(from, { replace: true });
    }
  }, [from, navigate, status]);

  function submit() {
    if (!username.trim() || !password) {
      setFormError('사용자명과 비밀번호를 모두 입력하십시오.');
      return;
    }
    setFormError(null);
    void login(username.trim(), password)
      .then(() => navigate(from, { replace: true }))
      .catch(() => {
        /* 오류는 loginError 로 표시한다 — 여기서 삼키지 않으면 콘솔만 시끄럽다. */
      });
  }

  const errorMessage = formError ?? describeLoginError(loginError);

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6 py-12">
      <div className="w-full max-w-sm">
        {/*
          로그인 화면은 `Layout` 바깥이라 사이드바의 테마 토글이 없다. 어두운 방에서
          앱을 처음 여는 순간이 정확히 여기라 토글을 한 번 더 둔다.
        */}
        <div className="mb-6 flex items-center justify-between gap-3">
          <div>
            <p className="text-2xl font-bold tracking-tight text-ink">AILA</p>
            <p className="mt-0.5 text-sm text-muted">AI 로그 분석기</p>
          </div>
          <ThemeToggle />
        </div>

        <form
          className="rounded-xl border border-line bg-surface px-6 py-6 shadow-sm shadow-slate-900/5"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <h1 className="text-base font-semibold text-ink">로그인</h1>
          <p className="mt-1 text-sm text-muted">계정에 따라 실행 권한이 갈립니다.</p>

          <div className="mt-5 space-y-4">
            <Field label="사용자명" required>
              <Input
                name="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </Field>
            <Field label="비밀번호" required>
              <Input
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
          </div>

          {errorMessage && (
            <div className="mt-4">
              <Notice tone="danger">{errorMessage}</Notice>
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="mt-5 w-full"
            disabled={loginPending || status === 'loading'}
          >
            {loginPending ? (
              <>
                <Spinner className="size-4 border-sky-200 border-t-white" />
                확인 중…
              </>
            ) : (
              '로그인'
            )}
          </Button>

          {user && (
            <p className="mt-3 text-center text-xs text-muted">
              {user.username} 으로 로그인되어 있습니다.
            </p>
          )}

          {USE_MOCK && (
            <div className="mt-5 border-t border-line-soft pt-4 text-xs text-muted">
              <p className="font-medium text-ink-soft">MOCK 데이터 계정</p>
              <p className="mt-1">
                <Code>admin / admin</Code> — 전체 권한
              </p>
              <p className="mt-0.5">
                <Code>viewer / viewer</Code> — 조회만
              </p>
            </div>
          )}
        </form>

        {/* 고지는 한 줄 + ⓘ — 지우지 않고 접는다. */}
        <p className="mt-4 flex items-center justify-center gap-1 text-center text-xs text-muted">
          세션은 httpOnly 쿠키로만 유지됩니다.
          <InfoTip label="세션·로그 취급 방식 보기" title="이 앱이 다루는 값" align="center">
            비밀번호·세션 토큰은 화면 코드가 읽을 수 없습니다(httpOnly 쿠키).
            <span className="mt-1.5 block">
              로그인 후 보게 되는 로그는 전부 <strong>마스킹된 값</strong>이며, 마스킹 전 원본은
              저장하지 않습니다.
            </span>
          </InfoTip>
        </p>
      </div>
    </div>
  );
}

/** 401 은 "자격 증명이 틀렸다"이지 장애가 아니다 — 문구를 갈라 준다. */
function describeLoginError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return error.detail || '사용자명 또는 비밀번호가 올바르지 않습니다.';
    }
    return `로그인 요청이 실패했습니다 (HTTP ${error.status}): ${error.detail}`;
  }
  return error instanceof Error ? error.message : '로그인에 실패했습니다.';
}
