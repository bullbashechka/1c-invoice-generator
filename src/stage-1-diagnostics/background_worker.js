"use strict";

const IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/;
const EMPLOYEE_ID = /^[1-9][0-9]{0,9}$/;
const DEMO_GROUP_ID = "36";
const DIAGNOSTIC_CODES = new Set([
    "SLIDER_CLOSED", "PATH_NOT_AVAILABLE", "METHOD_NOT_SUPPORTED_ON_DEVICE",
    "NAVIGATION_FAILED", "NAVIGATION_RESULT_UNKNOWN", "DESKTOP_ACTIVATION_FAILED",
    "DESKTOP_ACTIVATION_TIMEOUT", "WORKER_STOPPED", "SDK_CALL_FAILED",
]);

function randomId(cryptoProvider) {
    if (typeof cryptoProvider?.randomUUID === "function") return cryptoProvider.randomUUID();
    if (typeof cryptoProvider?.getRandomValues !== "function") throw new Error("WORKER_ID_UNAVAILABLE");
    const bytes = cryptoProvider.getRandomValues(new Uint8Array(16));
    return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
}

function taskPath(message) {
    const operationId = message?.operationId;
    const payload = message?.payload;
    if (typeof operationId !== "string" || !IDENTIFIER.test(operationId)
        || !payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error("INVALID_TASK_REQUEST");
    }
    const title = payload.TITLE;
    const description = payload.DESCRIPTION || "";
    if (typeof title !== "string" || !title.trim() || /[\r\n\u0000]/.test(title)
        || typeof description !== "string" || /\u0000/.test(description)
        || String(payload.GROUP_ID) !== DEMO_GROUP_ID) {
        throw new Error("INVALID_TASK_FIELDS");
    }
    // Live TaskMappers decodes TITLE, but not TAGS or DESCRIPTION. Keep the tag
    // raw after AppLayout's decode. Description remains manual: raw query values
    // cannot safely represent an arbitrary saved order containing '&' or '#'.
    const query = `TITLE=${encodeURIComponent(title)}&GROUP_ID=${DEMO_GROUP_ID}&TAGS=КА-${operationId}`;
    return `/workgroups/group/${DEMO_GROUP_ID}/tasks/task/edit/0/?${encodeURIComponent(query)}`;
}

function propertyValues(item) {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("INVALID_ENTITY_ITEM");
    let values = item.PROPERTY_VALUES ?? item.PROPERTY_VALUE;
    if (Array.isArray(values)) {
        const parsed = {};
        for (const property of values) {
            if (!property || typeof property !== "object" || Array.isArray(property)) {
                throw new Error("INVALID_ENTITY_ITEM");
            }
            const key = property.PROPERTY ?? property.CODE ?? property.NAME;
            if (typeof key !== "string" || Object.prototype.hasOwnProperty.call(parsed, key)) {
                throw new Error("INVALID_ENTITY_ITEM");
            }
            parsed[key] = property.VALUE;
        }
        values = parsed;
    }
    if (!values || typeof values !== "object" || Array.isArray(values)) throw new Error("INVALID_ENTITY_ITEM");
    return values;
}

function decodeMessage(item) {
    const fields = propertyValues(item);
    let payload;
    try { payload = typeof fields.PAYLOAD_JSON === "string" ? JSON.parse(fields.PAYLOAD_JSON) : {}; }
    catch { throw new Error("INVALID_ENTITY_MESSAGE"); }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("INVALID_ENTITY_MESSAGE");
    const id = fields.MESSAGE_ID ?? item.NAME;
    const message = {
        messageId:id,
        type:fields.MESSAGE_TYPE,
        operationId:fields.OPERATION_ID,
        baseId:fields.BASE_ID,
        orderId:fields.ORDER_ID,
        initiatorId:String(fields.INITIATOR_ID ?? ""),
        workplaceId:fields.WORKPLACE_ID,
        sessionId:fields.SESSION_ID,
        instanceId:fields.INSTANCE_ID || "",
        permissionId:fields.PERMISSION_ID || "",
        expiresAt:Number(fields.EXPIRES_AT || 0),
        payload,
    };
    for (const key of ["messageId", "operationId", "baseId", "orderId", "workplaceId", "sessionId"]) {
        if (typeof message[key] !== "string" || !IDENTIFIER.test(message[key])) {
            throw new Error("INVALID_ENTITY_MESSAGE");
        }
    }
    if (!EMPLOYEE_ID.test(message.initiatorId)) throw new Error("INVALID_ENTITY_MESSAGE");
    return message;
}

