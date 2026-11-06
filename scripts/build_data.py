"""Build TruckSimEnv's static data pack (PLAN.md, milestone M2).

Two phases, so the data is a snapshot and never fetched at run time:

  fetch  download every raw source into data/raw/ (git-ignored), once; a file already there is kept.
         Each download's URL, time, size and sha256 go to data/raw/retrieved.json.
  build  derive data/*.json from data/raw/ alone, deterministically, and write data/manifest.json:
         the same raw files always give byte-identical output.

Usage (from the repo root):  .venv/bin/python scripts/build_data.py fetch|build|all
Needs: xlrd (EIA's .xls), openpyxl (FAF's metadata), httpx (installed with agentenv-framework).

Every number in data/ traces to a manifest entry. Where no public source was fetched, the value is marked
"source": "assumption" with a note saying so; nothing is filled in silently.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"
RETRIEVED = RAW / "retrieved.json"

# ---------------------------------------------------------------------------------------------------------------
# The board: a design choice, not data. Cities that are their own FAF5 zone generate freight; the others are
# waypoints on the corridors (their FAF zone is a whole "rest of state", so any share for them would be invented).
# Fort Worth is a waypoint because FAF5 has one Dallas-Fort Worth zone, whose freight is Dallas's. McAllen is a
# waypoint for domestic freight too, but carries the Rio Grande Valley's border crossings (BORDER_NODES).
# id, name, state, Census place name, county (for storm events), FAF5 zone or None, EIA PADD for diesel
CITIES = [
    ("LAR", "Laredo", "TX", "Laredo city", "Webb County", "487", 3),
    ("SA", "San Antonio", "TX", "San Antonio city", "Bexar County", "488", 3),
    ("AUS", "Austin", "TX", "Austin city", "Travis County", "481", 3),
    ("WAC", "Waco", "TX", "Waco city", "McLennan County", None, 3),
    ("DAL", "Dallas", "TX", "Dallas city", "Dallas County", "484", 3),
    ("OKC", "Oklahoma City", "OK", "Oklahoma City city", "Oklahoma County", "401", 2),
    ("HOU", "Houston", "TX", "Houston city", "Harris County", "486", 3),
    ("CRP", "Corpus Christi", "TX", "Corpus Christi city", "Nueces County", "483", 3),
    ("BMT", "Beaumont", "TX", "Beaumont city", "Jefferson County", "482", 3),
    ("SHV", "Shreveport", "LA", "Shreveport city", "Caddo Parish", None, 3),
    ("TXK", "Texarkana", "TX", "Texarkana city", "Bowie County", None, 3),
    ("LR", "Little Rock", "AR", "Little Rock city", "Pulaski County", None, 3),
    ("MEM", "Memphis", "TN", "Memphis city", "Shelby County", "471", 2),
    ("BTR", "Baton Rouge", "LA", "Baton Rouge city", "East Baton Rouge Parish", "221", 3),
    ("NOL", "New Orleans", "LA", "New Orleans city", "Orleans Parish", "223", 3),
    # v1.1: cities that give the roads detours, two more FAF zones, and the Valley's border crossings
    ("FTW", "Fort Worth", "TX", "Fort Worth city", "Tarrant County", None, 3),
    ("BCS", "College Station", "TX", "College Station city", "Brazos County", None, 3),
    ("LFK", "Lufkin", "TX", "Lufkin city", "Angelina County", None, 3),
    ("VCT", "Victoria", "TX", "Victoria city", "Victoria County", None, 3),
    ("MFE", "McAllen", "TX", "McAllen city", "Hidalgo County", None, 3),
    ("LCH", "Lake Charles", "LA", "Lake Charles city", "Calcasieu Parish", "222", 3),
    ("LFT", "Lafayette", "LA", "Lafayette city", "Lafayette Parish", None, 3),
    ("JAN", "Jackson", "MS", "Jackson city", "Hinds County", None, 3),
    ("TUL", "Tulsa", "OK", "Tulsa city", "Tulsa County", "402", 2),
]
# McAllen's freight: FAF5 counts trade with Mexico (FAF foreign zone 802) by truck at its zone of entry or exit,
# which is 489 ("rest of Texas") for every Texas port outside El Paso's and Laredo's zones. BTS's Border Crossing
# Entry Data counts trucks by port, so the Rio Grande Valley ports' share of zone 489's truck crossings places
# that share of the flow at McAllen. The share is from inbound crossings (BTS counts those) and is used both ways.
BORDER_NODES = {
    "MFE": {"zone": "489", "partner": "802",
            "valley_ports": ("Hidalgo", "Brownsville", "Progreso", "Roma", "Rio Grande City"),
            "zone_ports": ("Hidalgo", "Brownsville", "Progreso", "Roma", "Rio Grande City", "Eagle Pass", "Del Rio",
                           "Presidio")},
}

# a, b, the corridor we expect OSRM to route along (checked against its step refs, reported, never altered)
EDGES = [
    # I-35 and its Fort Worth branch
    ("LAR", "SA", "I 35"), ("SA", "AUS", "I 35"), ("AUS", "WAC", "I 35"), ("WAC", "DAL", "I 35E"),
    ("WAC", "FTW", "I 35W"), ("DAL", "FTW", "I 30"), ("DAL", "OKC", "I 35"),
    # the Gulf coast
    ("SA", "CRP", "I 37"), ("HOU", "VCT", "US 59"), ("VCT", "CRP", "US 77"), ("SA", "VCT", "US 87"),
    ("SA", "HOU", "I 10"), ("HOU", "BMT", "I 10"), ("BMT", "LCH", "I 10"), ("LCH", "LFT", "I 10"),
    ("LFT", "BTR", "I 10"), ("BTR", "NOL", "I 10"),
    # the Rio Grande Valley
    ("MFE", "SA", "US 281"), ("MFE", "CRP", "US 77"), ("MFE", "LAR", "US 83"),
    # Houston's roads north and west, besides I-45
    ("AUS", "HOU", "US 290"), ("WAC", "BCS", "TX 6"), ("BCS", "HOU", "TX 6"), ("DAL", "HOU", "I 45"),
    ("HOU", "LFK", "US 59"), ("LFK", "SHV", "US 79"),
    # east
    ("DAL", "TXK", "I 30"), ("TXK", "LR", "I 30"), ("DAL", "SHV", "I 20"), ("SHV", "TXK", "I 49"),
    ("LFT", "SHV", "I 49"), ("SHV", "JAN", "I 20"), ("LR", "MEM", "I 40"), ("MEM", "JAN", "I 55"),
    ("JAN", "NOL", "I 55"),
    # north
    ("OKC", "TUL", "I 44"), ("TUL", "DAL", "US 75"), ("TUL", "LR", "I 40"),
]
# A town a road must pass through, where OSRM's fastest car route takes another highway than the corridor we want
# (a design choice, like the board): the back road would otherwise share pavement with another segment.
EDGE_VIA = {
    ("AUS", "HOU"): [("Brenham, TX", 30.16688, -96.39774)],      # US 290, not TX 71 onto I-10
    ("MFE", "CRP"): [("Harlingen, TX", 26.19063, -97.69610)],    # US 77, not US 281 (MFE-SA's road)
    ("JAN", "NOL"): [("McComb, MS", 31.24379, -90.45315)],       # I-55, not US 49 and I-59
    ("LFK", "SHV"): [("Carthage, TX", 32.15738, -94.33742)],     # back roads (TX 315, US 259) onto US 79
}
STATE_NAMES = {"TX": "TEXAS", "OK": "OKLAHOMA", "LA": "LOUISIANA", "AR": "ARKANSAS", "TN": "TENNESSEE",
               "MS": "MISSISSIPPI"}
# A road crosses a county where its line comes within the county's radius (sqrt(land area / pi)) of the
# county's Census internal point: a circle standing in for the county's shape (an approximation, in the manifest).
COUNTY_DENSIFY_MILES = 1.0
STORM_YEARS = range(2019, 2026)
# Storm Events types that can slow or stop trucks on the board's roads. The sim decides how much each slows.
ROAD_EVENT_TYPES = {
    "Winter Storm", "Ice Storm", "Heavy Snow", "Winter Weather", "Blizzard", "Freezing Fog", "Dense Fog",
    "Flash Flood", "Flood", "Heavy Rain", "High Wind", "Strong Wind", "Tropical Storm", "Tropical Depression",
    "Hurricane", "Tornado", "Thunderstorm Wind", "Extreme Cold/Wind Chill", "Cold/Wind Chill", "Coastal Flood",
    "Storm Surge/Tide",
}
# SCTG2 codes we treat as needing a reefer (an assumption, see the manifest): other agricultural products (03,
# which holds fresh fruit and vegetables), meat/seafood (05) and other prepared foodstuffs (07).
REEFER_SCTG2 = {"03", "05", "07"}
# ---------------------------------------------------------------------------------------------------------------
# Real road closures (DESIGN.md "v1.1", M13). closure_candidates() lists every Storm Events record whose narrative
# has a sentence naming one of its county's board highways next to a closure word. Each candidate was read and
# decided by hand (2026-10-21); a candidate in neither table fails the build, so new data gets read too.
# Kept: event id -> (roads closed, direction, what the narrative says). Direction "both", or "a>b": only the way
# from the road's first city toward its second (e.g. "northbound I-35" on DAL-OKC).
CLOSURES_KEPT = {
    832644: ((("OKC", "TUL"),), "both", "I-44 shut down by the highway patrol around 29th St, Oklahoma City"),
    797490: ((("LFT", "SHV"),), "both", "ice closed I-49 through Shreveport between I-20 and Bert Kouns"),
    814830: ((("AUS", "WAC"),), "a>b", "flash flooding closed northbound I-35 between US 183 and Rundberg Ln, Austin"),
    823952: ((("AUS", "HOU"),), "both", "flash flooding closed US 290 at FM 2336 in McDade"),
    830502: ((("DAL", "OKC"),), "a>b", "I-35 northbound shut down by flash flooding in Sanger"),
    **{e: ((("TXK", "LR"),), "both", "I-30 shut down by flooding near Gurdon") for e in (841301, 841303, 841304, 841316, 841317)},
    **{e: ((("TXK", "LR"),), "both", "I-30 flooded and closed between Prescott and Gurdon")
       for e in (833010, 833016, 833017, 833019, 833026, 833028, 833035, 833036)},
    883789: ((("SHV", "JAN"),), "b>a", "trees down across I-20 westbound near exit 52 closed the interstate"),
    866676: ((("LFT", "SHV"),), "both", "blown debris closed I-49 between Kings Highway and I-20"),
    869001: ((("DAL", "TXK"),), "both", "I-30 flooded and closed at the Franklin/Hopkins county line"),
    908989: ((("LFT", "SHV"),), "both", "a fallen tree closed I-49 near LA 174, mile marker 154"),
    943633: ((("SA", "AUS"),), "both", "Kyle police closed part of I-35 for ice and multiple crashes"),
    960081: ((("SA", "HOU"),), "both", "floodwater closed I-10 between mile markers 712 and 716"),
    977076: ((("SA", "CRP"), ("VCT", "CRP")), "both", "high water closed US 77 Robstown-Calallen and I-37 Calallen-Annaville"),
    977083: ((("SA", "CRP"),), "both", "flooding closed I-37 between Mathis and Edroy"),
    972396: ((("LAR", "SA"),), "both", "Leon Creek's flood closed I-35 at its bridge, southwest San Antonio"),
    963284: ((("SA", "CRP"),), "both", "I-37 at Carbon Plant flooded and closed, Corpus Christi"),
    1059547: ((("AUS", "WAC"),), "both", "I-35 shut down about two hours while wind damage was cleared"),
    1008632: ((("AUS", "HOU"),), "both", "ice closed US 290 near the Waller County line"),
    1008628: ((("SA", "HOU"),), "both", "ice closed I-10 near the Austin/Waller county line"),
    995633: ((("LFT", "SHV"), ("SHV", "TXK")), "both", "ice closed I-49 between I-20 and Bert Kouns, and from I-220 to the Arkansas line"),
    1053312: ((("WAC", "FTW"),), "a>b", "high water closed northbound I-35W near US 67"),
    1053486: ((("DAL", "TXK"),), "both", "high water closed I-30 near Cesar Chavez Blvd, Dallas"),
    1054301: ((("DAL", "HOU"),), "both", "water over the road closed I-45 north of Palmer"),
    1089015: ((("TUL", "DAL"),), "both", "high water made US 75 at FM 902 impassable"),
    1142728: ((("BTR", "NOL"),), "both", "high water closed I-10 both ways at Metairie Road"),
    **{e: ((("JAN", "NOL"),), "both", "ice shut I-55 down in both directions") for e in (1159938, 1159939, 1159947)},
    **{e: ((("BTR", "NOL"),), "both", "ice shut I-10 down") for e in (1159962, 1159963, 1159965, 1159966)},
    1159968: ((("LFT", "BTR"),), "both", "ice shut I-10 down"),
    1159300: ((("MFE", "CRP"),), "both", "ice closed US 77 in Kingsville"),
    1177180: ((("HOU", "LFK"),), "both", "flash flooding closed all lanes of US 59 near the Baskin Loop overpass"),
    1186232: ((("TUL", "DAL"),), "both", "high water closed US 75"),
    1180715: ((("DAL", "OKC"),), "both", "floodwater several feet deep closed the main lanes of I-35"),
    1195490: ((("SHV", "JAN"),), "b>a", "trees down across I-20 westbound near mile 106 closed it"),
    1175597: ((("SHV", "JAN"),), "a>b", "trees down across I-20 eastbound at mile 101 closed it for a few hours"),
    1226487: ((("MEM", "JAN"),), "both", "flooding closed I-55 both ways north of McLemore Ave"),
    1292167: ((("TUL", "DAL"),), "b>a", "flooding closed US 75 northbound"),
    1256370: ((("DAL", "TXK"),), "both", "flooding shut I-30 down from mile 220 to the Arkansas line"),
    1294444: ((("LFT", "SHV"),), "a>b", "fog: I-49 northbound closed about 12 hours (southbound only briefly)"),
    1273203: ((("DAL", "OKC"),), "both", "a section of I-35 in Lewisville flooded and impassable"),
    860006: ((("SA", "AUS"),), "both", "I-35 closed both ways under three feet of water near Wetmore, northeast San Antonio"),
}
CLOSURE_HALF_MILES = 2.0   # a closure with a NOAA point closes this far either side of it along the road
CLOSURE_POINT_MAX_MILES = 10.0   # ... when the point is this close to the road's line; else the county's stretch
CLOSURES_DROPPED = {
    "a local road near, at or under the highway": (792248, 792249, 792250, 801549, 814381, 837497, 855168, 858086, 868831,
        1004355, 1008634, 1077646, 1102309, 1133254, 1180641, 1184089, 1188001, 1194701, 1206325, 1288010),
    "a frontage, service or access road, a ramp, an exit or an underpass": (895417, 972918, 995642, 995643, 995645,
        1162274, 1177848, 1184091, 1187440, 1187472, 1188018, 1188451, 1212487, 1217876, 1233205, 1233293, 1255956,
        1258219, 1266223, 1276423, 1297741, 1300296),
    "one lane, not the road": (1237988, 1256664),
    "the closure was in another county (the episode's text, filed for each county)": (797491, 797492, 797494, 797495,
        797497, 797499, 797505, 833008, 833014, 833023, 833029, 833030, 864824, 908986, 908987, 909900, 909901, 995634,
        995635),
    "another highway, or a stretch the board's road doesn't use": (824456, 881439, 882256, 1255500, 1265186, 1295139),
    "not a closure": (1076986,),
}
FAF_TRUCK_MODE = "1"
FAF_YEARS = ("2023", "2024")
BTS_BORDER_URL = ("https://data.bts.gov/resource/keg4-3bc2.csv?$select=port_name,port_code,border,state,date,measure,value"
                  "&$where=state%3D%27Texas%27%20AND%20measure%3D%27Trucks%27&$order=date,port_code&$limit=50000")
DIESEL_FROM = dt.date(2019, 1, 1)

# ---------------------------------------------------------------------------------------------------------------
# Raw sources. url is either a string or a callable resolving the current URL (files whose names change).
NOAA_DIR = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
BLS_SERIES = {"hourly_mean": "OEUN000000000000053303203", "annual_mean": "OEUN000000000000053303204",
              "light_hourly_mean": "OEUN000000000000053303303"}     # 53-3032 heavy/tractor-trailer; 53-3033 light truck


def _noaa_url(year: int):
    def resolve(client: httpx.Client) -> str:
        listing = client.get(NOAA_DIR).text
        names = sorted(set(re.findall(rf"StormEvents_details-ftp_v1\.0_d{year}_c\d+\.csv\.gz", listing)))
        if not names:
            raise RuntimeError(f"no Storm Events details file for {year} in {NOAA_DIR}")
        return NOAA_DIR + names[-1]
    return resolve


def _ecfr_url(client: httpx.Client) -> str:
    titles = client.get("https://www.ecfr.gov/api/versioner/v1/titles.json").json()["titles"]
    as_of = next(t["up_to_date_as_of"] for t in titles if t["number"] == 49)
    return f"https://www.ecfr.gov/api/versioner/v1/full/{as_of}/title-49.xml?part=395&section=395.3"


SOURCES = {
    "census_places_2023.zip": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip",
    "census_counties_2023.zip": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip",
    "eia_diesel_padd3.xls": "https://www.eia.gov/dnav/pet/hist_xls/EMD_EPD2D_PTE_R30_DPGw.xls",
    "eia_diesel_padd2.xls": "https://www.eia.gov/dnav/pet/hist_xls/EMD_EPD2D_PTE_R20_DPGw.xls",
    "FAF5.7.1_2018-2024.zip": "https://faf.ornl.gov/faf5/Data/Download_Files/FAF5.7.1_2018-2024.zip",
    "FAF5_metadata.xlsx": "https://faf.ornl.gov/faf5/data/FAF5_metadata.xlsx",
    "fhwa_vm1_2023.html": "https://www.fhwa.dot.gov/policyinformation/statistics/2023/vm1.cfm",
    "bts_border_trucks_tx.csv": BTS_BORDER_URL,
    "atri_operational_costs_2024.pdf": "https://truckingresearch.org/wp-content/uploads/2024/06/ATRI-Operational-Cost-of-Trucking-06-2024.pdf",
    "ecfr_49_395.3.xml": _ecfr_url,
    **{f"bls_{k}.json": f"https://api.bls.gov/publicAPI/v1/timeseries/data/{sid}" for k, sid in BLS_SERIES.items()},
    **{f"noaa_storm_details_{y}.csv.gz": _noaa_url(y) for y in STORM_YEARS},
    # The 3D viewer's vehicles (scripts/build_models.py); the sim never reads them. Kenney's own upload of the kit.
    "kenney_car-kit_3.1.zip": "https://opengameart.org/sites/default/files/kenney_car-kit_3.1.zip",
}
# Sources the build only cites (their figures are transcribed in this file): a fetch that the site refuses warns and
# says how to fetch it by hand, instead of failing; once in data/raw/ and retrieved.json, the manifest cites its sha256.
CITED_ONLY = {"atri_operational_costs_2024.pdf"}
LICENSES = {
    "census": ("U.S. Census Bureau, Gazetteer Files 2023", "public domain (U.S. Government work)"),
    "eia": ("U.S. Energy Information Administration, weekly retail on-highway No. 2 diesel prices", "public domain (U.S. Government work); cite EIA"),
    "faf": ("FHWA/BTS Freight Analysis Framework 5.7.1 (ORNL), regional database 2018-2024", "public domain (U.S. Government work)"),
    "fhwa": ("FHWA Highway Statistics 2023, Table VM-1", "public domain (U.S. Government work)"),
    "ecfr": ("eCFR, 49 CFR 395.3", "public domain (U.S. Government work)"),
    "bls": ("BLS Occupational Employment and Wage Statistics, SOC 53-3032 Heavy and Tractor-Trailer Truck Drivers and 53-3033 Light Truck Drivers, national", "public domain (U.S. Government work)"),
    "noaa": ("NOAA NCEI Storm Events Database, details files", "public domain (U.S. Government work)"),
    "bts": ("BTS Border Crossing/Entry Data (data.bts.gov keg4-3bc2), Texas truck crossings by port", "public domain (U.S. Government work)"),
    "atri": ("ATRI, An Analysis of the Operational Costs of Trucking: 2024 Update (June 2024)", "(c) American Transportation Research Institute; figures cited from it, the report is not redistributed (data/raw is git-ignored)"),
    "osm": ("OpenStreetMap via the OSRM demo server (router.project-osrm.org), car profile", "ODbL 1.0: (c) OpenStreetMap contributors; data/roads.json is a derived database under ODbL"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_retrieved() -> dict:
    return json.loads(RETRIEVED.read_text()) if RETRIEVED.exists() else {}


def record(retrieved: dict, name: str, url: str) -> None:
    """Note a file in data/raw/ (fetched, or fetched by hand) with its URL, time, size and sha256."""
    path = RAW / name
    retrieved[name] = {"url": url, "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "bytes": path.stat().st_size, "sha256": sha256(path)}
    RETRIEVED.write_text(json.dumps(retrieved, indent=1, sort_keys=True) + "\n")


def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osrm").mkdir(exist_ok=True)
    retrieved = load_retrieved()
    headers = {"User-Agent": "TruckSimEnv data build (github.com/polakamtejas/agentenv-trucksim-plugin)",
               "Accept-Encoding": "gzip"}
    with httpx.Client(follow_redirects=True, timeout=900, headers=headers) as client:
        for name, url in SOURCES.items():
            path = RAW / name
            if path.exists() and name in retrieved:
                continue
            url = url(client) if callable(url) else url
            print(f"fetch {name} <- {url}")
            with client.stream("GET", url) as r:
                if r.status_code >= 400 and name in CITED_ONLY:
                    print(f"warning: {url} answered {r.status_code}; fetch it by hand (curl -L -o data/raw/{name} '{url}') "
                          f"and record it: the build cites it, its figures are transcribed")
                    continue
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            record(retrieved, name, url)
        # Routes need the cities' coordinates, which come from the Census file fetched above.
        coords = {c["id"]: (c["lat"], c["lon"]) for c in cities_from_raw()}
        for a, b, _ in EDGES:
            name = f"osrm/{a}-{b}.json"
            if (RAW / name).exists() and name in retrieved:
                continue
            points = [coords[a]] + [(la, lo) for _, la, lo in EDGE_VIA.get((a, b), [])] + [coords[b]]
            url = ("https://router.project-osrm.org/route/v1/driving/" + ";".join(f"{lo:.5f},{la:.5f}" for la, lo in points)
                   + "?overview=simplified&geometries=geojson&steps=true")
            print(f"fetch {name}")
            r = client.get(url)
            r.raise_for_status()
            if r.json().get("code") != "Ok":
                raise RuntimeError(f"OSRM refused {a}-{b}: {r.text[:300]}")
            (RAW / name).write_bytes(r.content)
            record(retrieved, name, url)
            time.sleep(1.1)  # the demo server's usage policy: at most one request a second


# ---------------------------------------------------------------------------------------------------------------
# build: each function reads data/raw/ only.

def _gazetteer(name: str) -> list[dict]:
    with zipfile.ZipFile(RAW / name) as z:
        member = next(m for m in z.namelist() if m.endswith(".txt"))
        text = z.read(member).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()} for r in rows]


def cities_from_raw() -> list[dict]:
    places = {(r["USPS"], r["NAME"]): r for r in _gazetteer("census_places_2023.zip")}
    counties = {(r["USPS"], r["NAME"]): r for r in _gazetteer("census_counties_2023.zip")}
    out = []
    for cid, name, state, place, county, faf_zone, padd in CITIES:
        p, c = places.get((state, place)), counties.get((state, county))
        if p is None or c is None:
            raise KeyError(f"{cid}: Census has no {'place ' + place if p is None else 'county ' + county} in {state}")
        out.append({"id": cid, "name": name, "state": state, "lat": round(float(p["INTPTLAT"]), 5),
                    "lon": round(float(p["INTPTLONG"]), 5), "place_geoid": p["GEOID"], "county": county,
                    "county_geoid": c["GEOID"], "faf_zone": faf_zone,
                    "freight_node": faf_zone is not None or cid in BORDER_NODES, "padd": padd})
    return out


def _miles_between(la1: float, lo1: float, la2: float, lo2: float) -> float:
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def board_counties() -> list[dict]:
    """Every county in the board's states, with its Census internal point and the radius of a circle of its land area."""
    return [{"geoid": r["GEOID"], "name": f"{r['NAME']}, {r['USPS']}", "lat": float(r["INTPTLAT"]),
             "lon": float(r["INTPTLONG"]), "radius_mi": math.sqrt(float(r["ALAND_SQMI"]) / math.pi)}
            for r in _gazetteer("census_counties_2023.zip") if r["USPS"] in STATE_NAMES]


