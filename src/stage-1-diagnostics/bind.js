"use strict";

function diagnosticRest(client, method, parameters, timeoutMs) {
    return new Promise((resolve) => {
        let done = false;
        const finish = (value) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            resolve(value);
        };
        const timer = setTimeout(() => finish({ error: "TIMEOUT" }), timeoutMs);
        try {
            client.callMethod(method, parameters, (result) => {
                try {
                    const error = result.error();
                    const code = typeof error === "string" ? error : error?.error;
                    finish(error ? { error: typeof code === "string" && /^[a-zA-Z0-9_]{1,80}$/.test(code)
                        ? code : "API_ERROR" } : { data: result.data() });
                } catch { finish({ error: "INVALID_RESPONSE" }); }
            });
        } catch { finish({ error: "SDK_CALL_FAILED" }); }
    });
}

async function bindDemoDiagnostic(client, { groupId, handler, timeoutMs = 10000 }) {
    if (typeof groupId !== "string" || !/^[1-9][0-9]{0,9}$/.test(groupId)) throw new Error("INVALID_GROUP");
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("INVALID_TIMEOUT");
    let url;
    try { url = new URL(handler); } catch { throw new Error("INVALID_HANDLER"); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash
        || !/\/app_local\/[^/]+\/index\.html$/.test(url.pathname)) throw new Error("INVALID_HANDLER");
    const read = async () => {
        const response = await diagnosticRest(client, "placement.get", {}, timeoutMs);
        if (response.error) return response;
        if (!Array.isArray(response.data) || response.data.some(row => !row || typeof row.placement !== "string")) {
            return { error: "INVALID_RESPONSE" };
        }
        const rows = response.data.filter(row => row.placement === "TASK_VIEW_TAB");
        if (!rows.length) return { status: "absent" };
        if (rows.length !== 1 || rows[0].handler !== handler || rows[0].options?.groupId !== groupId) {
            return { error: "BINDING_CONFLICT" };
        }
        return { status: "existing" };
    };
    const before = await read();
    if (before.error || before.status === "existing") return before;
    const write = await diagnosticRest(client, "placement.bind", {
        PLACEMENT: "TASK_VIEW_TAB", HANDLER: handler,
        TITLE: "КА — диагностика карточки", OPTIONS: { groupId },
    }, timeoutMs);
    const after = await read();
    if (after.status === "existing") return { status: write.error ? "bound_after_unknown" : "bound" };
    return { error: after.error || write.error || "BINDING_NOT_CONFIRMED" };
}

if (typeof module !== "undefined") module.exports = { bindDemoDiagnostic };
