"use strict";

const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");

test("closing a disabled worker page does not raise and never opens a task",async()=>{
    const html=fs.readFileSync(require.resolve("../../src/stage-1-diagnostics/worker.html"),"utf8");
    const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
    let onHide;
    const window={STAGE1_WORKER_CONFIG:{enabled:false},addEventListener(name,fn){
        assert.equal(name,"pagehide");onHide=fn;
    }};
    const {startBackgroundWorker}=require("../../src/stage-1-diagnostics/background_worker.js");
    vm.runInNewContext(script,{window,startBackgroundWorker});
    await Promise.resolve();
    assert.equal(window.STAGE1_WORKER_STATUS,"disabled");
    assert.doesNotThrow(()=>onHide());
});