function bitrixCall(client, method, parameters, {setTimeoutImpl = globalThis.setTimeout,
    clearTimeoutImpl = globalThis.clearTimeout} = {}) {
    if (typeof client?.callMethod !== "function") return Promise.reject(new Error("WORKER_SDK_UNAVAILABLE"));
    return new Promise((resolve, reject) => {
        let settled = false;
        const timer = setTimeoutImpl(() => {
            if (!settled) { settled = true; reject(new Error("BITRIX_REST_TIMEOUT")); }
        }, 10000);
        const finish = (error, value) => {
            if (settled) return;
            settled = true;
            clearTimeoutImpl(timer);
            if (error) reject(error);
            else resolve(value);
        };
        try {
            client.callMethod(method, parameters, result => {
                try {
                    if (!result || typeof result.data !== "function" || typeof result.error !== "function") {
                        return finish(new Error("BITRIX_REST_INVALID_RESPONSE"));
                    }
                    const error = result.error();
                    if (error) return finish(new Error("BITRIX_REST_REJECTED"));
                    return finish(null, result.data());
                } catch { return finish(new Error("BITRIX_REST_INVALID_RESPONSE")); }
            });
        } catch { finish(new Error("BITRIX_REST_UNAVAILABLE")); }
    });
}

function normalizeItems(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object" && Array.isArray(value.items)) return value.items;
    throw new Error("BITRIX_ENTITY_LIST_INVALID");
}

function bitrixItems(client, parameters, environment) {
    return new Promise((resolve,reject) => {
        const items=[];
        let pages=0;
        const clear=environment.clearTimeoutImpl || globalThis.clearTimeout;
        const timer=(environment.setTimeoutImpl || globalThis.setTimeout)(()=>reject(new Error("BITRIX_REST_TIMEOUT")),10000);
        const page=result=>{
            try {
                if(result.error()) throw new Error("BITRIX_REST_REJECTED");
                const values=normalizeItems(result.data());
                if(values.length>50) throw new Error("BITRIX_ENTITY_LIST_INVALID");
                items.push(...values);
                if(result.more?.()) {
                    if(++pages>=200) throw new Error("BITRIX_ENTITY_SCAN_LIMIT");
                    result.next(page);
                } else {clear(timer);resolve(items);}
            } catch(error) {clear(timer);reject(error);}
        };
        try {client.callMethod("entity.item.get",parameters,page);}
        catch(error) {clear(timer);reject(error);}
    });
}

function desktopRefusal(windowRef) {
    if (!windowRef) return "browser_refused";
    try {
        if (windowRef.self === windowRef.top) return "browser_refused";
    } catch { return "browser_refused"; }
    if (typeof windowRef.BXDesktopSystem?.ExecuteCommand !== "function") {
        return "desktop_bridge_unavailable";
    }
    return null;
}

function desktopActivationRefusal(windowRef) {
    const refusal = desktopRefusal(windowRef);
    if (refusal) return refusal;
    if (typeof windowRef.BXDesktopSystem.SetActiveTab !== "function"
        || typeof windowRef.BXDesktopSystem.IsActiveTab !== "function"
        || typeof windowRef.BXDesktopWindow?.ExecuteCommand !== "function"
        || typeof windowRef.BXDesktopWindow.GetProperty !== "function") {
        return "desktop_activation_unavailable";
    }
    return null;
}

function navigationResultCode(result) {
    if (result?.result === "close") return "SLIDER_CLOSED";
    if (result?.result !== "error") return "NAVIGATION_RESULT_UNKNOWN";
    const code = result.errorCode;
    return code === "PATH_NOT_AVAILABLE" || code === "METHOD_NOT_SUPPORTED_ON_DEVICE"
        ? code : "NAVIGATION_FAILED";
}

function activateDesktopWindow(windowRef) {
    const system = windowRef?.BXDesktopSystem;
    const desktopWindow = windowRef?.BXDesktopWindow;
    try {
        system.SetActiveTab();
        desktopWindow.ExecuteCommand("show.active");
    } catch {
        return "DESKTOP_ACTIVATION_FAILED";
    }
    return null;
}

