"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getGuide, guideGroups } from "./guides/data";

const orientation = [
  {title:"Give Claude the connector link",body:"In Claude, open Settings → Connectors → Add custom connector and paste the private link ending in /mcp/…. That is the whole connection step.",prompt:"What do you know about me?",proof:"Wingman answers, even if the honest answer is “nothing yet.”"},
  {title:"Say who you admire",body:"Name people and organisations you would be glad — or horrified — to be named alongside, then explain why. This needs no documents and gives Wingman evidence about what you value.",prompt:"Help me build my profile.",proof:"Asking what you value draws on your answers rather than a guess."},
  {title:"Add your CV and LinkedIn",body:"Open your private page and upload the document you maintain. Add the LinkedIn export if you want positions, recommendations, and connection names in the record.",prompt:"What do you know about me now?",proof:"Each role and skill traces to a line in a file you supplied."},
  {title:"Tell it what you are looking for",body:"Define hard limits, plausible role shapes, weighted wants, anti-signals, and the trade-offs between good options. Skip this and openings arrive unscored.",prompt:"Let’s set up my job criteria.",proof:"You get a criteria document you can read and edit, not an opaque score.",important:true},
];

const refusals = [
  ["It will not send anything","It will prepare talking points, intro bullets, and application packs. You will turn them into your own words and send them."],
  ["It will not invent familiarity","A warmth signal will name real signals. It will not imply a referral or closeness the record cannot support."],
  ["It will not fill a gap with a guess","Where there is no evidence, the answer will remain a gap or unknown, followed by what would resolve it."],
  ["It will not hide what it skipped","Keyless capabilities will remain useful. Any model step or fetch it skips will be reported plainly."],
];

function Part({number,title,description}:{number:string;title:string;description:string}){return <div className="part"><span>Part {number}</span><h2>{title}</h2><p>{description}</p></div>}
function Divider({children}:{children:React.ReactNode}){return <div className="sectionDivider"><span>{children}</span><i /></div>}
function Brand(){return <span className="brand"><i/>Wingman</span>}

const parts=[
  {id:"shape",label:"What it’s for",subtabs:[{id:"assessment",label:"What it hands back"},{id:"refusals",label:"What it won’t do"}]},
  {id:"manual",label:"What’s there",subtabs:guideGroups.map((group,index)=>({id:`manual-${index}`,label:group.label}))},
  {id:"start",label:"How to use it",subtabs:[{id:"orientation",label:"First twenty minutes"},{id:"run-it-yourself",label:"Running it yourself"},{id:"weekly-next-move",label:"Next move"}]},
] as const;

function Tabs({items,selected,onSelect,label,className=""}:{items:readonly {id:string;label:string}[];selected:string;onSelect:(id:string)=>void;label:string;className?:string}){
  const refs=useRef<(HTMLButtonElement|null)[]>([]);
  function move(index:number){const next=(index+items.length)%items.length;onSelect(items[next].id);refs.current[next]?.focus()}
  return <div className={`tabs ${className}`} role="tablist" aria-label={label}>{items.map((item,index)=><button ref={node=>{refs.current[index]=node}} key={item.id} id={`${item.id}-tab`} role="tab" aria-selected={selected===item.id} aria-controls={`${item.id}-panel`} tabIndex={selected===item.id?0:-1} onClick={()=>onSelect(item.id)} onKeyDown={event=>{if(event.key==="ArrowRight"){event.preventDefault();move(index+1)}else if(event.key==="ArrowLeft"){event.preventDefault();move(index-1)}else if(event.key==="Home"){event.preventDefault();move(0)}else if(event.key==="End"){event.preventDefault();move(items.length-1)}}}>{item.label}</button>)}</div>
}

