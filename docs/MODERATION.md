# Moderation (Beta)

The public map shows only reports that are explicitly submitted and then reviewed.

## Core principles
- **No passive scraping** — only intentional reports are processed.
- **Location required** — coordinates OR street+city (approx. house number optional).
- **One image** — exactly one photo per report.
- **No private data** — blur faces, license plates, private addresses, etc.
- **Illegal extremist symbols** — must be blurred/censored by the reporter (otherwise rejected).

## Status model (high level)
- **PENDING**: received, awaiting review
- **PUBLISHED**: approved and shown on the map
- **NEEDS_INFO**: missing/unclear location; reporter must provide a better location
- **REJECTED**: violates rules (no image, too many images, illegal unblurred symbols, missing mention, etc.)
- **REMOVED**: sticker confirmed removed (kept for history/tracking)

## Removals / confirmations
- A report can confirm removal (the map keeps history; it does not “forget” removals).
- “Last confirmed” can be updated by explicit confirmations.

This document describes only public behavior. Internal review procedures are not documented publicly.
