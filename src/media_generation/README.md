# Media Generation

This subsystem powers large-scale, deeply personalized video asset generation for Go-To-Market (GTM) workflows.

Unlike lightweight video personalization tools that offer limited, surface-level customization, this system enables **full creative control** over video narratives by programmatically rendering highly flexible After Effects templates using structured prospect and account data.

The result is the ability to generate **one-to-one personalized videos at one-to-many scale**, without sacrificing creative expressiveness.

---

## What This Subsystem Enables

The media generation layer makes it possible to:

- Render fully customized video narratives per prospect or account
- Replace any variable that can be expressed in an After Effects template
- Encode complex stories, explanations, or presentations into video
- Scale high-effort creative output without manual rendering work

Personalization is not limited to greetings or overlays — it is constrained only by:
1. What is designed into the After Effects template
2. What data can be sourced upstream

---

## Personalization Model (Important)

This system is **not “magic video AI.”**

Instead, it follows a deterministic and highly reliable model:

> If something can be defined as a variable in After Effects  
> and populated from structured data,  
> it can be personalized at scale.

This includes (but is not limited to):

- Company name, logo, and brand colors
- Industry- or segment-specific copy
- Screenshots, profiles, or visual references
- Narrative paths based on account attributes
- Different video structures for different GTM motions

The depth of personalization is driven by **template design**, not guesswork.

---

## Core Script: Batch Video Rendering Orchestrator

The central component of this subsystem is:
batch_video_rendering_orchestrator.py

This script coordinates the entire rendering lifecycle, including:

- Variable swapping across hundreds or thousands of renders
- Audio and video asset preparation
- Automated batch rendering via After Effects
- Resource management, retries, and crash recovery
- Output validation and deterministic file naming

It was built to support **production-scale rendering**, not demos.

This orchestration layer is what makes high-effort creative viable inside real GTM operations.

---

## GTM Context

In practice, this subsystem was used to:

- Power personalized outbound video campaigns
- Support sales, founder-led sales, and solution engineering workflows
- Deliver videos that explained *specific problems* using *prospect-specific context*
- Generate thousands of personalized assets without linear creative effort

Conceptually, this plays a similar role to what tools like Clay do for text-based personalization —  
but applied to **fully custom video narratives instead of email copy**.

---

## Why This Matters

Most GTM teams are constrained by a tradeoff:

- **High personalization** → high manual effort
- **High scale** → shallow personalization

This subsystem removes that tradeoff **when teams are willing to invest in template design**.

It turns creative effort into an upfront cost that can be amortized across large outbound or lifecycle campaigns.

---

## Intended Use

This folder contains representative scripts illustrating how automated media generation was engineered and scaled as part of a larger GTM automation system.

Client-specific assets, templates, credentials, and proprietary configurations have been removed.
