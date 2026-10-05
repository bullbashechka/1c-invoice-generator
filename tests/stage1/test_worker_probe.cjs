"use strict";
const {test}=require("node:test");const assert=require("node:assert/strict");
const {bindPersonalWorkerDiagnostic}=require("../../src/stage-1-diagnostics/worker_probe.js");
test("worker diagnostic binds USER_ID and verifies saved handler and error URL",async()=>{
 const rows=[];const calls=[];
 const client={callMethod(method,params,cb){calls.push({method,params});
 if(method==="placement.bind"){rows.push({placement:params.PLACEMENT,handler:params.HANDLER,userId:params.USER_ID,options:params.OPTIONS});}
 cb({error:()=>null,data:()=>method==="user.current"?{ID:"30"}:method==="placement.get"?rows:true});}};
 const result=await bindPersonalWorkerDiagnostic(client,"https://cdn.example/package/index.html");
 assert.equal(result.status,"bound");const bind=calls.find(c=>c.method==="placement.bind").params;
 assert.equal(bind.USER_ID,30);assert.equal(bind.HANDLER,"https://cdn.example/package/worker.html");
 assert.deepEqual(bind.OPTIONS,{errorHandlerUrl:"https://cdn.example/package/worker-error.html"});
});
