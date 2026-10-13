# Invitee message template

Send this with the connector address when you invite someone to the shared
Wingman server (see the [runbook](OAUTH-LAUNCH-RUNBOOK.md) §4). Replace the
placeholders. With an email invite, name the exact address you invited: a
sign-in with any other Google account is not matched and waits for you
instead. Keep the "couldn't connect" paragraph — Claude's own error does not
tell them what to do next, and it looks the same for a wrong account, an
outage, or a failure before sign-in, so the paragraph must not promise which
one it is.

---

Hi <name>,

You're invited to Wingman. Sign in with your Google account
**<the address you invited>** — that address is your invitation. You can start
in either place below, in either order.

To connect it to Claude:

1. In Claude, open **Settings → Connectors → Add custom connector**.
2. Name it `Wingman` and paste this URL:
   `<https://your-server.example.ts.net/shared/mcp>`
3. Click **Connect** and sign in with your Google account when asked. You may
   not see a sign-in window at all if you're already signed in — that's fine.

To set up your workspace in a browser (adding your own API key, uploading a
CV), sign in at `<https://your-server.example.ts.net/shared/login>` with the
**same** Google account.

**If Claude says "Couldn't connect" or "Couldn't reach":** first check you
signed in with the address above. Claude shows the same message for several
different problems, so don't assume either way. Open the browser sign-in page
above: if it shows a Wingman reference like `ABCD-EFGH`, send me that and
roughly when you tried. (A code starting `ofid_` comes from Claude, not from
Wingman. It's fine to include, but it isn't what I look you up by.) Once I've
checked, I'll either let you in or tell you what's wrong. Then click
**Reconnect** in Claude.

<your name>
