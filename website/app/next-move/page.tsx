import Link from "next/link";
import { getGuide } from "../guides/data";

const rows=[
  ["A name in mind, nothing read","What do we talk about?","meeting-brief"],
  ["A company in mind, no one named","Who do I talk to?","research-a-company"],
  ["A posting open in another tab","Why this conversation?","assess-a-role"],
  ["A name, but no way in","Who do I talk to?","warm-introduction"],
  ["Openings arriving unscored","Why this conversation?","job-criteria"],
  ["A pile of links and no time","Who do I talk to?","capture-links"],
  ["A noisy morning digest","Who do I talk to?","morning-digest"],
  ["A profile claim that is wrong","Why this conversation?","fix-my-profile"],
] as const;
function Brand(){return <span className="brand"><i/>Wingman</span>}
export default function NextMove(){return <>
  <header><div className="container nav"><Link href="/"><Brand/></Link><nav><Link href="/#manual">All guides</Link><Link href="/#start">Setup</Link></nav></div></header>
  <main className="container nextMove"><nav className="breadcrumb" aria-label="Breadcrumb"><ol><li><Link href="/">Wingman manual</Link></li><li aria-current="page">Next move</li></ol></nav><section className="nextIntro"><h1>Find the sentence for this week.</h1><p>Start with where you are. The table names the question still unanswered and gives you one sentence to type.</p></section>
    <div className="moveTable"><div className="moveHead"><span>Where you are this week</span><span>Which question that leaves unanswered</span><span>The sentence to type</span></div>{rows.map(([where,question,slug])=>{const guide=getGuide(slug)!;return <div className="moveRow" key={where}><p>{where}</p><b>{question}</b><div><blockquote>“{guide.ask}”</blockquote><Link href={`/guides/${slug}`}>{guide.title} ↗</Link></div></div>})}</div>
    <p className="prereqClose">Without a criteria document, every row in the “Why this conversation?” column returns unknowns. <Link href="/guides/job-criteria">Set your criteria →</Link></p>
    <footer><Brand/><p>One row · one prompt · one guide</p><span className="footerLinks"><Link href="/">Manual ↑</Link><a href="https://www.dhk.io">DHK ↗</a></span></footer>
  </main>
  </>}
