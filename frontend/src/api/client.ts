/**
 * fetch 기반 API 클라이언트.
 *
 * dev 서버는 `/api` 를 백엔드(기본 http://localhost:8000)로 프록시하므로 기본 base 는 빈 문자열이다.
 * `VITE_USE_MOCK=true` 면 네트워크를 타지 않고 `./mock/handler` 의 fixture 로 응답한다 —
 * Phase 1 시점에는 백엔드 다수 엔드포인트가 501 이라 화면 단독 확인 경로가 필요하다.
 */

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  /** 'json' (기본) 또는 'text' — Markdown 보고서처럼 본문이 JSON 이 아닌 경우. */
  parse?: 'json' | 'text' | 'void';
  signal?: AbortSignal;
}

/** HTTP 오류. 백엔드는 FastAPI HTTPException 형식(`{"detail": "..."}`)으로 준다. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/**
 * 아직 구현되지 않은 엔드포인트(백엔드 스텁)인지. 화면은 이 경우를 실패가 아니라
 * "다음 단계에서 채워짐"으로 안내한다.
 */
export function isNotImplemented(error: unknown): boolean {
  return error instanceof ApiError && error.status === 501;
}

/**
 * 라이브 백엔드에 **아직 그 경로가 없는가**. 화면은 이 경우 기능을 실패로 보여주지 않고
 * 폴백한다 (모델 목록 → 자유 입력, 실행 이력 → 안내 문구).
 *
 * 404·501 뿐 아니라 405·422 도 포함한다 — 같은 prefix 의 기존 라우트가 먼저 잡아
 * "메서드 없음"이나 "경로 파라미터 파싱 실패"로 응답하는 경우가 있기 때문이다.
 * (예: `POST /api/llm-connections/models` 를 모르는 백엔드는 같은 경로의 다른 메서드
 * 때문에 404 가 아니라 405 를 준다)
 */
export function isEndpointMissing(error: unknown): boolean {
  return (
    error instanceof ApiError && [404, 405, 422, 501].includes(error.status)
  );
}

/** 미인증 (세션이 없거나 만료). 화면은 이 경우 /login 으로 보낸다. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * 권한 부족. `viewer` 계정이 GET 이 아닌 요청을 보냈을 때 서버가 준다.
 *
 * **판정은 서버가 한다.** 화면이 쓰기 버튼을 감추는 것은 편의이고, 여기 잡히는 403 은
 * 그 편의를 우회했을 때의 진짜 방어선이다 — 오류로 그대로 보여준다.
 */
export function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// ------------------------------------------------------------ 401 인터셉트

/**
 * 미인증 응답을 받았을 때 한 번만 도는 콜백. **여기 한 곳**에서만 가로챈다 —
 * 화면마다 401 을 따로 처리하면 어떤 화면은 리다이렉트하고 어떤 화면은 오류를
 * 그대로 보여주는 상태가 된다.
 *
 * auth 라우트 자신은 제외한다: 부트스트랩(`GET /api/auth/me`)의 401 은 "아직 로그인
 * 안 함"이라는 **정상 응답**이고, 로그인 실패의 401 은 폼이 직접 보여줘야 한다.
 */
type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function isAuthPath(path: string): boolean {
  return path.startsWith('/api/auth/');
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const text = await response.text();
    if (!text) return `HTTP ${response.status}`;
    try {
      // FastAPI 는 보통 ErrorResponse 형식이지만 422 는 detail 이 배열이라 unknown 으로 받는다.
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === 'string') return parsed.detail;
      // FastAPI 422 는 detail 이 배열이다.
      if (Array.isArray(parsed.detail)) {
        return parsed.detail
          .map((item) =>
            typeof item === 'object' && item !== null && 'msg' in item
              ? String((item as { msg: unknown }).msg)
              : JSON.stringify(item),
          )
          .join(', ');
      }
      return text.slice(0, 500);
    } catch {
      return text.slice(0, 500);
    }
  } catch {
    return `HTTP ${response.status}`;
  }
}

export async function apiRequest<T>(
  method: HttpMethod,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  try {
    return await performRequest<T>(method, path, options);
  } catch (error) {
    // mock 과 라이브 양쪽이 같은 지점을 지나야 한다 — mock 모드에서만 로그인 화면이
    // 안 뜨거나, 그 반대가 되면 인증 동작을 화면에서 검증할 수 없다.
    if (isUnauthorized(error) && !isAuthPath(path)) unauthorizedHandler?.();
    throw error;
  }
}

async function performRequest<T>(
  method: HttpMethod,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  if (USE_MOCK) {
    const { mockRequest } = await import('./mock/handler');
    return mockRequest<T>(method, path, options);
  }

  const init: RequestInit = {
    method,
    headers: { Accept: options.parse === 'text' ? 'text/markdown, text/plain' : 'application/json' },
    signal: options.signal,
    // 세션은 httpOnly 쿠키다. 같은 오리진(dev 프록시·compose)이면 기본값으로도 붙지만
    // VITE_API_BASE 로 다른 오리진을 가리키는 배치에서는 이 옵션이 없으면 쿠키가 빠진다.
    credentials: 'include',
  };

  if (options.body !== undefined) {
    init.headers = { ...init.headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(buildUrl(path, options.query), init);

  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }

  if (options.parse === 'void' || response.status === 204) {
    return undefined as T;
  }
  if (options.parse === 'text') {
    return (await response.text()) as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>('POST', path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>('PATCH', path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>('PUT', path, { ...options, body }),
  delete: (path: string, options?: RequestOptions) =>
    apiRequest<void>('DELETE', path, { ...options, parse: 'void' }),
  /** 메서드를 값으로 넘겨야 할 때. 위 헬퍼로 표현되지 않는 경우에만 쓴다. */
  request: apiRequest,
};
