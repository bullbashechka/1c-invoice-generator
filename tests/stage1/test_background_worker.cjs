"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { buildTaskPath, startBackgroundWorker, readWorkplaceProfile } = require("../../src/stage-1-diagnostics/background_worker.js");

function entityItem(message, itemId) {
    return {
        ID:itemId,
        NAME:message.messageId,
        PROPERTY_VALUES:{
            MESSAGE_ID:message.messageId, MESSAGE_TYPE:message.type,
            OPERATION_ID:message.operationId, BASE_ID:message.baseId,
            ORDER_ID:message.orderId, INITIATOR_ID:message.initiatorId,
            WORKPLACE_ID:message.workplaceId, SESSION_ID:message.sessionId,
            INSTANCE_ID:message.instanceId || "", PERMISSION_ID:message.permissionId || "",
            TASK_ID:message.taskId || "", PAYLOAD_JSON:JSON.stringify(message.payload || {}),
            EXPIRES_AT:String(message.expiresAt || ""),
        },
    };
}

function createEnvironment({outgoing = [], desktop = true, activationAvailable = true,
    activationFailure = false, activationNeverForeground = false, frame = true, userId = "30",
    openDelayMs = 0, openResult} = {}) {
    const calls = [];
    const writes = [];
    const store = new Map();
    const timers = new Map();
    let sequence = 0;
    let timestamp = 100000;
    let activeTab = false;
    let foreground = false;
    let openedPath;
    let openCallback;
    let timerCallback;
    const desktopCalls = [];
    const uuid = () => `id-${++sequence}`;
    const localStorage = {
        getItem(key) { return store.get(key) || null; },
        setItem(key, value) { store.set(key, String(value)); },
    };
    store.set("stage1.workplace.bitrix24.30", "workplace-1");
    const result = value => ({error:() => null, data:() => value});
    const bx24 = {
        init(callback) { callback(); },
        callMethod(method, params, callback) {
            calls.push({method, params});
            if (method === "user.current") return callback(result({ID:userId}));
            if (method === "entity.item.get") {
                return callback(result(params.ENTITY === `Q_${userId}` ? outgoing : writes));
            }
            if (method === "entity.item.add") {
                const fields = params.PROPERTY_VALUES;
                writes.push({ID:writes.length + 1, NAME:params.NAME, PROPERTY_VALUES:fields});
                return callback(result(writes.length));
            }
            callback({error:() => "UNEXPECTED_METHOD", data:() => null});
        },
        openPath(path, callback) {
            openedPath = path;
            desktopCalls.push("openPath");
            openCallback = callback;
            timestamp += openDelayMs;
            if (openResult !== undefined) callback(openResult);
        },
    };
    const windowRef = {
        self:frame ? {} : null,
        top:frame ? {} : null,
        localStorage,
        crypto:{randomUUID:uuid},
        BX24:bx24,
    };
    if (!frame) windowRef.self = windowRef.top = windowRef;
    if (desktop) windowRef.BXDesktopSystem = {
        ExecuteCommand() {},
        SetActiveTab() {
            desktopCalls.push("SetActiveTab");
            if (activationFailure) throw new Error("native activation failed");
            activeTab = true;
        },
        IsActiveTab() { return activeTab; },
    };
    if (desktop && activationAvailable) windowRef.BXDesktopWindow = {
        ExecuteCommand(command) {
            desktopCalls.push(command);
            if (activationFailure) throw new Error("native activation failed");
            if (!activationNeverForeground && command === "show.active") foreground = true;
        },
        GetProperty(name) { return name === "isForeground" && foreground; },
    };
    return {
        calls, writes, localStorage, windowRef, bx24, uuid,
        randomId:uuid,
        now:()=>timestamp,
        desktopCalls,
        advance:milliseconds => { timestamp += milliseconds; },
        setDesktopState({active, visible}) { activeTab = active; foreground = visible; },
        waitImpl:async milliseconds => { timestamp += milliseconds; },
        setIntervalImpl(callback, interval) { timerCallback = callback; const id = timers.size + 1; timers.set(id, {callback, interval}); return id; },
        clearIntervalImpl(id) { timers.delete(id); },
        setTimeoutImpl() { return 1; },
        clearTimeoutImpl() {},
        openedPath:() => openedPath,
        closeSlider:() => openCallback && openCallback({result:"close"}),
        captureTimer(callback) { timerCallback = callback; },
        runTimer() { timerCallback && timerCallback(); },
        outgoing,
    };
}

