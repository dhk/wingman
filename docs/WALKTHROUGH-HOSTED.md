# Wingman — Getting Started

Someone is running Wingman for you. You install nothing, and there is no
terminal anywhere in this document.

They will have sent you **two links**:

| | |
|---|---|
| **Connector link** | ends in `/mcp/<a long string>` — this is what you give Claude |
| **Your page** | ends in `/ui/<a long string>/` — open it in a browser |

Both links are secret. Anyone who has one *is* you, as far as Wingman is
concerned, so treat them like a password: don't forward them, don't paste
them into a group chat. If one leaks, tell whoever set this up and they
can issue you a new pair in a few seconds.

## What this thing is

Wingman helps you find a small number of genuinely good roles and be the
obvious candidate for them — rather than helping you apply to more things.

Its one rule is that **everything it tells you is traceable**. Claude
proposes; ordinary code checks. Every claim in your profile, every point in
a briefing, has to carry a real sentence from a real document behind it, or
it doesn't get stored. You will never find a polished line in a Wingman
document that you can't trace back to something somebody actually wrote.

And it **never sends anything for you**. It drafts raw material — talking
points, intro bullets, notes for a conversation — that you turn into your
own words and send yourself. No message, no application, nothing.

## Step 1 — Give Claude the connector link

**In the Claude desktop app:** Settings → Connectors → Add custom connector.
Paste the connector link. Name it whatever you like.

**On claude.ai:** the same, under Settings → Connectors.

That's the whole setup. There's nothing to install and nothing to
configure.

Now start a new chat and say:

> what do you know about me?

If it answers — even with "nothing yet" — you're connected.

## Step 2 — Open your page and add your CV

Open the second link in a browser. It's your own page: uploads at the top,
everything Wingman has written for you underneath.

Upload whichever of these you have:

- **Your CV or résumé** — PDF, Word, Markdown, plain text
- **Your LinkedIn profile as a PDF** — on your profile, *More → Save to PDF*
- **Your LinkedIn data export**, if you want your connections too —
  Settings → Get a copy of your data. It arrives by email, usually within
  ten minutes.

Then go back to the chat:

> what do you know about me now?

You should see your roles, what you've done, and the skills it found —
each one traceable to a line in the document you uploaded.

**If something looks wrong, say so.** "That job title is wrong", "I never
worked there", "drop that one" — it will fix it, and the correction sticks.

## Step 3 — Tell it what you're looking for

This is the highest-value ten minutes in the whole thing, and the easiest
to skip.

> let's set up my job criteria

It walks you through five short areas: what you won't accept, the shapes of
job you'd actually take, what makes you lean in, what makes you close the
tab, and what wins when two good things compete.

Until you do this, Wingman can find openings but can't tell you which ones
matter. Everything downstream gets better once it's done.

*Your host may instead send you a form with the same questions, if you'd
rather answer them in one sitting than in a conversation.*

## Step 4 — Say who you admire

> help me build my profile

A handful of questions about people you admire and people you'd rather not
be associated with, and the same for organizations. It sounds soft and
isn't: what you value predicts which roles you'll be happy in far better
than a skills list does, and it's the part a CV can never tell anyone.

No CV required for this — it works on its own.

## What comes next

All of these are sentences you type to Claude, not commands to remember.

**Finding work**

- **Assess a role** — paste a job posting or its link and ask *"does this
  fit me?"* You get a cited fit brief: what matches, what doesn't, and what
  it can't tell.
- **Build an application pack** — *"build me an application pack for this"*
  pulls together the material for a specific role.
- **Save answers you'll reuse** — mid-application and answering the same
  question for the fifth time? *"save this answer for reuse"* keeps it,
  with the context, so you're not rewriting it again.
- **Dump leads somewhere** — a burst of links with no time to sort them?
  *"add these to the heap"* catches them instantly; nothing is fetched or
  spent until you come back to sort.

**Companies and people**

- **Follow a company** — *"follow Acme at https://acme.com"* watches it,
  and its people, for anything worth knowing.
- **Company intelligence** — *"give me Acme's dossier"*, *"what's Acme's
  point of view?"*, *"what's the latest on Acme?"*
- **Deep-dive a person** — *"do a deep dive on Jane Author."* This one
  reaches the open web and costs real money, so it asks before spending and
  saves nothing until you approve what it found.
- **Find a warm introduction** — *"who do I know who could introduce me at
  Acme?"*
- **Give a relationship a goal** — *"what's my goal with Jane?"*, and
  afterwards *"log that I had coffee with Jane, we talked about X."* Both
  become evidence for the next conversation.

**Every day**

- **Your digest** — *"show me today's digest, then help me triage it."*
  Something runs overnight for you; the digest is what it found. Triage
  teaches it what to stop showing you.
- **Ask your workspace anything** — *"what do we know about semantic
  layers?"* searches everything at once — documents, notes, stances, news —
  and cites what it finds, including things that match by meaning rather
  than by word.
- **A point-of-view document** — *"build me a POV doc on Jane at Acme"*
  gathers what you know about a person and their company into something you
  can actually walk into a conversation with.
- **What you value** — *"what do I actually value?"* once you've done Step
  4, drawn from your own answers rather than from a personality test.

## Because someone else is hosting this

Worth being clear about, since it's different from running it yourself:

- **Your data lives on their machine**, not yours. Whoever runs the server
  can read it. That's a matter of trusting them, not of software.
- **Your workspace is yours alone.** Other people using the same server
  can't see your data, and you can't see theirs.
- **The overnight run happens for you automatically.** You don't schedule
  anything.
- **Lost your links?** Ask your host. They can print them again or issue
  new ones — there's deliberately no self-service password reset, because
  there's no password.
- **Backups are theirs to run**, not yours.

If you'd rather run the whole thing yourself, on your own machine and under
your own control, that's what [`WALKTHROUGH-DESKTOP.md`](WALKTHROUGH-DESKTOP.md)
covers — same product, you hold the keys.
