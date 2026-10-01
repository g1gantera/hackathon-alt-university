# Kazakhstan railway rules: dispatcher research basis

Checked **1 October 2026** against official Adilet consolidated texts. This is a
source map for the educational simulator, not a claim that it reproduces KTZ's
operational dispatch system. Implemented behaviour and remaining simplifications
are described in [RULES.md](RULES.md).

## Sources and versions

| Reference | Official instrument | Latest amendment shown |
|---|---|---|
| PTE | [Technical operation rules, Order 544 of 30 April 2015](https://old.adilet.zan.kz/rus/docs/V1500011897) | [18 December 2025](https://old.adilet.zan.kz/rus/docs/V1500011897/info) |
| IDP | [Train movement and shunting instruction, Order 291 of 19 May 2011](https://old.adilet.zan.kz/rus/docs/V1100007021) | [26 April 2023](https://old.adilet.zan.kz/rus/docs/V1100007021/info) |
| ISI | [Signalling instruction, Order 209 of 18 April 2011](https://old.adilet.zan.kz/rus/docs/V1100006954) | [26 April 2023](https://old.adilet.zan.kz/rus/docs/V1100006954/info) |
| Network access | [Mainline network usage rules, Order 366 of 27 March 2015](https://old.adilet.zan.kz/rus/docs/V1500011257) | Consolidated text consulted |

All four documents were marked updated rather than repealed. Adilet's metadata
reported a database update of 1 October 2026, covering documents through
28 September 2026. Amendment dates are not necessarily their effective dates.
The PTE's December 2025 changes include radio blocking; a historical 2015 PDF
alone therefore does not represent the current consolidated rules.

## Rules relevant to occupation and authority

The following are short paraphrases, not quotations.

| Source and paragraph | Rule relevant to the simulator |
|---|---|
| PTE 124–125 | Three-aspect signal spacing is related to braking distance. Paragraph 124 also requires at least 1,000 m for newly equipped lines or visibility below 400 m, with stated legacy exceptions. This is not a universal fixed block length. |
| PTE 131 | Signal-control failure produces restrictive indications. |
| PTE 141 | An exit or block signal cannot open until rolling stock clears its protected block or interstation section. |
| PTE 142 | Opening departure onto a single-track section prevents opposing departure signals for that section. Automatic block signals become restrictive when their block is occupied or its track circuit loses integrity. |
| PTE 144, 148–149 | Station routes require clear receiving tracks, correctly positioned and locked points, and exclusion of conflicting routes. Points cannot move beneath rolling stock. |
| IDP 21(1), 21(3) | A permissive exit or block signal authorizes entry to a block. A driver who knows the next block is occupied waits for clearance. |
| IDP 29 | Station routes are prepared before signals open. Single-track section occupation is coordinated with the dispatcher, or the neighbouring station under the stated communications exception. |
| IDP 88–90 | Semi-automatic blocking authorizes the interstation section; single-track departure needs consent/direction setting. Arrival must be confirmed complete before the arrival block indication is transmitted. |
| IDP 300–302, 311–314 | Complete arrival/departure and clearance of station limits matter when preparing subsequent or opposing movements. Established routes cannot simply be changed under an open signal. |
| IDP 2–3, 19–20 | Station technical-administrative acts and infrastructure-operator instructions supply local operating details. |

These clauses distinguish **a physically occupied block**, **a prepared station
route**, and **the selected direction of a shared interstation track**. A scheduled
destination is not inherently a boundary for any of these resources.

## Signal aspects

For ordinary automatic-block signals, **ISI 25** gives green for at least two
clear blocks, yellow for movement prepared to stop at the next closed signal,
and red for stop. **ISI 26** gives the four-aspect variant: green for at least
three clear blocks, yellow with green for two, yellow for one, red for stop.
**ISI 28** describes semi-automatic block signals, while **ISI 29** addresses
short-block warning indicators. Entry, exit, turnout and other signal types
have additional indications elsewhere in the instruction; one generic
three-colour overlay does not implement all of them.

## Dispatch preference is not a verified formula

Network-access **paragraphs 8 and 31** concern allocating timetable capacity by
train category and other access criteria; **paragraph 49** concerns dispatcher
schedules for special and auxiliary services when capacity is available. These
do not establish the simulator's importance/delay/wait/energy score.

The frequently reproduced emergency/passenger/freight priority list in
**paragraph 155 of [Order 261 of 10 May 2011](https://old.adilet.zan.kz/rus/docs/V1100007028)**
belongs to a document explicitly marked repealed in 2012. It must not be
presented as current authority. No current KTZ local priority order was
verified in this research; the application's ranking remains a simulation
policy.

## Applying these sources to this graph

The graph lacks surveyed signals, block-system types, station boundaries,
receiving-track inventories, usable siding lengths and KTZ local instructions.
An OpenStreetMap edge or vertex therefore cannot be assumed to equal an actual
block or interlocking resource. Any block subdivision used by the simulator is
an explicit modelling assumption and must preserve the supplied route geometry
and connectivity. Its chosen length is not a measured KTZ block length.

Rolling reservations should cover a bounded braking envelope, not the entire
route to a distant passenger stop. The limit must remain large enough to stop
within committed authority; arbitrary clipping below braking distance is
unsafe within the model. Released resources must follow the train's rear,
including the model's clearance margin.

Opposing-direction protection needs a separate scope: real interstation
sections and suitable receiving/passing tracks. Shortening that protection to
the same rolling window can admit opposing trains into a section with nowhere
to pass. Retaining the simulator's full-leg direction commitments is
conservative but can still cause excessive remote waits; it is a documented
limitation until station and passing-place data are modelled.

The simulator also omits the rules' special permissions, degraded operation,
staff communications, route-cancellation procedures, actual braking tables,
and radio-block/locomotive-signalling systems. These omissions prevent an
exact operational KTZ reproduction.
