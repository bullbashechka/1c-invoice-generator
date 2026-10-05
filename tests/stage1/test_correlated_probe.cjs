"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {openCorrelatedTaskProbe, readCorrelatedTask} = require("../../src/stage-1-diagnostics/correlated_probe.js");
const desktop = {self:{}, top:{}, BXDesktopSystem:{ExecuteCommand() {}}};
const operationId = "diag-20261005-001";

test("correlated diagnostic refuses browser before invoking openPath", () => {
    let calls = 0;
    const result = openCorrelatedTaskProbe({openPath() {calls++;}}, {
        windowRef:{self:{}, top:{}}, operationId, onResult() {},
    });
    assert.equal(result.status, "desktop_bridge_unavailable");
    assert.equal(calls, 0);
});

test("desktop probe carries exact operation tag and preserves edited text contract", () => {
    let path;
    openCorrelatedTaskProbe({openPath(value) {path=value;}}, {
        windowRef:desktop, operationId, onResult() {},
    });
    const url = new URL(decodeURIComponent(path), "https://example.invalid");
    assert.equal(url.searchParams.get("TAGS"), `КА-${operationId}`);
    assert.equal(url.searchParams.get("GROUP_ID"), "36");
});

test("task lookup rereads and rejects a removed tag rather than trusting list result", async () => {
    const calls = [];
    const client = {callMethod(method, params, cb) {
        calls.push(method);
        cb({error:()=>null, data:()=>method === "tasks.task.list"
            ? {tasks:[{id:"123"}]} : {task:{id:"123", groupId:"36", tags:[]}}});
    }};
    const result = await readCorrelatedTask(client, operationId);
    assert.deepEqual(calls, ["tasks.task.list", "tasks.task.get"]);
    assert.equal(result.status, "unknown");
});

test("lookup follows SDK pages and reports duplicate exact tags as conflict", async () => {
    const reads = [];
    const client = {callMethod(method, params, cb) {
        if (method === "tasks.task.list") return cb({error:()=>null,data:()=>({tasks:[{id:"123"}]}),
            more:()=>true,next(next) {next({error:()=>null,data:()=>({tasks:[{id:"456"}]}),more:()=>false});}});
        reads.push(params.id);
        cb({error:()=>null,data:()=>({task:{id:params.id,tags:[`КА-${operationId}`]}})});
    }};
    const result = await readCorrelatedTask(client,operationId);
    assert.deepEqual(reads,["123","456"]);
    assert.equal(result.status,"conflict");
});

test("one exact reread result returns task ID without relying on title or description", async () => {
    const client = {callMethod(method,params,cb) {cb({error:()=>null,data:()=>method === "tasks.task.list"
        ? {tasks:[{id:"123"}]} : {task:{id:"123",tags:[`КА-${operationId}`],title:"Ручное изменение"}}});}};
    assert.deepEqual(await readCorrelatedTask(client,operationId),{
        status:"created",operationId,tag:`КА-${operationId}`,taskIds:["123"],
    });
});

test("sparse numeric tag map from the live portal is read without substring matching", async () => {
    const client={callMethod(method,params,cb) {cb({error:()=>null,data:()=>method === "tasks.task.list"
        ? {tasks:[{id:"123"}]} : {task:{id:"123",tags:{"17":`КА-${operationId}`}}}});}};
    assert.equal((await readCorrelatedTask(client,operationId)).status,"created");
});

test("live portal tag records expose exact title", async () => {
 const client={callMethod(method,params,cb) {cb({error:()=>null,data:()=>method === "tasks.task.list"
 ? {tasks:[{id:"9926"}]} : {task:{id:"9926",tags:{"4":{id:4,title:`КА-${operationId}`}}}}});}};
 assert.equal((await readCorrelatedTask(client,operationId)).status,"created");
});
