"""Every constant the sim uses, in one place. Values that come from the data pack are read there (costs.json,
hos.json); the ones here are the sim's own modelling choices, each marked as an assumption where it is one.

Time is counted in ticks of 15 minutes, so every time in the sim is an integer and nothing drifts."""

from dataclasses import dataclass, field

TICK_HOURS = 0.25
TICKS_PER_HOUR = 4


def ticks(hours: float) -> int:
    """Hours to whole ticks, rounding to the nearest tick."""
    return int(round(hours * TICKS_PER_HOUR))


def hours(n_ticks: int) -> float:
    return n_ticks * TICK_HOURS


# The world's version (DESIGN.md "v1.1"): task ids and reference files carry it, so a run is always scored against
# references from the world it was played in. v1's files stay in data/references/ as history.
WORLD = "v1.1"
WORLD_TAG = "v11"


@dataclass(frozen=True)
class VehicleClass:
    """A kind of unit (DESIGN.md "v1.1", M14): what it carries, how it runs, and the costs.json entries that price it."""
    name: str
    label: str
    pallets: int
    payload_lb: int
    reefer: bool                  # can carry reefer freight (one kind of freight aboard at a time)
    sleeper: bool                 # the driver can rest in the cab; without one, a rest away from home is a motel
    hours_rule: str               # "49 CFR 395", or "company policy" for a vehicle the federal rules don't cover
    speed_factor: float           # its moving speed as a share of OSRM's car speed (assumption)
    speed_cap_mph: float          # (assumption)
    mpg: str                      # costs.json keys
    lease_per_day: str
    insurance_per_day: str
    repair_per_mile: str
    tires_per_mile: str
    driver_wage_hourly: str
    repair_cost_factor: float     # its repairs cost this share of a tractor's (assumption)
    components: tuple


VEHICLES = {
    "tractor_dry": VehicleClass("tractor_dry", "tractor + 53' dry van", 26, 45_000, False, True, "49 CFR 395", 0.90, 62.0,
                                "fuel_economy_mpg", "truck_trailer_lease_per_day_usd", "insurance_per_day_usd",
                                "repair_maintenance_per_mile_usd", "tires_per_mile_usd", "driver_wage_hourly_usd", 1.0,
                                ("tires", "brakes", "engine")),
    "tractor_reefer": VehicleClass("tractor_reefer", "tractor + 53' reefer", 26, 43_500, True, True, "49 CFR 395", 0.90, 62.0,
                                   "fuel_economy_mpg", "truck_trailer_lease_per_day_usd", "insurance_per_day_usd",
                                   "repair_maintenance_per_mile_usd", "tires_per_mile_usd", "driver_wage_hourly_usd", 1.0,
                                   ("tires", "brakes", "engine", "reefer")),
    "box": VehicleClass("box", "26' box truck", 12, 10_000, False, False, "49 CFR 395", 0.90, 65.0,
                        "box_fuel_economy_mpg", "box_lease_per_day_usd", "box_insurance_per_day_usd",
                        "box_repair_maintenance_per_mile_usd", "box_tires_per_mile_usd", "light_driver_wage_hourly_usd", 0.6,
                        ("tires", "brakes", "engine")),
    # under 10,001 lb it isn't a commercial motor vehicle, so 49 CFR 395 doesn't apply; a company fatigue policy with
    # the same limits stands in, labelled as policy
    "van": VehicleClass("van", "cargo van", 3, 3_500, False, False, "company policy", 0.95, 70.0,
                        "van_fuel_economy_mpg", "van_lease_per_day_usd", "van_insurance_per_day_usd",
                        "van_repair_maintenance_per_mile_usd", "van_tires_per_mile_usd", "light_driver_wage_hourly_usd", 0.35,
                        ("tires", "brakes", "engine")),
}


