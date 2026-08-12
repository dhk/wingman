"""Who may run an operator-only tool (docs/RFC.md RFC-068, issue #271).

A shared multi-tenant process (RFC-048) serves several people from one
OS process, and every MCP tool it exposes is reachable by every tenant
holding a valid capability token. Most tools only ever touch the caller's
own workspace, so that is exactly right. A few do not:

- 'coach_persona' acts on somebody else's behalf;
- 'carve_off_persona' writes a profile into another workspace;
- a future 'motd'/'qotd' write surface would put a line at the top of
  every other tenant's next actions (RFC-065/RFC-067), outranking every
  computed one by design.

Without a privilege concept those are callable by anyone who can reach
them — a hole nobody sees until it is used. This module is the whole
check: one boolean on Config, one refusal string, one call per gated
tool. It deliberately does NOT decide what a caller may do to files
(RFC-047's '750 root:wingman' tier still governs that, separately and by
different means); it decides whether this workspace is an operator's.
"""

from __future__ import annotations

from wingman.infrastructure.config import Config


def operator_only_refusal(config: Config, tool: str) -> str | None:
    """The message to return instead of running 'tool', or None to run it.

    Returns text rather than raising, because these are MCP tools whose
    contract is "return a string the assistant reads out": an exception
    surfaces to the person as a traceback or a generic tool error, which
    tells them nothing about why and reads like a bug in Wingman.

    The wording says the tool is not theirs and stops there. It names no
    file, no flag and no registry path on purpose — a tenant cannot see
    '/etc/wingman/tenants.toml' (RFC-047: 750 root:wingman), cannot edit
    it, and telling them to go and change it would be an instruction that
    cannot be followed. Nor does it suggest asking to be made an
    operator: that is a decision for whoever runs the box, made in front
    of the file, not a support request a refusal message should generate.
    """
    if config.privileged:
        return None
    return (
        f"{tool} isn't available in this workspace. It's an operator tool — it acts on "
        "other people's data rather than your own — so it belongs to whoever runs this "
        "Wingman. Nothing was changed, and no other tool is affected."
    )