def county_spans(geometry: list, miles: float, counties: list[dict], end_counties: tuple[str, str]) -> list[dict]:
    """The stretches of a road, in road miles from its first city, that cross each county (COUNTY_DENSIFY_MILES).
    The two end cities' counties always hold the road's ends."""
    pts = [(geometry[0][0], geometry[0][1], 0.0)]
    for (la1, lo1), (la2, lo2) in zip(geometry, geometry[1:]):
        d = _miles_between(la1, lo1, la2, lo2)
        n, base = max(1, math.ceil(d / COUNTY_DENSIFY_MILES)), pts[-1][2]
        pts += [(la1 + (la2 - la1) * i / n, lo1 + (lo2 - lo1) * i / n, base + d * i / n) for i in range(1, n + 1)]
    scale = miles / pts[-1][2]
    lat0, lat1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lon0, lon1 = min(p[1] for p in pts), max(p[1] for p in pts)
    spans = {}
    for c in counties:
        reach = (c["radius_mi"] + 5) / 69.0                  # degrees, a loose box before measuring
        if not (lat0 - reach <= c["lat"] <= lat1 + reach and lon0 - 1.3 * reach <= c["lon"] <= lon1 + 1.3 * reach):
            continue
        inside = [m for la, lo, m in pts if _miles_between(la, lo, c["lat"], c["lon"]) <= c["radius_mi"]]
        if inside:
            spans[c["geoid"]] = [min(inside) * scale, max(inside) * scale]
    for geoid, at_end, near in zip(end_counties, (0.0, miles), (min(5.0, miles), max(0.0, miles - 5.0))):
        lo, hi = spans.get(geoid, [at_end, at_end])            # an end city's county holds at least 5 miles of road
        spans[geoid] = [min(lo, at_end, near), max(hi, at_end, near)]
    names = {c["geoid"]: c["name"] for c in counties}
    return [{"county": g, "name": names[g], "m0": round(lo, 1), "m1": round(hi, 1)}
            for g, (lo, hi) in sorted(spans.items(), key=lambda kv: (kv[1][0], kv[0]))]


