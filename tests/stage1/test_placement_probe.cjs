"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { runProbe } = require("../../src/stage-1-diagnostics/probe.js");

function sdk({ commands = [], events = [], apiError, silentInterface = false } = {}) {
    const calls = [];
    return {
        calls,
        init(callback) { callback(); },
        placement: {
            info() { return { placement: "DEFAULT", options: { token: "SECRET" } }; },
            getInterface(callback) {
                calls.push("getInterface");
                if (!silentInterface) callback({ command: commands, event: events });
            },
        },
        callMethod(method, parameters, callback) {
            calls.push(method);
            assert.deepEqual(parameters, {});
            assert.ok(["scope", "placement.list"].includes(method));
            callback({
                error: () => method === "placement.list" ? apiError : null,
                data: () => method === "scope"
                    ? ["task", "placement"]
                    : ["TASK_VIEW_TAB", "TASK_LIST_CONTEXT_MENU"],
            });
        },
    };
}

test("reads only scope, placements and current interface; empty interface does not verify stage 1", async () => {
    const client = sdk();
    const report = await runProbe(client, { timeoutMs: 50 });
    assert.equal(report.status, "read_complete");
    assert.equal(report.stage1, "not_verified");
    assert.deepEqual(report.interface.command, []);
    assert.deepEqual(report.placements.values, ["TASK_VIEW_TAB", "TASK_LIST_CONTEXT_MENU"]);
    assert.deepEqual(client.calls.sort(), ["getInterface", "placement.list", "scope"].sort());
    assert.ok(!JSON.stringify(report).includes("SECRET"));
});

test("scope refusal remains an explicit error even if interface callback succeeds", async () => {
    const report = await runProbe(sdk({ apiError: { error: "insufficient_scope", error_description: "SECRET" } }), { timeoutMs: 50 });
    assert.equal(report.status, "incomplete");
    assert.equal(report.placements.error, "insufficient_scope");
    assert.equal(report.stage1, "not_verified");
    assert.ok(!JSON.stringify(report).includes("SECRET"));
});

test("missing interface callback is unknown, never an empty successful interface", async () => {
    const report = await runProbe(sdk({ silentInterface: true }), { timeoutMs: 5 });
    assert.equal(report.status, "incomplete");
    assert.equal(report.interface.error, "TIMEOUT");
    assert.ok(!Object.hasOwn(report.interface, "command"));
});

test("missing SDK produces a bounded diagnostic result", async () => {
    const report = await runProbe(undefined, { timeoutMs: 5 });
    assert.equal(report.status, "incomplete");
    assert.equal(report.initialization.error, "SDK_UNAVAILABLE");
    assert.equal(report.stage1, "not_verified");
});

test("listed commands are preserved but never invoked", async () => {
    const client = sdk({ commands: ["setTaskFields"], events: ["Task::Saved"] });
    const report = await runProbe(client, { timeoutMs: 50 });
    assert.deepEqual(report.interface.command, ["setTaskFields"]);
    assert.deepEqual(report.interface.event, ["Task::Saved"]);
    assert.ok(!client.calls.includes("setTaskFields"));
    assert.equal(report.stage1, "not_verified");
});
