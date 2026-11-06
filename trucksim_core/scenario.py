"""A week's pre-drawn truth: who sits where, every offer the week will post (with its hidden detention and
cancellation draws), the week's real weather and diesel, and each truck's failure draws.

Everything here is a pure function of (seed, monday, seats): no operator decision can change it. The one thing a
decision does change is *when* a failure mileage is reached, which is physics, not luck (see failure_draw)."""

import datetime as dt
import math
from dataclasses import asdict, dataclass, field

from .data import DataPack
from .params import VEHICLES, Params, hours, ticks
from .rng import lognormal_mean, poisson, stream

# Monday 00:00 Central Standard Time is 06:00 UTC; the sim's clock runs on CST all year (no DST).
CST_OFFSET_HOURS = -6
DRIVER_NAMES = ["Maria R.", "Dev P.", "Sam K.", "Lena O.", "Joe M.", "Ana C.", "Ray T.", "Kim L.", "Omar S.",
                "Beth W.", "Luis G.", "Tara N.", "Hugo B.", "Ivy D.", "Cole F.", "Nina H.", "Raj V.", "Ola A.",
                "Pete S.", "Rosa M.", "Dan K.", "Mei L.", "Abe J.", "Sofia T.", "Jon E.", "Lucy P.", "Malik D.",
                "Tess G.", "Ivan R.", "Nora B.", "Eli C.", "Gina F.", "Hank W.", "Yara N.", "Zeke O.", "Lia S.",
                "Ben T.", "Ada M.", "Cruz V.", "Dot H."]


@dataclass(frozen=True)
class Offer:
    id: str
    orig: str
    dest: str
    equipment: str          # "dry" or "reefer"
    miles: float
    posted: int             # ticks
    expires: int
    pickup_start: int
    pickup_end: int
    appointment: int
    linehaul_usd: float
    fsc_per_mile: float
    # hidden from the operator:
    dwell_pickup: int       # ticks at the shipper, handling + any detention
    dwell_delivery: int
    cancel_at: int | None
    # its size (M14): what it takes on a truck
    size: str               # "full", "partial" or "small"
    pallets: int
    weight_lb: int
    expedite: bool          # a small load with a tight appointment


@dataclass(frozen=True)
class TruckSpec:
    id: str
    seat: str
    equipment: str
    owner: str              # "company" or "owner_operator"
    hub: str
    odometer0: float
    service_miles0: dict    # component -> miles since its last service at the start of the week
    vclass: str             # params.VEHICLES


@dataclass(frozen=True)
class DriverSpec:
    id: str
    name: str
    seat: str
    kind: str               # "company", "local", "owner_operator" (params.crew_per_seat)
    home: str               # the seat's hub
    truck: str | None       # the unit they start the week on, or None: a spare at the hub


@dataclass(frozen=True)
class Closure:
    """A stretch of road closed by a storm: real (a NOAA narrative says so) or modeled from the storm's type."""
    closure_id: str
    a: str
    b: str
    m0: float               # miles from a
    m1: float
    direction: str          # "both", or "a>b" / "b>a": only the way from one city toward the other
    start: int              # ticks
    end: int
    estimate: int           # the reopening announced when it closes (noisy)
    cause: str              # the storm's type
    where: str              # its county
    source: str             # "NOAA narrative" or "modeled"
    what: str


@dataclass(frozen=True)
class WeatherEvent:
    event_id: int
    county: str             # Census GEOID
    where: str              # "Walker County, TX"
    city: str | None        # the board city whose county it is, if any
    spans: tuple            # ((a, b, from mile, to mile), ...): the road stretches in that county
    type: str
    start: int
    end: int
    speed_factor: float


