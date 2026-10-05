"use strict";
const probeWorker = typeof module !== "undefined" ? require("./background_worker.js") : null;
function diagnosticEnvironment(windowRef) {
    return {status:(probeWorker ? probeWorker.desktopRefusal(windowRef) : desktopRefusal(windowRef)) || "desktop_detected",
        placement:windowRef?.BX24?.placement?.info?.()?.placement || "UNKNOWN"};
}
function openCorrelatedTaskProbe(client, {windowRef, operationId, onResult}) {
    const environment = diagnosticEnvironment(windowRef);
    if (environment.status !== "desktop_detected") return environment;
    const buildPath = probeWorker ? probeWorker.buildTaskPath : taskPath;
    const path = buildPath({operationId, payload:{TITLE:"КА демо — проверка метки.",
        DESCRIPTION:"Диагностика сохранения служебной метки. Измените название и описание вручную.", GROUP_ID:"36"}});
    if (typeof client?.openPath !== "function") return {status:"sdk_unavailable"};
    client.openPath(path, () => onResult({status:"unknown", operationId}));
    return {status:"request_sent", operationId, tag:`КА-${operationId}`};
}
async function readCorrelatedTask(client, operationId) {
    if (typeof operationId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(operationId)) throw new Error("INVALID_OPERATION");
    const tag = `КА-${operationId}`;
    const call = probeWorker ? probeWorker.bitrixCall : bitrixCall;
    const ids = await new Promise((resolve,reject) => {
        const found = new Set();
        let pages = 0;
        const timer = setTimeout(() => reject(new Error("REST_TIMEOUT")),10000);
        const page = result => {
            try {
                if (result.error()) throw new Error("REST_REJECTED");
                const tasks = result.data()?.tasks;
                if (!Array.isArray(tasks)) throw new Error("REST_INVALID_RESPONSE");
                for (const task of tasks) {
                    const id = String(task.id ?? task.ID ?? "");
                    if (!/^[1-9][0-9]*$/.test(id)) throw new Error("REST_INVALID_RESPONSE");
                    found.add(id);
                }
                if (result.more?.()) {
                    if (++pages >= 100) throw new Error("SEARCH_INCOMPLETE");
                    result.next(page);
                } else {clearTimeout(timer); resolve([...found]);}
            } catch(error) {clearTimeout(timer); reject(error);}
        };
        try {client.callMethod("tasks.task.list",{filter:{TAG:tag},select:["ID","TAGS"],start:0},page);}
        catch(error) {clearTimeout(timer); reject(error);}
    });
    const confirmed = [];
    for (const id of ids) {
        const response = await call(client,"tasks.task.get",{id,select:["ID","TAGS","GROUP_ID"]});
        const task = response?.task;
        if (!task || String(task.id ?? task.ID) !== id) throw new Error("REST_INVALID_RESPONSE");
        let tags = task.tags ?? task.TAGS;
        if (tags && !Array.isArray(tags) && typeof tags === "object"
            && Object.keys(tags).every(key=>/^[0-9]+$/.test(key))) {
            tags = Object.entries(tags).map(([key,value]) => {
                if (typeof value === "string") return value;
                if (value && String(value.id) === key && typeof value.title === "string") return value.title;
                throw new Error("REST_INVALID_TAGS");
            });
        }
        if (!Array.isArray(tags) || tags.some(value=>typeof value !== "string")) {
            const error = new Error("REST_INVALID_TAGS");
            error.tagShape = task.tags ?? task.TAGS;
            throw error;
        }
        if (tags.includes(tag)) confirmed.push(id);
    }
    return {status:confirmed.length === 1 ? "created" : confirmed.length > 1 ? "conflict" : "unknown",
        operationId, tag, taskIds:confirmed};
}
if (typeof module !== "undefined") module.exports = {openCorrelatedTaskProbe, readCorrelatedTask};
