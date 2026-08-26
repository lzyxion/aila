/**
 * 세션 컨텍스트 — 로그인 상태와 **쓰기 권한 한 곳**.
 *
 * 세 가지 상태를 구분한다. 이 구분이 무너지면 화면은 "백엔드가 아직 인증을 안 붙였다"와
 * "로그인이 필요하다"를 같은 모양으로 보여주고, 사용자는 있지도 않은 로그인 화면 앞에서
 * 멈춘다.
 *
 * | 상태 | `GET /api/auth/me` | 화면 |
 * | --- | --- | --- |
 * | `authenticated` | 200 | 정상. `role` 에 따라 쓰기 UI 를 켜고 끈다 |
 * | `anonymous` | 401 | `/login` 으로 보낸다 |
 * | `disabled` | 404·405·501 | **인증 미배포** — 안내 배지만 띄우고 기존처럼 동작한다 |
 *
 * `disabled` 는 폴백이지 기본값이 아니다. 백엔드 트랙이 auth 를 올리는 순간 같은 코드가
 * 자동으로 `authenticated`/`anonymous` 경로로 넘어간다.
 *
 * **권한 판정의 진짜 주체는 서버다.** 여기서 계산하는 `canWrite` 는 버튼을 감추기 위한
 * 편의이고, 우회하면 서버가 403 을 준다 (기간·라인 수 상한과 같은 규칙이다).
 */

import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router';

import { isEndpointMissing, setUnauthorizedHandler } from '../api/client';
import { useAuthMe, useLogin, useLogout } from '../api/queries';
import type { AuthUser, UserRole } from '../api/types';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'disabled';

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  role: UserRole | null;
  /** 쓰기 동작(POST·PATCH·PUT·DELETE)을 시도해도 되는가. */
  canWrite: boolean;
  /** 쓰기가 막힌 이유. 버튼의 `title` 로 그대로 쓴다 (권한 없음을 눈에 보이게). */
  writeDeniedReason: string | null;
  login: (username: string, password: string) => Promise<AuthUser>;
  loginPending: boolean;
  loginError: unknown;
  logout: () => void;
  logoutPending: boolean;
}

const VIEWER_REASON =
  '읽기 전용(viewer) 계정입니다 — 조회는 할 수 있지만 실행·저장·삭제는 admin 만 할 수 있습니다.';

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const meQuery = useAuthMe();
  const loginMutation = useLogin();
  const logoutMutation = useLogout();

  /**
   * 401 인터셉트. api client 한 곳에서 올라오는 신호를 여기서 라우팅으로 바꾼다 —
   * 화면마다 401 을 따로 처리하면 어떤 화면은 리다이렉트하고 어떤 화면은 오류 박스를
   * 띄우는 상태가 된다.
   */
  const refetchMe = meQuery.refetch;
  useEffect(() => {
    setUnauthorizedHandler(() => {
      // 지금 어디를 보고 있었는지 남긴다 — 로그인 후 그 자리로 돌려보내기 위해서.
      if (window.location.pathname === '/login') return;
      const from = `${window.location.pathname}${window.location.search}`;
      void refetchMe();
      navigate('/login', { replace: true, state: { from } });
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, refetchMe]);

  const value = useMemo<AuthContextValue>(() => {
    const status = resolveStatus(meQuery.isLoading, meQuery.data, meQuery.error);
    const user = status === 'authenticated' ? (meQuery.data ?? null) : null;
    const role = user?.role ?? null;
    // 인증이 배포되지 않은 백엔드에서는 예전처럼 전부 열어 둔다 (역할 자체가 없다).
    const canWrite = status === 'disabled' || role === 'admin';

    return {
      status,
      user,
      role,
      canWrite,
      writeDeniedReason: canWrite ? null : role === 'viewer' ? VIEWER_REASON : '로그인이 필요합니다.',
      login: async (username: string, password: string) =>
        loginMutation.mutateAsync({ username, password }),
      loginPending: loginMutation.isPending,
      loginError: loginMutation.error,
      logout: () =>
        logoutMutation.mutate(undefined, {
          // 로그아웃 뒤 남는 화면은 데이터가 비어 있어야 한다 — 캐시는 훅이 비우고,
          // 여기서는 곧바로 로그인 화면으로 보낸다.
          onSettled: () => navigate('/login', { replace: true }),
        }),
      logoutPending: logoutMutation.isPending,
    };
  }, [
    meQuery.data,
    meQuery.error,
    meQuery.isLoading,
    loginMutation,
    logoutMutation,
    navigate,
  ]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function resolveStatus(
  isLoading: boolean,
  data: AuthUser | undefined,
  error: unknown,
): AuthStatus {
  if (data) return 'authenticated';
  if (isLoading) return 'loading';
  // 404·405·501 = 백엔드에 auth 라우트가 아직 없다. 실패로 표시하지 않고 폴백한다.
  if (isEndpointMissing(error)) return 'disabled';
  if (error) return 'anonymous';
  return 'loading';
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('AuthProvider 안에서만 useAuth 를 쓸 수 있습니다.');
  return value;
}

/**
 * 쓰기 UI 한 줄 가드.
 *
 * ```tsx
 * const write = useWriteAccess();
 * <Button disabled={!write.allowed} title={write.reason ?? '정책을 실행합니다.'}>실행</Button>
 * ```
 */
export function useWriteAccess(): { allowed: boolean; reason: string | null } {
  const { canWrite, writeDeniedReason } = useAuth();
  return { allowed: canWrite, reason: writeDeniedReason };
}
