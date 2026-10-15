// Lightweight offline runtime smoke test; does not replace visual/browser QA.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'prototype/index.html'),'utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes={};const document={getElementById(id){return nodes[id]??=( {innerHTML:'',textContent:'',value:'',classList:{add(){},remove(){}},showModal(){},close(){}})}};
const context={document,window:{},setTimeout(){},clearTimeout(){},fetch:async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync(path.join(root,'prototype/sample-summary.json'),'utf8'))})};
vm.createContext(context);vm.runInContext(code,context);
setImmediate(()=>{
 for(const p of ['home','bills','subs','future','tasks','settings','review']){
  vm.runInContext(`show('${p}')`,context);assert(nodes.content.innerHTML.length>200,p);
  assert(!nodes.content.innerHTML.includes('NaN'),p);assert(!nodes.content.innerHTML.includes('undefined'),p);
 }
 const cases=JSON.parse(fs.readFileSync(path.join(root,'benchmark/cashflow_cases.json'),'utf8'));
 for(const c of cases){const r=vm.runInContext(`calc(${c.salary_day},${c.purchase_day},8000)`,context);assert.deepStrictEqual(Array.from(r.pts),c.expected.points_cents,c.id);assert.equal(r.min,c.expected.min_balance_cents,c.id);}
 document.getElementById('ask').value='工资晚5天';vm.runInContext('ask()',context);assert(nodes.content.innerHTML.includes('工资第17天'));assert(nodes.content.innerHTML.includes('电脑第19天'));
 console.log('Prototype runtime: 7 routes + 30 cashflow scenarios + delayed-salary interaction passed; visual QA not included.');
});
