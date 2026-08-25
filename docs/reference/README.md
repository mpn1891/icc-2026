# Reference materials

Vendor manuals and external specs kept for demo data-model design. Not runtime
inputs — nothing in compose or Ignition reads these.

| Document | Source | Use |
|---|---|---|
| [MAN0019567 — Countess 3 FL Automated Cell Counter User Guide](MAN0019567-Countess-3FL-Automated-Cell-Counter-UG.pdf) | [Thermo Fisher](https://documents.thermofisher.com/TFS-Assets/LSG/manuals/MAN0019567-Countess-3FL-Automated-Cell-Counter-UG.pdf) | Cell-count / viability result fields and analyzer workflow when shaping QC analyzer payloads and LIMS sample-result models |
| [Countess 3 FL — OPC UA information model](countess-3fl-opcua-model.md) | Derived from MAN0019567 Appendix E | Address space, DataTypes, event/counter trigger contract and MQTT projection for a simulated cell-counter OPC UA server. **Not a vendor model** — the real instrument exports CSV only. The Countess came **out of the demo on 2026-08-25**; this doc and `services/opcua-countess` stay as the designed-model reference and the worked example |
| [LPN 60644B — BioProfile FLEX2 OPC Server Instructions for Use](LPN%2060644%20-BioProfile-FLEX2-IFU-EN-Manual-OPC.pdf) | Nova Biomedical, 2024-03 | Section 9 is a complete tag list for a **real, shipping vendor OPC UA server** — ~400 tags across `OPCSystemObjects` and `OPCSystemCommands`. Also the security policies, licensing and the vendor's own liveness acceptance test (§6) |
| [BioProfile FLEX2 — OPC UA information model](novaflex2-opcua-model.md) | Transcribed from LPN 60644B §9 | **A report, not a proposal** — unlike the Countess doc, this describes an address space that already exists. Carries the analyte tables, the missing-trigger analysis, the defects in the published table, and what the demo added on top |
