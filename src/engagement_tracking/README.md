# Engagement Tracking Subsystem

This subsystem is responsible for capturing **post-send engagement signals**
from outbound GTM campaigns and feeding them back into real-time workflows.

It closes the loop between:
**outbound activity → prospect behavior → internal action**

---

## Why Engagement Tracking Matters

In outbound GTM, *opens and clicks are weak signals*.

High-intent signals come from:
- How much of a video was watched
- Whether a prospect meaningfully engaged
- When that engagement happened

These signals enable:
- Prioritization of hot leads
- Real-time Slack alerts for sales teams
- Adaptive sequencing and follow-up logic

---

## High-Level Workflow

```text
Outbound Email Sent
        ↓
Prospect Clicks Video
        ↓
Video Platform Event (view)
        ↓
Zapier Webhook Trigger
        ↓
video_watch_rate_listener.py
        ↓
Engagement Metrics Extracted
        ↓
Zapier Response
        ↓
Slack / CRM / Scoring Logic