def build_roads(cities: list[dict]) -> tuple[dict, list[str]]:
    edges, warnings = [], []
    counties, county_of = board_counties(), {c["id"]: c["county_geoid"] for c in cities}
    for a, b, expected in EDGES:
        route = json.loads((RAW / f"osrm/{a}-{b}.json").read_text())["routes"][0]
        by_ref: dict[str, float] = {}
        for leg in route["legs"]:
            for step in leg["steps"]:
                for ref in (step.get("ref") or "").split(";"):
                    if ref.strip():
                        by_ref[ref.strip()] = by_ref.get(ref.strip(), 0.0) + step["distance"]
        refs = sorted(by_ref, key=lambda r: -by_ref[r])[:3]
        if expected not in refs:
            warnings.append(f"{a}-{b}: expected {expected}, OSRM's main refs are {refs}")
        geometry = [[round(lat, 5), round(lon, 5)] for lon, lat in route["geometry"]["coordinates"]]
        miles = round(route["distance"] / 1609.344, 1)
        edges.append({"a": a, "b": b, "route": expected, "via": [v[0] for v in EDGE_VIA.get((a, b), [])],
                      "osrm_refs": refs, "miles": miles,
                      "osrm_car_hours": round(route["duration"] / 3600, 2), "geometry": geometry,
                      "counties": county_spans(geometry, miles, counties, (county_of[a], county_of[b]))})
    return {"cities": cities, "edges": edges}, warnings


