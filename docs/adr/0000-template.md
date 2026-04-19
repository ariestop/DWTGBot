# ADR-NNNN — <Decision title>

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX | Deprecated | Rejected
- **Date:** YYYY-MM-DD
- **Deciders:** <names / handles>
- **Tags:** <topic, topic, …>
- **Supersedes:** (optional) ADR-XXXX
- **Superseded by:** (optional) ADR-XXXX

---

## 1. Context

What is the problem? What forces / constraints are at play? What was
the situation before this decision?

Be concrete. Reference code paths, docs, or external links. State the
**non-goals** as well — what this ADR is *not* trying to solve.

```
Examples of "context":
- "We need to deliver files >50MB to Telegram users; the Bot API forbids it."
- "Two services need to share state; the existing Redis is the obvious candidate
   but we want to confirm it isn't a single point of failure."
```

---

## 2. Decision

State the decision in one or two sentences, then expand. Use the
imperative present tense:

> "We use X for Y because Z."

Include just enough detail that an implementer can act without
guessing.

---

## 3. Consequences

What changes after this decision? Both:

### 3.1 Positive

- …
- …

### 3.2 Negative / accepted trade-offs

- …
- …

### 3.3 Operational impact

- Deployment changes (new container? new env var?).
- Cost / hardware impact.
- On-call / monitoring changes.

---

## 4. Alternatives considered

For each alternative, one short paragraph: **what it is, why we
didn't pick it.**

### 4.1 <Alternative A>
…

### 4.2 <Alternative B>
…

---

## 5. Compliance

How will the team / agents notice if this ADR is being violated?

- Code: lint rules / type checks / tests that catch regressions.
- Reviews: keywords in PR templates.
- Docs: which `docs/*.md` should be updated to reflect the decision.

---

## 6. References

- Code: `path/to/file.py`
- Docs: `docs/XX-...md` (replace with the actual file(s) you updated)
- Issues / PRs: #NNN, #MMM
- External: links to articles, RFCs, etc.

---

## 7. History

| Date | Status | Note |
|---|---|---|
| YYYY-MM-DD | Proposed | Drafted by … |
| YYYY-MM-DD | Accepted | Merged in PR #… |