export default function Home(){
  const [activePart,setActivePart]=useState("shape");
  const [activeSubtabs,setActiveSubtabs]=useState<Record<string,string>>({shape:"assessment",manual:"manual-0",start:"orientation"});
  function selectPart(id:string,writeHash=true){setActivePart(id);if(writeHash)history.replaceState(null,"",`#${id}`)}
  function selectSubtab(part:string,id:string){setActivePart(part);setActiveSubtabs(current=>({...current,[part]:id}));history.replaceState(null,"",`#${id}`)}
  useEffect(()=>{function syncHash(){const id=location.hash.slice(1);for(const part of parts){if(id===part.id){setActivePart(part.id);return}if(part.subtabs.some(tab=>tab.id===id)){setActivePart(part.id);setActiveSubtabs(current=>({...current,[part.id]:id}));return}}}syncHash();addEventListener("hashchange",syncHash);return()=>removeEventListener("hashchange",syncHash)},[]);
  return <>
  <header><div className="container nav"><Link href="#top"><Brand/></Link><nav aria-label="Primary"><a href="#shape" aria-current={activePart==="shape"?"page":undefined} onClick={event=>{event.preventDefault();selectPart("shape")}}>What it&apos;s for</a><a href="#manual" aria-current={activePart==="manual"?"page":undefined} onClick={event=>{event.preventDefault();selectPart("manual")}}>What&apos;s there</a><a href="#start" aria-current={activePart==="start"?"page":undefined} onClick={event=>{event.preventDefault();selectPart("start")}}>How to use it</a></nav></div></header>
  <main id="top" className="container">
    <section className="statement">
      <div><span className="path">Wingman / manual /</span><h1>Find a small number of genuinely good roles, and be the obvious candidate for them.</h1><p>Wingman is a career intelligence system you talk to through Claude. It learns from what you have written and done, and from who you respect or would rather not be named alongside. Every claim it makes carries a real sentence you wrote behind it. It never sends anything for you.</p></div>
      <aside><span className="label">Were you sent two links?</span><p>Then someone is running Wingman for you. You install nothing and there is no terminal.</p><a href="#start">Skip to setup →</a><Link className="quiet" href="/guides/local-setup">Or run it on your own machine</Link></aside>
    </section>
    <div className="loop" aria-label="How Wingman works"><div><b>01</b><span>Your words, and who you admire</span></div><div><b>02</b><span>They become a cited profile</span></div><div><b>03</b><span>The profile judges a role</span></div><div><b>04</b><span>You decide and you send it</span></div></div>

    <Tabs items={parts} selected={activePart} onSelect={selectPart} label="Manual sections" className="primaryTabs" />

    <section id="shape-panel" role="tabpanel" aria-labelledby="shape-tab" hidden={activePart!=="shape"} className="partPanel"><Part number="one" title="What it's for" description="The shape of the thing, what it hands back, and the four things it will not do for you." /><Tabs items={parts[0].subtabs} selected={activeSubtabs.shape} onSelect={id=>selectSubtab("shape",id)} label="What it’s for topics" className="subtabs" />
    <section id="assessment-panel" role="tabpanel" aria-labelledby="assessment-tab" hidden={activeSubtabs.shape!=="assessment"}><Divider>01 — What it hands back</Divider><p className="lead">Ask whether a posting fits and you get a verdict per requirement, each one traceable. Gaps stay gaps. Where the evidence is missing, Wingman says unknown.</p>
      <div className="assessment"><div className="assessmentHead"><span>Fit assessment · Staff Data Engineer, Acme</span><span>7 met · 2 partial · 1 gap · 2 unknown</span></div><div className="assessmentLabels"><span>Verdict</span><span>Requirement</span><span>Evidence</span></div>
        <div className="assessmentRow"><b className="met">Met</b><span>Owned a warehouse migration end to end</span><p>“Led the move from Redshift to Snowflake across 40 pipelines.” <small>— cv.pdf, p2</small></p></div>
        <div className="assessmentRow"><b className="partial">Partial</b><span>Managed a team of five or more</span><p>“Line managed three engineers and two contractors.” <small>— LinkedIn export</small></p></div>
        <div className="assessmentRow"><b className="gap">Gap</b><span>Regulated-industry experience</span><p>No profile evidence supports this. Address it directly or drop the role.</p></div>
        <div className="assessmentRow"><b className="unknown">Unknown</b><span>Comfortable with on-call rotation</span><p>No evidence either way. Answer it once and it becomes part of the record.</p></div>
      </div><p className="caption">Illustrative specimen. Real assessments cite your own documents.</p>
    </section>
    <section id="refusals-panel" role="tabpanel" aria-labelledby="refusals-tab" hidden={activeSubtabs.shape!=="refusals"}><Divider>02 — What it will not do</Divider><div className="refusalGrid">{refusals.map(([title,body])=><article key={title}><h3>{title}</h3><p>{body}</p></article>)}</div></section></section>

    <section id="manual-panel" role="tabpanel" aria-labelledby="manual-tab" hidden={activePart!=="manual"} className="partPanel"><Part number="two" title="What's there" description="Twelve things people ask Wingman to do, grouped by the question each one answers." /><Tabs items={parts[1].subtabs} selected={activeSubtabs.manual} onSelect={id=>selectSubtab("manual",id)} label="Guide groups" className="subtabs" /><p className="tabLead">A directed search is three questions asked about a different company each week. Each guide opens with the sentence you would type.</p><div className="guideGroups">{guideGroups.map((group,index)=><section id={`manual-${index}-panel`} role="tabpanel" aria-labelledby={`manual-${index}-tab`} hidden={activeSubtabs.manual!==`manual-${index}`} className="guideGroup" key={group.label}><div className="groupHead"><span>{group.label}</span><b>{group.slugs.length}</b></div>{group.slugs.map(slug=>{const guide=getGuide(slug)!;return <Link className="guideRow" href={`/guides/${slug}`} key={slug}><div><h3>{guide.title}</h3><p>{guide.summary}</p></div><blockquote>“{guide.ask}”</blockquote><span>↗</span></Link>})}</section>)}</div><div className="manualFoot"><span>Worth knowing</span><p>Wingman ships no scheduler. An overnight run happens because you — or whoever hosts your instance — scheduled it.</p></div></section>

    <section id="start-panel" role="tabpanel" aria-labelledby="start-tab" hidden={activePart!=="start"} className="partPanel"><Part number="three" title="How to use it" description="Four ordered steps to a useful workspace, then one table for every week after that." /><Tabs items={parts[2].subtabs} selected={activeSubtabs.start} onSelect={id=>selectSubtab("start",id)} label="How to use Wingman topics" className="subtabs" />
    <section id="orientation-panel" role="tabpanel" aria-labelledby="orientation-tab" hidden={activeSubtabs.start!=="orientation"}><Divider>04 — Your first twenty minutes</Divider><p className="lead">There are no commands to memorise. Everything below is a sentence you type to Claude. Do these four in order; skip the fourth and Wingman can find openings but cannot rank them.</p><aside className="interviewForm"><div><span className="label">Prefer a form?</span><h3>Answer the interview in your own time.</h3></div><p>Once your account is set up, email Wingman to request a customised Google Form. You can use it instead of answering the interview questions in Claude, or work across both.</p><a href="mailto:wingman@dhk.io?subject=Wingman%20interview%20form">Request your form →</a></aside><div className="stepList">{orientation.map((step,i)=><article className={`stepRow ${step.important?"important":""}`} key={step.title}><span className="stepNumber">{String(i+1).padStart(2,"0")}</span><div className="stepBody"><div className="stepTitle"><h3>{step.title}</h3>{step.important&&<span>Do not skip</span>}</div><p>{step.body}</p><div className="prompt"><span>Type this</span>{step.prompt}</div></div><div className="proof"><span>You know it worked when</span><p>{step.proof}</p></div></article>)}</div></section>
      <section id="run-it-yourself-panel" role="tabpanel" aria-labelledby="run-it-yourself-tab" hidden={activeSubtabs.start!=="run-it-yourself"}><div className="runLocal"><span>Running it yourself</span><p>Same product, you hold the keys: a local workspace, your database, your model providers. The steps above are identical once the connector is in place. <Link href="/guides/local-setup">Desktop walkthrough →</Link></p></div></section>
      <section id="weekly-next-move-panel" role="tabpanel" aria-labelledby="weekly-next-move-tab" hidden={activeSubtabs.start!=="weekly-next-move"}><div className="nextMoveCallout"><div><span className="label">Your weekly action list</span><h3>Find the sentence for your next move.</h3><p>Start with what you have—a name, a company, a job posting, or a noisy pile of links—and jump to the one guide that moves it forward.</p></div><Link href="/next-move">Open the next-move table →</Link></div></section></section>
    <footer><Brand/><p>Local-first career intelligence · Quality over volume · Evidence before assertion</p><span className="footerLinks"><a href="https://github.com/dhk/wingman">GitHub ↗</a><a href="https://www.dhk.io">DHK ↗</a></span></footer>
  </main>
  </>}
