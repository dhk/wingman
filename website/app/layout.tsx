import type { Metadata } from "next";
import { DM_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const mono=DM_Mono({variable:"--font-mono",subsets:["latin"],weight:["400","500"]});
export async function generateMetadata():Promise<Metadata>{
  const incoming=await headers();
  const host=incoming.get("host")??"localhost:3000";
  const protocol=host.startsWith("localhost")?"http":"https";
  const metadataBase=new URL(`${protocol}://${host}`);
  const title="Wingman — The manual for a directed job search";
  const description="Find a small number of genuinely good roles, and be the obvious candidate for them.";
  return {metadataBase,title:{default:title,template:"%s · Wingman"},description,icons:{icon:"/favicon.svg",shortcut:"/favicon.svg"},openGraph:{title,description,type:"website",images:[{url:"/og.png",width:1200,height:630,alt:"Wingman — The manual for a directed job search"}]},twitter:{card:"summary_large_image",title,description,images:["/og.png"]}};
}
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body className={mono.variable}>{children}</body></html>}
