"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sourceDir = path.resolve(__dirname, "../../src/stage-1-diagnostics");

async function page({ missingSdk = false, native = false, init = "ready", sdkEvent = "load", openPath } = {}) {
    const html = fs.readFileSync(path.join(sourceDir, "index.html"), "utf8");
    const elements = new Map([...html.matchAll(/<\w+\b[^>]*\bid="([^"]+)"[^>]*>/g)].map(match => {
        const attributes = Object.fromEntries([...match[0].matchAll(/([\w:-]+)="([^"]*)"/g)]
            .map(attribute => [attribute[1], attribute[2]]));
        return [match[1], { textContent: "", value: "", hidden: /\bhidden\b/.test(match[0]),
            disabled: /\bdisabled\b/.test(match[0]), getAttribute: name => attributes[name] ?? null,
            setAttribute(name, value) { attributes[name] = value; } }];
    }));
    const calls = [];
    const window = { self: {}, top: {}, crypto: { randomUUID: () => "page-diagnostic-001" } };
    if (native) window.BXDesktopSystem = { ExecuteCommand() {} };
    if (!missingSdk) window.BX24 = {
        init(callback) {
            if (init === "throws") throw new Error("SECRET");
            if (init === "ready") callback();
        },
        openPath: openPath || (() => assert.fail("initialization must not navigate")),
        callMethod(method, _, callback) {
            calls.push(method);
            assert.ok(["scope", "placement.list"].includes(method));
            callback({ error: () => null, data: () => method === "scope" ? ["task", "placement"] : [] });
        },
        placement: { info: () => ({ placement: "DEFAULT" }),
            getInterface: callback => callback({ command: [], event: [] }) },
    };
    const context = vm.createContext({ window, document: { getElementById: id => elements.get(id) },
        localStorage: { getItem: () => "LOCAL_WORKER_OBSERVATION" }, setTimeout, clearTimeout });
    for (const match of html.matchAll(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/g)) {
        if (match[1].startsWith("https://")) {
            const handler = match[0].match(new RegExp(`on${sdkEvent}="([^"]*)"`))?.[1];
            if (handler) {
                context.scriptElement = elements.get("bx24-sdk");
                vm.runInContext(`(function () { ${handler} }).call(scriptElement)`, context);
            }
        } else vm.runInContext(fs.readFileSync(path.join(sourceDir, match[1]), "utf8"), context);
    }
    vm.runInContext("const fixtureRunProbe = runProbe; runProbe = client => fixtureRunProbe(client, { timeoutMs: 10 });", context);
    const main = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
    await vm.runInContext(main, context);
    return { elements, calls, report: JSON.parse(elements.get("report").textContent) };
}

function assertSdkCommandsBlocked(elements) {
    for (const id of ["open-browser-probe", "open-correlated", "read-correlated", "bind-worker",
        "read-workplace", "export-oauth", "bind-demo", "open-task-probe"]) {
        assert.equal(elements.get(id).disabled, true, `${id} must remain disabled`);
        assert.equal(elements.get(id).onclick, undefined, `${id} must not attach an SDK command`);
    }
}

for (const native of [false, true]) test(`missing SDK blocks every SDK command with native=${native}`, async () => {
    const { elements, calls, report } = await page({ missingSdk: true, native, sdkEvent: "error" });
    assert.equal(report.initialization.error, "SDK_UNAVAILABLE");
    assertSdkCommandsBlocked(elements);
    assert.equal(elements.get("browser-probe").hidden, true);
    assert.deepEqual(calls, []);
});

for (const init of ["throws", "never_ready"]) test(`failed initialization ${init} blocks SDK commands`, async () => {
    const { elements, calls, report } = await page({ native: true, init });
    assert.equal(report.initialization.error, init === "throws" ? "SDK_CALL_FAILED" : "TIMEOUT");
    assertSdkCommandsBlocked(elements);
    assert.deepEqual(calls, []);
    assert.equal(JSON.stringify(report).includes("SECRET"), false);
});

for (const sdkEvent of ["load", "error"]) test(`report records SDK script ${sdkEvent} separately from initialization`, async () => {
    const { report } = await page({ missingSdk: true, sdkEvent });
    assert.equal(report.sdk?.load, sdkEvent === "load" ? "loaded" : "failed");
    assert.equal(report.sdk.hasInit, false);
    assert.equal(report.sdk.source, "https://api.bitrix24.tech/api/v1/");
    assert.equal(report.build, "browser-sdk-guard-20261006");
});

test("local worker observation remains readable without SDK", async () => {
    const { elements } = await page({ missingSdk: true });
    assert.equal(elements.get("read-worker").disabled, false);
    elements.get("read-worker").onclick();
    assert.equal(elements.get("worker-report").textContent, "LOCAL_WORKER_OBSERVATION");
});

test("native main page preserves synchronous close callback", async () => {
    const { elements } = await page({ native: true, openPath: (_, callback) => callback({ result: "close" }) });
    elements.get("open-correlated").onclick();
    assert.equal(JSON.parse(elements.get("correlation-report").textContent).status, "unknown");
    assert.equal(elements.get("open-correlated").disabled, true);
});

test("native main page reports invalid operation without unhandled error or navigation", async () => {
    const { elements } = await page({ native: true });
    elements.get("probe-operation").value = "";
    assert.doesNotThrow(() => elements.get("open-correlated").onclick());
    assert.equal(JSON.parse(elements.get("correlation-report").textContent).error, "INVALID_TASK_REQUEST");
});

test("native main page reports SDK exception without leaking raw error", async () => {
    const { elements } = await page({ native: true, openPath: () => { throw new Error("SECRET"); } });
    assert.doesNotThrow(() => elements.get("open-correlated").onclick());
    const report = JSON.parse(elements.get("correlation-report").textContent);
    assert.equal(report.error, "SDK_CALL_FAILED");
    assert.equal(JSON.stringify(report).includes("SECRET"), false);
});

test("native main page preserves navigation error instead of treating it as a close", async () => {
    const { elements } = await page({ native: true,
        openPath: (_, callback) => callback({ result: "error", errorCode: "PATH_NOT_AVAILABLE", token: "SECRET" }) });
    elements.get("open-correlated").onclick();
    const report = JSON.parse(elements.get("correlation-report").textContent);
    assert.equal(report.status, "error");
    assert.equal(report.error, "PATH_NOT_AVAILABLE");
    assert.equal(JSON.stringify(report).includes("SECRET"), false);
});