def build_diesel() -> dict:
    import xlrd
    series = {}
    for padd in (2, 3):
        book = xlrd.open_workbook(RAW / f"eia_diesel_padd{padd}.xls")
        sheet = book.sheet_by_name("Data 1")
        if sheet.row_values(1)[1] != f"EMD_EPD2D_PTE_R{padd}0_DPG":
            raise ValueError(f"PADD {padd} file holds series {sheet.row_values(1)[1]!r}")
        rows = []
        for i in range(3, sheet.nrows):
            serial, price = sheet.row_values(i)[:2]
            day = xlrd.xldate_as_datetime(serial, book.datemode).date()
            if day >= DIESEL_FROM and price != "":
                rows.append([day.isoformat(), round(float(price), 3)])
        series[f"padd{padd}"] = rows
    if [d for d, _ in series["padd2"]] != [d for d, _ in series["padd3"]]:
        raise ValueError("the PADD 2 and PADD 3 weekly series have different dates")
    return {"unit": "USD per gallon, retail on-highway No. 2 diesel, weekly (Monday)",
            "padd_names": {"2": "Midwest", "3": "Gulf Coast"}, **series}


def border_shares() -> dict:
    """Each border node's share of its FAF zone's truck crossings (BTS), over FAF_YEARS."""
    trucks: dict[str, float] = {}
    with open(RAW / "bts_border_trucks_tx.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"][:4] in FAF_YEARS and r["measure"] == "Trucks":
                trucks[r["port_name"]] = trucks.get(r["port_name"], 0.0) + float(r["value"])
    out = {}
    for node, spec in BORDER_NODES.items():
        other_zones = {"Laredo", "El Paso", "Ysleta", "Tornillo", "Fabens", "Boquillas"}   # zones 487 and 485
        unplaced = sorted(set(trucks) - set(spec["zone_ports"]) - other_zones)
        if unplaced:
            raise ValueError(f"BTS lists Texas truck ports not assigned to a FAF zone: {unplaced}")
        valley = sum(trucks.get(p, 0.0) for p in spec["valley_ports"])
        zone = sum(trucks.get(p, 0.0) for p in spec["zone_ports"])
        out[node] = {"zone": spec["zone"], "partner": spec["partner"], "share": round(valley / zone, 4),
                     "valley_trucks": round(valley), "zone_trucks": round(zone), "years": list(FAF_YEARS),
                     "valley_ports": list(spec["valley_ports"])}
    return out


