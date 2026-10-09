# Invitee message template

Send this with the connector address when you invite someone to the shared
Wingman server (see the [runbook](OAUTH-LAUNCH-RUNBOOK.md) §4). Replace the
placeholders; keep the "couldn't connect" paragraph — Claude's own error does
not tell them what to do next, and it looks the same for a pending approval,
an outage, or a failure before sign-in, so the paragraph must not promise
which one it is.

---

Hi <name>,

You're invited to Wingman. To connect it to Claude:

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. Name it `Wingman` and paste this URL:
   `<https://your-server.example.ts.net/shared/mcp>`
3. Click **Connect** and sign in with your Google account when asked. You may
   not see a sign-in window at all if you're already signed in — that's fine.

To set up your workspace in a browser (adding your own API key, uploading a
CV), sign in at `<https://your-server.example.ts.net/shared/login>` with the
**same** Google account.

**If Claude says "Couldn't connect" or "Couldn't reach":** this can happen
while you're waiting for me to approve you, but Claude shows the same message
for several different connection problems, so don't assume either way. Tell me
roughly when you tried. If the browser sign-in page shows a Wingman reference
like `ABCD-EFGH`, send me that too. (A code starting `ofid_` comes from Claude,
not from Wingman. It's fine to include, but it isn't what I look you up by.)
Once I've checked, I'll either approve you or tell you what's wrong. Then click
**Reconnect** in Claude.

<your name>
