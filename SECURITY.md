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

Two things are true about this tool by design, and worth reading before you
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

**Out of scope:** this tool does not defend against an attacker who already
has read access to the machine it runs on. The vault has no encryption and
no per-user access control in Phase 1 — a local file-read primitive already
wins against it, and that is a design boundary, not an oversight. What
PrivaParse protects is the network hop to a model provider. It does not, and
is not intended to, protect the machine itself.
