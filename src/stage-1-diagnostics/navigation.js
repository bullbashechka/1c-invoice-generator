"use strict";

function openStandardTaskProbe(client, { groupId, title, onResult }) {
    if (typeof groupId !== "string" || !/^[1-9][0-9]{0,9}$/.test(groupId)) throw new Error("INVALID_GROUP");
    if (typeof title !== "string" || !title.trim() || /[\r\n\u0000]/.test(title)) throw new Error("INVALID_TITLE");
    if (typeof onResult !== "function") throw new Error("INVALID_CALLBACK");
    const report = value => ({ ...value, stage1: "not_verified" });
    if (typeof client?.openPath !== "function") return report({ error: "SDK_UNAVAILABLE" });
    // AppLayout.openPath decodes the whole path before parsing its query.
    // Preserve the query encoding across that observed source boundary.
    const query = `TITLE=${encodeURIComponent(title)}&GROUP_ID=${groupId}`;
    const path = `/workgroups/group/${groupId}/tasks/task/edit/0/?${encodeURIComponent(query)}`;
    try {
        client.openPath(path, result => {
            if (result?.result === "close") {
                onResult(report({ status: "slider_closed_result_unknown" }));
            } else {
                const code = result?.errorCode;
                onResult(report({ status: "error", error: typeof code === "string"
                    && /^[A-Z0-9_]{1,80}$/.test(code) ? code : "UNKNOWN_RESPONSE" }));
            }
        });
        return report({ status: "request_sent" });
    } catch {
        return report({ status: "error", error: "SDK_CALL_FAILED" });
    }
}

if (typeof module !== "undefined") module.exports = { openStandardTaskProbe };
