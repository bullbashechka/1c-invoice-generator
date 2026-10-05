"use strict";
async function bindPersonalWorkerDiagnostic(client, pageUrl) {
    const call = typeof module !== "undefined" ? require("./background_worker.js").bitrixCall : bitrixCall;
    const handler=new URL("worker.html",pageUrl).href;
    const errorHandlerUrl=new URL("worker-error.html",pageUrl).href;
    if(new URL(handler).protocol!=="https:") throw new Error("INVALID_WORKER_URL");
    const user=await call(client,"user.current",{});
    if(!/^[1-9][0-9]{0,9}$/.test(String(user?.ID))) throw new Error("INVALID_WORKER_USER");
    const userId=Number(user.ID);
    const exact=row=>row.placement==="PAGE_BACKGROUND_WORKER" && row.handler===handler
        && Number(row.userId)===userId && row.options?.errorHandlerUrl===errorHandlerUrl;
    const rows=await call(client,"placement.get",{});
    if(!Array.isArray(rows)) throw new Error("INVALID_PLACEMENT_LIST");
    if(rows.some(exact)) return {status:"verified",userId,handler,errorHandlerUrl};
    if(rows.some(row=>row.placement==="PAGE_BACKGROUND_WORKER")) throw new Error("WORKER_PLACEMENT_CONFLICT");
    if(await call(client,"placement.bind",{PLACEMENT:"PAGE_BACKGROUND_WORKER",HANDLER:handler,
        USER_ID:userId,TITLE:"КА: диагностика фонового фрейма",OPTIONS:{errorHandlerUrl}})!==true) {
        throw new Error("WORKER_BIND_UNCONFIRMED");
    }
    const readback=await call(client,"placement.get",{});
    if(!Array.isArray(readback)||!readback.some(exact)) throw new Error("WORKER_READBACK_UNCONFIRMED");
    return {status:"bound",userId,handler,errorHandlerUrl};
}
if(typeof module!=="undefined")module.exports={bindPersonalWorkerDiagnostic};
