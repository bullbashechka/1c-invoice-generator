"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { bindDemoDiagnostic } = require("../../src/stage-1-diagnostics/bind.js");
const handler = "https://cdn-ru.bitrix24.kz/example/app_local/test/index.html";
const options = { groupId: "123", handler, timeoutMs: 20 };
const existing = { id: 5, placement: "TASK_VIEW_TAB", handler, options: { groupId: "123" } };
function sdk({ rows = [], silent = false, error = null } = {}) {
    const calls = [];
    let currentRows = rows;
    return { calls, callMethod(method, parameters, callback) {
        calls.push({ method, parameters });
        if (method === "placement.bind") {
            currentRows = [existing];
            if (silent) return;
        }
        callback({ error: () => error, data: () => method === "placement.get" ? currentRows : true });
    } };
}
test("binds only task tab scoped to an explicit demo group and verifies readback", async () => {
    const client = sdk();
    const result = await bindDemoDiagnostic(client, options);
    assert.equal(result.status, "bound");
    assert.deepEqual(client.calls.map(x => x.method), ["placement.get", "placement.bind", "placement.get"]);
    assert.equal(client.calls[1].parameters.PLACEMENT, "TASK_VIEW_TAB");
    assert.deepEqual(client.calls[1].parameters.OPTIONS, { groupId: "123" });
});
test("rejects absent group and credential-bearing handler before any API call", async () => {
    for (const invalid of [{ groupId: "" }, { groupId: "0" }, { groupId: "123,456" }, { handler: handler + "?auth=SECRET" }, { handler: "http://localhost/index.html" }]) {
        const client = sdk();
        await assert.rejects(bindDemoDiagnostic(client, { ...options, ...invalid }), /INVALID_/);
        assert.deepEqual(client.calls, []);
    }
});
test("exact existing binding is reused without another write", async () => {
    const client = sdk({ rows: [existing] });
    assert.equal((await bindDemoDiagnostic(client, options)).status, "existing");
    assert.deepEqual(client.calls.map(x => x.method), ["placement.get"]);
});
test("conflicting group is preserved and no new binding is made", async () => {
    const client = sdk({ rows: [{ ...existing, options: { groupId: "456" } }] });
    assert.equal((await bindDemoDiagnostic(client, options)).error, "BINDING_CONFLICT");
    assert.deepEqual(client.calls.map(x => x.method), ["placement.get"]);
});
test("lost bind response is resolved by readback instead of a repeated bind", async () => {
    const client = sdk({ silent: true });
    assert.equal((await bindDemoDiagnostic(client, options)).status, "bound_after_unknown");
    assert.equal(client.calls.filter(x => x.method === "placement.bind").length, 1);
});
test("read refusal prevents writing and does not expose error details", async () => {
    const client = sdk({ error: { error: "insufficient_scope", error_description: "SECRET" } });
    const result = await bindDemoDiagnostic(client, options);
    assert.equal(result.error, "insufficient_scope");
    assert.ok(!JSON.stringify(result).includes("SECRET"));
    assert.deepEqual(client.calls.map(x => x.method), ["placement.get"]);
});
