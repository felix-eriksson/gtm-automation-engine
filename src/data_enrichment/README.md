# Data Enrichment

This folder represents the **data enrichment layer** of a larger GTM automation system.

Its role is to transform raw, incomplete prospect and company inputs into
**structured, reliable data** that downstream AI personalization and media
generation systems can safely depend on.

---

## Business Purpose

In high-volume GTM workflows, the bottleneck is rarely outreach execution —
it is **data quality and routing**.

This subsystem exists to:

- Normalize inconsistent inbound prospect and account data
- Enrich company- and domain-level metadata programmatically
- Convert loosely structured inputs into deterministic, machine-readable formats
- Reduce manual research and copy-paste work for sales and GTM teams
- Prevent downstream failures caused by missing, ambiguous, or malformed data

---

## What This Layer Does (Practically)

In practice, this layer is used to **process large CSV- or sheet-based datasets**
exported from tools like:

- LinkedIn Sales Navigator
- Apollo and similar prospecting tools
- Website scrapers and enrichment pipelines
- Internal GTM spreadsheets

These datasets are typically *partially structured* and require human judgment
to be useful. This layer replaces that manual judgment with **LLM-powered
classification, normalization, and routing logic**.

Typical capabilities include:

- Row-by-row enrichment of large datasets (thousands to tens of thousands of rows)
- Deterministic outputs suitable for filtering, routing, and automation
- Incremental processing with retry logic and safe persistence
- Easy re-import into Google Sheets, CRMs, or downstream pipelines

---

## Example Use Cases

### 1. ICP Qualification at Scale

A common use case is **filtering large company lists down to a true ICP**.

Example:
- Export 10,000 companies labeled broadly as *“Healthcare”*
- Many will be hospitals, consultancies, services, or vendors
- Only a subset are *MedTech SaaS companies with digital platforms*

Instead of manually reviewing each company:

- Company descriptions are passed through this enrichment layer
- An LLM classifies each company as:
  - `Yes` → matches ICP
  - `No` → exclude
- The output is written back to the CSV as a new column
- The filtered dataset can then be used safely for GTM campaigns

This reduces days of manual work to minutes.

---

### 2. Lead Routing by Role or Persona

Another frequent use case is **routing leads into the correct campaign or motion**.

Examples:
- Marketing
- Sales
- C-suite / Executive
- Technical / Product

Prospect role titles are often inconsistent or ambiguous.
This layer classifies each prospect into a **finite, controlled set of categories**
that downstream systems (email sequences, personalization logic, CRM workflows)
can rely on.

---

### 3. Time Zone Normalization

Location data is often inconsistent:

- “New York”
- “Los Angeles, United States”
- “SF Bay Area”
- “Remote – US”

This layer standardizes such inputs into controlled outputs like:

- Eastern
- Central
- Mountain
- Pacific
- Other

This enables correct send-time logic, campaign scheduling, and automation
without brittle string matching.

---

### 4. Company Name Normalization

Prospect and account data often includes noisy company names:

- “Apple Inc.”
- “Apple, Inc”
- “APPLE INC”
- “Apple Incorporated”

This layer normalizes company names into a clean, human-readable form
(e.g. `Apple`) so that downstream personalization tokens render correctly
in emails, videos, and sales assets.

---

## How LLMs Are Used Here

Large Language Models (via the OpenAI API) are used **selectively** in this layer
for tasks that are difficult to solve with static rules alone, such as:

- Semantic classification
- Context-aware normalization
- Ambiguous role or company categorization

Importantly:

- The LLM is treated as a **deterministic transformation step**
- Inputs and outputs are structured and persisted
- Results are designed to be consumed by automation, not humans

This is not chat or experimentation — it is **operational enrichment logic**.

---

## What This Layer Does *Not* Do

- It does not generate outreach copy
- It does not generate media
- It does not send messages or trigger campaigns

Its sole responsibility is to **prepare clean, trustworthy inputs**
for the systems that do.

---

## Why This Matters

Personalization systems are only as good as the data they consume.

By isolating enrichment as its own subsystem, the overall GTM pipeline becomes:

- More reliable
- Easier to debug
- Easier to extend
- Safer to scale

This folder illustrates how enrichment logic — including practical,
high-volume LLM usage — was implemented as a **first-class component**
of a production GTM system.
