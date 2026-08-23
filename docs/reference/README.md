# Reference materials

Vendor manuals and external specs kept for **demo** data-model design. Not runtime
inputs — nothing in compose or Ignition reads these.

Dropped-from-the-talk sources (Countess, LIMS, …) live in [`../extra/`](../extra/README.md),
not here.

| Document | Source | Use |
|---|---|---|
| [LPN 60644B — BioProfile FLEX2 OPC Server Instructions for Use](LPN%2060644%20-BioProfile-FLEX2-IFU-EN-Manual-OPC.pdf) | Nova Biomedical, 2024-03 | Section 9 is a complete tag list for a **real, shipping vendor OPC UA server** — ~400 tags across `OPCSystemObjects` and `OPCSystemCommands`. Also the security policies, licensing and the vendor's own liveness acceptance test (§6) |
| [BioProfile FLEX2 — OPC UA information model](novaflex2-opcua-model.md) | Transcribed from LPN 60644B §9 | **A report, not a proposal.** Describes an address space that already exists. Analyte tables, missing-trigger analysis, defects in the published table, and what the demo added on top |
| [AP Connect + Haze 3001 — the parts patterns 5 and 6 need](apconnect-haze3001-model.md) | Distilled from the Anton Paar documentation set, 2026-08-23 | **Read this before specs 05 and 06.** The instrument (Haze 3001 turbidity module), the application that stores its data (AP Connect 4.0), the REST data model, `WellKnownMeasurementStatus`, the `Variant` representation, the MS SQL Server dependency, and which ids are transcribed vs modelled |
| [AP Connect REST API 1.4.0 — OpenAPI definition](apconnect-openapi-4.0.json) | Anton Paar, shipped with AP Connect 4.0 | The machine-readable source of truth for endpoints, parameters and schemas. Prefer it over the printed PDFs |
