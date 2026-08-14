# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | :white_check_mark: |

PrivaParse is pre-1.0. Until a 1.0 release, only the latest 0.1.x release
receives security fixes.

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting: open a
draft advisory at the repository's
[advisory page](https://github.com/Jhiynn/PrivaParse/security/advisories/new),
or use the "Report a vulnerability" button under the repository's Security
tab. **Do not open a public issue for a suspected vulnerability.** This
project's job is to keep personal data from leaking; a public report of a
detection bypass or a vault exposure is a disclosure before there's a fix,
which is the exact failure mode the tool exists to prevent elsewhere.

Include what you'd include in any bug report — version, reproduction steps,
what you expected instead — and, specifically, whether the issue lets PII
escape pseudonymisation, lets one session's mapping resolve a placeholder it
didn't issue, or exposes vault contents to something other than the process
that owns them.

**Expected response time:** an acknowledgement within 7 days. This project
has one maintainer, not a security team with a published SLA — a fix
timeline follows the acknowledgement once the report is understood, not
before.

## Threat model

Three things are true about this tool by design, and worth reading before you
point it at anything you care about:

**The vault stores plaintext PII on disk.** `privaparse.db` is a local
SQLite database, and every real name, address, and number the tool has ever
pseudonymised is in it, unencrypted, for as long as the mapping needs to
stay resolvable. It is the most sensitive file the tool produces. See
[The vault holds plaintext PII](docs/architecture.md#the-vault-holds-plaintext-pii)
for what's actually stored and why the encryption seam exists in the code
but isn't used yet.

**Restoration writes real PII back into the client, on purpose.** That is
the feature: the model provider sees a placeholder, and the answer handed
back to you has your real data in it again. It also means the client
you're running — its terminal, its logs, anything it writes to disk — sees
exactly the values PrivaParse spent the request pseudonymising away from
the provider. See
[Restoration puts real PII into the client](docs/gateway.md#restoration-puts-real-pii-into-the-client)
for what that means concretely, including why a server-side client changes
the picture.

**A configured key is what stands between a reachable gateway and every
mapping the vault has ever issued — and it is one key, not a permission
system.** `privaparse serve` still refuses to bind anything but loopback on
its own; setting `PRIVAPARSE_API_KEY` is what lifts that refusal, and from
that point every route requires the key, presented as `X-PrivaParse-Key`,
**except `GET /healthz`** — left open because the container healthcheck
curls it without a key — see
[Binding beyond loopback](docs/gateway.md#binding-beyond-loopback). This
used to be moot: a loopback-only bind meant the only way to reach the API at
all was already being on the machine, which is a strictly stronger position
than holding a key. That is no longer the whole story once the bind is
wider, and three things follow from it, none of them solved by the key
merely existing. The bind refusal itself lives in the `privaparse serve` CLI
command, not in the gateway app it builds — a caller who constructs
`create_app` directly and serves it themselves owns the bind decision, and
gets only a log warning, not a guard, if they do it with no key configured:

- **One key, one trust level.** There is no per-caller scoping, and this
  design does not add one. Whoever holds the key can call
  `/privaparse/reverse` and read back everything the vault has ever
  pseudonymised, not only what their own requests produced. Handing the key
  to one integration hands it the whole vault.
- **A Docker network is not a security boundary.** Putting the gateway on a
  compose network so one container can reach it does not limit who else
  can. Any sibling service on that network reaches the API the same way —
  including one added to it later, by someone who never read this file and
  has no reason to expect a de-pseudonymisation endpoint sitting on the
  network they just joined.
- **Binding beyond loopback puts a de-pseudonymisation endpoint on a
  network, full stop.** The key is the only thing in front of it: not a
  firewall rule, not TLS, not a per-route permission. Generate it randomly,
  give it to as few processes as the deployment actually needs, and treat a
  leak of it as a leak of the vault, because that is what it is.

**There is no TLS anywhere in this design.** Not "the key doesn't give you
TLS" as a caveat — there is no TLS to give, on any bind. On a non-loopback
bind, the shared key and every restored answer this gateway hands back —
real names, addresses, numbers, by definition — cross the network in
plaintext, both directions. The compose example in
[Binding beyond loopback](docs/gateway.md#binding-beyond-loopback) uses
`http://` throughout for exactly this reason: it is what the gateway speaks.
Anything beyond a container-local network wants a TLS terminator in front of
it.

**Out of scope, unchanged by any of the above:** an attacker who already has
read access to the machine PrivaParse runs on. The vault has no encryption
in Phase 1 — a local file-read primitive wins against it no matter what
stands in front of the network. What PrivaParse protects is the hop to a
model provider, and, once a key is configured, the hop to the gateway
itself. Neither protection extends to the machine the vault lives on, and
that is a design boundary, not an oversight.
