const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.attributes = {}; this.value = '' }
  focus() {}
  querySelector() { return this.submit ||= new Node('button') }
  addEventListener(type, handler) { (this.listeners ||= {})[type] = handler }
  append(...items) { this.children.push(...items) }
  replaceChildren() { this.children = [] }
  setAttribute(k, v) { this.attributes[k] = v }
  set innerHTML(_) { throw Error('unsafe HTML') }
  get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(' ') }
  set textContent(v) { this.text = String(v) }
}
function load(stored = {}, full = false) {
  let storage = JSON.stringify(stored), calls = 0
  const nodes = Object.fromEntries(['send', 'question', 'help', 'toast'].map(id => [id, new Node('div')]))
  const context = vm.createContext({ document: { getElementById: id => nodes[id] || (full ? nodes[id] = new Node('div') : null), createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag), createTextNode: text => { const n = new Node('text'); n.textContent = text; return n } }, localStorage: { getItem: () => storage, setItem: (_, value) => storage = value }, setTimeout: () => {}, fetch: async () => { calls++; return { ok: true, json: async () => context.response } } })
  const html = fs.readFileSync(require('node:path').resolve(__dirname, '../../../demo/web/index.html'), 'utf8')
  vm.runInContext(html.split('<script>')[1].split(full ? "api('/api/health')" : "$('nav').addEventListener")[0], context)
  return { context, run: code => vm.runInContext(code, context), storage: () => storage, calls: () => calls, nodes }
}
test('legacy blocks credentials before title, messages, persistence and network', async () => {
  const h = load(); h.run("authToken='token';loggedInUser='user';activeChat={id:'c',title:'新对话',sessionId:'s',messages:[]};allChats.user=[activeChat]")
  for (const text of ['密码：secret123', '我的密码 123456，请查支出', 'password synthetic-password', '验证码123456', 'api_key=abcdef']) {
    await h.run(`sendQuestion(${JSON.stringify(text)})`)
    assert.equal(h.calls(), 0)
    assert.equal(h.run('activeChat.messages.length'), 0)
    assert.equal(h.run('activeChat.title'), '新对话')
    assert.equal(h.storage().includes(text), false)
  }
})
test('legacy removes stored secrets while retaining safe historical records', () => {
  const h = load({ user: [{ id: 'c', title: '密码：secret123', messages: [{role:'user',text:'密码：secret123'}, {role:'assistant',text:'安全的历史结果', result:{answer:'safe'}}, {role:'user',text:'上月支出'}] }] })
  assert.equal(h.storage().includes('secret123'), false)
  assert.equal(h.run('allChats.user[0].messages.length'), 2)
  assert.equal(h.run('allChats.user[0].title'), '新对话')
  assert.equal(h.run('allChats.user[0].messages[0].result.answer'), 'safe')
})
test('legacy preserves and safely renders simulated plans and readable table metadata', async () => {
  const h = load(); h.context.response = {status:'simulated',message:'模拟完成',result:{points_cents:[90000,50000],min_balance_cents:50000,min_day:2,feasible:true,assumptions:{floor_cents:30000},events:[{day:2,source:'<img onerror=boom>',signed_cents:-40000}]},scenarios:[{name:'延后购买',result:{points_cents:[90000,80000],min_balance_cents:80000,min_day:2,feasible:false,events:[],assumptions:{}}}],assumptions:['假设工资按时到账']}
  h.run("authToken='t';activeChat={id:'c',title:'查询',messages:[],sessionId:'s'};allChats.user=[activeChat]")
  await h.run("sendQuestion('模拟买电脑')")
  const m = h.run('activeChat.messages[1]'); assert.ok(m.result)
  const restored = load(JSON.parse(h.storage())); assert.equal(restored.run('allChats.user[0].messages[1].result.scenarios[0].name'), '延后购买')
  const card = new Node('div'); h.context.card = card; h.run('renderResult(card,activeChat.messages[1].result)')
  for (const text of ['现金流模拟','延后购买','最低余额','¥500.00','可行','存在余额风险','假设工资按时到账','最低保留余额','<img onerror=boom>']) assert.ok(card.textContent.includes(text), text)
  const walk = n => [n,...n.children.flatMap(walk)]
  assert.ok(walk(card).some(n => n.tag === 'polyline' || n.tag === 'path'))
  const table = new Node('div'); h.context.table = table; h.run("renderTable(table,[{group_key:'a',series_key:'b',value_cents:100}], 'money', '月份', '支出类别')")
  assert.ok(table.textContent.includes('月份')); assert.ok(table.textContent.includes('支出类别'))
})
test('legacy planning uses current main account and never invents an unavailable balance', () => {
  const h = load()
  h.run("accountSnapshot=[{account_id:'acc_other',account_name:'隔离账户',balance_cents:700000}]")
  assert.equal(h.run('calc().pts[0]'), 692000)
  let html = h.run('future()'); assert.ok(html.includes('隔离账户')); assert.ok(html.includes('¥7,000.00'))
  h.run("accountSnapshot=[{account_id:'acc_savings',account_name:'储蓄',balance_cents:1500000},{account_id:'acc_daily',account_name:'日常主账户',balance_cents:897500}]")
  assert.equal(h.run('calc().pts[0]'), 889500)
  html = h.run('future()'); assert.ok(html.includes('日常主账户')); assert.ok(html.includes('¥8,975.00'))
  h.run('accountSnapshot=[]'); assert.equal(h.run('calc()'), null)
  html = h.run('future()'); assert.ok(html.includes('账户余额暂不可用')); assert.ok(!html.includes('30天日终余额'))
})

