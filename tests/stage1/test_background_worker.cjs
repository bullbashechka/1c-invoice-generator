"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { buildTaskPath, startBackgroundWorker } = require("../../src/stage-1-diagnostics/background_worker.js");

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
        },
    };
}

function createEnvironment({outgoing = [], desktop = true, frame = true, userId = "30"} = {}) {
    const calls = [];
    const writes = [];
    const store = new Map();
    const timers = new Map();
    let sequence = 0;
    let openedPath;
    let openCallback;
    let timerCallback;
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
        openPath(path, callback) { openedPath = path; openCallback = callback; },
    };
    const windowRef = {
        self:frame ? {} : null,
        top:frame ? {} : null,
        localStorage,
        crypto:{randomUUID:uuid},
        BX24:bx24,
    };
    if (!frame) windowRef.self = windowRef.top = windowRef;
    if (desktop) windowRef.BXDesktopSystem = {ExecuteCommand() {}};
    return {
        calls, writes, localStorage, windowRef, bx24, uuid,
        randomId:uuid,
        setIntervalImpl(callback, interval) { const id = timers.size + 1; timers.set(id, {callback, interval}); return id; },
        clearIntervalImpl(id) { timers.delete(id); },
        setTimeoutImpl() { return 1; },
        clearTimeoutImpl() {},
        openedPath:() => openedPath,
        closeSlider:() => openCallback && openCallback(),
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

function grantFor(worker, sourceRequest = request) {
    const fields = sourceRequest.PROPERTY_VALUES;
    return entityItem({
        messageId:"grant-1", type:"grant", operationId:fields.OPERATION_ID,
        baseId:fields.BASE_ID, orderId:fields.ORDER_ID, initiatorId:fields.INITIATOR_ID,
        workplaceId:fields.WORKPLACE_ID, sessionId:fields.SESSION_ID,
        instanceId:worker.instanceId, permissionId:"permission-1",
        payload:JSON.parse(fields.PAYLOAD_JSON),
    }, 2);
}

test("task path validates project and encodes title, description and correlation tag", () => {
    const path = buildTaskPath({operationId:"op-7", correlationTag:"КА-op-7",
        payload:{TITLE:"ТОО А & Б.", DESCRIPTION:"Заказ №7 & 100%", GROUP_ID:36}});
    const received = new URL(decodeURIComponent(path), "https://example.invalid");
    assert.equal(received.pathname, "/workgroups/group/36/tasks/task/edit/0/");
    assert.equal(received.searchParams.get("TITLE"), "ТОО А & Б.");
    assert.equal(received.searchParams.get("DESCRIPTION"), "Заказ №7 & 100%");
    assert.equal(received.searchParams.get("GROUP_ID"), "36");
    assert.equal(received.searchParams.get("TAGS"), "КА-op-7");
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
    assert.ok(env.openedPath());
    const path = new URL(decodeURIComponent(env.openedPath()), "https://example.invalid");
    assert.equal(path.searchParams.get("TITLE"), "ТОО А.");
    assert.equal(path.searchParams.get("TAGS"), "КА-op-7");
    assert.deepEqual(env.writes.map(item => item.PROPERTY_VALUES.MESSAGE_TYPE),
        ["claim", "opening", "opened"]);
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
    env.runTimer();
    await worker.waitForPendingTransition();
    assert.deepEqual(intervals, [5000, 10000]);
    assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "heartbeat"));
    env.closeSlider();
    await worker.waitForPendingTransition();
    assert.deepEqual(intervals, [5000, 10000, "cleared"]);
    assert.ok(env.writes.some(item => item.PROPERTY_VALUES.MESSAGE_TYPE === "closed"));
});
