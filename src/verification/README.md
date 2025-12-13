# Verification

This folder represents the **verification and deliverability intelligence layer**
of a larger GTM automation system.

Its role is not only to validate prospect contact data, but to **surface
infrastructure-level signals** that downstream outreach and warmup systems can
act on intelligently.

---

## Business Purpose

At scale, **high-quality messaging only performs if it reliably reaches the inbox**. This layer exists to ensure that strong copy and personalization are not undermined by avoidable deliverability failures.

This subsystem exists to:

- Verify domains and email infrastructure before outreach
- Detect mailbox providers (e.g. Google Workspace, Microsoft 365, other)
- Prevent hard bounces and reduce spam complaints
- Protect sender reputation and long-term domain health
- Enable provider-aware routing and warmup strategies

---

## Why Provider Detection Matters

Inbox providers evaluate sender reputation **independently**.

Gmail, Outlook, and other providers each maintain their own spam filters,
reputation models, and throttling behavior. Warming up or sending “generically”
across all providers produces diluted reputation signals.

This layer enables a more deliberate approach:

- Identify which provider each prospect’s domain uses
- Group prospects by receiving infrastructure
- Route outreach through inboxes that have been warmed specifically
  against that provider’s ecosystem

Example:

- Gmail prospects → sent from inboxes warmed primarily against Gmail
- Outlook prospects → sent from inboxes warmed primarily against Outlook
- Other providers → routed separately

This dramatically improves inbox placement when working with
**high-value or low-volume prospect lists**, where deliverability matters
more than raw throughput.

---

## What This Layer Does (Practically)

- Accepts prospect domains or email addresses as input
- Resolves MX records and provider fingerprints
- Classifies domains by mailbox provider
- Outputs structured verification and routing signals for downstream systems

The output of this layer feeds directly into:

- Inbox warmup orchestration
- Outreach account selection
- Campaign routing logic
- Risk-aware GTM execution

---

## Why This Matters Architecturally

By isolating verification and provider detection as its own subsystem:

- Deliverability logic stays explicit and testable
- Warmup and sending systems become provider-aware
- High-stakes outreach can be handled differently from bulk campaigns
- The overall GTM pipeline becomes safer to scale

This folder illustrates how verification was treated as
**infrastructure intelligence**, not just a hygiene step.