const request = entityItem({
    messageId:"request-1", type:"request", operationId:"op-7", baseId:"base-a",
    orderId:"order-7", initiatorId:"30", workplaceId:"workplace-1",
    sessionId:"session-1", payload:{TITLE:"ТОО А.", DESCRIPTION:"Заказ 7", GROUP_ID:36},
}, 1);
const settings = {enabled:true, standardCardVerified:true, baseId:"base-a"};

test("disabled demo can read the exact profile later used by worker without queue traffic", async () => {
    const env = createEnvironment();
    const profile = await readWorkplaceProfile({...settings, enabled:false}, env);
    assert.deepEqual(profile, {baseId:"base-a", initiatorId:"30", workplaceId:"workplace-1"});
    assert.deepEqual(env.calls.map(call => call.method), ["user.current"]);
    assert.equal(env.openedPath(), undefined);
    const worker = await startBackgroundWorker(settings, env);
    assert.equal(worker.userId, profile.initiatorId);
    assert.equal(worker.workplaceId, profile.workplaceId);
    worker.stop();
});

test("profile persists its generated workplace and isolates hosting domain and employee", async () => {
    const env = createEnvironment({userId:"31"});
    env.windowRef.location = {hostname:"cdn.example"};
    const first = await readWorkplaceProfile(settings, env);
    assert.equal(env.localStorage.getItem("stage1.workplace.bitrix24.30"), "workplace-1");
    assert.equal((await readWorkplaceProfile(settings, env)).workplaceId, first.workplaceId);
    env.windowRef.location.hostname = "other.example";
    assert.notEqual((await readWorkplaceProfile(settings, env)).workplaceId, first.workplaceId);
    assert.equal(env.writes.length, 0);
});

test("profile refuses browser, missing desktop bridge and corrupted local ID", async () => {
    for (const options of [{frame:false}, {desktop:false}]) {
        const env = createEnvironment(options);
        await assert.rejects(readWorkplaceProfile(settings, env), /browser_refused|desktop_bridge_unavailable|desktop_activation_unavailable/);
        assert.equal(env.calls.length, 0);
    }
    const env = createEnvironment();
    env.localStorage.setItem("stage1.workplace.bitrix24.30", "invalid/id");
    await assert.rejects(readWorkplaceProfile(settings, env), /WORKPLACE_ID_UNAVAILABLE/);
    assert.equal(env.localStorage.getItem("stage1.workplace.bitrix24.30"), "invalid/id");
});

test("worker refuses to poll when the hidden frame lacks the native activation bridge", async () => {
    const env = createEnvironment({activationAvailable:false});
    const worker = await startBackgroundWorker(settings, env);
    assert.equal(worker.status, "desktop_activation_unavailable");
    assert.deepEqual(env.calls, []);
    assert.equal(env.openedPath(), undefined);
});

function grantFor(worker, sourceRequest = request, type = "grant", expiresAt = 130) {
    const fields = sourceRequest.PROPERTY_VALUES;
    return entityItem({
        messageId:type + "-1", type, expiresAt, operationId:fields.OPERATION_ID,
        baseId:fields.BASE_ID, orderId:fields.ORDER_ID, initiatorId:fields.INITIATOR_ID,
        workplaceId:fields.WORKPLACE_ID, sessionId:fields.SESSION_ID,
        instanceId:worker.instanceId, permissionId:"permission-1",
        payload:JSON.parse(fields.PAYLOAD_JSON),
    }, 2);
}

test("task path matches the live slider mapper: title decoded, tag raw, description entered manually", () => {
    const path = buildTaskPath({operationId:"op-7", correlationTag:"КА-op-7",
        payload:{TITLE:"ТОО А & Б.", DESCRIPTION:"Заказ №7 & 100%", GROUP_ID:36}});
    // AppLayout decodes the path once. Uri.getQueryParams preserves raw values;
    // TaskMappers decodes TITLE only, while TAGS is split without URL decoding.
    const received = decodeURIComponent(path);
    const query = Object.fromEntries(received.split("?")[1].split("&").map(part=>part.split("=")));
    assert.equal(decodeURIComponent(query.TITLE), "ТОО А & Б.");
    assert.equal(query.DESCRIPTION, undefined);
    assert.equal(query.GROUP_ID, "36");
    assert.equal(query.TAGS, "КА-op-7");
    assert.throws(() => buildTaskPath({operationId:"op/7",
        payload:{TITLE:"Demo.", DESCRIPTION:"", GROUP_ID:36}}), /INVALID_/);
});

