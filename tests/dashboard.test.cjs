const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('control-plane/dashboard.html','utf8');
const js=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(js);
class Element {
 constructor(){this.options=[];this._value='';this.innerHTML='';}
 get value(){return this._value;}
 set value(v){this._value=v;}
 replaceChildren(){this.options=[];this._value='';}
 add(o){this.options.push(o);if(this.options.length===1)this._value=o.value;}
}
const elements={};const ctx={Set,Option:function(text,value){this.text=text;this.value=value;},
 document:{getElementById(id){return elements[id]||(elements[id]=new Element());}},
 escH:x=>String(x||''),nodeScore:()=>0.5,scoreBadge:()=>'',loadBadge:()=>'',
 tierClass:()=>'',statusDotClass:s=>s,formatUptime:x=>x};
vm.createContext(ctx);
vm.runInContext(js.slice(js.indexOf('function updateDiagnosticNodes('),js.indexOf('async function diagMeshNodes')),ctx);
vm.runInContext(js.slice(js.indexOf('async function refreshNodes('),js.indexOf('setInterval(refreshNodes')),ctx);
const nodes=[{node_id:'win',alias:'Windows',status:'active',endpoint:'http://win'},
 {node_id:'mac',status:'active',endpoint:'http://mac'},
 {node_id:'old',status:'unreachable',endpoint:''}];
ctx.updateDiagnosticNodes(nodes);
assert.equal(elements.chFrom.options.length,2);
assert.equal(elements.chTo.value,'mac');
assert.equal(elements.sendChatButton.disabled,false);
elements.chFrom.value='mac';elements.chTo.value='win';
ctx.updateDiagnosticNodes(nodes);
assert.equal(elements.chFrom.value,'mac');
assert.equal(elements.chTo.value,'win');
ctx.fetch=async()=>({ok:true,json:async()=>nodes});
(async()=>{await ctx.refreshNodes();
 assert.match(elements.nodesGrid.innerHTML,/win/);
 assert.doesNotMatch(elements.nodesGrid.innerHTML,/old/);
 assert.match(elements.inactiveNodesGrid.innerHTML,/old/);
 assert.equal(elements.inactiveNodes.hidden,false);
 ctx.updateDiagnosticNodes([nodes[0]]);assert.equal(elements.sendChatButton.disabled,true);
 ctx.updateDiagnosticNodes([]);assert.equal(elements.drNode.disabled,true);
 assert.equal(elements.sendDreamButton.disabled,true);
 console.log('PASS dashboard syntax, grouping, discovered-node selectors, refresh and empty states');
})().catch(e=>{console.error(e);process.exitCode=1;});
