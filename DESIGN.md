# TruckSimEnv: design draft (plan step 1)

Status: design locked 2026-10-20. Nothing is built yet. Smaller choices are settled during the build
(PLAN.md, "Choices settled along the way").

## Decisions (locked 2026-10-20)

1. **Seats, not solo vs arena.** The board has hubs, and each seat owns a hub with its fleet and
   drivers. Any seat is either a model or a rule-based rival carrier.
   - **Leaderboard runs:** one model plus fixed rivals, with the model rotated through every hub
     on the same seed.
   - **Showmatches:** every seat is a model.
   - **Turns:** every seat moves at once. Conflicts go to a fixed rule: higher bid, then the closer
     truck, then a seeded coin.
   - **Precedent:** the OpenCiv3 plugin's matches. Each seat gets its own MCP endpoint, and
     `end_turn` waits for every seat.
2. **Region and scale:** the Texas Triangle plus the I-35, I-10, I-20 and I-30 corridors (about 15
   cities); small fleets.
3. **Real data is a static snapshot.** EIA diesel, FAF5 flows and the road network are taken once
   into the data pack, with a manifest of sources and licences. Nothing is fetched at run time.
4. **The operator knows what a real one knows:**
   - the load board and lane rate trends;
   - today's diesel plus an outlook;
   - weather forecasts;
   - truck fault-code warnings;
   - each driver's hours left.

   Every forecast is a noisy view of the pre-drawn truth. Forecasts are in v1.
5. **Drivers and brokers who answer back come in v3,** as rule-based A2A agents (deterministic, as
   in PortSim v5).

## The question the env answers

> Given the same fleet, the same freight market and the same luck, which operator (model) makes
> the most profit?

The agent is the **operator/dispatcher** of a small carrier, one seat on the board. It never
drives. The env simulates the drivers, trucks, shippers/brokers, the market, the roads, the weather,
the repair shops and the rival carriers in the other seats.

### Fair comparison: common random numbers

Each kind of randomness gets its own pre-drawn stream, keyed by (seed, entity). None of it comes
from one global RNG that the agent's actions consume. Then:

- every model faces the same fuel path, the same load offers, the same weather and the same detention draws;
- truck 3's engine fails at the same **cumulative mileage** in every run. A model that drives
  truck 3 harder reaches that mileage sooner, which is fair, because that is physics, not luck;
- two runs differ only because of their decisions.

## World model (functional fidelity)

**Fidelity level: dispatcher.** A discrete-event sim in continuous time (minutes), on a city-node
road graph. The agent sees the world at decision points.

| Entity | State | Notes |
|---|---|---|
| Road graph | ~15 freight cities, interstate edges with miles and polylines | real geometry (OSM/OSRM or FHWA NHPN); travel time = miles / speed × delay |
| Truck | equipment (dry van / reefer), mpg, odometer, component wear (engine, tires, brakes, reefer), fixed $/day, location, status | breakdown hazard grows with wear; maintenance resets it |
| Driver | home base, pay (cents per mile), HOS clocks, days out, satisfaction, safety record | HOS = the real FMCSA rules (49 CFR 395): 11 h driving, 14 h window, 30 min break after 8 h of driving, 60/70 h, 10 h off-duty reset, 34 h restart. The env enforces them; the agent sees them |
| Load | lane, equipment, weight, pickup window, delivery appointment, rate (linehaul + fuel surcharge), late penalty, detention terms, shipper reliability, offer expiry | spot or contract |
| Market | load offers per lane over time, rate index, regional diesel | lane imbalance is what makes dispatch interesting |
| Shop | location, repair menu, cost, duration | hub shop vs roadside |

