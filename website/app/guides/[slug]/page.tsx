import Link from "next/link";
import { notFound } from "next/navigation";
import { getGuide, guides } from "../data";

export function generateStaticParams(){return guides.map(({slug})=>({slug}))}
function Brand(){return <span className="brand"><i/>Wingman</span>}

export default async function GuidePage({params}:{params:Promise<{slug:string}>}){
  const {slug}=await params; const guide=getGuide(slug); if(!guide) notFound();
  const next=getGuide(guide.nextSlug)!;
  return <>
    <header><div className="container nav"><Link href="/"><Brand/></Link><nav><Link href="/#manual">All guides</Link><Link href="/next-move">Next move</Link></nav></div></header>
    <main className="container guidePage">
      <nav className="breadcrumb" aria-label="Breadcrumb"><ol><li><Link href="/">Wingman manual</Link></li><li><Link href="/#manual">Guides</Link></li><li aria-current="page">{guide.title}</li></ol></nav>
      <section className="guideIntro"><span className="ask">“{guide.ask}”</span><h1>{guide.title}</h1><p>{guide.intro}</p></section>
      <div className="prerequisites"><span>Prerequisites</span>{guide.prerequisites.length?guide.prerequisites.map(item=><Link href={`/guides/${item.slug}`} key={item.slug}>{item.label} ↗</Link>):<b>None</b>}</div>
      <section className="guideSteps"><div className="sectionDivider"><span>Do this in order</span><i/></div><div className="stepList">{guide.steps.map((step,i)=><article className="stepRow" key={step.title}><span className="stepNumber">{String(i+1).padStart(2,"0")}</span><div className="stepBody"><h2>{step.title}</h2><p>{step.body}</p><div className="prompt"><span>Type this</span>{step.prompt}</div>{step.note&&<aside className="inlineNote"><span>Limit</span><p>{step.note}</p></aside>}</div><div className="proof"><span>You know it worked when</span><p>{step.proof}</p></div></article>)}</div></section>
      <section className="adjacent"><span>This answered</span><h2>{guide.question}</h2><p>The natural next question is <b>{guide.nextQuestion}</b></p><Link href={`/guides/${next.slug}`}>{next.title} →</Link></section>
      <footer><Brand/><p>Evidence before assertion · Human approval before external action</p><span className="footerLinks"><Link href="/#manual">All guides ↑</Link><a href="https://www.dhk.io">DHK ↗</a></span></footer>
    </main>
  </>
}