function deferred() { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve } }
function setupUser(h, user) { h.run(`authToken='token-${user}';loggedInUser='${user}';activeChat={id:'chat-${user}',title:'新对话',sessionId:'session-${user}',messages:[]};allChats['${user}']=[activeChat]`) }
for (const outcome of ['answer', 'error', '401', '403']) test(`legacy discards delayed ${outcome} after logout and another login`, async () => {
  const h = load({}, true), pending = deferred(); setupUser(h, 'A')
  h.context.fetch = path => path === '/api/ask' ? pending.promise : Promise.resolve({ok:true,json:async()=>path === '/api/auth/login' ? {auth_token:'token-B',username:'B'} : path === '/api/sessions' ? {session_id:'session-B'} : {accounts:[]}})
  const sending = h.run("sendQuestion('上月支出')")
  await h.run('logout()'); h.run("show=()=>{};allChats.B=[{id:'chat-B',title:'新对话',sessionId:null,messages:[]}];$('authUser').value='B';$('authPassword').value='B-password'"); await h.nodes.authForm.listeners.submit({preventDefault(){}})
  pending.resolve({ok: outcome === 'answer', status: Number(outcome) || 500, json: async () => outcome === 'answer' ? {status:'answer',answer:'A PRIVATE',rows:[]} : {detail:'A ERROR'}})
  await sending
  assert.equal(h.run('authToken'), 'token-B')
  assert.equal(h.run('activeChat.messages.length'), 0)
  assert.equal(h.storage().includes('A PRIVATE'), false)
  assert.equal(h.storage().includes('A ERROR'), false)
})
test('legacy delayed answer stays in originating chat after chat switch', async () => {
  const h = load({}, true), pending = deferred(); setupUser(h, 'A'); h.run('origin=activeChat')
  h.context.fetch = () => pending.promise
  const sending = h.run("sendQuestion('上月支出')")
  h.run("activeChat={id:'other',sessionId:'other-session',title:'另一对话',messages:[]};allChats.A.push(activeChat)")
  pending.resolve({ok:true,json:async()=>({status:'answer',answer:'original answer',rows:[]})}); await sending
  assert.equal(h.run('activeChat.messages.length'),0)
  assert.equal(h.run('origin.messages[1].text'),'original answer')
})
test('legacy keeps rightful errors and expires only current authentication', async () => {
  const h=load({},true);setupUser(h,'A')
  h.context.fetch=async()=>({ok:false,status:500,json:async()=>({detail:'rightful error'})})
  await h.run("sendQuestion('上月支出')");assert.equal(h.run('activeChat.messages[1].text'),'rightful error')
  h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'expired'})})
  await h.run("sendQuestion('上月收入')");assert.equal(h.run('authToken'),null)
})
for(const operation of ['newChat()', 'refreshAccounts()']) test(`legacy discards stale ${operation}`,async()=>{
  const h=load({},true),pending=deferred();setupUser(h,'A');h.context.crypto={randomUUID:()=> 'new-chat'};h.context.fetch=()=>pending.promise
  const task=h.run(operation);h.run('showLogin()');setupUser(h,'B')
  pending.resolve({ok:true,json:async()=>({session_id:'A-session',accounts:[{balance_cents:12345}]})});await task
  assert.equal(h.run('activeChat.id'),'chat-B');assert.equal(h.run('accountSnapshot.length'),0)
})
for(const authorized of [false,true]) for(const status of [200,401,403,404]) test(`legacy stale action ${authorized?'confirm':'authorize'} ${status} cannot affect another login`,async()=>{
  const h=load({},true),pending=deferred();setupUser(h,'A')
  h.run(`reauthMessage={actionId:'A-action',actionStatus:'pending',text:'A transfer'${authorized?",authorizationId:'A-auth'":''}};oldMessage=reauthMessage`)
  h.context.fetch=()=>pending.promise
  const task=h.nodes.reauthForm.listeners.submit({preventDefault(){},target:h.nodes.reauthForm})
  h.run('showLogin()');setupUser(h,'B');h.run("reauthMessage={actionId:'B-action',text:'B transfer'}")
  pending.resolve({ok:status===200,status,json:async()=>({authorization_id:'A-authorization',message:'A private receipt',detail:'A error'})});await task
  assert.equal(h.run('authToken'),'token-B');assert.equal(h.run('activeChat.sessionId'),'session-B')
  assert.equal(h.run('reauthMessage.text'),'B transfer');assert.equal(h.run('reauthMessage.authorizationId'),undefined)
  assert.equal(h.run('oldMessage.actionStatus'),'pending');assert.equal(h.storage().includes('A private receipt'),false)
})
test('legacy wrong reauthentication password displays error without logout',async()=>{
  const h=load({},true);setupUser(h,'A');h.run("reauthMessage={actionId:'action',text:'transfer'}")
  h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'wrong password'})})
  await h.nodes.reauthForm.listeners.submit({preventDefault(){},target:h.nodes.reauthForm})
  assert.equal(h.run('authToken'),'token-A');assert.equal(h.nodes.reauthMessage.textContent,'wrong password')
})
test('legacy delayed login cannot replace newer login',async()=>{
  const h=load({},true),pending=deferred();h.context.fetch=()=>pending.promise
  h.run("$('authUser').value='A';$('authPassword').value='A-password'")
  const task=h.nodes.authForm.listeners.submit({preventDefault(){}})
  h.run('showLogin()');setupUser(h,'B')
  pending.resolve({ok:true,json:async()=>({auth_token:'A-token',username:'A'})});await task
  assert.equal(h.run('authToken'),'token-B');assert.equal(h.run('activeChat.sessionId'),'session-B')
})
test('legacy canceled action response cannot authorize a reopened dialog',async()=>{
  const h=load({},true),pending=deferred();setupUser(h,'A');h.run("action={actionId:'action',text:'transfer'};confirmAction(action)")
  h.context.fetch=()=>pending.promise
  const task=h.nodes.reauthForm.listeners.submit({preventDefault(){},target:h.nodes.reauthForm})
  h.run('resetReauth();confirmAction(action)')
  pending.resolve({ok:true,json:async()=>({authorization_id:'obsolete'})});await task
  assert.equal(h.run('action.authorizationId'),undefined);assert.equal(h.nodes.reauthPassword.disabled,false)
})
