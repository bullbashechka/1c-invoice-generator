"use strict";

function readCallback(start, timeoutMs) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            resolve(value);
        };
        const timer = setTimeout(() => finish({ error: "TIMEOUT" }), timeoutMs);
        try {
            start(finish);
        } catch {
            finish({ error: "SDK_CALL_FAILED" });
        }
    });
}

function names(value) {
    if (!Array.isArray(value) || value.some((item) =>
        typeof item !== "string" || !/^[a-zA-Z0-9_.:\/-]{1,128}$/.test(item))) {
        throw new Error("INVALID_RESPONSE");
    }
    return [...value];
}

function errorCode(value) {
    const code = typeof value === "string" ? value : value?.error;
    return typeof code === "string" && /^[a-zA-Z0-9_]{1,80}$/.test(code)
        ? code : "API_ERROR";
}

async function runProbe(client, { timeoutMs = 10000 } = {}) {
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("INVALID_TIMEOUT");
    const report = { schema: 1, stage1: "not_verified", status: "incomplete" };
    if (!client || typeof client.init !== "function") {
        report.initialization = { error: "SDK_UNAVAILABLE" };
        return report;
    }
    report.initialization = await readCallback((finish) => {
        client.init(() => finish({ status: "ready" }));
    }, timeoutMs);
    if (report.initialization.error) return report;

    try {
        const placement = client.placement.info()?.placement;
        report.context = typeof placement === "string" && /^[A-Z0-9_]{1,128}$/.test(placement)
            ? placement : "UNKNOWN";
    } catch {
        report.context = "UNKNOWN";
    }

    const restRead = (method) => readCallback((finish) => {
        client.callMethod(method, {}, (result) => {
            try {
                const error = result.error();
                finish(error ? { error: errorCode(error) } : { values: names(result.data()) });
            } catch {
                finish({ error: "INVALID_RESPONSE" });
            }
        });
    }, timeoutMs);

    [report.scopes, report.placements, report.interface] = await Promise.all([
        restRead("scope"),
        restRead("placement.list"),
        readCallback((finish) => {
            client.placement.getInterface((result) => {
                try {
                    finish({ command: names(result.command), event: names(result.event) });
                } catch {
                    finish({ error: "INVALID_RESPONSE" });
                }
            });
        }, timeoutMs),
    ]);
    if (![report.scopes, report.placements, report.interface].some((item) => item.error)) {
        report.status = "read_complete";
    }
    return report;
}

if (typeof module !== "undefined") module.exports = { runProbe };