test("worker refuses an ordinary browser page and a frame without native desktop bridge", async () => {
    const browser = createEnvironment({frame:false});
    assert.equal((await startBackgroundWorker(settings, browser)).status, "browser_refused");
    assert.deepEqual(browser.calls, []);
    const noBridge = createEnvironment({desktop:false});
    assert.equal((await startBackgroundWorker(settings, noBridge)).status, "desktop_bridge_unavailable");
    assert.deepEqual(noBridge.calls, []);
});

test("worker polls only its per-user entity channels and creates stable workplace and fresh instance IDs", async () => {
    const env = createEnvironment({outgoing:[request]});
    const first = await startBackgroundWorker(settings, env);
    const second = await startBackgroundWorker(settings, env);
    assert.equal(first.workplaceId, "workplace-1");
    assert.equal(first.workplaceId, second.workplaceId);
    assert.notEqual(first.instanceId, second.instanceId);
    assert.equal(env.calls.filter(call => call.method === "user.current").length, 2);
    await first.pollOnce();
    assert.ok(env.calls.some(call => call.method === "entity.item.get"
        && call.params.ENTITY === "Q_30"));
    assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "claim"));
    assert.ok(env.calls.every(call => !["fetch", "worker.session", "worker.next"].includes(call.method)));
});

test("worker reads entity property arrays without Object.hasOwn", async () => {
    const arrayItem = {
        ID:request.ID,
        NAME:request.NAME,
        PROPERTY_VALUES:Object.entries(request.PROPERTY_VALUES).map(([PROPERTY, VALUE]) => ({PROPERTY, VALUE})),
    };
    const env = createEnvironment({outgoing:[arrayItem]});
    const hasOwn = Object.hasOwn;
    try {
        Object.hasOwn = undefined;
        const worker = await startBackgroundWorker(settings, env);
        assert.equal((await worker.pollOnce()).status, "idle");
        assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "claim"));
    } finally {
        Object.hasOwn = hasOwn;
    }
});

test("request is not opened until a matching one-use permission is read from the entity channel", async () => {
    const env = createEnvironment({outgoing:[request]});
    const worker = await startBackgroundWorker(settings, env);
    await worker.pollOnce();
    assert.equal(env.openedPath(), undefined);
    env.outgoing.push(grantFor({instanceId:"different-instance"}));
    await worker.pollOnce();
    assert.equal(env.openedPath(), undefined);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    assert.equal(env.openedPath(), undefined, "entity write is not a service opening acknowledgment");
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    assert.ok(env.openedPath());
    const path = new URL(decodeURIComponent(env.openedPath()), "https://example.invalid");
    assert.equal(path.searchParams.get("TITLE"), "ТОО А.");
    assert.equal(path.searchParams.get("TAGS"), "КА-op-7");
    assert.deepEqual(env.writes.map(item => item.PROPERTY_VALUES.MESSAGE_TYPE),
        ["claim", "opening", "opened"]);
    assert.deepEqual(env.desktopCalls, ["SetActiveTab", "show.active", "openPath"]);
});

test("native activation failure never calls openPath and reports a safe diagnostic code", async () => {
    const env = createEnvironment({outgoing:[request], activationFailure:true});
    const worker = await startBackgroundWorker(settings, env);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    await worker.waitForPendingTransition();
    assert.equal(env.openedPath(), undefined);
    const closed = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed");
    assert.ok(closed);
    assert.deepEqual(JSON.parse(closed.PROPERTY_VALUES.PAYLOAD_JSON), {code:"DESKTOP_ACTIVATION_FAILED"});
});

test("native activation is checked after navigation and remains bounded to two seconds", async () => {
    const env = createEnvironment({outgoing:[request],activationNeverForeground:true});
    const worker = await startBackgroundWorker(settings, env);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    await worker.waitForPendingTransition();
    assert.equal(env.now(),102000);
    assert.ok(env.openedPath());
    assert.deepEqual(env.desktopCalls, ["SetActiveTab", "show.active", "openPath"]);
    const closed = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed");
    assert.ok(closed);
    assert.deepEqual(JSON.parse(closed.PROPERTY_VALUES.PAYLOAD_JSON), {code:"DESKTOP_ACTIVATION_TIMEOUT"});
});

test("openPath error is kept separate from slider close and never reported as opened", async () => {
    const env = createEnvironment({outgoing:[request], openResult:{result:"error",errorCode:"PATH_NOT_AVAILABLE"}});
    const worker = await startBackgroundWorker(settings, env);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    await worker.waitForPendingTransition();
    assert.ok(env.openedPath());
    assert.ok(!env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "opened"));
    const closed = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed");
    assert.ok(closed);
    assert.deepEqual(JSON.parse(closed.PROPERTY_VALUES.PAYLOAD_JSON), {code:"PATH_NOT_AVAILABLE"});
});

