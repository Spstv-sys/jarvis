import { beforeEach, afterEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { apiFetch, getApiKey, setApiKey, validateServerStatus, websocketProtocols, websocketUrl } from '../src/api.js'

const originalFetch = globalThis.fetch
beforeEach(() => {
  const values = new Map()
  globalThis.sessionStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  }
  globalThis.window = new EventTarget()
})
afterEach(() => { globalThis.fetch = originalFetch })

test('an old backend is reported before opening an incompatible WebSocket', () => {
  assert.throws(() => validateServerStatus({ ready: true, provider: 'deepseek' }), /Перезапустіть/)
  const current = { api_version: 1, ready: true, provider: 'deepseek' }
  assert.equal(validateServerStatus(current), current)
})

test('WebSocket follows the current host, port, and HTTPS scheme', () => {
  assert.equal(websocketUrl({ href: 'https://neo.example:8443/', protocol: 'https:' }),
    'wss://neo.example:8443/ws')
  assert.equal(websocketUrl({ href: 'http://127.0.0.1:8001/', protocol: 'http:' }),
    'ws://127.0.0.1:8001/ws')
})

test('keys travel in subprotocols without appearing in the URL', () => {
  const key = 'тест-ключ+/'
  const protocols = websocketProtocols(key)
  assert.equal(protocols[0], 'jarvis')
  assert.equal(Buffer.from(protocols[1].slice('jarvis-key.'.length), 'base64url').toString(), key)
  assert.doesNotMatch(protocols[1], /[=+/]/)
})

test('authenticated requests keep the original multipart body and headers', async () => {
  setApiKey('session-secret')
  const body = new FormData()
  body.append('prompt', 'hello')
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/upload')
    assert.equal(options.headers.get('X-API-Key'), 'session-secret')
    assert.equal(options.headers.get('X-Test'), 'preserved')
    assert.equal(options.headers.has('Content-Type'), false)
    assert.equal(options.body, body)
    return new Response('{}')
  }
  await apiFetch('/upload', { method: 'POST', headers: { 'X-Test': 'preserved' }, body })
})

test('401 requests the access form and rejects the operation', async () => {
  let requested = false
  window.addEventListener('jarvis-auth-required', () => { requested = true })
  globalThis.fetch = async () => new Response('{}', { status: 401 })
  await assert.rejects(apiFetch('/command', { method: 'POST' }), /401/)
  assert.equal(requested, true)
})

test('failed requests are not reported as successful', async () => {
  globalThis.fetch = async () => new Response('{}', { status: 503 })
  await assert.rejects(apiFetch('/command', { method: 'POST' }), /503/)
})

test('clearing the session removes the access key', () => {
  setApiKey('session-secret')
  assert.equal(getApiKey(), 'session-secret')
  setApiKey('')
  assert.equal(getApiKey(), '')
})
