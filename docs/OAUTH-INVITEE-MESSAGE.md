# Invitee message template

Send this with the connector address when you invite someone to the shared
Wingman server (see the [runbook](OAUTH-LAUNCH-RUNBOOK.md) §4). Replace the
placeholders; keep the "couldn't connect" paragraph — Claude's own error does
not tell them what to do next.

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

**If Claude says "Couldn't connect" or "Couldn't reach":** that's expected the
first time. It means you're signed in and waiting for me to approve you. Send
me the reference shown (if there is one), or just text me that you've tried,
and I'll approve you — usually within the day. Then click **Reconnect** in
Claude.

If it still won't connect after I've confirmed you're approved, tell me
roughly what time you tried. That's all I need to look it up.

<your name>
