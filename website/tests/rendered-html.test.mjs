import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(path="/"){
  const workerUrl=new URL("../dist/server/index.js",import.meta.url);
  workerUrl.searchParams.set("test",`${process.pid}-${Date.now()}-${path}`);
  const {default:worker}=await import(workerUrl.href);
  return worker.fetch(new Request(`http://localhost${path}`,{headers:{accept:"text/html"}}),{ASSETS:{fetch:async()=>new Response("Not found",{status:404})}},{waitUntil(){},passThroughOnException(){}});
}

test("renders the three-part manual and full evidence specimen",async()=>{
  const response=await render(); assert.equal(response.status,200); const html=await response.text();
  assert.match(html,/Find a small number of genuinely good roles/);
  assert.match(html,/Part\s*(?:<!-- -->)?one/); assert.match(html,/Part\s*(?:<!-- -->)?two/); assert.match(html,/Part\s*(?:<!-- -->)?three/);
  assert.match(html,/Who do I talk to\?/); assert.match(html,/Why this conversation\?/); assert.match(html,/What do we talk about\?/);
  assert.match(html,/Led the move from Redshift to Snowflake across 40 pipelines/);
  assert.match(html,/Do not skip/); assert.doesNotMatch(html,/actually|refuses to bluff/i);
});

test("renders every guide ask from the canonical data",async()=>{
  const [{guides},home]=await Promise.all([import(new URL("../app/guides/data.ts",import.meta.url)),render()]);
  const html=await home.text();
  for(const guide of guides) assert.ok(html.includes(guide.ask),`missing ask: ${guide.ask}`);
});

test("homepage tabs expose accessible relationships and keyboard-ready controls",async()=>{
  const response=await render(); assert.equal(response.status,200); const html=await response.text();
  assert.match(html,/role="tablist"/); assert.match(html,/role="tab"/); assert.match(html,/role="tabpanel"/);
  assert.match(html,/aria-controls="shape-panel"/); assert.match(html,/aria-labelledby="shape-tab"/);
  assert.match(html,/aria-controls="run-it-yourself-panel"/);
  assert.match(html,/aria-controls="weekly-next-move-panel"/); assert.match(html,/Your weekly action list/);
});

test("guide steps always carry a prompt and observable proof",async()=>{
  const {guides}=await import(new URL("../app/guides/data.ts",import.meta.url));
  for(const guide of guides) for(const step of guide.steps){assert.ok(step.prompt);assert.ok(step.proof)}
  const response=await render("/guides/assess-a-role"); assert.equal(response.status,200); const html=await response.text();
  assert.match(html,/Prerequisites/); assert.match(html,/You know it worked when/); assert.match(html,/This answered/);
});

test("next-move is server-rendered and links canonical prompts to guides",async()=>{
  const response=await render("/next-move"); assert.equal(response.status,200); const html=await response.text();
  assert.match(html,/Where you are this week/); assert.match(html,/Does this job fit me\?/); assert.match(html,/\/guides\/assess-a-role/);
  assert.doesNotMatch(html,/<form|<input|localStorage|sessionStorage/i);
});

test("uses Electric Cobalt with no stale green or Barlow",async()=>{
  const [css,layout]=await Promise.all([readFile(new URL("../app/globals.css",import.meta.url),"utf8"),readFile(new URL("../app/layout.tsx",import.meta.url),"utf8")]);
  assert.match(css,/#2b50e8/i); assert.doesNotMatch(css,/#16a34a|accent-blue|Barlow/i); assert.doesNotMatch(layout,/Barlow/);
});

test("worker protects every route with a fail-closed Basic Auth gate",async()=>{
  const source=await readFile(new URL("../worker/index.ts",import.meta.url),"utf8");
  assert.match(source,/BASIC_AUTH_ENABLED === "true"/);
  assert.match(source,/WWW-Authenticate/);
  assert.match(source,/status: 503/);
  assert.doesNotMatch(source,/BASIC_AUTH_PASSWORD\s*[:=]\s*["'][^"']+/);
});

test("every site footer links back to DHK",async()=>{
  for(const path of ["/","/guides/assess-a-role","/next-move"]){
    const response=await render(path); const html=await response.text();
    assert.match(html,/href="https:\/\/www\.dhk\.io"/);
  }
});

test("onboarding offers the customised interview form alongside Claude",async()=>{
  const response=await render(); const html=await response.text();
  assert.match(html,/customised Google Form/);
  assert.match(html,/mailto:wingman@dhk\.io\?subject=Wingman%20interview%20form/);
  assert.match(html,/instead of answering the interview questions in Claude, or work across both/);
});

test("interior routes provide visible breadcrumb navigation",async()=>{
  const [guide,nextMove]=await Promise.all([render("/guides/assess-a-role"),render("/next-move")]);
  for(const response of [guide,nextMove]){
    const html=await response.text(); assert.match(html,/aria-label="Breadcrumb"/); assert.match(html,/Wingman manual/); assert.match(html,/aria-current="page"/);
  }
});
