const {test}=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm')
const ts=require('typescript'), {parse,compileScript}=require('@vue/compiler-sfc')
const {createSSRApp}=require('vue'), {renderToString}=require('@vue/server-renderer')
async function render(name,props){
 const filename=path.resolve(__dirname,'../src/components',name+'.vue')
 const {descriptor}=parse(fs.readFileSync(filename,'utf8'))
 const code=compileScript(descriptor,{id:'test',inlineTemplate:true}).content
 const output=ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const context={exports:{},require};vm.runInNewContext(output,context)
 return renderToString(createSSRApp(context.exports.default,props))
}
test('count tables hide identifiers and use semantic labels',async()=>{
 const html=await render('ResultCard',{result:{chart:'table',value_format:'count',group_label:'类别',rows:[{group_key:'餐饮',value_cents:52,account_id:'secret',balance_cents:1200,kind:'expense',direction:'OUT'}]}})
 assert.match(html,/52笔/);assert.doesNotMatch(html,/secret|¥0.52/);assert.match(html,/类别/);assert.match(html,/余额/);assert.match(html,/支出/);assert.match(html,/流出/)
})
test('count bars show counts and negative values have visible geometry',async()=>{
 const html=await render('ResultCard',{result:{chart:'bar',value_format:'count',rows:[{group_key:'餐饮',value_cents:-52}]}})
 assert.match(html,/-52笔/);assert.doesNotMatch(html,/¥0.52/);assert.match(html,/width:\s*100%/)
})
test('planning renders curves, comparisons, assumptions and escaped content',async()=>{
 const result={points_cents:[10000,-2000],min_balance_cents:-2000,min_day:2,feasible:false,assumptions:{floor_cents:3000,days:2},events:[],conflicts:[{day:2,floor_cents:3000}]}
 const html=await render('PlanningResult',{result,scenarios:[{name:'<script>bad</script>',result}],assumptions:['工资按时到账']})
 assert.match(html,/工资按时到账/);assert.match(html,/第2天/);assert.match(html,/¥-20.00/);assert.match(html,/<path/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/)
})

test('group labels fall back to answer semantics and lines follow each series',async()=>{
 const html=await render('ResultCard',{result:{chart:'table',answer:'按商户汇总',rows:[{group_key:'店铺',value_cents:100}]}})
 assert.match(html,/<th>商户<\/th>/)
 const line=await render('ResultCard',{result:{chart:'line',rows:[{group_key:'1月',series_key:'收入',value_cents:100},{group_key:'2月',series_key:'收入',value_cents:200},{group_key:'1月',series_key:'支出',value_cents:-100},{group_key:'2月',series_key:'支出',value_cents:-200}]}})
 const paths=[...line.matchAll(/<path d="([^"]+)"/g)].map(match=>match[1])
 assert.equal(paths.length,2);assert.notEqual(paths[0],paths[1]);assert.match(line,/¥-2.00/)
})

test('purchase recommendation stays primary with deterministic evidence collapsed',async()=>{
 const result={points_cents:[400000,500000],min_balance_cents:400000,min_day:1,feasible:true,assumptions:{floor_cents:300000,goal_cents:600000},events:[{day:1,source:'salary',signed_cents:100000}],conflicts:[]}
 const html=await render('PlanningResult',{result,earliestPurchaseDay:17,assumptions:['工资按时到账']})
 assert.match(html,/建议在第17天或之后购买/)
 assert.match(html,/<details[^>]*><summary>查看模拟依据与方案比较<\/summary>/)
 assert.ok(html.indexOf('建议在第17天')<html.indexOf('<details'))
 assert.match(html,/工资按时到账/);assert.match(html,/工资/);assert.match(html,/¥1,000.00/)
})
test('zero goal is a cashflow projection without a purchase rejection',async()=>{
 const result={points_cents:[400000,500000],min_balance_cents:400000,min_day:1,feasible:true,assumptions:{floor_cents:300000,goal_cents:0},events:[],conflicts:[]}
 const html=await render('PlanningResult',{result,earliestPurchaseDay:null})
 assert.match(html,/现金流预测/);assert.match(html,/最低余额为¥4,000.00/)
 assert.doesNotMatch(html,/购买建议|没有满足.*购买日期|如果现在购买/)
})
