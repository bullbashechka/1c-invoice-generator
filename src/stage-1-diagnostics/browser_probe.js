"use strict";

const browserPathBuilder = typeof module !== "undefined"
    ? require("./background_worker.js").buildTaskPath : null;

function createBrowserTaskProbe(client, { windowRef, operationId, onResult }) {
    if (typeof operationId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(operationId)) {
        throw new Error("INVALID_OPERATION");
    }
    if (typeof onResult !== "function") throw new Error("INVALID_CALLBACK");
    const report = value => ({ ...value, operationId, tag: `КА-${operationId}`,
        mode: "browser_diagnostic", stage1: "not_verified" });
    let requested = false;
    return { open() {
        if (requested) return report({ status: "already_requested" });
        if (!windowRef || windowRef.self === windowRef.top) {
            return report({ status: "app_iframe_required" });
        }
        let placement;
        try { placement = client?.placement?.info?.()?.placement; }
        catch { return report({ status: "context_refused" }); }
        if (placement !== "DEFAULT") return report({ status: "context_refused" });
        if (typeof client?.openPath !== "function") return report({ status: "sdk_unavailable" });
        const path = (browserPathBuilder || taskPath)({ operationId, payload: {
            TITLE: "Проверка контрагента & филиал №1.", GROUP_ID: "36",
        } });
        // An unknown outcome must not open a second card, even after onClose.
        requested = true;
        try {
            client.openPath(path, result => {
                if (result?.result === "close") {
                    onResult(report({ status: "slider_closed_result_unknown" }));
                } else if (result?.result === "error") {
                    const code = ["PATH_NOT_AVAILABLE", "METHOD_NOT_SUPPORTED_ON_DEVICE"].includes(result.errorCode)
                        ? result.errorCode : "UNKNOWN_RESPONSE";
                    onResult(report({ status: "error", error: code }));
                } else {
                    onResult(report({ status: "navigation_result_unknown" }));
                }
            });
            return report({ status: "request_sent" });
        } catch {
            return report({ status: "error", error: "SDK_CALL_FAILED" });
        }
    } };
}

if (typeof module !== "undefined") module.exports = { createBrowserTaskProbe };
