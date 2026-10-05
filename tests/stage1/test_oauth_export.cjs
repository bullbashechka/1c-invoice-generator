"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {readOAuthBootstrap}=require("../../src/stage-1-diagnostics/oauth_export.js");

function fixture() {
    const auth={access_token:"test-access",refresh_token:"test-refresh",
        expires_in:200000,domain:"portal.example"};
    const calls=[];
    const client={init:cb=>cb(),getAuth:()=>auth,
        callMethod(method,params,cb){calls.push(method);cb({error:()=>null,data:()=>({ID:"30"})});}};
    const windowRef={self:{},top:{},BXDesktopSystem:{ExecuteCommand(){}},
        location:{hostname:"cdn.example"},localStorage:{getItem:()=>"pc-1"}};
    return {auth,calls,environment:{client,windowRef,now:()=>100000},
        settings:{baseId:"ka-demo",portal:"https://portal.example"}};
}

test("local OAuth export includes exact current profile and SDK expiry in seconds",async()=>{
    const f=fixture();
    const result=await readOAuthBootstrap(f.settings,f.environment);
    assert.deepEqual(result,{baseId:"ka-demo",portal:"https://portal.example",initiatorId:"30",
        workplaceId:"pc-1",accessToken:"test-access",refreshToken:"test-refresh",expiresAt:200});
    assert.deepEqual(f.calls,["user.current"]);
});
test("local OAuth export rejects different portal and expired or missing authorization",async()=>{
    const f=fixture();
    f.auth.domain="other.example";
    await assert.rejects(readOAuthBootstrap(f.settings,f.environment),/OAUTH_PORTAL_MISMATCH/);
    f.auth.domain="portal.example";f.auth.expires_in=100000;
    await assert.rejects(readOAuthBootstrap(f.settings,f.environment),/OAUTH_UNAVAILABLE/);
    f.auth.expires_in=200000;delete f.auth.refresh_token;
    await assert.rejects(readOAuthBootstrap(f.settings,f.environment),/OAUTH_UNAVAILABLE/);
});
test("local OAuth export refuses browser before reading user or tokens",async()=>{
    const f=fixture();f.environment.windowRef.BXDesktopSystem=null;
    await assert.rejects(readOAuthBootstrap(f.settings,f.environment),/desktop_bridge_unavailable/);
    assert.deepEqual(f.calls,[]);
});
