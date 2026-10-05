"use strict";

// One-time local download for the service. Tokens never enter the diagnostic UI,
// localStorage, entity messages, application package or a browser-service request.
async function readOAuthBootstrap(settings, environment = {}) {
    let portal;
    try {
        portal=new URL(settings?.portal);
        if(portal.protocol!=="https:" || portal.username || portal.password
            || portal.port || portal.search || portal.hash || portal.pathname!=="/") throw new Error();
    } catch {throw new Error("OAUTH_CONFIG_INVALID");}
    const readProfile=typeof module!=="undefined"
        ? require("./background_worker.js").readWorkplaceProfile : readWorkplaceProfile;
    const profile=await readProfile(settings,environment);
    const windowRef=environment.windowRef || globalThis.window;
    const client=environment.client || windowRef.BX24;
    let auth;
    try {auth=client.getAuth();} catch {throw new Error("OAUTH_UNAVAILABLE");}
    const now=(environment.now || (()=>Date.now()))();
    if(!auth || !Number.isFinite(Number(auth.expires_in)) || Number(auth.expires_in)<=now
        || ![auth.access_token,auth.refresh_token].every(token=>typeof token==="string"
            && /^[\x21-\x7e]{1,4096}$/.test(token))) throw new Error("OAUTH_UNAVAILABLE");
    if(auth.domain!==portal.hostname) throw new Error("OAUTH_PORTAL_MISMATCH");
    return {...profile,portal:portal.origin,accessToken:auth.access_token,
        refreshToken:auth.refresh_token,expiresAt:Number(auth.expires_in)/1000};
}

if(typeof module!=="undefined")module.exports={readOAuthBootstrap};