async function waitForDesktopActivation(windowRef, environment = {}) {
    const system = windowRef?.BXDesktopSystem;
    const desktopWindow = windowRef?.BXDesktopWindow;
    const wait = environment.waitImpl || (milliseconds => new Promise(resolve =>
        setTimeout(resolve, milliseconds)));
    for (let attempt = 0; attempt <= 20; attempt += 1) {
        try {
            if (system.IsActiveTab() === true
                && desktopWindow.GetProperty("isForeground") === true) return null;
        } catch {
            return "DESKTOP_ACTIVATION_FAILED";
        }
        if (attempt < 20) await wait(100);
    }
    return "DESKTOP_ACTIVATION_TIMEOUT";
}

function validContext(message, worker) {
    return message.baseId === worker.baseId
        && message.initiatorId === worker.userId
        && message.workplaceId === worker.workplaceId;
}

function samePayload(left, right) {
    try { return JSON.stringify(left) === JSON.stringify(right); }
    catch { return false; }
}

async function readWorkplaceProfile(settings, environment = {}) {
    const windowRef = environment.windowRef || globalThis.window;
    const refusal = desktopRefusal(windowRef);
    if (refusal) throw new Error(refusal);
    const client = environment.client || windowRef.BX24;
    const localStorage = environment.localStorage || windowRef.localStorage;
    const cryptoProvider = environment.cryptoProvider || windowRef.crypto || globalThis.crypto;
    if (!client || typeof client.init !== "function" || typeof client.callMethod !== "function"
        || !localStorage) {
        throw new Error("WORKER_SDK_UNAVAILABLE");
    }
    if (typeof settings?.baseId !== "string" || !IDENTIFIER.test(settings.baseId)) {
        throw new Error("WORKER_CONFIG_INVALID");
    }

    await new Promise((resolve, reject) => {
        let done = false;
        const timeout = (environment.setTimeoutImpl || globalThis.setTimeout)(() => {
            if (!done) { done = true; reject(new Error("WORKER_SDK_UNAVAILABLE")); }
        }, 4000);
        try {
            client.init(() => {
                if (!done) {
                    done = true;
                    (environment.clearTimeoutImpl || globalThis.clearTimeout)(timeout);
                    resolve();
                }
            });
        } catch {
            if (!done) {
                done = true;
                (environment.clearTimeoutImpl || globalThis.clearTimeout)(timeout);
                reject(new Error("WORKER_SDK_UNAVAILABLE"));
            }
        }
    });

    let currentUser;
    try { currentUser = await bitrixCall(client, "user.current", {}, environment); }
    catch { throw new Error("WORKER_AUTH_UNAVAILABLE"); }
    const userId = currentUser?.ID == null ? "" : String(currentUser.ID);
    if (!EMPLOYEE_ID.test(userId)) throw new Error("WORKER_IDENTITY_INVALID");
    const idFactory = environment.randomId || (() => randomId(cryptoProvider));
    const locationHost = String(windowRef.location?.hostname || "bitrix24")
        .toLowerCase().replace(/[^a-z0-9.-]/g, "_");
    const workplaceKey = `stage1.workplace.${locationHost}.${userId}`;
    let workplaceId;
    try {
        workplaceId = localStorage.getItem(workplaceKey);
        if (!workplaceId) {
            workplaceId = idFactory();
            if (typeof workplaceId !== "string" || !IDENTIFIER.test(workplaceId)) {
                throw new Error("WORKER_ID_UNAVAILABLE");
            }
            localStorage.setItem(workplaceKey, workplaceId);
        }
    } catch { throw new Error("WORKPLACE_ID_UNAVAILABLE"); }
    if (typeof workplaceId !== "string" || !IDENTIFIER.test(workplaceId)) {
        throw new Error("WORKPLACE_ID_UNAVAILABLE");
    }
    return {baseId:settings.baseId, initiatorId:userId, workplaceId};
}

