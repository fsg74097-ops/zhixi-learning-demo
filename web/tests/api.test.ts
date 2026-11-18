import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiClient, ApiError } from '../src/api.ts';

test('server scope is carried by the cookie and mutations are never retried automatically', async () => {
  const calls: Array<{url: string; init?: RequestInit}> = [];
  const client = new ApiClient(async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ ok: false, error: { code: 'BUDGET_EXCEEDED', message: '调用预算已用完', retryable: true }, trace_id: 'T-1' }), { status: 429 });
  });
  await assert.rejects(client.post('/agent', { goal: '本次观察', use_model: true }), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.code, 'BUDGET_EXCEEDED');
    assert.equal(error.traceId, 'T-1');
    return true;
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init?.credentials, 'same-origin');
});

test('long analysis follows a job with read requests and returns its real result', async () => {
  const calls: string[] = [];
  const results = [
    { ok: true, data: { job_id: 'J-1', status: 'QUEUED' } },
    { ok: true, data: { status: 'RUNNING', progress: ['检索选定证据'] } },
    { ok: true, data: { status: 'SUCCEEDED', result: { ok: true, data: { claims: [{ assertion_text: '真实结果' }] } } } },
  ];
  const client = new ApiClient(async (input) => { calls.push(String(input)); return Response.json(results.shift()); });
  const progress: unknown[] = [];
  const result = await client.run('/agent', { goal: '分析本次观察', use_model: false }, { pollMs: 0, onProgress: state => progress.push(state) });
  assert.deepEqual(result, { claims: [{ assertion_text: '真实结果' }] });
  assert.deepEqual(calls, ['/api/agent', '/api/jobs/J-1', '/api/jobs/J-1']);
  assert.equal(progress.length, 2);
});

test('aborting a pending analysis stops polling and does not submit a second model request', async () => {
  const controller = new AbortController();
  let count = 0;
  const client = new ApiClient(async () => { count += 1; controller.abort(); return Response.json({ok:true,data:{job_id:'J-2',status:'PENDING'}}); });
  await assert.rejects(client.run('/agent', {goal:'分析',use_model:true}, {signal:controller.signal,pollMs:0}), { name: 'AbortError' });
  assert.equal(count, 1);
});

test('UI events carry the server-issued trace and snapshot without copying session fields', async () => {
  const requests: Array<{url: string; init?: RequestInit}> = [];
  const client = new ApiClient(async (input, init) => {
    requests.push({url: String(input), init});
    return Response.json({ok: true, data: {accepted: true}});
  });
  const context = {session_trace: 'UI-TRACE', snapshot_hash: 'snapshot-1', role: 'TEACHER'};
  await client.uiEvent(context, 'NODE_SELECTED', {node_id: 'T03I1', selected: true});
  await client.uiEvent(context, 'EDGE_SELECTED', {source: 'T03I1', target: 'T03I2'});
  const events = requests.map(request => JSON.parse(String(request.init?.body)));
  assert.ok(events[0].client_event_id);
  assert.notEqual(events[0].client_event_id, events[1].client_event_id);
  assert.equal(requests[0].url, '/api/ui-events');
  assert.equal(requests[0].init?.credentials, 'same-origin');
  assert.deepEqual({...events[0], client_event_id: undefined}, {
    schema_version: '1.1',
    event_type: 'NODE_SELECTED',
    trace_id: 'UI-TRACE',
    snapshot_hash: 'snapshot-1',
    payload: {node_id: 'T03I1', selected: true},
    client_event_id: undefined,
  });
});
