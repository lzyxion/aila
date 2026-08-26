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
  if (USE_MOCK) {
    const { mockRequest } = await import('./mock/handler');
    return mockRequest<T>(method, path, options);
  }

  const init: RequestInit = {
    method,
    headers: { Accept: options.parse === 'text' ? 'text/markdown, text/plain' : 'application/json' },
    signal: options.signal,
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