async function startBackgroundWorker(settings, environment = {}) {
    if (!settings || settings.enabled !== true || settings.standardCardVerified !== true) {
        return {status:"disabled"};
    }
    const windowRef = environment.windowRef || globalThis.window;
    const refusal = desktopActivationRefusal(windowRef);
    if (refusal) return {status:refusal};
    const client = environment.client || windowRef.BX24;
    const cryptoProvider = environment.cryptoProvider || windowRef.crypto || globalThis.crypto;
    const setIntervalImpl = environment.setIntervalImpl || globalThis.setInterval;
    const clearIntervalImpl = environment.clearIntervalImpl || globalThis.clearInterval;
    const now = environment.now || (() => Date.now());
    const pollMs = environment.pollMs || 5000;
    const heartbeatMs = environment.heartbeatMs || 10000;
    if (typeof client?.openPath !== "function"
        || typeof setIntervalImpl !== "function" || typeof clearIntervalImpl !== "function"
        || typeof now !== "function" || pollMs !== 5000 || heartbeatMs !== 10000) {
        throw new Error("WORKER_SDK_UNAVAILABLE");
    }
    const {initiatorId:userId, workplaceId} = await readWorkplaceProfile(settings, environment);
    const idFactory = environment.randomId || (() => randomId(cryptoProvider));
    const instanceId = idFactory();
    if (typeof instanceId !== "string" || !IDENTIFIER.test(instanceId) || instanceId === workplaceId) {
        throw new Error("WORKER_ID_UNAVAILABLE");
    }
    const worker = {baseId:settings.baseId, userId, workplaceId, instanceId};
    const outgoingEntity = `Q_${userId}`;
    const incomingEntity = `R_${userId}`;
    const claimSent = new Map();
    const permissionUsed = new Set();
    let stopped = false;
    let polling = false;
    let active = null;
    let awaiting = null;
    let heartbeatTimer = null;
    let lastTransition = Promise.resolve();

    const readOutgoing = async () => (await bitrixItems(client, {
        ENTITY:outgoingEntity,
        SORT:{ID:"ASC"},
        start:0,
    }, environment)).map(decodeMessage);

    const send = async (message) => {
        const payload = JSON.stringify(message.payload || {});
        const fields = {
            MESSAGE_ID:message.messageId,
            MESSAGE_TYPE:message.type,
            OPERATION_ID:message.operationId,
            BASE_ID:message.baseId,
            ORDER_ID:message.orderId,
            INITIATOR_ID:message.initiatorId,
            WORKPLACE_ID:message.workplaceId,
            SESSION_ID:message.sessionId,
            INSTANCE_ID:message.instanceId,
            PERMISSION_ID:message.permissionId || "",
            TASK_ID:message.taskId || "",
            PAYLOAD_JSON:payload,
            CREATED_AT:String(Math.floor(now() / 1000)),
        };
        const id = await bitrixCall(client, "entity.item.add", {
            ENTITY:incomingEntity,
            NAME:message.messageId,
            PROPERTY_VALUES:fields,
        }, environment);
        const remoteId = typeof id === "object" && id !== null ? (id.ID ?? id.id) : id;
        if (!(typeof remoteId === "number" && Number.isSafeInteger(remoteId) && remoteId > 0)
            && !(typeof remoteId === "string" && /^[1-9][0-9]*$/.test(remoteId))) {
            throw new Error("BITRIX_ENTITY_WRITE_UNCONFIRMED");
        }
    };

    const event = (request, type, extra = {}) => ({
        messageId:idFactory(), type, operationId:request.operationId,
        baseId:request.baseId, orderId:request.orderId, initiatorId:request.initiatorId,
        workplaceId:request.workplaceId, sessionId:request.sessionId,
        instanceId, permissionId:extra.permissionId || "",
        payload:DIAGNOSTIC_CODES.has(extra.code) ? {code:extra.code} : {},
    });

    const stopHeartbeat = () => {
        if (heartbeatTimer !== null) clearIntervalImpl(heartbeatTimer);
        heartbeatTimer = null;
    };

    const finishOpen = (request, code) => {
        if (!active || active.operationId !== request.operationId || active.closed) return;
        active.closed = true;
        stopHeartbeat();
        lastTransition = send(event(request, "closed", {
            permissionId:active.permissionId, code,
        }))
            .catch(() => null).finally(() => { active = null; });
    };

    const openGranted = async (request, grant, confirmed = false) => {
        if (active || stopped || permissionUsed.has(grant.permissionId)) return false;
        if (!validContext(request, worker) || !validContext(grant, worker)
            || grant.type !== (confirmed ? "open" : "grant") || grant.operationId !== request.operationId
            || grant.orderId !== request.orderId || grant.sessionId !== request.sessionId
            || grant.instanceId !== instanceId || !grant.permissionId
            || !Number.isFinite(grant.expiresAt) || grant.expiresAt <= now() / 1000
            ) return false;
        const path = taskPath(grant);
        if (!confirmed) {
            awaiting = {request,grant};
            try {await send(event(request, "opening", {permissionId:grant.permissionId}));}
            catch {awaiting = null;}
            if (stopped) awaiting = null;
            return false;
        }
        if (!awaiting || awaiting.grant.permissionId !== grant.permissionId
            || awaiting.request.operationId !== request.operationId
            || !samePayload(awaiting.grant.payload,grant.payload)) return false;
        awaiting = null;
        permissionUsed.add(grant.permissionId);
        active = {operationId:request.operationId, permissionId:grant.permissionId,
            request, closed:false};
        const activationError = activateDesktopWindow(windowRef);
        if (activationError) {
            finishOpen(request, activationError);
            return false;
        }
        if (stopped) {
            finishOpen(request, "WORKER_STOPPED");
            return false;
        }
        try {
            client.openPath(path, result => finishOpen(request, navigationResultCode(result)));
        } catch {
            finishOpen(request, "SDK_CALL_FAILED");
            return false;
        }
        if (!active?.closed) {
            send(event(request, "opened", {permissionId:grant.permissionId})).catch(() => null);
            heartbeatTimer = setIntervalImpl(() => {
                if (active && !active.closed && !stopped) {
                    lastTransition = send(event(request, "heartbeat", {permissionId:grant.permissionId}))
                        .catch(() => null);
                }
            }, heartbeatMs);
        }
        if (!active?.closed) {
            const foregroundError = await waitForDesktopActivation(windowRef, environment);
            if (foregroundError) {
                finishOpen(request, foregroundError);
                return false;
            }
        }
        return true;
    };

    const controller = {
        status:"ready",
        userId,
        workplaceId,
        instanceId,
        async pollOnce() {
            if (stopped) return {status:"stopped"};
            if (active) return {status:"busy"};
            if (polling) return {status:"busy"};
            polling = true;
            try {
                const messages = await readOutgoing();
                if (stopped) return {status:"stopped"};
                if (awaiting) {
                    if (awaiting.grant.expiresAt <= now() / 1000) {
                        permissionUsed.add(awaiting.grant.permissionId);
                        awaiting = null;
                        return {status:"permission_expired"};
                    }
                    const confirmation = messages.find(message=>message.type === "open"
                        && message.permissionId === awaiting.grant.permissionId);
                    if (confirmation && await openGranted(awaiting.request,confirmation,true)) {
                        return {status:"opening",operationId:confirmation.operationId};
                    }
                    return {status:"awaiting_confirmation"};
                }
                const requests = new Map();
                for (const message of messages) {
                    if (message.type === "request" && validContext(message, worker)) {
                        requests.set(message.operationId, message);
                    }
                }
                for (const request of requests.values()) {
                    if (stopped) return {status:"stopped"};
                    const key = `${request.operationId}:${request.sessionId}`;
                    const previous = claimSent.get(key);
                    if (previous !== undefined && now() - previous < heartbeatMs) continue;
                    claimSent.set(key, now());
                    try { await send(event(request, "claim")); }
                    catch { claimSent.delete(key); }
                }
                for (const grant of messages) {
                    if (grant.type !== "grant" || grant.instanceId !== instanceId
                        || !validContext(grant, worker)) continue;
                    const request = requests.get(grant.operationId);
                    if (!request) continue;
                    await openGranted(request, grant);
                    if (awaiting) return {status:"awaiting_confirmation",operationId:grant.operationId};
                }
                return {status:"idle"};
            } catch {
                return {status:"entity_unavailable"};
            } finally { polling = false; }
        },
        async waitForPendingTransition() { await lastTransition; },
        stop() {
            stopped = true;
            awaiting = null;
            stopHeartbeat();
            if (pollTimer !== null) clearIntervalImpl(pollTimer);
            if (active && !active.closed) finishOpen(active.request);
        },
    };
    const pollTimer = setIntervalImpl(() => { void controller.pollOnce(); }, pollMs);
    return controller;
}

if (typeof module !== "undefined") module.exports = {buildTaskPath:taskPath, startBackgroundWorker, readWorkplaceProfile, desktopRefusal, desktopActivationRefusal, bitrixCall};
