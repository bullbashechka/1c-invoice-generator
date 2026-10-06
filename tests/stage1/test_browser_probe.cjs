"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { createBrowserTaskProbe } = require("../../src/stage-1-diagnostics/browser_probe.js");
const { runProbe } = require("../../src/stage-1-diagnostics/probe.js");
const { desktopRefusal } = require("../../src/stage-1-diagnostics/background_worker.js");
const operationId = "browser-demo-001";
const browser = { self: {}, top: {} };
const clientFor = openPath => ({ placement: { info: () => ({ placement: "DEFAULT" }) }, openPath });

test("browser app requests one unsaved demo card carrying title and exact tag through SDK", () => {
    const paths = [];
    const client = clientFor(path => paths.push(path));
    client.callMethod = () => assert.fail("navigation must not write REST data");
    const probe = createBrowserTaskProbe(client, { windowRef: browser, operationId, onResult() {} });
    assert.equal(probe.open().status, "request_sent");
    assert.equal(paths.length, 1);
    const query = decodeURIComponent(paths[0]).split("?")[1];
    const fields = Object.fromEntries(query.split("&").map(part => part.split("=")));
    assert.equal(decodeURIComponent(fields.TITLE), "Проверка контрагента & филиал №1.");
    assert.equal(fields.GROUP_ID, "36");
    assert.equal(fields.TAGS, `КА-${operationId}`);
    assert.equal(probe.open().status, "already_requested");
    assert.equal(paths.length, 1);
});

test("browser probe refuses top level, non-default placement and missing SDK", () => {
    let calls = 0;
    const client = clientFor(() => calls++);
    const self = {};
    assert.equal(createBrowserTaskProbe(client, { windowRef: { self, top: self }, operationId, onResult() {} }).open().status,
        "app_iframe_required");
    assert.equal(createBrowserTaskProbe({ ...client, placement: { info: () => ({ placement: "TASK_VIEW_TAB" }) } },
        { windowRef: browser, operationId, onResult() {} }).open().status, "context_refused");
    assert.equal(createBrowserTaskProbe({ placement: client.placement },
        { windowRef: browser, operationId, onResult() {} }).open().status, "sdk_unavailable");
    assert.equal(calls, 0);
});

test("close and unknown callbacks never claim creation, cancellation or permit reopening", () => {
    let callback;
    const results = [];
    const probe = createBrowserTaskProbe(clientFor((_, cb) => { callback = cb; }),
        { windowRef: browser, operationId, onResult: result => results.push(result) });
    probe.open();
    callback({ result: "close", taskId: 123, token: "SECRET" });
    assert.equal(results[0].status, "slider_closed_result_unknown");
    assert.equal(JSON.stringify(results).includes("SECRET"), false);
    assert.equal("taskId" in results[0], false);
    assert.equal(probe.open().status, "already_requested");
});

test("SDK failures are sanitized and are never retried", () => {
    let calls = 0;
    const results = [];
    const probe = createBrowserTaskProbe(clientFor((_, cb) => {
        calls++;
        cb({ result: "error", errorCode: "SECRET_CODE", description: "SECRET" });
    }), { windowRef: browser, operationId, onResult: result => results.push(result) });
    probe.open();
    assert.equal(results[0].error, "UNKNOWN_RESPONSE");
    assert.equal(JSON.stringify(results).includes("SECRET"), false);
    assert.equal(probe.open().status, "already_requested");
    assert.equal(calls, 1);
});

test("invalid operation cannot be injected into task fields", () => {
    for (const invalid of ["", "x&GROUP_ID=1", null]) {
        assert.throws(() => createBrowserTaskProbe(clientFor(() => assert.fail()),
            { windowRef: browser, operationId: invalid, onResult() {} }), /INVALID_OPERATION/);
    }
});

async function appFixture(openPath) {
    const html = fs.readFileSync(require.resolve("../../src/stage-1-diagnostics/index.html"), "utf8");
    const elements = new Map([...html.matchAll(/<\w+\b[^>]*\bid="([^"]+)"[^>]*>/g)]
        .map(match => [match[1], { textContent: "", hidden: /\bhidden\b/.test(match[0]),
            disabled: /\bdisabled\b/.test(match[0]) }]));
    const calls = [];
    const window = { ...browser, BX24: {
        init(callback) { callback(); }, openPath,
        callMethod(method, _, callback) {
            calls.push(method);
            assert.ok(["scope", "placement.list"].includes(method), "initialization must remain read-only");
            callback({ error: () => null, data: () => method === "scope" ? ["task", "placement"] : [] });
        },
        placement: { info: () => ({ placement: "DEFAULT" }),
            getInterface: callback => callback({ command: [], event: [] }) },
    } };
    const context = vm.createContext({ window, document: { getElementById: id => elements.get(id) },
        runProbe, desktopRefusal, randomId: () => "diagnostic-operation-001", createBrowserTaskProbe });
    vm.runInContext(fs.readFileSync(require.resolve("../../src/stage-1-diagnostics/correlated_probe.js"), "utf8"), context);
    const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
    await vm.runInContext(script, context);
    return { elements, calls };
}

test("main browser app exposes manual probe while native actions remain blocked and queue stays idle", async () => {
    const paths = [];
    const { elements, calls } = await appFixture(path => paths.push(path));
    assert.equal(elements.get("browser-probe").hidden, false);
    assert.equal(elements.get("open-browser-probe").disabled, false);
    for (const id of ["open-correlated", "bind-worker", "read-workplace", "export-oauth"]) {
        assert.equal(elements.get(id).disabled, true);
    }
    assert.equal(paths.length, 0);
    elements.get("open-browser-probe").onclick();
    elements.get("open-browser-probe").onclick();
    assert.equal(paths.length, 1);
    assert.equal(elements.get("open-browser-probe").disabled, true);
    assert.deepEqual(calls, ["scope", "placement.list"]);
});

test("main app keeps synchronous callback result and stays blocked after unknown close", async () => {
    const { elements } = await appFixture((_, callback) => callback({ result: "close" }));
    elements.get("open-browser-probe").onclick();
    assert.equal(JSON.parse(elements.get("browser-report").textContent).status, "slider_closed_result_unknown");
    assert.equal(elements.get("open-browser-probe").disabled, true);
});

test("SDK exception and unknown response cannot leak payload or trigger a second request", () => {
    let calls = 0;
    const results = [];
    const throwing = createBrowserTaskProbe(clientFor(() => { calls++; throw new Error("SECRET"); }),
        { windowRef: browser, operationId, onResult() {} });
    assert.equal(throwing.open().error, "SDK_CALL_FAILED");
    assert.equal(throwing.open().status, "already_requested");
    assert.equal(calls, 1);
    const unknown = createBrowserTaskProbe(clientFor((_, callback) => callback({ taskId: 123, token: "SECRET" })),
        { windowRef: browser, operationId, onResult: result => results.push(result) });
    unknown.open();
    assert.equal(results[0].status, "navigation_result_unknown");
    assert.equal(JSON.stringify(results).includes("SECRET"), false);
});
