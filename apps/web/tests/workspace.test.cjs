const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const pinia = require('pinia')

function load(relative, fetch) {
  const file = path.resolve(__dirname, '..', relative)
  const source = fs.readFileSync(file, 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const context = { exports: {}, require, Headers, fetch }
  vm.runInNewContext(code, context, { filename: file })
  return context.exports
}

function response(body, status = 200) { return { ok: status < 400, status, json: async () => body } }

test('wrong transfer password preserves login; forbidden session clears it', async () => {
  pinia.setActivePinia(pinia.createPinia())
  let invalidSession = false
  const { useWorkspace } = load('src/stores/workspace.ts', async (url) => {
    if (url === '/api/auth/login') return response({ auth_token: 'token', username: 'test_user' })
    if (url === '/api/sessions') return response({ session_id: 'session' })
    if (url.startsWith('/api/accounts')) return response({ accounts: [{ account_id: 'a', account_name: '主账户', balance_cents: 900000 }] })
    if (url.endsWith('/authorize')) return response({ detail: { code: 'INVALID_CREDENTIALS', message: '密码错误' } }, 401)
    if (invalidSession) return response({ detail: { code: 'FORBIDDEN', message: '会话失效' } }, 403)
    throw Error(url)
  })
  const store = useWorkspace()
  await store.login('test_user', 'synthetic-password')
  await assert.rejects(store.authorizeAction('action', 'wrong'))
  assert.equal(store.authenticated, true)
  assert.equal(store.sessionId, 'session')
  invalidSession = true
  await assert.rejects(store.send('query'))
  assert.equal(store.authenticated, false)
})

test('account snapshot is loaded at login and refreshed after transfer; original card completes', async () => {
  pinia.setActivePinia(pinia.createPinia())
  let balance = 900000
  const { useWorkspace } = load('src/stores/workspace.ts', async (url) => {
    if (url === '/api/auth/login') return response({ auth_token: 'token', username: 'test_user' })
    if (url === '/api/sessions') return response({ session_id: 'session' })
    if (url.startsWith('/api/accounts')) return response({ accounts: [{ account_id: 'a', account_name: '主账户', balance_cents: balance }] })
    if (url === '/api/ask') return response({ status: 'confirmation_required', action_id: 'action', message: '确认' })
    if (url.endsWith('/confirm')) { balance -= 2500; return response({ status: 'transfer_complete', message: '完成' }) }
    throw Error(url)
  })
  const store = useWorkspace()
  await store.login('test_user', 'synthetic-password')
  assert.equal(store.totalBalanceCents, 900000)
  await store.send('transfer')
  await store.confirmAction('action', 'authorization', '购物')
  assert.equal(store.totalBalanceCents, 897500)
  assert.equal(store.messages.find(item => item.action_id === 'action').status, 'transfer_complete')
  await store.logout()
  assert.equal(store.accounts.length, 0)
})

test('two transfer cards keep independent authorization; expired authorization can be retried', async () => {
  const { useTransferAction } = load('src/composables/transfer.ts')
  let expire = true
  const api = {
    authorizeAction: async action => ({ authorization_id: 'auth-' + action, categories: ['购物'] }),
    confirmAction: async (action, auth) => {
      assert.equal(auth, 'auth-' + action)
      if (expire) throw Object.assign(Error('授权过期'), { code: 'AUTHORIZATION_EXPIRED' })
    },
  }
  const a = useTransferAction(api, 'a')
  const b = useTransferAction(api, 'b')
  a.password.value = 'password'
  await a.authorize()
  assert.equal(a.authorizationId.value, 'auth-a')
  assert.equal(b.authorizationId.value, '')
  await a.confirm()
  assert.equal(a.authorizationId.value, '')
  expire = false
  a.password.value = 'password'
  await a.authorize()
  await a.confirm()
  assert.equal(a.completed.value, true)
})

test('late account and query responses cannot repopulate a logged-out workspace', async () => {
  pinia.setActivePinia(pinia.createPinia())
  let resolveAccounts, resolveQuery, delay = false
  const { useWorkspace } = load('src/stores/workspace.ts', async url => {
    if (url === '/api/auth/login') return response({ auth_token: 'token', username: 'test_user' })
    if (url === '/api/sessions') return response({ session_id: 'session' })
    if (url === '/api/auth/logout') return response({ status: 'logged_out' })
    if (url.startsWith('/api/accounts')) {
      if (delay) return new Promise(resolve => { resolveAccounts = resolve })
      return response({ accounts: [] })
    }
    if (url === '/api/ask') return new Promise(resolve => { resolveQuery = resolve })
    throw Error(url)
  })
  const store = useWorkspace()
  await store.login('test_user', 'synthetic-password')
  delay = true
  const accounts = store.refreshAccounts()
  const query = store.send('query')
  await store.logout()
  resolveAccounts(response({ accounts: [{ account_id: 'private', account_name: '主账户', balance_cents: 900000 }] }))
  resolveQuery(response({ status: 'answer', answer: 'private answer' }))
  await Promise.all([accounts, query])
  assert.equal(store.accounts.length, 0)
  assert.equal(store.messages.length, 0)
})

test('old session rejection cannot log out the next user', async () => {
  pinia.setActivePinia(pinia.createPinia())
  let rejectOld, user = 'test_user'
  const { useWorkspace } = load('src/stores/workspace.ts', async url => {
    if (url === '/api/auth/login') return response({ auth_token: 'token-' + user, username: user })
    if (url === '/api/sessions') return response({ session_id: 'session-' + user })
    if (url.startsWith('/api/accounts')) return response({ accounts: [] })
    if (url === '/api/auth/logout') return response({ status: 'logged_out' })
    if (url === '/api/ask') return new Promise(resolve => { rejectOld = resolve })
    throw Error(url)
  })
  const store = useWorkspace()
  await store.login('test_user', 'password')
  const query = store.send('old query')
  await store.logout()
  user = 'isolation_user'
  await store.login(user, 'password')
  rejectOld(response({ detail: { code: 'FORBIDDEN', message: 'old session expired' } }, 403))
  await assert.rejects(query)
  assert.equal(store.authenticated, true)
  assert.equal(store.username, 'isolation_user')
  assert.equal(store.messages.length, 0)
})

test('credentials in a chat draft never enter messages or network requests', async () => {
  pinia.setActivePinia(pinia.createPinia())
  let calls = 0
  const { useWorkspace } = load('src/stores/workspace.ts', async url => {
    calls++
    if (url === '/api/auth/login') return response({ auth_token: 'token', username: 'test_user' })
    if (url === '/api/sessions') return response({ session_id: 'session' })
    if (url.startsWith('/api/accounts')) return response({ accounts: [] })
    throw Error(url)
  })
  const store = useWorkspace()
  await store.login('test_user', 'synthetic-password')
  const before = calls
  for (const text of ['2026年9月支出，密码：chat-sensitive-password', '我的密码 123456，请查支出', 'password synthetic-password', '验证码123456']) {
    const result = await store.send(text)
    assert.equal(result.status, 'blocked')
    assert.equal(calls, before)
  }
  assert.equal(JSON.stringify(store.messages).includes('chat-sensitive-password'), false)
  assert.equal(JSON.stringify(store.messages).includes('123456'), false)
})
