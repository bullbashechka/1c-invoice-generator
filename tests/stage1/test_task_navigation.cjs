"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { openStandardTaskProbe } = require("../../src/stage-1-diagnostics/navigation.js");

test("requests a standard unsaved task in the explicit group with an intact title", () => {
    let path;
    const report = openStandardTaskProbe({ openPath(value) { path = value; } }, {
        groupId: "36", title: "ТОО Ромашка & 100%.", onResult() {},
    });
    const receivedByPortal = new URL(decodeURIComponent(path), "https://example.invalid");
    assert.equal(receivedByPortal.pathname, "/workgroups/group/36/tasks/task/edit/0/");
    assert.equal(receivedByPortal.searchParams.get("TITLE"), "ТОО Ромашка & 100%.");
    assert.equal(receivedByPortal.searchParams.get("GROUP_ID"), "36");
    assert.equal(report.status, "request_sent");
    assert.equal(report.stage1, "not_verified");
});

test("closing a slider never proves cancellation or task creation", () => {
    let callback;
    const events = [];
    openStandardTaskProbe({ openPath(_, cb) { callback = cb; } }, {
        groupId: "36", title: "Проба.", onResult: value => events.push(value),
    });
    callback({ result: "close", taskId: 123, secret: "SECRET" });
    assert.deepEqual(events, [{ status: "slider_closed_result_unknown", stage1: "not_verified" }]);
});

test("invalid input and missing SDK never trigger navigation", () => {
    let calls = 0;
    const client = { openPath() { calls++; } };
    for (const invalid of [{ groupId: "" }, { groupId: "36/../14" }, { title: "" }, { title: null }]) {
        assert.throws(() => openStandardTaskProbe(client, { groupId: "36", title: "Проба.", onResult() {}, ...invalid }), /INVALID_/);
    }
    assert.equal(calls, 0);
    assert.equal(openStandardTaskProbe({}, { groupId: "36", title: "Проба.", onResult() {} }).error, "SDK_UNAVAILABLE");
});

test("SDK errors are reported without raw descriptions or automatic retry", () => {
    const events = [];
    let calls = 0;
    openStandardTaskProbe({ openPath(_, cb) { calls++; cb({ result: "error", errorCode: "PATH_NOT_AVAILABLE", description: "SECRET" }); } }, {
        groupId: "36", title: "Проба.", onResult: value => events.push(value),
    });
    assert.equal(calls, 1);
    assert.deepEqual(events, [{ status: "error", error: "PATH_NOT_AVAILABLE", stage1: "not_verified" }]);
});
