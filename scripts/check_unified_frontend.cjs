// Runtime smoke test for the single live FutureLedger frontend.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'demo/web/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert(scripts.length, 'live frontend must contain an inline script');
const code = scripts.at(-1)[1];

function node(tag = 'div') {
  return {
    tag,
    innerHTML: '', textContent: '', value: '', hidden: false, disabled: false,
    className: '', style: {}, dataset: {}, children: [], attributes: {},
    listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; }, focus() {},
    showModal() {}, close() {}, querySelector() { return node(); },
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
  };
}

const nodes = {};
const document = {
  getElementById(id) { return nodes[id] ??= node(); },
  createElement(tag) { return node(tag); },
  createElementNS(namespaceURI, tag) { const item = node(tag); item.namespaceURI = namespaceURI; return item; },
  createTextNode(text) { return {tag: '#text', textContent: String(text), children: []}; },
};
const context = {
  document,
  window: {},
  localStorage: {getItem() { return '{}'; }, setItem() {}},
  crypto: {randomUUID() { return 'smoke-id'; }},
  fetch: async () => ({ok: true, json: async () => ({status: 'ready', provider: 'local'})}),
  setTimeout() {}, clearTimeout() {}, console,
};
vm.createContext(context);
vm.runInContext(code, context);

context.localStorage.getItem = () => '{"test_user":{}}';
let sanitized = vm.runInContext('loadChats()', context);
assert.deepStrictEqual(Object.keys(sanitized), ['test_user']);
assert(Array.isArray(sanitized.test_user), 'malformed per-user storage must become an empty chat list');
context.localStorage.getItem = () => '{"test_user":[{}]}';
sanitized = vm.runInContext('loadChats()', context);
assert(Array.isArray(sanitized.test_user) && sanitized.test_user.length === 0, 'malformed chat entries must be discarded');

function descendants(item) {
  return [item, ...(item.children || []).flatMap(descendants)];
}
vm.runInContext("loggedInUser='test_user'; activeChat={sessionId:'session-chart',messages:[]}", context);
context.chartCard = node('section');
vm.runInContext("renderResult(chartCard,{answer:'分类',chart:'bar',rows:[{group_key:'餐饮',value_cents:1200}],valueFormat:'money'})", context);
assert(descendants(context.chartCard).some(item => item.style?.cssText?.includes('height:9px')), 'bar results must render bars');
context.chartCard = node('section');
vm.runInContext("renderResult(chartCard,{answer:'趋势',chart:'line',rows:[{group_key:'2026-09',series_key:'餐饮',value_cents:1200}],valueFormat:'money'})", context);
assert(descendants(context.chartCard).some(item => item.tag === 'svg'), 'line results must render an SVG chart');
assert(html.includes('chart:response.chart'), 'chat responses must retain the API chart type');

const preservedPanels = {
  home: ['从一件事开始', '一个目标，几种安排', '接下来的固定支出'],
  bills: ['按类别看净支出', '账单问答', '发现需核查的线索'],
  subs: ['黄色 · 查询范围确认示意', '模拟云盘', '模拟健身'],
  future: ['30天日终余额', '调整假设', '方案说明', '查看30天余额表'],
  tasks: ['任务进度，一眼看清', '请求超时 ≠ 转账失败'],
  settings: ['最低留存余额', '保护云盘代扣', '规划账户'],
  review: ['离线参考对齐', '银行/安全验收', '未来执行轨迹应包含'],
  agent: ['智能体小智', 'agentPage'],
};

for (const route of Object.keys(preservedPanels)) {
  vm.runInContext(`show('${route}')`, context);
  assert(nodes.content.innerHTML.length > 150, route + ' must render meaningful content');
  assert(!nodes.content.innerHTML.includes('NaN'), route + ' rendered NaN');
  assert(!nodes.content.innerHTML.includes('undefined'), route + ' rendered undefined');
  for (const marker of preservedPanels[route]) {
    assert(nodes.content.innerHTML.includes(marker), route + ' lost existing panel: ' + marker);
  }
}

const nav = html.split('<nav id="nav"', 2)[1].split('</nav>', 1)[0];
assert(nav.indexOf('评审工作台') < nav.indexOf('智能体小智'));
assert(html.includes('id="authGate"'));
assert(html.includes('id="assistantRail"'));
const mobileCss = html.split('@media(max-width:730px)', 2)[1].split('</style>', 1)[0];
assert(!mobileCss.includes('.nav-user,.nav-note'), 'mobile layout must keep the only logout control reachable');