def build_freight(cities: list[dict]) -> dict:
    zone_city = {c["faf_zone"]: c["id"] for c in cities if c["faf_zone"]}
    border = border_shares()
    by_zone = {b["zone"]: (node, b["partner"], b["share"]) for node, b in border.items()}
    totals: dict[tuple[str, str], dict] = {}
    with zipfile.ZipFile(RAW / "FAF5.7.1_2018-2024.zip") as z:
        with z.open("FAF5.7.1_2018-2024.csv") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                if row["dms_mode"] != FAF_TRUCK_MODE:
                    continue
                o, d, weight = zone_city.get(row["dms_orig"]), zone_city.get(row["dms_dest"]), 1.0
                if o is None and row["dms_orig"] in by_zone and row["trade_type"] == "2":    # imports, entering there
                    node, partner, share = by_zone[row["dms_orig"]]
                    if row["fr_orig"] == partner:
                        o, weight = node, share
                if d is None and row["dms_dest"] in by_zone and row["trade_type"] == "3":    # exports, leaving there
                    node, partner, share = by_zone[row["dms_dest"]]
                    if row["fr_dest"] == partner:
                        d, weight = node, share
                if o is None or d is None or o == d:
                    continue
                t = totals.setdefault((o, d), {f"tons_{y}": 0.0 for y in FAF_YEARS}
                                      | {f"reefer_tons_{y}": 0.0 for y in FAF_YEARS})
                for y in FAF_YEARS:
                    tons = float(row[f"tons_{y}"] or 0) * weight
                    t[f"tons_{y}"] += tons
                    if row["sctg2"] in REEFER_SCTG2:
                        t[f"reefer_tons_{y}"] += tons
    lanes = [{"orig": o, "dest": d, **{k: round(v, 3) for k, v in t.items()}} for (o, d), t in sorted(totals.items())]
    return {"unit": "thousand tons a year by truck (FAF5 dms_mode 1), all trade types, between the board's FAF zones; "
                    "a border node's lanes are its zone's trade with the partner country times its share of the zone's truck crossings",
            "years": list(FAF_YEARS), "reefer_sctg2": sorted(REEFER_SCTG2), "border_nodes": border, "lanes": lanes}