@dataclass(frozen=True)
class Scenario:
    seed: int
    monday: str
    key: str                # "seed:monday": every random stream of this week is named under it
    seats: tuple            # ((seat id, hub), ...)
    trucks: tuple
    drivers: tuple
    offers: tuple
    weather: tuple
    closures: tuple
    diesel: dict            # padd -> usd/gal for the week
    diesel_next: dict | None
    params: Params = field(compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("params")
        return d


def week_start_utc(monday: dt.date) -> dt.datetime:
    return dt.datetime(monday.year, monday.month, monday.day, tzinfo=dt.timezone.utc) - dt.timedelta(hours=CST_OFFSET_HOURS)


def failure_draw(key: str, truck: str, component: str, k: int, service_miles: float, p: Params) -> dict:
    """The k-th failure of a truck's component, drawn from its own stream: miles from now to failure (Weibull life,
    conditional on the miles already run since service), whether a fault-code warning precedes it and how early,
    and what each repair would take. A truck that drives more reaches the mileage sooner; the mileage doesn't move."""
    shape, scale, roadside, shop = p.components[component]
    rng = stream(key, "failure", truck, component, k)
    life = scale * ((service_miles / scale) ** shape - math.log(1.0 - rng.random())) ** (1.0 / shape)
    warn = rng.random() < p.fault_warning_probability
    lead = rng.uniform(*p.fault_warning_lead_miles)
    repairs = {}
    for name, spec in (("roadside", roadside), ("shop", shop)):
        if spec is None:
            repairs[name] = None
            continue
        h, usd = spec
        repairs[name] = {"ticks": max(1, ticks(lognormal_mean(rng, h, p.repair_cost_sigma))),
                         "usd": round(lognormal_mean(rng, usd, p.repair_cost_sigma), 2)}
    return {"miles_to_failure": life - service_miles, "warn_lead_miles": lead if warn else None, **repairs}


def _dwell(rng, p: Params, handling_hours: float) -> int:
    extra = lognormal_mean(rng, p.detention_extra_hours_mean, p.detention_extra_sigma) if rng.random() < p.detention_probability else 0.0
    return math.ceil((handling_hours + extra) * 4)


def _offer_spec(pack: DataPack, p: Params, diesel: dict, ratio: dict, o: str, d: str, posted: int, draw, reefer_share: float,
                size: str | None = None, equipment: str | None = None, pickup_start: int | None = None) -> dict:
    """One offer's fields (all but its id), drawn from its own stream: kind of freight, size, windows, price and the
    hidden draws. A shipper's batch passes its size, freight and pickup time."""
    miles = pack.miles(o, d)
    drawn_equipment = "reefer" if draw.random() < reefer_share else "dry"
    drawn_size = _pick(draw, p.load_sizes)
    size = size or drawn_size
    equipment = equipment or ("dry" if size == "small" else drawn_equipment)   # small loads go by van or box truck: dry
    pallets = draw.randint(*p.size_pallets[size])
    weight = round(draw.uniform(*p.size_weight_lb[size]) / 100) * 100
    if equipment == "reefer":
        weight = min(weight, p.reefer_max_weight_lb)
    expedite = size == "small" and draw.random() < p.expedite_share
    handling = p.size_handling_hours[size]
    drawn_start = _hour_ceil(posted + ticks(draw.uniform(*p.pickup_lead_hours)))
    pickup_start = drawn_start if pickup_start is None else pickup_start
    pickup_end = pickup_start + ticks(p.pickup_window_hours)
    transit_h = miles / 50.0 + 10.0 * math.floor(miles / 550.0)
    slack = draw.uniform(*(p.expedite_slack_hours if expedite else p.appointment_slack_hours))
    appointment = _hour_ceil(pickup_start + ticks(handling + transit_h + slack))
    rpm = p.base_rate_per_mile * ratio.get(o, 1.0) ** p.imbalance_exponent * draw.lognormvariate(0.0, p.rate_noise_sigma)
    if equipment == "reefer":
        rpm *= p.reefer_rate_premium
    full_price = round((p.stop_charge_usd + rpm * miles) / 10) * 10.0
    fsc_full = round(max(0.0, (diesel[pack.cities[o].padd] - p.fsc_base_price) / p.fsc_mpg), 3)
    if size == "full":
        linehaul, fsc = full_price, fsc_full
    elif size == "partial":                             # priced by the trailer space it takes
        share = (pallets / 26) ** p.partial_price_exponent
        linehaul, fsc = round(full_price * share / 10) * 10.0, round(fsc_full * share, 3)
    else:                                               # an all-in van rate: no separate fuel surcharge
        van = max(p.small_minimum_usd, p.small_rate_per_mile * rpm / p.base_rate_per_mile * miles)
        linehaul, fsc = round(van * (p.expedite_premium if expedite else 1.0) / 5) * 5.0, 0.0
    dwell_pickup, dwell_delivery = _dwell(draw, p, handling), _dwell(draw, p, handling)
    cancel_at = draw.randrange(posted, pickup_start) if draw.random() < p.cancel_probability else None
    return {"orig": o, "dest": d, "equipment": equipment, "miles": round(miles, 1), "posted": posted,
            "expires": min(posted + ticks(p.offer_ttl_hours), pickup_start - ticks(2)), "pickup_start": pickup_start,
            "pickup_end": pickup_end, "appointment": appointment, "linehaul_usd": linehaul, "fsc_per_mile": fsc,
            "dwell_pickup": dwell_pickup, "dwell_delivery": dwell_delivery, "cancel_at": cancel_at, "size": size,
            "pallets": pallets, "weight_lb": int(weight), "expedite": expedite}


def _pick(rng, shares: tuple) -> str:
    x, acc = rng.random(), 0.0
    for name, share in shares:
        acc += share
        if x < acc:
            return name
    return shares[-1][0]


def _hour_ceil(t: int) -> int:
    return -(-t // 4) * 4


def build_scenario(pack: DataPack, seed: int, monday: dt.date, seats: list[tuple[str, str]], p: Params = Params()) -> Scenario:
    if monday.weekday() != 0:
        raise ValueError(f"{monday} is not a Monday")
    for seat, hub in seats:
        if hub not in pack.cities or not pack.cities[hub].freight_node:
            raise ValueError(f"seat {seat}'s hub {hub} is not a freight city")
    diesel = pack.diesel_week(monday)
    key = f"{seed}:{monday.isoformat()}"
    ratio = pack.out_in_ratio()

    # trucks and drivers
    trucks, name_i = [], 0
    drivers = []
    for seat, hub in seats:
        for i, (vclass, owner) in enumerate(p.fleet_per_seat):
            tid = f"{seat}{i + 1:02d}"
            vc = VEHICLES[vclass]
            rng = stream(key, "truck", tid)
            trucks.append(TruckSpec(tid, seat, "reefer" if vc.reefer else "dry", owner, hub, round(rng.uniform(80_000, 400_000), 1),
                                    {c: round(rng.uniform(0, p.components[c][1] * 0.6), 1) for c in vc.components}, vclass))
        for k, (kind, unit) in enumerate(p.crew_per_seat):
            drivers.append(DriverSpec(f"{seat}-D{k + 1:02d}", DRIVER_NAMES[name_i % len(DRIVER_NAMES)], seat, kind, hub,
                                      None if unit is None else f"{seat}{unit + 1:02d}"))
            name_i += 1

    # offers: Poisson per lane, lane weight ~ FAF tons ** exponent, scaled to offers_per_truck_week
    lanes = [lane for lane in pack.lanes if lane["tons_2024"] > 0]
    weights = {(ln["orig"], ln["dest"]): ln["tons_2024"] ** p.lane_weight_exponent for ln in lanes}
    total_offers = p.offers_per_truck_week * len(trucks)
    wsum = sum(weights.values())
    raw = []
    for ln in sorted(lanes, key=lambda x: (x["orig"], x["dest"])):
        o, d = ln["orig"], ln["dest"]
        rng = stream(key, "offers", o, d)
        for j in range(poisson(rng, weights[(o, d)] / wsum * total_offers)):
            draw = stream(key, "offer", o, d, j)
            posted = draw.randrange(0, ticks(p.offer_post_until_hours))
            raw.append((posted, o, d, j, ln, draw))
    specs = []
    for posted, o, d, j, ln, draw in sorted(raw, key=lambda r: r[:4]):
        reefer_share = ln["reefer_tons_2024"] / ln["tons_2024"]
        parent = _offer_spec(pack, p, diesel, ratio, o, d, posted, draw, reefer_share)
        specs.append(((posted, o, d, j, 0), parent))
        if parent["size"] == "full":
            continue
        # a shipper's batch (an assumption): a distribution center tenders a few partial or small loads at once,
        # same pickup window, to the same city or a freight city near it -- what makes sharing a trailer pay
        batch = stream(key, "batch", o, d, j)
        if batch.random() >= p.batch_probability:
            continue
        near = [c for c in sorted(pack.cities) if pack.cities[c].freight_node and c != o and pack.miles(d, c) <= p.batch_near_miles]
        room_pallets, room_lb = 26 - parent["pallets"], (p.reefer_max_weight_lb if parent["equipment"] == "reefer" else 45_000) - parent["weight_lb"]
        for k in range(1, 1 + batch.randint(1, p.batch_max_siblings)):
            dest = batch.choice(near)
            sib = _offer_spec(pack, p, diesel, ratio, o, dest, posted, stream(key, "offer", o, d, j, "sibling", k), reefer_share,
                              size=parent["size"], equipment=parent["equipment"],
                              pickup_start=parent["pickup_start"] + ticks(batch.choice((0, 1, 2))))
            if sib["pallets"] > room_pallets or sib["weight_lb"] > room_lb:
                break
            room_pallets, room_lb = room_pallets - sib["pallets"], room_lb - sib["weight_lb"]
            specs.append(((posted, o, d, j, k), sib))
    offers = [Offer(f"L-{1001 + n}", **spec) for n, (_, spec) in enumerate(sorted(specs, key=lambda x: x[0]))]

    # the week's real weather (NOAA Storm Events), in sim ticks
    start_utc = week_start_utc(monday)
    weather = []
    for ev in pack.weather_events:
        b = dt.datetime.strptime(ev["begin_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
        e = dt.datetime.strptime(ev["end_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
        e = max(e, b + dt.timedelta(hours=p.weather_min_event_hours))
        s_t = math.floor((b - start_utc).total_seconds() / 900)
        e_t = math.ceil((e - start_utc).total_seconds() / 900)
        if e_t <= 0 or s_t >= p.week_ticks:
            continue
        weather.append(WeatherEvent(ev["id"], ev["county"], pack.county_names[ev["county"]], pack.county_city.get(ev["county"]),
                                    tuple(pack.county_spans[ev["county"]]), ev["type"], max(0, s_t), min(p.week_ticks, e_t),
                                    p.weather_speed[ev["type"]]))
    weather.sort(key=lambda w: (w.start, w.event_id))
    closures = _closures(pack, key, weather, p)

    return Scenario(seed, monday.isoformat(), key, tuple(seats), tuple(trucks), tuple(drivers), tuple(offers), tuple(weather), closures, diesel,
                    pack.diesel_next_week(monday), p)


def _closures(pack: DataPack, key: str, weather: list, p: Params) -> tuple:
    """The week's road closures: the NOAA-reported ones for its storms (each lasting its storm, at least
    real_closure_min_hours), and for every other storm a modeled draw per stretch of road in its county. Drawn per
    (storm, road), so they're the same whatever anyone does."""
    real: dict[int, list] = {}
    for c in pack.noaa_closures:
        real.setdefault(c["event_id"], []).append(c)
    lo_h, hi_h = p.closure_hours_bounds
    out = []
    for w in weather:
        if w.event_id in real:
            for c in real[w.event_id]:
                rng = stream(key, "closure_estimate", w.event_id, c["a"], c["b"])
                end = max(w.end, w.start + ticks(p.real_closure_min_hours))
                estimate = w.start + max(4, ticks(hours(end - w.start) * math.exp(rng.gauss(0, p.closure_estimate_sigma))))
                out.append(Closure(f"C{w.event_id}-{c['a']}-{c['b']}", c["a"], c["b"], c["m0"], c["m1"], c["direction"],
                                   w.start, end, estimate, w.type, w.where, "NOAA narrative", c["what"]))
            continue
        chance = p.closure_probability.get(w.type, 0.0)
        for a, b, m0, m1 in (w.spans if chance else ()):
            rng = stream(key, "closure", w.event_id, a, b)
            if rng.random() >= chance:
                continue
            start = w.start + int(rng.random() * 0.5 * (w.end - w.start))
            length_h = min(hi_h, max(lo_h, lognormal_mean(rng, p.closure_hours_mean[w.type], p.closure_hours_sigma)))
            at, road = rng.uniform(m0, m1), pack.edge(a, b).miles
            estimate = start + max(4, ticks(length_h * math.exp(rng.gauss(0, p.closure_estimate_sigma))))
            out.append(Closure(f"C{w.event_id}-{a}-{b}", a, b, round(max(0.0, at - p.closure_half_miles), 1),
                               round(min(road, at + p.closure_half_miles), 1), "both", start, start + ticks(length_h),
                               estimate, w.type, w.where, "modeled", f"modeled from the {w.type.lower()}"))
    out.sort(key=lambda c: (c.start, c.closure_id))
    return tuple(out)


def describe(s: Scenario) -> str:
    kinds = {}
    for w in s.weather:
        kinds[w.type] = kinds.get(w.type, 0) + 1
    return (f"week {s.monday} seed {s.seed}: {len(s.trucks)} trucks, {len(s.offers)} offers "
            f"({sum(o.equipment == 'reefer' for o in s.offers)} reefer), {len(s.weather)} weather events {kinds}, "
            f"diesel {s.diesel}, offers span {hours(min(o.posted for o in s.offers)):.0f}-{hours(max(o.posted for o in s.offers)):.0f} h")