**Abstracted (the env decides these, the agent doesn't):** routes inside a corridor, individual
fuel stops (regional price + spread, under a fuel policy), parking, dock operations, speed.

**Simulated (they change the P&L):** HOS, time windows, deadhead, breakdowns, detention, fuel,
weather closures, driver retention, late penalties.

## Randomness (all seeded, per stream)

| Event | Distribution (to calibrate) | Exogenous / endogenous | Grounding candidate |
|---|---|---|---|
| Diesel price | a real historical weekly path by PADD + small daily jitter | exo | EIA weekly on-highway diesel by PADD |
| Load offers | Poisson per lane, with weekday/seasonal intensity | exo (pre-drawn offer list) | FAF5 O-D flows (BTS/FHWA) |
| Spot rate | lognormal around the lane mean, drifting with a market index | exo | USDA AMS truck rate reports (reefer lanes); otherwise modeled |
| Transit delay | multiplicative lognormal per leg + weather closures | exo | NOAA Storm Events / NWS alerts for a historical week |
| Breakdown | Weibull per component in miles (shape > 1: wear-out); scale set by age and maintenance; the failure mileage is pre-drawn | endogenous via miles (CRN) | ATRI repair cost per mile (check terms) |
| Repair | roadside vs tow; cost and hours lognormal | exo per breakdown | ATRI / industry figures |
| Detention | Bernoulli(detained) × lognormal hours, paid after the free time | exo per stop | ATRI/FMCSA detention studies |
| Cancellation / TONU | Bernoulli, small | exo | - |
| Driver | sick-day Bernoulli; quit hazard logistic in satisfaction; rare per-mile accident | quit is endogenous | FMCSA crash rates; ATA turnover |
| Late delivery | not random: it follows from the sim | - | - |

Every source's licence is to be checked before it ships. Grounding is Edgar's hook ("real ships,
real wind"), so it's worth the effort.

## Action space (how granular)

| Level | What the operator decides | Cadence | v1? |
|---|---|---|---|
| L0 Strategy | lease/return trucks, hire/fire, pay rate, home-time policy, fuel contracts | weekly | no (v2 "season") |
| L1 Commercial | accept / decline / bid on loads, contract commitments | per offer | yes |
| L2 Dispatch | which driver + truck takes which load(s), load chaining, deadhead repositioning, sending drivers home | per assignment | yes |
| L3 Exceptions | breakdown response (roadside / tow / relay the load to another truck), reschedule an appointment, maintenance timing | per event | yes |
| L4 Execution | route within a corridor, rest placement inside HOS, fuel stops, speed | continuous | **never**: env-owned; the operator only sets policies |

Recommendation: L1-L3 in v1, L0 added in v2, never L4. This matches the "operator, not driver"
brief and keeps an episode to roughly 50-150 tool calls.

### Tools (v1 sketch)

| Kind | Tool | Notes |
|---|---|---|
| observe | `get_situation()` | clock, fleet board (each truck: status, location, ETA), P&L so far, news since the last watch |
| observe | `list_loads(region?, equipment?)` | the current load board, with expiry times |
| observe | `get_truck(id)` | the truck and its driver (one each): HOS clocks, today's duty log, fault warnings, a breakdown's repair options |
| observe | `get_market()` | diesel by region, rate index, lane rate trends |
| observe | `get_forecast()` | diesel outlook and weather forecast: noisy views of the pre-drawn truth |
| plan | `plan_trip(driver, truck, load_ids[], reposition_to?)` | feasibility under HOS and time windows, timeline, deadhead miles, estimated fuel and profit. Changes nothing (like PortSim's `check_plan`) |
| act | `accept_load(load_id)` | a claim at the posted rate, settled when every seat has ended the turn (negotiating is v3's brokers) |
| act | `dispatch(assignments[])` | batched, to keep turns down; each item is driver + truck + ordered load_ids |
| act | `reposition(driver, truck, city)` | deadhead |
| act | `schedule_maintenance(truck, shop, at)` | |
| act | `handle_breakdown(truck, option)` | `roadside` (not for engines) or `tow` to the nearest shop; handing the load to another truck is left out of v1 |
| act | `release_load(load_id)` | TONU / penalty |
| time | `end_turn()` | ends this seat's turn, waits until every seat has ended it, then returns what happened (the OpenCiv3 pattern) |

A tool call that breaks a rule (HOS-infeasible, wrong equipment, missed window) is **rejected with
the reason**, never silently fixed.

**Turns:** every 6 virtual hours, all seats at once (28 turns a week). An event in mid-turn (a
breakdown, a delivery, a driver quitting) happens in the sim at its own time, and its seat reads it
in the next turn's brief.

**Difficulty knobs:** fleet size, horizon, offer volume and imbalance, breakdown and weather rates,
and whether L0 is on. Tiers: standard / busy / storm, as in PortSim.

**Built in M3** (`trucksim_core/`): every sim constant is in `params.py`, marked as an assumption where it is one;
`send_home` waits for v2, where drivers have satisfaction.

## Grading

**Profit** = revenue (linehaul + fuel surcharge + detention pay + accessorials)
minus fuel, driver pay, maintenance and repairs, tolls, fixed truck $/day, insurance, late penalties,
TONU, and turnover (the recruiting cost when a driver quits).

**Terminal value**, so a run can't game the end of the horizon: wear is charged per mile, the
deadhead back to the domicile is charged for trucks left far away, and loads still in progress are
credited pro rata.

**References** (built in M4, `trucksim_core/references.py`). Each plays the model's seat against the same rivals:
- naive: a greedy dispatcher (`policies.greedy`): each idle truck takes the feasible load with the best revenue per
  total mile, one load at a time;
- the anchor: the better of two configurations of the rolling re-planner (`planner.py`). Each turn it simulates
  candidate one- and two-load chains per truck with the real driver model, values them as estimated profit, less a
  charge per on-duty hour, less a share of the late penalty they'd take with slow stops, and picks one chain per truck
  with CP-SAT. Loads already won stay on their truck.
- rivals: every other seat plays `rolling-a`.
- hindsight (all future offers known) is deferred: it is a multi-turn problem of its own, and scoring doesn't need it.

**Reward:** `max(0, 0.2 + 0.8 x (profit - naive) / (anchor - naive))`: 0.2 at naive, 1.0 at the anchor, 0 a quarter
of the gap below naive. There is no ground truth here (no known optimum for a stochastic, multi-seat week), so the
references are a **ruler, not a ceiling**: beating the anchor scores above 1.0. The first version capped it at 1.0, and
the M9 pilot showed why that was wrong: Opus 5.5 beat the anchor by $3.3k on one week and the cap scored it like
Haiku's break-even. The leaderboard also shows the raw $ profit, which is what "which one profits the most" means to
a person.

**Qualifying weeks:** the anchor beats naive by at least $1,000. In M4, 33 of 40 candidates qualified; the eval set
is 15 of them, 5 storm weeks and 5 per seat (`data/references/v1-eval.jsonl`).

**Determinism:** the env computes profit; `verify.py` reads it through `data/get`. A validity audit
checks that every scheduled event fired and that the run broke no rule.

## Visualization

**Data contract first:** the env writes an **event log** (JSONL: virtual time, entity, event,
payload) plus the scenario. The viewer is a pure function of those two, so any run, including runs
in the HF dataset, can be replayed.

| Tier | What | Cost | v1? |
|---|---|---|---|
| V0 | charts only: P&L curve, per-truck Gantt, ELD grid | low | yes (sweep reports) |
| V1 | 2D map replay: trucks animated along interstate polylines (deck.gl TripsLayer on MapLibre), status colors, load pins with time windows, diesel by region, weather polygons; side panels: P&L, the ELD 24 h log for the selected driver, the event feed, the agent's tool calls synced to the timeline | medium | **yes** |
| V2 | compare: N models on one seed, ghost fleets on the map + a cumulative profit race chart | medium (on top of V1) | yes (it is the leaderboard visual) |
| V3 | 3D (three.js) trucks at hubs/yards; films | high | later, for pitches |

**Board-game look on real geography:** hub tokens, truck pieces and a turn counter on the real city
graph. With several seats, **color means the owner** (the palette validates three owner colors; a
fourth seat needs a shape too), and a truck's state moves to icons.

Mock (single-seat view, made before the seat decision): [design/viewer-mock.html](design/viewer-mock.html).

## v1.1 "A real carrier" (decided 2026-10-21)

v1.1 replaces v1's world. The seats, turns, tool pattern and reward ruler stay; the v1 pilot stays in git and on
the site as history. Decided with the user on 2026-10-21:

| Decision | Choice |
|---|---|
| Company size | about 10 trucks and 12 drivers per seat, the same mix in every seat (a seat rotation compares like with like) |
| Owner-operators | can turn down a load, by a fixed rule (deterministic; v3 adds talking back) |
| Extras in this round | more cities and back roads; partial loads sharing a trailer |
| Not in this round | market-indexed rates; trailers separate from tractors (drop-and-hook, power-only); cash and payment terms (v2); drivers who talk back (v3) |
| v1 | replaced by v1.1; new model runs on the user's go |

### The board: more cities and back roads (M12)

- **24 cities:** v1's 15 plus Fort Worth, Bryan-College Station, Lufkin, Victoria, McAllen, Lake Charles,
  Lafayette, Jackson (MS) and Tulsa.
- **Freight cities** stay the ones that are their own FAF5 zone (Lake Charles 222 and Tulsa 402 join), plus
  McAllen. McAllen's freight is FAF5's imports from Mexico by truck entering zone 489 ("rest of Texas"), times
  the Rio Grande Valley ports' share of that zone's truck crossings (BTS Border Crossing Entry Data): the
  produce lane, mostly reefer. Its domestic freight stays out (it would be invented).
- **About 40 road segments:** v1's interstates split at the new cities, plus the back roads that make detours:
  US 290 Austin-Houston, TX 6 Waco-Bryan-Houston, US 59 Houston-Lufkin, Lufkin-Shreveport, US 87 San
  Antonio-Victoria, US 77 Victoria-Corpus Christi, US 281 McAllen-San Antonio, US 77 McAllen-Corpus Christi,
  US 83 McAllen-Laredo, I-35W Fort Worth, I-49 Lafayette-Shreveport, I-20 Shreveport-Jackson, I-44 Oklahoma
  City-Tulsa, US 75 Tulsa-Dallas, I-40 Tulsa-Little Rock.
- **Storms reach roads, not only cities:** each segment gets the stretches (in road miles) where it crosses a
  county: where its line comes within the radius of a circle of the county's land area around the county's Census
  internal point. An end city's county holds at least 5 miles of each of its roads. A storm filed for a county
  slows only those stretches, so the driver model moves a truck piece by piece within a tick.
- **Where OSRM's fastest car route takes another highway** than the corridor (Austin-Houston onto TX 71 and
  I-10, McAllen-Corpus Christi up US 281, Jackson-New Orleans by Hattiesburg), the segment is routed through a
  town (Brenham, Harlingen, McComb) so a back road doesn't share pavement with another segment. Lufkin-Shreveport
  is a real back road (TX 315, US 259, US 79), labelled US 79.
- **What the operator sees:** the brief and the forecast give one line per road: its storms, the miles they
  cover, the slowest speed on it, and the hours they add to a trip down the whole road. NOAA files one storm per
  county, so a line per record would be hundreds in a bad week (221 in Uri's week).

### Weather road closures (M13)

- **Real closures:** NOAA Storm Events narratives in the board's counties that say one of the board's highways
  was closed or shut down (e.g. "I-44 was shut down by OHP around 29th St", Oklahoma County, 2019). They are
  extracted at build time with the text as provenance and reviewed by hand: a narrative that names a road and a
  closure separately is not one. 122 of the region's 2,881 v1 records name a closed road, mostly flash floods.
- **Modeled closures:** a severe storm on a segment (or at a city, for its roads) closes the segment with a
  probability by type (high for ice storms, blizzards and hurricanes; medium for flash floods and winter
  storms; low for heavy rain and fog), for a lognormal share of the storm, pre-drawn per (seed, week, storm,
  segment). The probabilities are assumptions, checked against the narratives' rate.
- **What a closure does:** no truck enters a closed segment. A truck on it when it closes stops where it is
  until it reopens; past an hour, the driver goes off duty (sleeper berth in a sleeper cab). At a city, a
  driver detours when the detour is quicker than the expected wait, and otherwise waits there.
- **What the operator sees:** closures as they're announced, with a noisy estimated reopening (as on
  DriveTexas). The weather forecast flags closure risk, and `plan_trip` routes around known closures and shows
  the waits.
- **Routing:** a driver takes the usual (shortest) way unless a closure they know of is on it; then the quickest
  way, waits included (a detour, or waiting at the city for the road, or at the closure's start, turning back when
  that's quicker). Tows ignore closures.
- **Hours of service, the adverse driving conditions exception** (49 CFR 395.1(b)(1), since 2020-09-29): 2 more
  hours of driving and of the 14-hour window, claimed once a shift when a road that closed after the trip was
  dispatched holds the truck or sends it on a detour. A trip dispatched into a known closure doesn't get it, nor
  does a driver who has just had 10 hours off (a new shift, nothing to extend). The independent checker allows the
  extension only in a shift with a claim.
- **Built (M13):** 127 narrative candidates in the board's counties, each read: 57 storm records kept (59
  closures, 41 placed at NOAA's point, 2 miles either side), 70 dropped with the reason (local roads, frontage
  roads and ramps, one lane, another county's text, another highway, not a closure). With the modeled ones, the
  median week has none and the mean 1.7; Uri's week has 38, the January 2024 freeze 12 (9 of them real).

### Fleet and loads (M14)

Each seat has the same 10 units:

| Unit | Count | Carries | Fuel | Licence and hours |
|---|---|---|---|---|
| Company tractor + 53' dry van | 4 | 26 pallets, 45,000 lb | 6.68 mpg (FHWA VM-1, combination trucks) | CDL; hours of service |
| Company tractor + 53' reefer | 1 | 26 pallets, 43,500 lb; dry or reefer freight | as above, plus the reefer unit's fuel | CDL; hours of service |
| Owner-operator tractor + company trailer (M15) | 2 (1 dry, 1 reefer) | as above | the owner pays | CDL; hours of service |
| 26' box truck | 2 | 12 pallets, 10,000 lb; dry | FHWA VM-1, single-unit trucks | no CDL; hours of service (over 10,000 lb) |
| Cargo van (Sprinter class) | 1 | 3 pallets, 3,500 lb; dry | FHWA VM-1, light vehicles | not a commercial vehicle, so the federal hours rules don't apply; a company fatigue policy (11 h driving, 14 h window, 10 h off) stands in, labelled as policy |

Loads:
- **Sizes:** full truckload (18-26 pallets, 20,000-45,000 lb), partial (6-14 pallets, 5,000-20,000 lb), and
  small or expedited (1-3 pallets, under 3,000 lb, tighter appointments, a premium). The mix is an assumption,
  to check against the Commodity Flow Survey's shipment-weight tables.
- **Price:** full = stop charge + rate x miles (v1's). Partial = the full price x (pallets / 26) ^ 0.6, so
  partials pay more per pallet. Expedited = a minimum plus a van rate per mile. All assumptions.
- **Partials share a trailer:** a unit carries several loads at once when pallets and weight fit and the
  equipment matches (no dry and reefer freight in one trailer). `plan_trip` and `dispatch` take an ordered stop
  list (pick up A, pick up B, deliver A, deliver B); each stop is its own handling and detention draw.
- **Volume:** offers scale with the units on the board (v1's 12 per truck a week).
- **Shippers' batches** (an assumption): a partial or small load comes, with chance 0.35, with one or two siblings
  from the same shipper: same posting, pickups within 2 hours, to its destination or a freight city within 120
  miles, all fitting one trailer. Without them two partials almost never start from one place at one time (72 such
  pairs in a week), so sharing a trailer never paid; with them the re-planner shares 8-14 times a week.
- **Built (M14):** 8 company units per seat (4 dry-van tractors, a reefer tractor, 2 box trucks, a van; ids A01-A08);
  the 2 owner-operators come with M15. Fuel economy from FHWA VM-1 (combination 6.68, single-unit 7.81, light long
  wheelbase 17.94 mpg), light-truck drivers from BLS OEWS 53-3033 ($23.45/h); box and van costs, the reefer unit's
  0.6 gal/h and a $110 motel are labelled assumptions. Small and partial prices were set so a box truck or van can
  earn its keep: small loads $1.85/mi (cargo-van rates run about $1.50-2.50), $200 at least, expedited x1.35;
  partials 65% of a full load's price at 10 pallets. The re-planner then runs about break-even in ordinary weeks
  (2019 and 2023 were freight recessions), the greedy dispatcher $1,000-8,000 a week worse.
- **Not modeled:** local work inside a metro (FAF's within-zone flows): box trucks and vans do much of it in reality.

### Drivers: a marketplace, not one per truck (M15)

Each seat has the same 12 drivers:

| Who | Count | Drives | Paid | Notes |
|---|---|---|---|---|
| Company CDL drivers | 7 | any company unit | per mile, all miles: ATRI 2023 wages $0.779 + benefits $0.188 | 4 solos, a team of 2 on one tractor (paid 1.25x a solo's rate per mile, split), 1 spare at the hub |
| Local drivers (no CDL) | 3 | box trucks and the van | hourly on duty (BLS OEWS 53-3033, light truck drivers) | |
| Owner-operators | 2 | their own tractor only | 68% of the linehaul plus all of the fuel surcharge and detention (USA Truck's published split); they pay their own fuel, repairs, tires and truck payment | truth in leasing (49 CFR 376.12) |
| Temp drivers | on call | company tractors | an agency's hourly rate, with a day's minimum | called 12 h ahead, to the hub |

- **Slip-seating:** a company driver can take any company tractor where both are (the hub, or the same city).
  A tractor whose driver is out of hours can go out again with a rested spare.
- **Teams:** two drivers on one tractor, each with their own hours clock. One drives while the other is in the
  sleeper berth, so the truck can run about 20 hours a day.
- **Owner-operators turn down loads by a fixed rule.** One takes a trip only if (a) their net per mile after
  their own costs, over all the trip's miles (loaded and empty), is at least their floor, and (b) it gets them
  home by their home-time day. A refusal comes back with the numbers ("pays me $0.41/mi after my costs; I need
  $0.60"). Company drivers can't refuse, as in v1.
- **An owner-operator's breakdown** is the owner's bill; the carrier loses the truck's time.
- **The ledger** adds driver pay per mile, team pay, temp drivers, owner-operator settlements, and the carrier's
  own cost per owner-operator unit (the trailer, cargo insurance).
- **Built (M15):** 10 units per seat (A09 and A10 are the owner-operators') and 12 drivers (A-D01 to A-D12), each
  with their own hours clock and duty log; the audit checks every driver's log. A unit with no driver is parked.
  - Crews change only at the hub (slip-seating at the terminal); a tractor needs a CDL driver.
  - A team: the driver at the wheel drives; the other is in the sleeper, and they swap when one is out of hours.
    Measured: a team tractor drives 23.5 of its first 24 hours.
  - Owner-operators' terms: 68% of the linehaul, all of the fuel surcharge and detention, and $45/day of the
    carrier's for their trailer and cover. Their own costs, for the refusal rule, come from ATRI 2023 per mile
    ($0.611 plus fuel; the shares of truck payments and insurance are assumptions); their floor is $0.60/mi; home
    by Saturday noon. They pay and pick their own repairs, run empty only toward home, and can't be maintained.
    ATRI's $2.10/mi owner-operator pay (2023, mostly contract freight) is in costs.json for comparison.
  - Temps: $40/h on duty with an 8-hour daily minimum, 12 hours' notice, at most 2 at a time (assumptions).
  - The references: greedy never offers an owner-operator a trip they'd refuse (but changes no crews); the
    re-planner also slip-seats at the hub, a driver with under 4 hours of driving left swapped for a rested one.
  - Reproducible on every platform: CP-SAT broke ties between equally good plans differently on macOS and Linux
    (same version, one worker, a fixed seed), so the env in Docker played the anchor week to -$2,682 against the
    reference's $11. The objective now adds a fixed tie-break weight per chain (from a hash of its name, too small to
    outweigh a cent), so the optimum is unique: the same 84 solves on both. CI's golden-reference test (Linux) keeps
    it honest.
  - Costs are 2023's (ATRI) for every week: the rates aren't indexed to the year, so year-matched costs would
    bias the years against each other.
  - Overhead: ATRI's marginal costs leave out dispatch, the office, safety and IT; the ledger books $60 per unit per
    day for them (an assumption), set so the re-planner's margins land near ATRI's truckload margins (Table 21: 10%
    in 2021, 8% in 2022, 3% in 2023). It is the same for every seat and week, so it changes no decision or score.
    Revenue per unit runs about $240,000 a year, against ATRI's truckload average of about $226,000 (2023).

### Tools, references and viewer in v1.1 (M16)

- **New tools:** `list_drivers()`, `assign_drivers(truck, driver_ids)` and `hire_temp_driver(days)`; `dispatch`
  already takes several trucks' assignments in one call, so ten trucks aren't ten calls. `plan_trip` and `dispatch` take `stops` and
  `drivers`; `list_loads` filters by size, equipment and region, with a limit. An owner-operator's answer comes
  back from `dispatch`.
- **References:** greedy over every unit (one load at a time, a spare driver at the hub when a tractor's driver
  is out of hours, owner-operators asked in turn); the CP-SAT re-planner over units, with single loads and pairs
  of compatible partials as candidate trips, owner-operator acceptance as a filter and known closures in its
  expected world. The same reward ruler; references and the 15 eval weeks are rebuilt.
- **Viewer:** closed roads in red with the reopening time, storms on the roads they hit, unit type by glyph,
  an owner-operator mark, both names on a team truck, a driver roster, and the driver log per driver.

## Versions

A version is a frozen set of rules with its own tasks and leaderboard, not separate code: one image,
with the env id choosing the rules, as PortSim does. v1.1 (above) replaces v1's world before any release.

| | v1 "Dispatch week" | v2 "Season" | v3 "People answer back" |
|---|---|---|---|
| Skill | tactics: run the trucks | strategy: run the business | coordination and negotiation |
| Horizon | 1 week | 4-8 weeks | 1 week (built on v1) |
| Operator controls | fixed fleet and drivers; L1-L3 | adds L0: buy/lease/sell trucks, hire/fire, pay, home-time policy, season contract bids | v1's, but others push back |
| New mechanics | seats, forecasts, all v1 randomness | drivers quit; cash and fleet value matter | drivers can refuse a load or ask for home time; brokers counter-offer (rule-based A2A agents) |
| Graded on | the week's profit | season profit + ending fleet value | the week's profit |

v2 is separate from v1 because investments only pay off over weeks, and its reference solver is much
harder. v3 is separate because outcomes start to depend on other agents' choices.