def _local_to_utc(text: str, tz: str) -> str:
    # Storm Events times are local time, e.g. "15-FEB-21 03:00:00" with CZ_TIMEZONE "CST-6": the zone's offset from UTC,
    # sign included (-6 h), so UTC is local time minus it (03:00 CST is 09:00Z).
    m = re.fullmatch(r"[A-Z]{3}([+-]?\d+)", tz.strip().upper())
    if m is None:
        raise ValueError(f"unknown Storm Events timezone {tz!r}")
    local = dt.datetime.strptime(text, "%d-%b-%y %H:%M:%S")
    return (local - dt.timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%dT%H:%MZ")


def build_weather(roads: dict) -> dict:
    """Storm Events in every county a board road crosses (its cities' counties among them). Each event keeps its
    county; the sim finds the road stretches from roads.json."""
    names = {s["county"]: s["name"] for e in roads["edges"] for s in e["counties"]}
    by_fips = {g: g for g in names}
    by_zone_name = {}                        # (state name, county words) -> geoid, for forecast-zone events
    for g, n in names.items():
        county, st = n.rsplit(", ", 1)
        by_zone_name[(STATE_NAMES[st], re.sub(r" (County|Parish)$", "", county).upper())] = g
    events = []
    for year in STORM_YEARS:
        import gzip
        with gzip.open(RAW / f"noaa_storm_details_{year}.csv.gz", "rt", encoding="latin-1") as f:
            for r in csv.DictReader(f):
                if r["EVENT_TYPE"] not in ROAD_EVENT_TYPES or r["STATE"] not in STATE_NAMES.values():
                    continue
                if r["CZ_TYPE"] == "C":
                    county = by_fips.get(f"{int(r['STATE_FIPS']):02d}{int(r['CZ_FIPS']):03d}")
                else:  # forecast zones: the county whose name's words the zone's name holds (the longest such name)
                    words = set(re.findall(r"[A-Z]+", r["CZ_NAME"].upper()))
                    hits = [(len(name.split()), g) for (st, name), g in by_zone_name.items()
                            if st == r["STATE"] and set(name.split()) <= words]
                    county = max(hits)[1] if hits else None
                if county is None:
                    continue
                events.append({"id": int(r["EVENT_ID"]), "county": county, "type": r["EVENT_TYPE"],
                               "begin_utc": _local_to_utc(r["BEGIN_DATE_TIME"], r["CZ_TIMEZONE"]),
                               "end_utc": _local_to_utc(r["END_DATE_TIME"], r["CZ_TIMEZONE"]),
                               "magnitude": float(r["MAGNITUDE"]) if r["MAGNITUDE"] else None,
                               "zone": f"{r['CZ_TYPE']}:{r['CZ_NAME']}"})
    events.sort(key=lambda e: (e["begin_utc"], e["id"]))
    return {"note": "events in the counties the board's roads cross (or forecast zones named for them) that can slow trucks",
            "years": [STORM_YEARS.start, STORM_YEARS.stop - 1], "types": sorted(ROAD_EVENT_TYPES), "events": events}


_HIGHWAY = re.compile(r"\b(?:(I|IH|Interstate)|(US|U\.S\.)(?:\s+(?:Highway|Hwy))?|(SH|TX|State Highway|Texas Highway|Highway|Hwy))"
                      r"[\s-]*(\d{1,3})(?:[EWNS]\b)?", re.I)
_CLOSED = re.compile(r"\b(closed|closing|closure|closures|shut down|shutdown|impassable|barricaded)\b", re.I)


def _narratives(events: list[dict]) -> dict[int, tuple]:
    """Event id -> (its event narrative + episode narrative, its type, its begin point or None), for weather.json's events."""
    import gzip
    wanted, out = {e["id"] for e in events}, {}
    for year in STORM_YEARS:
        with gzip.open(RAW / f"noaa_storm_details_{year}.csv.gz", "rt", encoding="latin-1") as f:
            for r in csv.DictReader(f):
                if int(r["EVENT_ID"]) in wanted:
                    point = (float(r["BEGIN_LAT"]), float(r["BEGIN_LON"])) if r["BEGIN_LAT"] and r["BEGIN_LON"] else None
                    out[int(r["EVENT_ID"])] = (" ".join(x for x in (r["EVENT_NARRATIVE"], r["EPISODE_NARRATIVE"]) if x),
                                               r["EVENT_TYPE"], point)
    return out


def closure_candidates(roads: dict, weather: dict, narratives: dict) -> dict[int, str]:
    """Events with a sentence naming one of their county's board highways (by number and kind) next to a closure
    word: event id -> that sentence."""
    on_county: dict[str, list[str]] = {}
    for e in roads["edges"]:
        for c in e["counties"]:
            on_county.setdefault(c["county"], []).append(e["route"])
    out = {}
    for ev in weather["events"]:
        routes = on_county.get(ev["county"], [])
        text = narratives.get(ev["id"], ("", ""))[0]
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if not _CLOSED.search(sentence):
                continue
            for m in _HIGHWAY.finditer(sentence):
                kind = "I" if m.group(1) else ("US" if m.group(2) else None)
                if any(r.split()[1].rstrip("EW") == m.group(4) and (kind is None or r.split()[0] == kind) for r in routes):
                    out.setdefault(ev["id"], sentence.strip())
                    break
    return out


def _project(edge: dict, lat: float, lon: float) -> tuple[float, float]:
    """The point on a road's line nearest (lat, lon): (road miles from its first city, miles away)."""
    g, best, along = edge["geometry"], (1e9, 0.0), 0.0
    total = sum(_miles_between(*p, *q) for p, q in zip(g, g[1:]))
    for (la1, lo1), (la2, lo2) in zip(g, g[1:]):
        d = _miles_between(la1, lo1, la2, lo2)
        n = max(1, math.ceil(d / 0.25))
        for i in range(n + 1):
            f = i / n
            away = _miles_between(la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f, lat, lon)
            if away < best[0]:
                best = (away, along + d * f)
        along += d
    return best[1] * edge["miles"] / total, best[0]


def build_closures(roads: dict, weather: dict) -> dict:
    narratives = _narratives(weather["events"])
    candidates = closure_candidates(roads, weather, narratives)
    dropped = {e for ids in CLOSURES_DROPPED.values() for e in ids}
    unread = sorted(set(candidates) - set(CLOSURES_KEPT) - dropped)
    if unread:
        raise ValueError(f"closure candidates nobody has read (decide them in CLOSURES_KEPT or CLOSURES_DROPPED): {unread}")
    stale = sorted((set(CLOSURES_KEPT) | dropped) - set(candidates))
    if stale:
        raise ValueError(f"reviewed closures that are no longer candidates: {stale}")
    county_of = {e["id"]: e["county"] for e in weather["events"]}
    stretch = {(e["a"], e["b"], c["county"]): (c["m0"], c["m1"]) for e in roads["edges"] for c in e["counties"]}
    edges = {(e["a"], e["b"]): e for e in roads["edges"]}
    closures = []
    for event_id, (closed_roads, direction, what) in sorted(CLOSURES_KEPT.items()):
        for a, b in closed_roads:
            key = (a, b, county_of[event_id])
            if key not in stretch:
                raise ValueError(f"closure {event_id}: road {a}-{b} doesn't cross county {key[2]}")
            m0, m1 = stretch[key]
            placed = "the county's stretch"
            point = narratives[event_id][2]
            if point:
                at, away = _project(edges[(a, b)], *point)
                if away <= CLOSURE_POINT_MAX_MILES:
                    m0, m1 = max(0.0, at - CLOSURE_HALF_MILES), min(edges[(a, b)]["miles"], at + CLOSURE_HALF_MILES)
                    placed = f"NOAA's point, {away:.1f} mi from the road"
            closures.append({"event_id": event_id, "a": a, "b": b, "m0": round(m0, 1), "m1": round(m1, 1),
                             "placed": placed, "direction": direction, "type": narratives[event_id][1], "what": what,
                             "narrative": candidates[event_id]})
    return {"note": "highway closures the Storm Events narratives report, each read by hand (scripts/build_data.py, "
                    "CLOSURES_KEPT and CLOSURES_DROPPED); a closure lasts its storm record's span",
            "candidates": len(candidates), "kept_events": len(CLOSURES_KEPT),
            "dropped": {why: len(ids) for why, ids in CLOSURES_DROPPED.items()}, "closures": closures}


def build_hos() -> dict:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (RAW / "ecfr_49_395.3.xml").read_text()))
    rules = [  # (key, value, the regulation's own words, which must still be in the text)
        ("off_duty_before_shift_h", 10, "may not drive without first taking 10 consecutive hours off duty", "395.3(a)(1)"),
        ("duty_window_h", 14, "may not drive after a period of 14 consecutive hours after coming on-duty", "395.3(a)(2)"),
        ("driving_per_shift_h", 11, "may drive a total of 11 hours during the period", "395.3(a)(3)(i)"),
        ("driving_before_break_h", 8, "more than 8 hours of driving time have passed without at least a consecutive 30-minute interruption", "395.3(a)(3)(ii)"),
        ("break_min", 30, "satisfied either by off-duty, sleeper berth or on-duty not driving time", "395.3(a)(3)(ii)"),
        ("cycle_h", 70, "on duty 70 hours in any period of 8 consecutive days", "395.3(b)(2)"),
        ("cycle_days", 8, "on duty 70 hours in any period of 8 consecutive days", "395.3(b)(2)"),
        ("restart_h", 34, "off-duty period of 34 or more consecutive hours", "395.3(c)(2)"),
    ]
    missing = [quote for _, _, quote, _ in rules if quote not in text]
    if missing:
        raise ValueError(f"49 CFR 395.3 no longer says: {missing}")
    return {"regulation": "49 CFR 395.3, property-carrying drivers; the carrier operates every day (70 h / 8 days)",
            "rules": [{"key": k, "value": v, "cfr": cite, "text": quote} for k, v, quote, cite in rules]}


