# Verification

This folder represents verification logic used to protect deliverability and data quality in outbound GTM workflows.

Business goals of this subsystem:
- Verify email existence before outreach to reduce bounce rates
- Detect mailbox providers (e.g. Google Workspace, Microsoft 365)
- Route inbox warmup and sending logic based on provider
- Protect domain reputation and sender score at scale

This subsystem is critical for ensuring outbound campaigns can scale without harming deliverability or inbox placement.
