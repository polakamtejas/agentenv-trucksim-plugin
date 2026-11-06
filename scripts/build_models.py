"""Build the 3D viewer's vehicles (viewer/cinema.html) from Kenney's Car Kit 3.1 (CC0).

Kenney's models are coloured by one shared palette texture (colormap.png): every face's UVs point into a strip of it,
and each strip is a top-to-bottom gradient. So a seat's colour is a palette with the body strips swapped, and a model
we add ourselves looks like the kit when its faces sample the same strips.

  box    Kenney's "delivery" (a box truck), its box in the seat's colour
  van    Kenney's "van", its body in the seat's colour
  semi   a tractor and 53' dry trailer assembled here from boxes on the kit's palette, with the kit's truck wheels
  reefer the same with a refrigeration unit on the trailer's nose

Every model is written for each seat (A, B, C) as one self-contained GLB (the palette embedded), forward along +Z
and up along +Y as in the kit, in the kit's units (a box truck is 3.25 long; the semi is 8.5).

Usage (from the repo root): .venv/bin/python scripts/build_models.py
Needs: data/raw/kenney_car-kit_3.1.zip (scripts/build_data.py fetch) and pillow (the `video` extra).
-> src/agentenv_trucksim/viewer/models/<model>-<seat>.glb and SOURCES.json"""

import hashlib
import io
import json
import struct
import sys
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "raw" / "kenney_car-kit_3.1.zip"
OUT = ROOT / "src" / "agentenv_trucksim" / "viewer" / "models"
GLB_DIR = "Models/GLB format/"

# The palette is 16 strips across (32 px each; even strips flat, odd ones a gradient) by 4 rows (128 px each).
STRIP_PX, ROW_PX = 32, 128
SEAT_STRIP = {"A": (1, 15), "B": (1, 11), "C": (1, 7)}      # (row, gradient strip): blue, orange, green
BODY_STRIPS = [(1, 7), (1, 15)]   # the delivery's box is row 1 strip 7, the van's body row 1 strip 15: both become the seat's

# Strips the semi samples, named. "seat" is row 1 strip 15, which the seat's palette repaints.
SEAT, WHITE, GREY, DARK, STEEL, GLASS, AMBER, RED = (1, 15), (2, 13), (2, 7), (2, 5), (2, 11), (3, 1), (3, 3), (3, 5)
SCALE = {"box": 1.25, "van": 1.1, "semi": 1.0, "reefer": 1.0}   # the kit's cars are toy-sized next to a 53' trailer


def palette_for(seat: str, base: Image.Image) -> bytes:
    im = base.copy()
    row, strip = SEAT_STRIP[seat]
    for flat_too in (0, 1):     # the flat strip left of each gradient too, so every face of the body changes
        src = im.crop(((strip - flat_too) * STRIP_PX, row * ROW_PX, (strip - flat_too + 1) * STRIP_PX, (row + 1) * ROW_PX))
        for r, s in BODY_STRIPS:
            im.paste(src, ((s - flat_too) * STRIP_PX, r * ROW_PX))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---- reading the kit's GLBs ------------------------------------------------------------------------------------------
def read_glb(data: bytes) -> tuple[dict, bytes]:
    magic, _, _ = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("not a GLB")
    jlen, _ = struct.unpack_from("<II", data, 12)
    gltf = json.loads(data[20:20 + jlen])
    blen, _ = struct.unpack_from("<II", data, 20 + jlen)
    return gltf, data[28 + jlen:28 + jlen + blen]