# ATRI's 2024 Update (data/raw/atri_operational_costs_2024.pdf), transcribed: Table 8, "Average Marginal Costs per
# Mile, 2014-2023", the 2023 column (p. 13), and Table 16, contracted owner-operator pay per mile (p. 23).
ATRI_2023_PER_MILE = {"fuel": 0.553, "truck_trailer_payments": 0.360, "repair_maintenance": 0.202, "insurance": 0.099,
                      "permits_licenses": 0.009, "tires": 0.046, "tolls": 0.034, "driver_wages": 0.779,
                      "driver_benefits": 0.188, "total": 2.270}
ATRI_2023_OWNER_OPERATOR_PAY_PER_MILE = 2.10


def build_costs() -> dict:
    vm1 = (RAW / "fhwa_vm1_2023.html").read_text(encoding="utf-8", errors="replace")
    import html as htmlmod
    cells = [c.strip() for c in htmlmod.unescape(re.sub(r"<[^>]+>", "|", vm1)).split("|") if c.strip()]

    def row_2023(label: str) -> list[float]:
        i = next(i for i, c in enumerate(cells) if c.startswith(label))
        nums = []
        for c in cells[i + 1:]:
            if re.fullmatch(r"[\d,]+", c):
                nums.append(float(c.replace(",", "")))
                if len(nums) == 9:
                    return nums
            elif nums:
                break
        raise ValueError(f"VM-1: no 9 numbers after {label!r}")

    # VM-1's 9 columns: light duty short WB, motorcycles, buses, light duty long WB, single-unit trucks,
    # combination trucks, subtotal light duty, subtotal single-unit + combination, all motor vehicles.
    vmt, fuel = row_2023("Total Rural and Urban"), row_2023("Fuel consumed")
    vmt_millions, fuel_thousand_gal = vmt[5], fuel[5]
    mpg = round(vmt_millions * 1e6 / (fuel_thousand_gal * 1e3), 2)
    mpg_of = lambda col: round(vmt[col] * 1e6 / (fuel[col] * 1e3), 2)   # noqa: E731

    def bls(name: str) -> tuple[float, str]:
        point = json.loads((RAW / f"bls_{name}.json").read_text())["Results"]["series"][0]["data"][0]
        return float(point["value"]), point["year"]

    wage_h, wage_year = bls("hourly_mean")
    wage_y, _ = bls("annual_mean")
    light_h, light_year = bls("light_hourly_mean")
    assumption = "assumption: no public source fetched"
    atri = "ATRI Operational Costs of Trucking 2024 Update, Table 8 (2023 average per mile)"
    small = "assumption: no public source fetched (a straight truck or a cargo van costs less than a tractor-trailer)"
    return {
        "fuel_economy_mpg": {"value": mpg, "source": "FHWA Highway Statistics 2023 VM-1, combination trucks: "
                             f"{vmt_millions:,.0f} M vehicle-miles / {fuel_thousand_gal:,.0f} thousand gallons"},
        "box_fuel_economy_mpg": {"value": mpg_of(4), "source": "FHWA Highway Statistics 2023 VM-1, single-unit trucks: "
                                 f"{vmt[4]:,.0f} M vehicle-miles / {fuel[4]:,.0f} thousand gallons"},
        "van_fuel_economy_mpg": {"value": mpg_of(3), "source": "FHWA Highway Statistics 2023 VM-1, light duty vehicles, "
                                 f"long wheelbase: {vmt[3]:,.0f} M vehicle-miles / {fuel[3]:,.0f} thousand gallons"},
        "light_driver_wage_hourly_usd": {"value": light_h, "source": f"BLS OEWS {light_year}, SOC 53-3033 Light Truck Drivers, national mean hourly wage"},
        "box_lease_per_day_usd": {"value": 60.0, "source": small + " (a 26' box truck on a full-service lease)"},
        "box_insurance_per_day_usd": {"value": 15.0, "source": small},
        "box_repair_maintenance_per_mile_usd": {"value": 0.12, "source": small},
        "box_tires_per_mile_usd": {"value": 0.03, "source": small},
        "van_lease_per_day_usd": {"value": 30.0, "source": small + " (a high-roof cargo van on a lease)"},
        "van_insurance_per_day_usd": {"value": 10.0, "source": small},
        "van_repair_maintenance_per_mile_usd": {"value": 0.08, "source": small},
        "van_tires_per_mile_usd": {"value": 0.02, "source": small},
        "reefer_unit_gallons_per_hour": {"value": 0.6, "source": "assumption: no public source fetched (a trailer reefer unit running, loaded)"},
        "lodging_per_night_usd": {"value": 110.0, "source": "assumption: about the GSA standard lodging rate; a driver without a sleeper cab resting away from home"},
        "driver_wage_hourly_usd": {"value": wage_h, "source": f"BLS OEWS {wage_year}, SOC 53-3032, national mean hourly wage"},
        "driver_wage_annual_usd": {"value": wage_y, "source": f"BLS OEWS {wage_year}, SOC 53-3032, national mean annual wage"},
        "driver_benefits_load": {"value": 0.30, "source": assumption + " (benefits as a share on top of wages)"},
        "repair_maintenance_per_mile_usd": {"value": ATRI_2023_PER_MILE["repair_maintenance"], "source": atri},
        "tires_per_mile_usd": {"value": ATRI_2023_PER_MILE["tires"], "source": atri},
        "truck_trailer_lease_per_day_usd": {"value": 100.0, "source": "assumption, in line with " + atri + ": truck/trailer payments $0.360/mi at about 100,000 miles a year"},
        "insurance_per_day_usd": {"value": 30.0, "source": "assumption, in line with " + atri + ": insurance $0.099/mi at about 100,000 miles a year"},
        "company_driver_pay_per_mile_usd": {"value": round(ATRI_2023_PER_MILE["driver_wages"] + ATRI_2023_PER_MILE["driver_benefits"], 3),
                                            "source": atri + ": driver wages $0.779 + benefits $0.188 per mile (a tractor's company driver is paid by the mile, every mile)"},
        "team_pay_factor": {"value": 1.25, "source": assumption + " (a team truck's pay per mile, split between its two drivers, against one solo driver's)"},
        "owner_operator_linehaul_share": {"value": 0.68, "source": "USA Truck's published owner-operator split, 68% of linehaul (DC Velocity, 2017); fuel surcharge and detention go to the owner in full"},
        "owner_operator_carrier_cost_per_day_usd": {"value": 45.0, "source": assumption + " (the carrier's trailer and cargo/liability insurance for a leased-on owner-operator)"},
        "owner_operator_cost_per_mile_usd": {"value": round(ATRI_2023_PER_MILE["repair_maintenance"] + ATRI_2023_PER_MILE["tires"] + 0.75 * ATRI_2023_PER_MILE["truck_trailer_payments"]
                                                             + 0.5 * ATRI_2023_PER_MILE["insurance"] + ATRI_2023_PER_MILE["permits_licenses"] + ATRI_2023_PER_MILE["tolls"], 3),
                                             "source": atri + ": an owner-operator's own costs per mile besides fuel (repair, tires, permits, tolls, 75% of truck/trailer payments for the tractor, 50% of insurance); the shares are assumptions"},
        "owner_operator_net_floor_per_mile_usd": {"value": 0.60, "source": assumption + " (the least an owner-operator clears per mile, after their own costs and fuel, to take a trip)"},
        "owner_operator_pay_per_mile_atri_usd": {"value": ATRI_2023_OWNER_OPERATOR_PAY_PER_MILE, "source": "ATRI 2024 Update, Table 16 (2023 contracted owner-operator pay per mile): for comparison"},
        "overhead_per_unit_per_day_usd": {"value": 60.0, "source": assumption + ": what ATRI's marginal costs leave out (dispatch, the office, safety, "
                                          "IT), set so the re-planner's operating margins land near ATRI's truckload margins (2024 Update, Table 21: "
                                          "10% in 2021, 8% in 2022, 3% in 2023). The same for every seat and week, so it changes no score"},
        "temp_driver_hourly_usd": {"value": 40.0, "source": assumption + " (a staffing agency's bill rate for a CDL-A driver)"},
        "temp_driver_minimum_hours_per_day": {"value": 8.0, "source": assumption},
        "detention_free_hours": {"value": 2.0, "source": assumption + " (common contract term)"},
        "detention_rate_per_hour_usd": {"value": 75.0, "source": assumption + " (paid by the shipper to the carrier)"},
        "tonu_fee_usd": {"value": 150.0, "source": assumption + " (truck ordered, not used)"},
    }


