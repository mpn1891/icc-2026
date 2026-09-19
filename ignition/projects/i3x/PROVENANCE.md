# Provenance — the `i3x` project

This project is **vendored third-party code**, not ours. It is Travis Cox's reference
implementation of the CESMII i3X API (spec v1.0) on top of Ignition 8.3, dropped into this repo so
the demo gateway serves a real, vendor-neutral information-model API at
`https://localhost:8043/system/webdev/i3x` without anyone having to import a project by hand.
`docker-compose.yml` already bind-mounts `./ignition/projects` to the gateway's `data/projects`, so
the folder name *is* the project name — it must stay `i3x`, because the WebDev base URL and every
`i3x.*` script reference are derived from it.

Read `README.md` in this folder for what the API does; it is the upstream README, unmodified.

## Upstream

| | |
|---|---|
| Repository | <https://github.com/iatraviscox/i3x-project> |
| Commit | `72323fdb716b06e7bb7ad2012536631242d4b92f` |
| Commit subject | "Fixed bugs with HasChildren for tags with alarms" |
| Commit date | 2026-07-11 |
| Ignition Exchange | resource **2916** (<https://inductiveautomation.com/exchange/2916>) |
| Vendored on | 2026-09-18 |

**The upstream repository carries no `LICENSE` file.** Nothing in it states terms. We are using it
as a demo of an Exchange resource, credited to its author here and in the talk track; that is the
whole of the claim. If this ever leaves the demo, ask the author for terms first.

## What was and was not copied

Copied, in full and unaltered except where the next section says otherwise:

- `README.md`, `project.json`
- `ignition/script-python/i3x/{handlers,ignition,tag,utils}/{code.py,resource.json}`
- `com.inductiveautomation.webdev/resources/{info,namespaces,objecttypes,relationshiptypes,objects,subscriptions}/*`
  — every file, including the disabled `doPut`/`doDelete`/`doHead`/`doOptions`/`doPatch`/`doTrace`
  stubs each resource ships with

Deliberately left behind:

- `.DS_Store` — noise.
- `com.inductiveautomation.perspective/` and `com.inductiveautomation.vision/` — the API is served
  entirely by WebDev; the client is the desktop i3X Explorer. The Vision resource is an opaque
  `data.bin` we could not review, which is reason enough on its own.
- ~~`ignition/global-props/`~~ — **copied after all, later the same day.** It is the project's
  General properties, and without it every authenticated endpoint answers `500 No user source for
  project`, because WebDev's `require-auth` with an empty `user-source` means *the project's* user
  source. The gzip-wrapped `data.bin` is Ignition's serialised `GlobalProps` and names the auth
  profile `default`, which is also this gateway's user source, so the upstream blob works
  unaltered. It is byte-for-byte the same object this repo's own `icc-2026` project carries.

Line endings were normalised CRLF → LF on the way in, per this repo's `.gitattributes`
(`* text=auto eol=lf`): these files are bind-mounted straight into a Linux container. Tabs — the
upstream indentation throughout the script library — are untouched.

## Local edits

Three. The first is a constant; the second and third are features, and they are the reason this
folder is now a fork rather than a copy. The third has since been extended once; its own section
says what changed and why.

### `ignition/script-python/i3x/ignition/code.py` — `PROVIDERS`

Added `PROVIDERS = ("default",)` beside the other module constants, and made `getTagProviders()`
return only the browsed providers named in it.

Upstream returns every tag provider on the gateway, which is right for a general server and wrong
here. This gateway carries six: `default`, `MQTT Engine`, `MQTT Transmission`, `MQTT Distributor`,
`pm-sensors` and `System`. Only `default` holds the UDT instances the demo is about. The other five
cost twice: they put five extra roots of chaff at the top of the i3X address space, where the whole
point of the Explorer beat is that a stranger can find `br-201` without being told where to look;
and they are browsed by the structural build in `getUdtInstances`, which queries every provider for
UDT instances and alarm status and is expensive enough that upstream hides it behind a 5 s cache in
`i3x.utils`. Narrowing the browse makes the first uncached request after any tag change faster, and
the address space honest.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — declared relationships

Added 2026-09-18. Upstream derives every relationship from the tag tree: HasParent from the
folder path, ComponentOf from UDT nesting, HasAlarm from alarm status, and the type list in
`getRelationshipTypes` is a literal dict of those eight. Nothing else about how the plant is wired
can be expressed, and the demo wants one such fact: the cell analyzer draws its samples from two
bioreactors that sit in another folder entirely.

A UDT instance may now declare its own edges through a String parameter named `Relationships`
(constant `RELATIONSHIPS_PARAM` in `i3x.ignition`) holding a JSON list of
`{"type", "reverse", "targets": [full tag paths]}`. The declaring instance is the source; the
reverse edge on each target is filled by upstream's own bidirectional pass, both directions are
to-many, and the declared pair is served from `/relationshiptypes` under the declaring instance's
`NamespaceUri`. Parameters were the right carrier because the structural build already fetches
them for every instance (that is how `NamespaceUri` reaches the server), so a declaration costs no
extra tag read. Every unusable declaration — bad JSON, a name that collides with a built-in, a
target that is not an object, a pair that contradicts an earlier one — is logged under
`i3x.ignition` and dropped, never raised, so one typo cannot blank the address space.

Touched, each marked `Local fork (icc-2026)` in a comment:

- `i3x/ignition/code.py`: constants `RELATIONSHIPS_PARAM`, `BUILTIN_RELATIONSHIP_IDS`,
  `BUILTIN_REVERSE`, `BUILTIN_TO_MANY`; new `declaredRelationships` and
  `getDeclaredRelationshipTypes`; a declared-edge pass in `getUdtInstances` just before the
  bidirectional pass, which now builds its `REVERSE`/`TO_MANY` tables from the constants plus the
  declared pairs instead of two literals.
- `i3x/handlers/code.py`: the literal type dict moved into `_relationshipTypeTable`, which merges
  the declared pairs in (built-ins win); `_relatedObjects` walks the declared ids after its fixed
  six.

The declaring side in this repo is `cell_analyzer` (parameter added to the type with default `[]`,
overridden on `cell-analyzer-01` with `AnalyzesSamplesFrom` / `SamplesAnalyzedBy` → `br-201`,
`br-202`). Bioreactor declares nothing; its `SamplesAnalyzedBy` edges are the server's doing.

Auth is **as shipped**: `require-auth: true` on every endpoint but `/info`, `required-roles` empty,
`require-https` false. No user or role was added. Clients log in as `admin` over HTTPS on :8043.

### `i3x/ignition/code.py` and `i3x/handlers/code.py` — event-store history for `lims_review`

Added 2026-09-18. Upstream serves every object's history from the tag historian, by collecting the
object's atomic tags and reassembling object states from their per-member point series: one row per
distinct change instant across all members, each member forward-filled to its last value as of that
instant. That is the correct reading of *state* — a process value and its limits move on their own
cadences, and forward-fill is exactly what you want — and it is the wrong reading of an *event*.

`lims_review` is an event. Its thirteen members are written together from one MQTT message by
`model_feed.write_review`, so they have no independent cadence, and reassembly invents states the
object was never in. Measured on `br-201`, 2026-09-18: fourteen history rows for one real review —
thirteen of them a 23 ms burst from the members initialising at their own milliseconds — and the
one real row reported `glucose_g_l: 0.0`, forward-filled from initialisation, while the live tag and
the LIMS both said `5.92`. Nothing errored; the object simply lied.

A UDT type named in `REVIEW_TYPE` is now served from `lims.review_event` instead
(`LIMS_DATASOURCE`, migrate-11), keyed to the vessel through the object's nearest UDT ancestor.
`_reviewHistory` in `i3x/handlers/code.py` runs the query and `_historyValue` gains one branch
ahead of the generic one, in the same position and shape as upstream's own `ignition-alarm` branch
— which is the precedent for all of this: upstream already serves alarm history from the alarm
journal rather than the historian, so "an object's history comes from whatever store holds that
object's truth" is upstream's design, not ours.

**Extended 2026-09-19 to the composition children.** The branch above is taken only for the object
the request names. A child reached through its parent — `POST /objects/history` on `br-201` with
`maxDepth: 2`, which is what the Explorer's history panel asks — went down the generic path
instead, so one review answered two ways: twenty-nine forward-filled member rows with `null` where
the live object had values and no `document` member at all (that one is deliberately not
historised), against the single event row it returns when asked for by its own id. Same
`elementId`, two shapes, and the wrong one is the shape a client gets by browsing.

`i3x/utils/code.py`'s `addChildrenHistory` now takes the two arguments `addChildrenValues` always
had — the instance map and, in place of the live path's per-child value, a `historyOverride`
callback — both defaulted to `None` so the signature stays compatible. `_historyValue` passes a
closure that makes the same `REVIEW_TYPE` test its own top level makes. The same change carries the
child's real `isComposition`: it was hard-coded `False` for every child, so `temperature`,
`pressure` and `do` each reported `true` from `/objects/value` and `false` from
`/objects/history` — an object contradicting itself across two endpoints.

**The returned document is handed back untouched.** `lims.review_event.document` is stored already
shaped as the object's member document, in the same encoding `/objects/value` uses, because this
project *cannot* import `model_feed` — neither project is inheritable and neither is the other's
parent. Any mapping written on this side would be a second copy of the write side, free to drift.
One writer, one shape, no translation. That constraint is why the store holds a shaped document
rather than the raw message, and it is the first thing to re-check if either side is edited.

Objects whose members genuinely move independently stay on the historian. `process_value` and its
nested `process_limits` are the reference case and were verified unchanged by this edit.

## Updating from upstream

Re-clone the repo at the new commit, re-copy the file list above, re-apply the `PROVIDERS` edit,
the declared-relationships edit and the `lims_review` history edit (diff this commit's two
`code.py` files against upstream's before overwriting; the two features are about ninety lines
between them and land in the places named above), and update the commit row in this file. There is
nothing else of ours in here.