test("generic openPath errors never echo arbitrary SDK error text", async () => {
    const env = createEnvironment({outgoing:[request],
        openResult:{result:"error",errorCode:"access_token_must_not_be_logged"}});
    const worker = await startBackgroundWorker(settings, env);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    await worker.waitForPendingTransition();
    const closed = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed");
    assert.deepEqual(JSON.parse(closed.PROPERTY_VALUES.PAYLOAD_JSON), {code:"NAVIGATION_FAILED"});
    assert.ok(!closed.PROPERTY_VALUES.PAYLOAD_JSON.includes("access_token"));
});

test("a slow open keeps the first heartbeat tied to the same one-use operation", async () => {
    const env = createEnvironment({outgoing:[request],openDelayMs:21000});
    const worker = await startBackgroundWorker(settings, env);
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    env.advance(10000);
    env.runTimer();
    await worker.waitForPendingTransition();
    const opened = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "opened");
    const heartbeat = env.writes.find(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "heartbeat");
    assert.equal(Number(opened.PROPERTY_VALUES.CREATED_AT),121);
    assert.equal(Number(heartbeat.PROPERTY_VALUES.CREATED_AT),131);
    assert.equal(heartbeat.PROPERTY_VALUES.OPERATION_ID,"op-7");
});

test("closing the task slider reports unknown and active slider heartbeats use entity messages", async () => {
    const env = createEnvironment({outgoing:[request]});
    const intervals = [];
    const worker = await startBackgroundWorker(settings, {
        ...env,
        setIntervalImpl(callback, interval) { env.captureTimer(callback); intervals.push(interval); return 1; },
        clearIntervalImpl() { intervals.push("cleared"); },
    });
    env.outgoing.push(grantFor(worker));
    await worker.pollOnce();
    env.outgoing.push(grantFor(worker,request,"open"));
    await worker.pollOnce();
    env.runTimer();
    await worker.waitForPendingTransition();
    assert.deepEqual(intervals, [5000, 10000]);
    assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "heartbeat"));
    env.closeSlider();
    await worker.waitForPendingTransition();
    assert.deepEqual(intervals, [5000, 10000, "cleared"]);
    assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed"));
});

test("expired permission never invokes openPath", async () => {
    const env=createEnvironment({outgoing:[request]});
    const worker=await startBackgroundWorker(settings,env);
    env.outgoing.push(grantFor(worker,request,"grant",99));
    env.outgoing.push(grantFor(worker,request,"open",99));
    await worker.pollOnce();
    assert.equal(env.openedPath(),undefined);
    assert.ok(!env.writes.some(item=>item.PROPERTY_VALUES.MESSAGE_TYPE === "opening"));
});

test("claim retries after temporary denial and immediately after 1C session revision", async () => {
 let time=100000;
 const env=createEnvironment({outgoing:[structuredClone(request)]});
 const worker=await startBackgroundWorker(settings,{...env,now:()=>time});
 await worker.pollOnce();await worker.pollOnce();
 assert.equal(env.writes.length,1);
 time+=10000;await worker.pollOnce();assert.equal(env.writes.length,2);
 env.outgoing[0].PROPERTY_VALUES.SESSION_ID="session-2";
 await worker.pollOnce();assert.equal(env.writes.length,3);
 assert.equal(env.writes[2].PROPERTY_VALUES.SESSION_ID,"session-2");
});
test("stop during entity read prevents any claim or opening", async () => {
 const env=createEnvironment({outgoing:[request]});
 const worker=await startBackgroundWorker(settings,env);
 const call=env.bx24.callMethod;
 let complete;
 env.bx24.callMethod=(method,params,cb)=>method==="entity.item.get" ? complete=()=>call(method,params,cb) : call(method,params,cb);
 const pending=worker.pollOnce();worker.stop();complete();
 assert.equal((await pending).status,"stopped");assert.equal(env.writes.length,0);assert.equal(env.openedPath(),undefined);
});

test("worker reads requests beyond first SDK entity page", async () => {
 const env=createEnvironment();const call=env.bx24.callMethod;
 env.bx24.callMethod=(method,params,cb)=>{
  if(method!=="entity.item.get")return call(method,params,cb);
  cb({error:()=>null,data:()=>[],more:()=>true,next(next){next({error:()=>null,data:()=>[request],more:()=>false});}});
 };
 const worker=await startBackgroundWorker(settings,env);await worker.pollOnce();
 assert.equal(env.writes[0]?.PROPERTY_VALUES.OPERATION_ID,"op-7");
});
