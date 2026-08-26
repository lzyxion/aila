/**
 * fetch 기반 API 클라이언트.
 *
 * dev 서버는 `/api` 를 백엔드(기본 http://localhost:8000)로 프록시하므로 기본 base 는 빈 문자열이다.
 * `VITE_USE_MOCK=true` 면 네트워크를 타지 않고 `./mock/handler` 의 fixture 로 응답한다 —
 * Phase 1 시점에는 백엔드 다수 엔드포인트가 501 이라 화면 단독 확인 경로가 필요하다.
 */

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE';

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
  delete: (path: string, options?: RequestOptions) =>
    apiRequest<void>('DELETE', path, { ...options, parse: 'void' }),
};
