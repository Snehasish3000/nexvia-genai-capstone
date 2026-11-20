import type {
  AdminStats,
  ApiErrorShape,
  ChatResponse,
  FeedbackRating,
  IngestResponse,
  RetrievalMode,
  Role,
  Session,
} from '../types';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const SESSION_KEY = 'nexvia-genai01-session-v1';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function getStoredSession(): Session | null {
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' && parsed !== null &&
      'accessToken' in parsed && typeof parsed.accessToken === 'string' &&
      'role' in parsed && ['admin', 'finance', 'engineering', 'guest'].includes(String(parsed.role)) &&
      'username' in parsed && typeof parsed.username === 'string' &&
      'expiresIn' in parsed && typeof parsed.expiresIn === 'number'
    ) {
      return parsed as Session;
    }
  } catch {
    window.localStorage.removeItem(SESSION_KEY);
  }
  return null;
}

export const sessionStore = {
  get: getStoredSession,
  set(session: Session): void {
    window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  },
  clear(): void {
    window.localStorage.removeItem(SESSION_KEY);
  },
};

async function readError(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}`;
  try {
    const body = (await response.json()) as ApiErrorShape;
    if (typeof body.detail === 'string') message = body.detail;
    else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg ?? 'Invalid request').join('; ');
    else if (body.message) message = body.message;
  } catch {
    const text = await response.text().catch(() => '');
    if (text) message = text.slice(0, 300);
  }
  return new ApiError(message, response.status);
}

async function request<T>(path: string, init: RequestInit = {}, authenticated = true): Promise<T> {
  const session = sessionStore.get();
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (authenticated) {
    if (!session?.accessToken) throw new ApiError('Sign in to continue.', 401);
    headers.set('Authorization', `Bearer ${session.accessToken}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) throw await readError(response);
  return (await response.json()) as T;
}

export async function createDemoSession(username: string, role: Role): Promise<Session> {
  const response = await request<{
    access_token: string;
    expires_in: number;
    role: Role;
  }>(
    '/api/v1/auth/demo-token',
    { method: 'POST', body: JSON.stringify({ username, role }) },
    false,
  );
  const session: Session = {
    accessToken: response.access_token,
    expiresIn: response.expires_in,
    role: response.role,
    username,
  };
  sessionStore.set(session);
  return session;
}

export interface StreamHandlers {
  onMetadata?: (metadata: { request_id?: string; confidence?: number }) => void;
  onToken?: (text: string) => void;
  onComplete?: (response: ChatResponse) => void;
}

export async function streamChat(
  question: string,
  retrievalMode: RetrievalMode,
  topK: number,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const session = sessionStore.get();
  if (!session?.accessToken) throw new ApiError('Sign in to continue.', 401);

  const response = await fetch(`${API_BASE_URL}/api/v1/chat/stream`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ question, retrieval_mode: retrievalMode, top_k: topK }),
    signal,
  });
  if (!response.ok) throw await readError(response);
  if (!response.body) throw new ApiError('The server did not return a streaming response.', 502);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let complete: ChatResponse | null = null;

  const dispatch = (frame: string): void => {
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0) return;
    const data = dataLines.join('\n');
    if (eventName === 'metadata') {
      handlers.onMetadata?.(JSON.parse(data) as { request_id?: string; confidence?: number });
    } else if (eventName === 'token') {
      const token = JSON.parse(data) as { text?: string };
      if (typeof token.text === 'string') handlers.onToken?.(token.text);
    } else if (eventName === 'complete') {
      complete = JSON.parse(data) as ChatResponse;
      handlers.onComplete?.(complete);
    } else if (eventName === 'error') {
      throw new ApiError(data, 502);
    }
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? '\n\n';
        buffer = buffer.slice(boundary + separator.length);
        dispatch(frame);
        boundary = buffer.search(/\r?\n\r?\n/);
      }
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }

  if (!complete) throw new ApiError('The stream ended before a completion event was received.', 502);
  return complete;
}

export async function sendFeedback(requestId: string, rating: FeedbackRating): Promise<void> {
  await request<{ status: 'recorded' }>('/api/v1/feedback', {
    method: 'POST',
    body: JSON.stringify({ request_id: requestId, rating }),
  });
}

export async function uploadDocument(file: File, allowedRoles: Role[]): Promise<IngestResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('allowed_roles', allowedRoles.join(','));
  return request<IngestResponse>('/api/v1/admin/documents/upload', { method: 'POST', body: form });
}

export async function ingestUrl(url: string, allowedRoles: Role[]): Promise<IngestResponse> {
  return request<IngestResponse>('/api/v1/admin/documents/url', {
    method: 'POST',
    body: JSON.stringify({ url, allowed_roles: allowedRoles }),
  });
}

export async function fetchAdminStats(): Promise<AdminStats> {
  return request<AdminStats>('/api/v1/admin/stats');
}

export async function checkHealth(): Promise<{ status: string; vector_store: string }> {
  return request<{ status: string; vector_store: string }>('/health', {}, false);
}
