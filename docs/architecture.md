# Architecture

```
Markdown
  ├─ protect()          mask code fences, inline code, URLs (length-preserving)
  ├─ detect             GLiNER2 for names + regex/phonenumbers for email & phone
  ├─ merge              resolve overlaps, then sweep for missed repeats
  ├─ normalize          per type: E.164, lowercase, title-stripped
  ├─ resolve            vault lookup → stable placeholder
  └─ replace            character spans, back to front
```

Four design decisions worth knowing about:

**The vault is global.** A value gets the same placeholder in every document,
forever. `Max Mustermann` is `[[PERSON_A1]]` today and next year.

**Reversal is scoped to one session.** Because placeholders are stable they are
also guessable, so `reverse()` only resolves placeholders that *this* mapping
issued. Anything else is left in place and reported. Without that, writing
`[[PERSON_A47]]` into a document would read back a stranger's name.

**The suffix counter is shared across types.** You get `PERSON_A1`, `EMAIL_A2`,
`PHONE_A3` — not three `A1`s. Phase 1 does no cross-type linking, and a per-type
counter would imply a link that isn't there.

**Email and phone come from rules, not the model.** They have well-defined
syntax, so a model only adds variance. It also makes evaluation honest: if
email and phone score near 1.0 and person doesn't, the model is the problem
rather than the pipeline.

## The vault holds plaintext PII

`privaparse.db` accumulates every real name, address and number the tool has
ever seen. It is the most sensitive file the tool produces, it is not encrypted
in Phase 1, and it is in `.gitignore` for a reason. Every read and write goes
through a `ValueCipher` seam (`privaparse/database/cipher.py`) so encryption is
a one-class swap later rather than a migration.

## Markdown handling

Fenced code, inline code, HTML comments and URLs are masked before detection, so
a `user.email` in a code sample is not pseudonymised. Two deliberate exceptions:

- **YAML frontmatter is scanned** — `author:` fields carry real names.
- **`mailto:` targets are scanned** — a mailto link *is* an email address.

Indented code blocks are *not* protected: four-space indentation is ambiguous
with list continuation, and hiding a real name is the more expensive mistake.

Known limitation: a name inside a URL path (`https://firma.de/team/max-mustermann`)
is not detected while URLs are protected. Use `--scan-code` if that matters more
to you than false positives on domain names.
