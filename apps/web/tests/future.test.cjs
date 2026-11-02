const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const ts = require('typescript'), { parse, compileScript } = require('@vue/compiler-sfc')
const { createRenderer, reactive, nextTick } = require('vue')
global.Document = class Document {}
global.document = Object.assign(new Document(), { activeElement: null })

// Exercise actual compiled template events and reactive SVG properties through Vue.
function mount(simulate) {
  const workspace = reactive({ sessionId: 'session-a', simulate })
  const filename = path.resolve(__dirname, '../src/views/FutureView.vue')
  const { descriptor } = parse(fs.readFileSync(filename, 'utf8'))
  const source = compileScript(descriptor, { id: 'future-test', inlineTemplate: true }).content
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const context = { exports: {}, require: name => name === '../stores/workspace' ? { useWorkspace: () => workspace } : require(name) }
  vm.runInNewContext(code, context, { filename })
  const renderer = createRenderer({
    createElement: type => ({ type, tagName: type.toUpperCase(), props: {}, children: [], addEventListener() {}, getRootNode() { return global.document }, getAttribute(key) { return this.props[key] } }),
    createText: text => ({ type: '#text', text }), createComment: text => ({ type: '#comment', text }),
    setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text; node.children = [] },
    patchProp: (node, key, old, value) => { node.props[key] = value },
    insert: (node, parent, anchor) => { node.parent = parent; const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, node) },
    remove: node => { const list = node.parent.children; list.splice(list.indexOf(node), 1) },
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
  })
  const root = { children: [] }, app = renderer.createApp(context.exports.default)
  app.mount(root)
  function all(type, node = root) { return [...(node.type === type ? [node] : []), ...(node.children || []).flatMap(child => all(type, child))] }
  return { workspace, app, all, run: () => all('button')[0].props.onClick() }
}
const result = { feasible: true, min_balance_cents: 400000, min_day: 1, assumptions: { floor_cents: 300000, goal_cents: 600000 }, points_cents: [400000, 500000], events: [] }

test('editing the next simulation floor preserves the completed chart and card snapshot', async () => {
  const view = mount(async () => ({ result, assumptions: [] }))
  await view.run(); await nextTick()
  const pathBefore = view.all('path')[0].props.d, floorBefore = view.all('line')[2].props.y1
  const cardsBefore = view.all('strong').map(node => node.text)
  view.all('input')[3].props['onUpdate:modelValue'](1000000)
  await nextTick()
  assert.equal(view.all('input')[3].value, 1000000)
  assert.equal(view.all('path')[0].props.d, pathBefore)
  assert.equal(view.all('line')[2].props.y1, floorBefore)
  assert.deepEqual(view.all('strong').map(node => node.text), cardsBefore)
  view.app.unmount()
})

test('session changes discard completed and pending simulation results', async () => {
  let resolve
  const view = mount(async () => ({ result, assumptions: [] }))
  await view.run(); await nextTick()
  assert.equal(view.all('path').length, 1)
  view.workspace.sessionId = ''
  await nextTick()
  assert.equal(view.all('path').length, 0)
  view.workspace.sessionId = 'session-b'
  view.workspace.simulate = () => new Promise(done => { resolve = done })
  const pending = view.run()
  view.workspace.sessionId = 'session-c'
  await nextTick()
  resolve({ result, assumptions: ['private assumption'] })
  await pending; await nextTick()
  assert.equal(view.all('path').length, 0)
  assert.equal(view.all('button')[0].props.disabled, false)
  view.app.unmount()
})
