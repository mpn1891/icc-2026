# Reference materials

Vendor manuals and external specs kept for **demo** data-model design. Not runtime
inputs — nothing in compose or Ignition reads these.

Dropped-from-the-talk sources (Countess, LIMS, …) live in [`../extra/`](../extra/README.md),
not here.

| Document | Source | Use |
|---|---|---|
| [LPN 60644B — BioProfile FLEX2 OPC Server Instructions for Use](LPN%2060644%20-BioProfile-FLEX2-IFU-EN-Manual-OPC.pdf) | Nova Biomedical, 2024-03 | Section 9 is a complete tag list for a **real, shipping vendor OPC UA server** — ~400 tags across `OPCSystemObjects` and `OPCSystemCommands`. Also the security policies, licensing and the vendor's own liveness acceptance test (§6) |
| [BioProfile FLEX2 — OPC UA information model](novaflex2-opcua-model.md) | Transcribed from LPN 60644B §9 | **A report, not a proposal.** Describes an address space that already exists. Analyte tables, missing-trigger analysis, defects in the published table, and what the demo added on top |
| Turbidity meter API / manual | **TBD — vendor docs coming.** | Patterns 5 and 6. Placeholder schema in [`plans/05-cdc-turbidity.md`](../plans/05-cdc-turbidity.md) until this row is a real file |