# ---------------------------------------------------------------------------------------------------------------
# The 3D viewer's road shapes: decoration only (the sim and the grading never read them), built from the fetched routes.
ROAD_SHAPE_TOLERANCE_DEG = 0.00003              # about 3 m: the trailer's close shots put trucks on aerial imagery's lanes


def build_road_shapes() -> dict:
    """Each corridor's full OSRM route shape (its steps' geometry; the sim keeps only the simplified overview), for the
    3D viewer, which puts a truck `mile` of `miles` along the edge at that share of this line's length."""
    edges = []
    for a, b, _ in EDGES:
        route = json.loads((RAW / f"osrm/{a}-{b}.json").read_text())["routes"][0]
        pts: list[tuple[float, float]] = []
        for leg in route["legs"]:
            for step in leg["steps"]:
                for lon, lat in step["geometry"]["coordinates"]:
                    if not pts or (lon, lat) != pts[-1]:
                        pts.append((lon, lat))
        edges.append({"a": a, "b": b, "coords": [[round(lo, 5), round(la, 5)] for lo, la in _simplify(pts, ROAD_SHAPE_TOLERANCE_DEG)]})
    return {"note": "Viewer only: each edge's OSRM route as [lon, lat] from a to b, simplified to `tolerance_deg`.",
            "tolerance_deg": ROAD_SHAPE_TOLERANCE_DEG, "edges": edges}


def _simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative; the first and last points are kept."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = pts[i], pts[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        best, k = -1.0, -1
        for m in range(i + 1, j):
            px, py = pts[m]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > best:
                best, k = d, m
        if k > 0 and best > tol:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, kept in zip(pts, keep) if kept]


def write_json(name: str, obj) -> Path:
    path = OUT / name
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    return path


def build() -> None:
    retrieved = load_retrieved()

    def inputs(*names: str) -> list[dict]:
        missing = [n for n in names if n not in retrieved]
        if missing:
            raise FileNotFoundError(f"run `fetch` first; not in data/raw/retrieved.json: {missing}")
        return [{"file": n, **retrieved[n]} for n in names]

    cities = cities_from_raw()
    roads, road_warnings = build_roads(cities)
    weather = build_weather(roads)
    outputs = {
        "roads.json": (roads, ["census", "osm"], inputs("census_places_2023.zip", "census_counties_2023.zip",
                                                        *[f"osrm/{a}-{b}.json" for a, b, _ in EDGES])),
        "diesel.json": (build_diesel(), ["eia"], inputs("eia_diesel_padd2.xls", "eia_diesel_padd3.xls")),
        "freight.json": (build_freight(cities), ["faf", "bts"], inputs("FAF5.7.1_2018-2024.zip", "bts_border_trucks_tx.csv")),
        "weather.json": (weather, ["noaa", "census"],
                         inputs(*[f"noaa_storm_details_{y}.csv.gz" for y in STORM_YEARS], "census_counties_2023.zip")),
        "closures.json": (build_closures(roads, weather), ["noaa"], inputs(*[f"noaa_storm_details_{y}.csv.gz" for y in STORM_YEARS])),
        "hos.json": (build_hos(), ["ecfr"], inputs("ecfr_49_395.3.xml")),
        "road_shapes.json": (build_road_shapes(), ["osm"], inputs(*[f"osrm/{a}-{b}.json" for a, b, _ in EDGES])),
        "costs.json": (build_costs(), ["fhwa", "bls", "atri"], inputs("fhwa_vm1_2023.html", "bls_hourly_mean.json",
                                                                     "bls_annual_mean.json", "bls_light_hourly_mean.json",
                                                                     *[n for n in CITED_ONLY if n in retrieved])),
    }
    manifest = {"built_from": "data/raw/ (see each entry's inputs); rebuild with scripts/build_data.py build",
                "assumptions": [
                    "The board (cities, corridors, which cities generate freight) is a design choice in scripts/build_data.py.",
                    "Fort Worth is a waypoint and its freight is Dallas's: FAF5 has one Dallas-Fort Worth zone.",
                    "Waco, College Station, Lufkin, Victoria, Lafayette, Shreveport, Texarkana, Little Rock and Jackson are waypoints: their FAF5 zones are whole states or remainders of states.",
                    "McAllen carries only the Rio Grande Valley ports' share (BTS truck crossings, inbound, used both ways) of FAF zone 489's truck trade with Mexico; its domestic freight is left out.",
                    "A road crosses a county where its line passes within the radius of a circle of the county's land area around its Census internal point.",
                    f"Reefer freight is SCTG2 {sorted(REEFER_SCTG2)}.",
                    "Road miles and geometry are OSRM's car-profile routes between Census place points; the sim sets truck speeds itself.",
                    "costs.json entries marked 'assumption' have no fetched source yet.",
                ],
                "road_warnings": road_warnings, "files": {}}
    for name, (obj, licence_keys, ins) in outputs.items():
        path = write_json(name, obj)
        manifest["files"][name] = {"sha256": sha256(path),
                                   "sources": [{"name": LICENSES[k][0], "licence": LICENSES[k][1]} for k in licence_keys],
                                   "inputs": ins}
    write_json("manifest.json", manifest)
    for w in road_warnings:
        print("warning:", w)
    print("built:", ", ".join(manifest["files"]))


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase not in ("fetch", "build", "all"):
        sys.exit(__doc__)
    if phase in ("fetch", "all"):
        fetch()
    if phase in ("build", "all"):
        build()
