const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const src=html.slice(html.indexOf('function sellEvaluate('),html.indexOf('function sellResults('));
const context={SELL_NAMES:{stop:1,profit:1,ma:1,candle:1},posStart:h=>h.start};
vm.createContext(context);vm.runInContext(src,context);
const now=Date.now(),day=22000;
function bars(){return {L:65,ser:Array.from({length:65},(_,i)=>day+i),t:Array.from({length:65},(_,i)=>(now/1000)-(64-i)*86400),c:Array(65).fill(100),o:Array(65).fill(99),h:Array(65).fill(120)}}
const h={avg:100,start:day};
test('수익폭 2/3, 손절 경계와 미완성 봉 제외',()=>{
 const x=bars();x.c[64]=110;
 let a=context.sellEvaluate(h,{profit:true},x,false,now)[0];
 assert.equal(a.state,'hit');assert.ok(Math.abs(a.threshold-113.333333)<.001);
 assert.equal(context.sellEvaluate(h,{stop:true,stopPct:5},x,false,now)[0].state,'clear');
 x.c[64]=95;assert.equal(context.sellEvaluate(h,{stop:true,stopPct:5},x,false,now)[0].state,'hit');
 assert.equal(context.sellEvaluate(h,{stop:true,stopPct:5},x,true,now)[0].state,'clear');
});
test('고가 이력 부족, 오래된 시세, 60일선 부족',()=>{
 let x=bars();assert.equal(context.sellEvaluate({...h,start:day-1},{profit:true},x,false,now)[0].state,'missing');
 assert.equal(context.sellEvaluate(h,{stop:true,stopPct:5},x,false,now+8*864e5)[0].state,'stale');
 x.L=30;x.t[29]=now/1000;assert.equal(context.sellEvaluate(h,{ma:true,period:60},x,false,now)[0].state,'missing');
});
test('독립된 음봉 및 갭 패턴',()=>{
 const x=bars();for(let i=62;i<65;i++){x.o[i]=101;x.c[i]=100}
 assert.equal(context.sellEvaluate(h,{candle:true},x,false,now)[0].state,'hit');
});