def accessor(gltf: dict, binary: bytes, i: int) -> list[tuple]:
    a = gltf["accessors"][i]
    view = gltf["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    fmt = {5126: "f", 5123: "H", 5125: "I", 5121: "B"}[a["componentType"]]
    size = struct.calcsize(fmt) * n
    stride = view.get("byteStride", size)
    start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    return [struct.unpack_from("<" + fmt * n, binary, start + k * stride) for k in range(a["count"])]


def kit_parts(gltf: dict, binary: bytes) -> list[dict]:
    """The model's nodes as parts: {name, translation, rotation, positions, normals, uvs, indices} (no tangents: the
    viewer's flat lighting doesn't use them)."""
    parts = []
    for node in gltf["nodes"]:
        if "mesh" not in node:
            continue
        for prim in gltf["meshes"][node["mesh"]]["primitives"]:
            at = prim["attributes"]
            parts.append({"name": node.get("name", "part"), "translation": node.get("translation"), "rotation": node.get("rotation"),
                          "positions": accessor(gltf, binary, at["POSITION"]), "normals": accessor(gltf, binary, at["NORMAL"]),
                          "uvs": accessor(gltf, binary, at["TEXCOORD_0"]), "indices": [i[0] for i in accessor(gltf, binary, prim["indices"])]})
    return parts


# ---- the semi --------------------------------------------------------------------------------------------------------
TOP = 2.4   # the semi's height, for its top-to-bottom gradient (the kit's faces get lighter toward the top)


def uv(strip: tuple[int, int], y: float) -> tuple[float, float]:
    row, s = strip
    shade = 0.04 + 0.92 * (1 - max(0.0, min(1.0, y / TOP)))
    return ((s * STRIP_PX + STRIP_PX / 2) / 512, (row * ROW_PX + shade * ROW_PX) / 512)


def box(x0, x1, y0, y1, z0, z1, strip) -> dict:
    """An axis-aligned box as a part: 6 faces of 4 vertices, outward normals, counter-clockwise from outside."""
    faces = [((1, 0, 0), [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)]),
             ((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
             ((0, 1, 0), [(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)]),
             ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
             ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
             ((0, 0, -1), [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)])]
    pos, nor, uvs, idx = [], [], [], []
    for n, quad in faces:
        b = len(pos)
        for p in quad:
            pos.append(p)
            nor.append(n)
            uvs.append(uv(strip, p[1]))
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return {"positions": pos, "normals": nor, "uvs": uvs, "indices": idx}


def merge(name: str, boxes: list[dict]) -> dict:
    out = {"name": name, "translation": None, "rotation": None, "positions": [], "normals": [], "uvs": [], "indices": []}
    for b in boxes:
        base = len(out["positions"])
        for k in ("positions", "normals", "uvs"):
            out[k] += b[k]
        out["indices"] += [base + i for i in b["indices"]]
    return out


def both_sides(x0, x1, *rest) -> list[dict]:
    """A box at x0..x1 and its mirror at -x1..-x0."""
    return [box(x0, x1, *rest), box(-x1, -x0, *rest)]


def semi(reefer: bool, wheel: dict) -> list[dict]:
    body = [
        box(-0.45, 0.45, 0.25, 0.45, -4.0, 4.1, DARK),               # frame
        box(-0.75, 0.75, 0.45, 1.85, 2.55, 3.75, SEAT),              # cab
        box(-0.66, 0.66, 0.45, 1.2, 3.75, 4.2, SEAT),                # hood
        box(-0.42, 0.42, 0.55, 1.1, 4.2, 4.24, GREY),                # grille
        box(-0.74, 0.74, 0.3, 0.55, 4.1, 4.3, STEEL),                # bumper
        *both_sides(0.48, 0.66, 0.72, 0.9, 4.19, 4.26, AMBER),       # headlights
        box(-0.64, 0.64, 1.22, 1.7, 3.75, 3.79, GLASS),              # windscreen
        *both_sides(0.75, 0.77, 1.22, 1.65, 3.0, 3.6, GLASS),        # side windows
        box(-0.75, 0.75, 0.45, 2.05, 1.7, 2.55, SEAT),               # sleeper
        box(-0.7, 0.7, 1.85, 2.05, 2.55, 3.2, SEAT),                 # roof fairing
        *both_sides(0.78, 0.86, 0.7, 2.35, 2.45, 2.53, STEEL),       # exhaust stacks
        *both_sides(0.55, 0.8, 0.28, 0.62, 1.8, 2.5, STEEL),         # fuel tanks
        box(-0.5, 0.5, 0.45, 0.55, 1.0, 1.7, DARK),                  # fifth wheel
        box(-0.75, 0.75, 0.62, 2.2, -4.25, 1.45, WHITE),             # trailer
        *both_sides(0.75, 0.765, 0.55, 0.68, -4.25, 1.45, GREY),     # bottom rails
        *both_sides(0.75, 0.77, 1.15, 1.45, -3.9, 1.1, SEAT),        # the seat's stripe
        box(-0.75, 0.75, 0.62, 2.2, -4.28, -4.25, STEEL),            # rear doors
        *both_sides(0.5, 0.7, 0.75, 0.88, -4.31, -4.27, RED),        # tail lights
        *both_sides(0.48, 0.58, 0.2, 0.62, 0.55, 0.68, DARK),        # landing gear
    ]
    if reefer:
        body += [box(-0.55, 0.55, 1.05, 2.0, 1.45, 1.78, STEEL), box(-0.42, 0.42, 1.2, 1.85, 1.78, 1.8, GREY)]
    parts = [merge("body", body)]
    for z in (3.85, 2.25, 1.6, -3.0, -3.65):       # steer axle, the drive tandem, the trailer tandem
        for side, rot in ((0.6, None), (-0.6, [0.0, 1.0, 0.0, 0.0])):   # the left wheels turned to face out
            parts.append({**wheel, "name": f"wheel-{z}-{'r' if side > 0 else 'l'}", "translation": [side, 0.3, z], "rotation": rot})
    return parts


# ---- writing GLBs ----------------------------------------------------------------------------------------------------
def write_glb(path: Path, name: str, parts: list[dict], png: bytes, scale: float) -> None:
    blob = bytearray()
    views, accessors, meshes, nodes = [], [], [], []

    def add(data: bytes, target: int | None) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), **({"target": target} if target else {})})
        blob.extend(data)
        return len(views) - 1

    def vec_accessor(rows: list[tuple], kind: str) -> int:
        flat = [v for r in rows for v in r]
        view = add(struct.pack(f"<{len(flat)}f", *flat), 34962)
        n = len(rows[0])
        acc = {"bufferView": view, "componentType": 5126, "count": len(rows), "type": kind}
        if kind == "VEC3":
            acc["min"] = [min(r[k] for r in rows) for k in range(n)]
            acc["max"] = [max(r[k] for r in rows) for k in range(n)]
        accessors.append(acc)
        return len(accessors) - 1

    mesh_of = {}                  # parts with the same geometry (the semi's wheels) share one mesh
    for part in parts:
        key = id(part["positions"])
        if key not in mesh_of:
            idx = part["indices"]
            iv = add(struct.pack(f"<{len(idx)}H", *idx), 34963)
            accessors.append({"bufferView": iv, "componentType": 5123, "count": len(idx), "type": "SCALAR"})
            ia = len(accessors) - 1
            attrs = {"POSITION": vec_accessor(part["positions"], "VEC3"), "NORMAL": vec_accessor(part["normals"], "VEC3"),
                     "TEXCOORD_0": vec_accessor(part["uvs"], "VEC2")}
            meshes.append({"name": part["name"], "primitives": [{"attributes": attrs, "indices": ia, "material": 0}]})
            mesh_of[key] = len(meshes) - 1
        node = {"name": part["name"], "mesh": mesh_of[key]}
        if part.get("translation"):
            node["translation"] = list(part["translation"])
        if part.get("rotation"):
            node["rotation"] = list(part["rotation"])
        nodes.append(node)
    image_view = add(png, None)
    nodes.append({"name": name, "children": list(range(len(nodes))), "scale": [scale] * 3})
    gltf = {"asset": {"version": "2.0", "generator": "TruckSimEnv scripts/build_models.py (from Kenney's Car Kit 3.1, CC0)"},
            "scene": 0, "scenes": [{"name": name, "nodes": [len(nodes) - 1]}], "nodes": nodes, "meshes": meshes,
            "materials": [{"name": "colormap", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
            "textures": [{"sampler": 0, "source": 0}], "samplers": [{"magFilter": 9729, "minFilter": 9987}],
            "images": [{"bufferView": image_view, "mimeType": "image/png", "name": "colormap"}],
            "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    while len(blob) % 4:
        blob.append(0)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    path.write_bytes(out)


def main() -> None:
    if not KIT.exists():
        sys.exit(f"missing {KIT.relative_to(ROOT)}: run scripts/build_data.py fetch")
    with zipfile.ZipFile(KIT) as z:
        base = Image.open(io.BytesIO(z.read(GLB_DIR + "Textures/colormap.png"))).convert("RGBA")
        kit = {n: kit_parts(*read_glb(z.read(GLB_DIR + f"{n}.glb"))) for n in ("delivery", "van", "wheel-truck")}
        licence = z.read("License.txt").decode("utf-8", "replace")
    wheel = kit["wheel-truck"][0]
    models = {"box": kit["delivery"], "van": kit["van"], "semi": semi(False, wheel), "reefer": semi(True, wheel)}
    OUT.mkdir(parents=True, exist_ok=True)
    written = {}
    for seat in sorted(SEAT_STRIP):
        png = palette_for(seat, base)
        for name, parts in models.items():
            path = OUT / f"{name}-{seat}.glb"
            write_glb(path, f"{name}-{seat}", parts, png, SCALE[name])
            written[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    sources = {"note": "Built by scripts/build_models.py; the box truck, van, wheels and palette are Kenney's, the semi is "
                       "assembled from boxes on Kenney's palette. Kenney's licence is CC0; crediting is optional.",
               "input": {"file": KIT.name, "sha256": hashlib.sha256(KIT.read_bytes()).hexdigest(),
                         "url": "https://opengameart.org/sites/default/files/kenney_car-kit_3.1.zip",
                         "licence": "CC0 1.0 (Kenney, www.kenney.nl)", "licence_text": licence.strip()},
               "files": written}
    (OUT / "SOURCES.json").write_text(json.dumps(sources, indent=1) + "\n")
    print(f"wrote {len(written)} models to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
