"""The static data pack (data/*.json, built by scripts/build_data.py) and the road graph on top of it."""

import datetime as dt
import heapq
import json
from dataclasses import dataclass
from pathlib import Path

# The data pack ships inside the package: in a checkout, data_pack is a link to the repo's data/; a wheel carries
# the files themselves (pyproject force-include); the env image copies them there.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data_pack"


@dataclass(frozen=True)
class City:
    id: str
    name: str
    state: str
    lat: float
    lon: float
    freight_node: bool   # generates freight (its own FAF5 zone); otherwise a waypoint
    padd: int            # EIA diesel region


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    route: str
    miles: float
    car_mph: float
    geometry: tuple      # ((lat, lon), ...) from a to b


class DataPack:
    """Read-only view of data/: cities, roads, diesel, freight lanes, weather events, HOS rules and costs."""

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        d = Path(data_dir)
        load = lambda name: json.loads((d / name).read_text())  # noqa: E731
        roads, diesel, freight = load("roads.json"), load("diesel.json"), load("freight.json")
        self.cities = {c["id"]: City(c["id"], c["name"], c["state"], c["lat"], c["lon"], c["freight_node"], c["padd"])
                       for c in roads["cities"]}
        self.edges = [Edge(e["a"], e["b"], e["route"], e["miles"], e["miles"] / e["osrm_car_hours"],
                           tuple(tuple(p) for p in e["geometry"])) for e in roads["edges"]]
        self._edge = {}
        for e in self.edges:
            self._edge[(e.a, e.b)] = e
            self._edge[(e.b, e.a)] = e
        self.diesel = {padd: dict(diesel[f"padd{padd}"]) for padd in (2, 3)}  # {padd: {monday iso: usd}}
        self.diesel_weeks = sorted(self.diesel[3])
        self.lanes = freight["lanes"]
        self.weather_events = load("weather.json")["events"]
        # the stretches of road (a, b, from mile, to mile; miles from a) in each county a board road crosses
        self.county_spans: dict[str, list[tuple[str, str, float, float]]] = {}
        self.county_names: dict[str, str] = {}
        for e in roads["edges"]:
            for c in e["counties"]:
                self.county_spans.setdefault(c["county"], []).append((e["a"], e["b"], c["m0"], c["m1"]))
                self.county_names[c["county"]] = c["name"]
        self.county_city = {c["county_geoid"]: c["id"] for c in roads["cities"]}
        self.noaa_closures = load("closures.json")["closures"]   # highway closures the narratives report, read by hand
        self.hos = {r["key"]: r["value"] for r in load("hos.json")["rules"]}
        self.costs = {k: v["value"] for k, v in load("costs.json").items()}
        self._paths = {c: self._dijkstra(c) for c in sorted(self.cities)}

    # --- roads ------------------------------------------------------------------------------------------------
    def edge(self, a: str, b: str) -> Edge:
        e = self._edge.get((a, b))
        if e is None:
            raise KeyError(f"no road between {a} and {b}")
        return e

    def road_name(self, a: str, b: str) -> str:
        return f"{self.edge(a, b).route} {self.cities[a].name}-{self.cities[b].name}"

    def neighbours(self, city: str) -> list[str]:
        return sorted({e.b if e.a == city else e.a for e in self.edges if city in (e.a, e.b)})

    def _dijkstra(self, src: str) -> dict[str, tuple[float, tuple[str, ...]]]:
        """Shortest path by miles from src to every city; ties broken by the path's city ids, so it's deterministic."""
        best = {src: (0.0, (src,))}
        heap = [(0.0, (src,))]
        while heap:
            miles, path = heapq.heappop(heap)
            here = path[-1]
            if best[here] != (miles, path):
                continue
            for nxt in self.neighbours(here):
                cand = (round(miles + self.edge(here, nxt).miles, 3), path + (nxt,))
                if nxt not in best or cand < best[nxt]:
                    best[nxt] = cand
                    heapq.heappush(heap, cand)
        return best

    def path(self, a: str, b: str) -> tuple[str, ...]:
        return self._paths[a][b][1]

    def miles(self, a: str, b: str) -> float:
        return self._paths[a][b][0]

    # --- diesel -----------------------------------------------------------------------------------------------
    def diesel_week(self, monday: dt.date) -> dict[int, float]:
        key = monday.isoformat()
        if key not in self.diesel[3]:
            raise KeyError(f"no EIA diesel price for the week of {key}; weeks run {self.diesel_weeks[0]}..{self.diesel_weeks[-1]}")
        return {padd: self.diesel[padd][key] for padd in (2, 3)}

    def diesel_next_week(self, monday: dt.date) -> dict[int, float] | None:
        nxt = (monday + dt.timedelta(days=7)).isoformat()
        return {padd: self.diesel[padd][nxt] for padd in (2, 3)} if nxt in self.diesel[3] else None

    # --- freight ----------------------------------------------------------------------------------------------
    def out_in_ratio(self, year: str = "2024") -> dict[str, float]:
        out, inn = {}, {}
        for lane in self.lanes:
            out[lane["orig"]] = out.get(lane["orig"], 0.0) + lane[f"tons_{year}"]
            inn[lane["dest"]] = inn.get(lane["dest"], 0.0) + lane[f"tons_{year}"]
        return {c: out[c] / inn[c] for c in out if inn.get(c)}
