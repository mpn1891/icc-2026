# Extra

Material we are **not using in the talk** and still want in the repo.

This is not a graveyard for mistakes. It is the shelf for work that was built or specified,
then dropped from the demo, and might be useful later — another talk, a question from the
floor, a teammate who needs the Countess address space. Live architecture, specs, and talk
tracks do not live here. If a fact is still true of the demo, it belongs in
[`../00-architecture.md`](../00-architecture.md) or `docs/plans/`.

| Item | Why it is here | What is in the tree |
|---|---|---|
| [LIMS approval webhook](lims-webhook-spec.md) ([talk track](lims-webhook.md)) | Pattern 4 until 2026-08-23. Built and broker-verified. Replaced by a NovaFlex HTTPS POST | Spec + talk track here. Service still at `services/lims/`, still in compose, until the NovaFlex webhook rebuild unwires it |
| [Countess 3 FL OPC UA model](countess-3fl-opcua-model.md) ([user guide PDF](MAN0019567-Countess-3FL-Automated-Cell-Counter-UG.pdf)) | Second analyzer for a vendor-vs-designed contrast. Pattern 3's talk is the NovaFlex | Model + manual here. Service still at `services/opcua-countess/`, still in compose, MQTT publish never wired |
| Particle counter (MET ONE / Modbus) | Pattern 6 candidate 2026-08-19. Not built | No spec. Mentioned in git history and the 2026-08-23 plan notes |
| Odoo | Pattern 5 candidate 2026-08-19. Dropped 2026-08-20 | Removed from the tree. Do not add it back |
| Vibration gateway / AMS / DCS aggregation | Pattern 1 original, then a pattern-7 lean. Not the plan | `services/sim-vibration/` on disk, unwired. No Extra spec |

Services listed above stay where they are. Extra holds **docs**, not compose. Unwiring a
container is a build step, not a filing step.