@dataclass(frozen=True)
class Params:
    # --- the game ---------------------------------------------------------------------------------------------
    week_ticks: int = ticks(168)                 # one week, Monday 00:00 to Monday 00:00, Central Standard Time
    turn_ticks: int = ticks(6)                   # every seat moves at once, every 6 virtual hours
    # each seat's units (DESIGN.md "v1.1"): (VEHICLES class, owner); ids A01, A02, ... in this order
    fleet_per_seat: tuple[tuple[str, str], ...] = (
        ("tractor_dry", "company"), ("tractor_dry", "company"), ("tractor_dry", "company"), ("tractor_dry", "company"),
        ("tractor_reefer", "company"), ("box", "company"), ("box", "company"), ("van", "company"),
        ("tractor_dry", "owner_operator"), ("tractor_reefer", "owner_operator"))
    # each seat's drivers: (kind, the unit they start on, by its index above, or None: a spare at the hub). Kinds:
    # "company" (a CDL employee, paid by the mile), "local" (no CDL, hourly: box trucks and vans), "owner_operator"
    # (their own tractor only, paid a share of the load, can turn a trip down). Two on one tractor are a team.
    crew_per_seat: tuple[tuple[str, int | None], ...] = (
        ("company", 0), ("company", 0), ("company", 1), ("company", 2), ("company", 3), ("company", 4),
        ("company", None), ("local", 5), ("local", 6), ("local", 7), ("owner_operator", 8), ("owner_operator", 9))
    owner_operator_home_by_hours: float = 132.0  # an owner-operator wants to be home by Saturday 12:00 (assumption)
    temp_notice_hours: float = 12.0              # a temp driver arrives at the hub this long after the call (assumption)
    temp_max_per_seat: int = 2                   # (assumption)

    # --- driving (assumptions; each vehicle class sets its own speed) -----------------------------------------
    pickup_window_hours: float = 4.0
    tow_mph: float = 40.0

    # --- the market (assumptions; FAF5 sets the lanes' relative volumes) -------------------------------------
    offers_per_truck_week: float = 12.0          # board-wide offers a week, per truck: about twice what a truck can haul
    lane_weight_exponent: float = 0.6            # offers per lane ~ FAF tons ** this: small carriers' boards are flatter than tonnage
    offer_post_until_hours: float = 156.0        # no new offers in the last 12 hours of the week
    pickup_lead_hours: tuple[float, float] = (6.0, 30.0)   # pickup window opens this long after posting (uniform)
    offer_ttl_hours: float = 12.0                # an offer leaves the board after this, or 2 h before its pickup window
    appointment_slack_hours: tuple[float, float] = (2.0, 10.0)
    stop_charge_usd: float = 250.0               # linehaul = stop charge + rate x miles: short hauls pay more per mile
    base_rate_per_mile: float = 2.00             # dry van, before lane imbalance and noise
    imbalance_exponent: float = 0.30             # rate x (origin's out/in tons) ** this: headhaul lanes pay more
    rate_noise_sigma: float = 0.20               # lognormal: spot rates for the same lane vary this much
    reefer_rate_premium: float = 1.15
    fsc_base_price: float = 1.25                 # fuel surcharge per mile = (diesel - base) / mpg, the common DOE-indexed formula
    fsc_mpg: float = 6.0
    cancel_probability: float = 0.04             # the shipper cancels after acceptance, before pickup
    detention_probability: float = 0.35          # a stop runs past its handling time
    detention_extra_hours_mean: float = 2.5      # lognormal, given a detained stop
    detention_extra_sigma: float = 0.6

    # --- load sizes (DESIGN.md "v1.1", M14; assumptions, to check against the Commodity Flow Survey) ------------
    load_sizes: tuple[tuple[str, float], ...] = (("full", 0.60), ("partial", 0.28), ("small", 0.12))   # share of offers
    size_pallets: dict = field(default_factory=lambda: {"full": (18, 26), "partial": (6, 14), "small": (1, 3)})
    size_weight_lb: dict = field(default_factory=lambda: {"full": (20_000, 45_000), "partial": (5_000, 20_000), "small": (300, 3_000)})
    reefer_max_weight_lb: int = 43_000           # a reefer trailer carries less (its unit weighs)
    size_handling_hours: dict = field(default_factory=lambda: {"full": 1.5, "partial": 1.0, "small": 0.5})  # per stop, before detention
    partial_price_exponent: float = 0.45         # a partial pays a full load's price x (pallets / 26) ** this (10 pallets: 65%)
    small_minimum_usd: float = 200.0             # a small load pays the larger of this and the van rate x miles
    small_rate_per_mile: float = 1.85            # at the base rate (cargo-van rates run about $1.50-2.50/mi); it moves with the lane
    expedite_share: float = 0.5                  # of small loads: a tight appointment, paid more
    expedite_premium: float = 1.35
    expedite_slack_hours: tuple[float, float] = (0.5, 2.0)
    batch_probability: float = 0.35              # a partial or small load comes with siblings from the same shipper ...
    batch_max_siblings: int = 2                  # ... up to this many, picked up within 2 h of it ...
    batch_near_miles: float = 120.0              # ... going to its destination or a freight city this close to it

    # --- breakdowns (assumptions): Weibull life in miles since last service, per component; tuned for about
    # one to two breakdowns a week across a 12-truck board, so the decision comes up most weeks ---------------
    components: dict = field(default_factory=lambda: {
        # name: (Weibull shape k, scale miles, roadside repair (hours, usd) or None, shop repair (hours, usd))
        "tires":  (1.5, 20_000, (2.0, 640), (1.0, 500)),
        "brakes": (2.0, 45_000, (3.0, 900), (3.0, 800)),
        "engine": (2.5, 120_000, None, (24.0, 4_500)),
        "reefer": (2.0, 20_000, (3.0, 1_100), (6.0, 1_500)),  # reefer trucks only
    })
    repair_cost_sigma: float = 0.30              # lognormal spread on repair cost and hours
    tow_base_usd: float = 350.0
    tow_per_mile_usd: float = 6.0
    maintenance_hours: float = 4.0
    maintenance_usd: float = 350.0
    fault_warning_probability: float = 0.70      # a failure is preceded by a fault-code warning with this probability
    fault_warning_lead_miles: tuple[float, float] = (150.0, 600.0)

    # --- penalties (assumptions) ------------------------------------------------------------------------------
    late_penalty_per_hour_usd: float = 50.0      # per hour past the delivery appointment, capped at half the linehaul
    missed_pickup_penalty_usd: float = 250.0     # an accepted load whose pickup window closes before a truck loads it
    release_penalty_usd: float = 150.0           # giving back an accepted load before pickup

    # --- forecasts (assumptions): what the operator is told ahead of time -----------------------------------
    weather_detect_by_lead_hours: tuple[tuple[float, float], ...] = ((24, 0.9), (48, 0.7), (72, 0.5))
    weather_timing_error_per_lead_hour: float = 0.10   # sd of the reported start/end, hours per hour of lead
    weather_false_alarm_per_city_turn: float = 0.02
    diesel_outlook_sd: float = 0.05

    # --- weather impact (assumptions): the speed share on the stretches of road in a storm's county ------------
    weather_speed: dict = field(default_factory=lambda: {
        "Hurricane": 0.10, "Ice Storm": 0.20, "Blizzard": 0.25, "Winter Storm": 0.40, "Heavy Snow": 0.45,
        "Tropical Storm": 0.50, "Flash Flood": 0.60, "Flood": 0.60, "Winter Weather": 0.70, "Coastal Flood": 0.70,
        "Storm Surge/Tide": 0.70, "Dense Fog": 0.75, "Freezing Fog": 0.70, "Extreme Cold/Wind Chill": 0.80,
        "Heavy Rain": 0.85, "High Wind": 0.85, "Strong Wind": 0.90, "Tornado": 0.90, "Tropical Depression": 0.80,
        "Thunderstorm Wind": 0.95, "Cold/Wind Chill": 0.95,
    })
    weather_min_event_hours: float = 1.0         # a Storm Events record shorter than this still lasts an hour

    # --- road closures (DESIGN.md "v1.1", M13) ----------------------------------------------------------------
    # Real ones come from the NOAA narratives (data/closures.json) and last their storm record, at least this long:
    real_closure_min_hours: float = 2.0
    # Modeled ones (assumptions): a storm record closes each stretch of road in its county with this chance, around
    # a point drawn on the stretch, starting in the first half of the storm, for a lognormal time with this mean.
    closure_probability: dict = field(default_factory=lambda: {
        "Hurricane": 0.50, "Ice Storm": 0.30, "Blizzard": 0.30, "Storm Surge/Tide": 0.15, "Winter Storm": 0.12,
        "Tropical Storm": 0.10, "Heavy Snow": 0.06, "Coastal Flood": 0.05, "Flash Flood": 0.04, "Flood": 0.03,
        "Tornado": 0.03, "Winter Weather": 0.02, "Freezing Fog": 0.02, "Dense Fog": 0.01, "Heavy Rain": 0.005,
        "High Wind": 0.005, "Thunderstorm Wind": 0.003, "Strong Wind": 0.002,
    })
    closure_hours_mean: dict = field(default_factory=lambda: {
        "Hurricane": 24.0, "Ice Storm": 8.0, "Blizzard": 10.0, "Storm Surge/Tide": 12.0, "Winter Storm": 6.0,
        "Tropical Storm": 8.0, "Heavy Snow": 4.0, "Coastal Flood": 6.0, "Flash Flood": 4.0, "Flood": 12.0,
        "Tornado": 3.0, "Winter Weather": 3.0, "Freezing Fog": 3.0, "Dense Fog": 3.0, "Heavy Rain": 2.0,
        "High Wind": 2.0, "Thunderstorm Wind": 2.0, "Strong Wind": 2.0,
    })
    closure_hours_sigma: float = 0.6
    closure_hours_bounds: tuple[float, float] = (1.0, 48.0)
    closure_half_miles: float = 2.0              # a modeled closure shuts this far either side of its point
    closure_estimate_sigma: float = 0.4          # lognormal error on the announced reopening time
    held_on_duty_hours: float = 1.0              # a driver stopped on a closed road is on duty this long, then in the sleeper
    closure_risk_levels: tuple[tuple[float, str], ...] = ((0.25, "high"), (0.05, "some"), (0.0, "low"))

    # --- end of the week (terminal value) ---------------------------------------------------------------------
    terminal_deadhead_mph: float = 50.0          # trucks away from their hub are charged the trip home