(async () => {
  let sessionCalls = 0;
  let accountCalls = 0;
  context.fetch = async path => {
    if (path === '/api/auth/login') {
      return {ok: true, status: 200, json: async () => ({auth_token: 'token', username: 'test_user'})};
    }
    if (path === '/api/sessions') {
      sessionCalls += 1;
      return {ok: true, status: 200, json: async () => ({session_id: 'session-' + sessionCalls})};
    }
    if (path.startsWith('/api/accounts?')) {
      accountCalls += 1;
      return {ok: true, status: 200, json: async () => ({
        status: 'ready', total_balance_cents: 1397500,
        accounts: [
          {account_id: 'acc_daily', account_name: '主账户', balance_cents: 897500},
          {account_id: 'acc_savings', account_name: '储蓄账户', balance_cents: 500000},
        ],
      })};
    }
    return {ok: true, status: 200, json: async () => ({status: 'ready', provider: 'local'})};
  };
  document.getElementById('authUser').value = 'test_user';
  document.getElementById('authPassword').value = 'test-password-123';
  await nodes.authForm.listeners.submit({preventDefault() {}});
  assert.strictEqual(sessionCalls, 1, 'login must create or renew exactly one server session');
  assert.strictEqual(accountCalls, 1, 'login must load the authoritative account snapshot');
  vm.runInContext("show('home')", context);
  assert(nodes.content.innerHTML.includes('¥8,975.00'), 'home must render the runtime checking balance');
  assert(nodes.content.innerHTML.includes('¥5,000.00'), 'home must render the runtime savings balance');
  vm.runInContext("accountSnapshot=[{account_id:'acc_other',account_name:'演示账户',balance_cents:702500}]; show('home')", context);
  assert(nodes.content.innerHTML.includes('演示账户'), 'home must label a single alternate user account');
  assert(nodes.content.innerHTML.includes('¥7,025.00'), 'home must render a single alternate user balance');
  vm.runInContext("accountSnapshot=[{account_id:'acc_daily',account_name:'主账户',balance_cents:897500},{account_id:'acc_savings',account_name:'储蓄账户',balance_cents:500000}]", context);

  const submit = node('button');
  const transferForm = node('form');
  transferForm.querySelector = () => submit;
  context.transferMessage = {role: 'assistant', text: '确认转账', actionId: 'action-1', actionStatus: 'pending'};
  vm.runInContext('confirmAction(transferMessage)', context);
  nodes.reauthPassword.required = true;
  nodes.reauthPassword.value = 'test-password-123';
  context.fetch = async path => {
    assert(path.endsWith('/authorize'));
    return {ok: true, status: 200, json: async () => ({authorization_id: 'authorization-1'})};
  };
  await nodes.reauthForm.listeners.submit({preventDefault() {}, target: transferForm});
  assert.strictEqual(nodes.reauthPassword.required, false, 'final transfer confirmation must not require a second password');
  assert.strictEqual(nodes.reauthPassword.disabled, true, 'verified password input must remain disabled during final confirmation');

  context.fetch = async path => {
    if (path.endsWith('/confirm')) {
      return {ok: true, status: 200, json: async () => ({message: '转账成功'})};
    }
    assert(path.startsWith('/api/accounts?'));
    accountCalls += 1;
    return {ok: true, status: 200, json: async () => ({
      status: 'ready', total_balance_cents: 1395000,
      accounts: [
        {account_id: 'acc_daily', account_name: '主账户', balance_cents: 895000},
        {account_id: 'acc_savings', account_name: '储蓄账户', balance_cents: 500000},
      ],
    })};
  };
  await nodes.reauthForm.listeners.submit({preventDefault() {}, target: transferForm});
  assert.strictEqual(nodes.reauthGate.hidden, true, 'successful final confirmation must close reauthentication');
  assert.strictEqual(nodes.reauthPassword.required, true, 'a later transfer must require its own password');
  assert.strictEqual(accountCalls, 2, 'confirmed transfer must refresh the account snapshot');
  vm.runInContext("show('home')", context);
  assert(nodes.content.innerHTML.includes('¥8,950.00'), 'home must show the post-transfer checking balance');

  context.accountCard = node('section');
  vm.runInContext("renderResult(accountCard,{answer:'余额',chart:'table',rows:[{account_id:'acc_savings',account_name:'储蓄账户',balance_cents:500000}],valueFormat:'money'})", context);
  const accountTableText = descendants(context.accountCard).map(item => item.textContent).filter(Boolean);
  assert(accountTableText.includes('账户'), 'account result tables must label the account-name column');
  assert(accountTableText.includes('¥5,000.00'), 'account result tables must format balance cents as currency');

  context.refreshFailureMessage = {role: 'assistant', text: '确认转账', actionId: 'action-refresh', actionStatus: 'pending', authorizationId: 'authorization-refresh'};
  vm.runInContext('confirmAction(refreshFailureMessage)', context);
  context.refreshFailureMessage.authorizationId = 'authorization-refresh';
  context.fetch = async path => path.endsWith('/confirm') ? ({
    ok: true,
    status: 200,
    json: async () => ({message: '转账成功'}),
  }) : Promise.reject(new Error('account refresh offline'));
  await nodes.reauthForm.listeners.submit({preventDefault() {}, target: transferForm});
  assert.strictEqual(context.refreshFailureMessage.actionStatus, 'done', 'a completed transfer must remain completed when balance refresh fails');
  assert.strictEqual(nodes.reauthGate.hidden, true, 'a completed transfer must close confirmation even when balance refresh fails');
  assert(context.refreshFailureMessage.text.includes('余额刷新失败'), 'a completed transfer must report a separate balance refresh warning');

  let finishStaleAccountRequest;
  context.fetch = () => new Promise(resolve => { finishStaleAccountRequest = resolve; });
  vm.runInContext("activeChat={sessionId:'session-a',messages:[]}; loggedInUser='user-a'; accountSnapshot=[]", context);
  const staleRefresh = vm.runInContext('refreshAccounts()', context);
  vm.runInContext("activeChat={sessionId:'session-b',messages:[]}; loggedInUser='user-b'; accountSnapshot=[{account_id:'acc_other',account_name:'演示账户',balance_cents:702500}]", context);
  finishStaleAccountRequest({ok: true, status: 200, json: async () => ({
    status: 'ready',
    accounts: [{account_id: 'acc_daily', account_name: '主账户', balance_cents: 111100}],
  })});
  await staleRefresh;
  assert.strictEqual(vm.runInContext('accountSnapshot[0].account_id', context), 'acc_other', 'a stale account response must not overwrite the new login');

  context.wrongPasswordMessage = {role: 'assistant', text: '确认转账', actionId: 'action-2', actionStatus: 'pending'};
  vm.runInContext("authToken='token'; confirmAction(wrongPasswordMessage)", context);
  nodes.reauthPassword.value = 'wrong-password';
  context.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({detail: {code: 'BAD_PASSWORD', message: '密码错误'}}),
  });
  await nodes.reauthForm.listeners.submit({preventDefault() {}, target: transferForm});
  assert.strictEqual(vm.runInContext('authToken', context), 'token', 'wrong transfer password must not log out the app session');
  assert.strictEqual(nodes.reauthGate.hidden, false, 'wrong transfer password must remain retryable in reauthentication');

  context.restartMessage = {role: 'assistant', text: '确认转账', actionId: 'lost-action', actionStatus: 'pending'};
  vm.runInContext("confirmAction(restartMessage); $('appShell').hidden=false; $('authGate').hidden=true", context);
  context.fetch = async path => path === '/api/sessions' ? ({
    ok: false,
    status: 401,
    json: async () => ({detail: {code: 'AUTH_REQUIRED', message: '登录已失效'}}),
  }) : ({
    ok: false,
    status: 404,
    json: async () => ({detail: {code: 'ACTION_NOT_FOUND', message: '操作不存在'}}),
  });
  await nodes.reauthForm.listeners.submit({preventDefault() {}, target: transferForm});
  assert.strictEqual(nodes.appShell.hidden, true, 'lost action plus expired session must hide stale app content');
  assert.strictEqual(nodes.authGate.hidden, false, 'lost action after restart must return to login');
  assert.strictEqual(nodes.reauthGate.hidden, true, 'returning to login must dismiss the transfer overlay');

  context.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({detail: {code: 'AUTH_REQUIRED', message: '登录已失效'}}),
  });
  vm.runInContext("authToken='expired'; $('appShell').hidden=false; $('authGate').hidden=true", context);
  await assert.rejects(vm.runInContext("api('/api/sessions', {})", context), /登录已失效/);
  assert.strictEqual(nodes.appShell.hidden, true, 'expired auth must hide the application');
  assert.strictEqual(nodes.authGate.hidden, false, 'expired auth must return to login');
  assert.strictEqual(vm.runInContext('authToken', context), null, 'expired auth token must be cleared');
  console.log('Unified frontend runtime: 8 routes, preserved panels, login recovery, assistant rail and agent navigation passed.');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
