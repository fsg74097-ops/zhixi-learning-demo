import type { Context } from './models.ts';

export interface Envelope<T> { ok: boolean; data: T; error?: {code: string; message: string; retryable?: boolean}; trace_id?: string; snapshot_hash?: string; limitations?: string[] }
type Transport = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type UiEventPayloads = {
  NODE_SELECTED: {node_id: string; selected: boolean};
  EDGE_SELECTED: {source: string; target: string; relation_type?: string};
  EVIDENCE_OPENED: {entity_id: string};
  FILTER_CHANGED: {class_id?: string; student_id?: string; filter_id?: string};
  TIME_RANGE_CHANGED: {node_id: string; comparison_node_id?: string};
  VIEW_CHANGED: {view_id: string};
};
type UiEventContext = Pick<Context, 'session_trace' | 'snapshot_hash'>;
export class ApiError extends Error {
  code: string; traceId?: string; retryable: boolean;
  constructor(code: string, message: string, traceId?: string, retryable = false) { super(message); this.name = 'ApiError'; this.code = code; this.traceId = traceId; this.retryable = retryable; }
}
function unwrap<T>(envelope: Envelope<T>, status = 200): T {
  if (!envelope.ok) throw new ApiError(envelope.error?.code ?? `HTTP_${status}`, envelope.error?.message ?? '请求未完成，请稍后重试。', envelope.trace_id, envelope.error?.retryable);
  return envelope.data;
}
function aborted(signal?: AbortSignal) { if (signal?.aborted) throw new DOMException('已停止等待', 'AbortError'); }
function pause(ms: number, signal?: AbortSignal) { return new Promise<void>((resolve, reject) => { aborted(signal); const timeout = setTimeout(done, ms); function done() { signal?.removeEventListener('abort', cancel); resolve(); } function cancel() { clearTimeout(timeout); reject(new DOMException('已停止等待', 'AbortError')); } signal?.addEventListener('abort', cancel, {once:true}); }); }
export interface JobState { job_id: string; status: string; progress?: unknown; result?: Envelope<unknown>; error?: {code?: string; message?: string} }
export class ApiClient {
  transport: Transport;
  constructor(transport: Transport = (...args) => fetch(...args)) { this.transport = transport; }
  async requestEnvelope<T>(path: string, init: RequestInit = {}): Promise<Envelope<T>> {
    const response = await this.transport(`/api${path}`, {credentials: 'same-origin', ...init, headers: {'Content-Type':'application/json', ...init.headers}});
    let envelope: Envelope<T>;
    try { envelope = await response.json(); } catch { throw new ApiError(`HTTP_${response.status}`, '服务返回了无法读取的内容。'); }
    if (!response.ok && envelope.ok) throw new ApiError(`HTTP_${response.status}`, '服务暂时不可用。');
    unwrap(envelope, response.status);
    return envelope;
  }
  async request<T>(path: string, init: RequestInit = {}): Promise<T> { return (await this.requestEnvelope<T>(path, init)).data; }
  get<T>(path: string, signal?: AbortSignal) { return this.request<T>(path, {signal}); }
  post<T>(path: string, body: unknown, signal?: AbortSignal) { return this.request<T>(path, {method:'POST',body:JSON.stringify(body),signal}); }
  uiEvent<K extends keyof UiEventPayloads>(context: UiEventContext, eventType: K, payload: UiEventPayloads[K]) {
    return this.post('/ui-events', {
      schema_version: '1.1',
      event_type: eventType,
      trace_id: context.session_trace,
      snapshot_hash: context.snapshot_hash,
      payload,
      client_event_id: crypto.randomUUID(),
    });
  }
  async run<T>(path: string, body: unknown, options: {signal?: AbortSignal; pollMs?: number; onProgress?: (state: JobState) => void} = {}): Promise<T> {
    let result = await this.post<T | JobState>(path, body, options.signal);
    if (!result || typeof result !== 'object' || !('job_id' in result)) return result as T;
    const id = (result as JobState).job_id;
    for (;;) {
      aborted(options.signal);
      await pause(options.pollMs ?? 1100, options.signal);
      const job = await this.get<JobState>(`/jobs/${encodeURIComponent(id)}`, options.signal);
      options.onProgress?.(job);
      if (job.status === 'SUCCEEDED') { if (!job.result) throw new ApiError('EMPTY_JOB_RESULT','任务没有返回可用结果。'); return unwrap(job.result) as T; }
      if (job.status === 'FAILED') throw new ApiError(job.error?.code ?? 'JOB_FAILED', job.error?.message ?? '本次分析未完成，请查看执行摘要。');
    }
  }
  async download(path: string, body: unknown): Promise<void> {
    const response = await this.transport(`/api${path}`, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if (!response.ok || response.headers.get('content-type')?.includes('application/json')) { const result = await response.json(); unwrap(result,response.status); throw new ApiError('EXPORT_UNAVAILABLE','导出文件尚未就绪。'); }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') ?? '';
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encoded ? decodeURIComponent(encoded) : disposition.match(/filename="?([^";]+)/i)?.[1] ?? '学习任务.zip';
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href=url; anchor.download=filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  }
}
export const api = new ApiClient();

export function emitUiEvent<K extends keyof UiEventPayloads>(context: UiEventContext, eventType: K, payload: UiEventPayloads[K]) {
  if (!context.session_trace) return;
  void api.uiEvent(context, eventType, payload).catch(() => console.warn('页面事件记录未完成', eventType));
}

export function viewPath(view: string, scope: object) {
  const query = new URLSearchParams(Object.entries(scope).filter((entry): entry is [string,string] => typeof entry[1] === 'string' && entry[1] !== ''));
  return `/views/${view}?${query}`;
}
