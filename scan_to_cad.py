"""Prepare sheets and turn interpreted, calibrated geometry into editable CAD."""
import argparse
import json
import math
import os
import tempfile
from pathlib import Path

import cv2
import ezdxf
import numpy as np
from ezdxf import path as cad_path
from ezdxf.math import ConstructionArc
from PIL import Image, ImageDraw


def prepare(source, page, dpi, folder):
    if page < 1 or dpi <= 0:
        raise ValueError("Page and DPI must be positive")
    if source.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(source) as pdf:
            if not 1 <= page <= len(pdf):
                raise ValueError("Page number is outside this PDF")
            pix = pdf[page - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            gray = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    else:
        with Image.open(source) as img:
            img.seek(page - 1)
            gray = img.convert("L")
    folder.mkdir(parents=True, exist_ok=True)
    gray.save(folder / "scan.png")
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(12, 12)).apply(np.asarray(gray))
    Image.fromarray(enhanced).save(folder / "contrast.png")


def pair(values):
    x, y = values
    if not all(math.isfinite(v) for v in (x, y)):
        raise ValueError("Nonfinite coordinate")
    return complex(x, y)


class Registration:
    def __init__(self, model):
        a, b = map(pair, model["calibration"]["px"])
        c, d = map(pair, model["calibration"]["mm"])
        if a == b or c == d:
            raise ValueError("Coincident calibration points")
        self.factor = (d - c) / (b - a).conjugate()
        self.origin = c - self.factor * a.conjugate()

    def cad(self, xy):
        z = self.origin + self.factor * pair(xy).conjugate()
        return z.real, z.imag

    def pixel(self, xy):
        z = ((pair(xy) - self.origin) / self.factor).conjugate()
        return z.real, z.imag


def dimensions(model, reg):
    checks = model.get("checks", [])
    for check in checks:
        if not math.isfinite(check["mm"]) or check["mm"] <= 0:
            raise ValueError("Expected a positive check dimension")
        if not math.isfinite(check["tolerance_mm"]) or check["tolerance_mm"] < 0:
            raise ValueError("Invalid check tolerance")
        a, b = map(reg.cad, check["px"])
        measured = math.dist(a, b)
        error = abs(measured - check["mm"])
        print(f'{check["label"]}: {measured:.2f} mm, deviation {error:.2f} mm')
        if error > check["tolerance_mm"]:
            raise ValueError("Check dimension exceeds tolerance")
    if not checks:
        print("Scale has no independent dimension check on this image.")


def add_entity(space, item, reg):
    kind = item["type"]
    if ("mm" in item) == ("px" in item):
        raise ValueError("Choose one coordinate system per entity")
    pts = item["mm"] if "mm" in item else [reg.cad(p) for p in item["px"]]
    if any(not all(math.isfinite(v) for v in pt) or len(pt) != 2 for pt in pts):
        raise ValueError("Invalid point")
    attr = {"layer": item["layer"]}
    if kind == "insert" and len(pts) == 1:
        if item["name"] not in space.doc.blocks:
            raise ValueError("Undefined block: " + item["name"])
        return space.add_blockref(item["name"], pts[0], dxfattribs={
            **attr, "rotation": item.get("rotation", 0)})
    if kind == "polyline" and len(pts) >= 2:
        return space.add_lwpolyline(pts, close=item.get("closed", False), dxfattribs=attr)
    if kind == "line" and len(pts) == 2:
        return space.add_line(*pts, dxfattribs=attr)
    if kind == "arc" and len(pts) == 3:
        start, through, end = pts
        cross = ((through[0] - start[0]) * (end[1] - start[1])
                 - (through[1] - start[1]) * (end[0] - start[0]))
        curve = ConstructionArc.from_3p(start, end, through, ccw=cross > 0)
        return space.add_arc(curve.center, curve.radius, curve.start_angle, curve.end_angle,
                             dxfattribs=attr)
    if kind == "circle" and len(pts) == 2:
        radius = math.dist(*pts)
        if radius <= 0:
            raise ValueError("Zero radius")
        return space.add_circle(pts[0], radius, dxfattribs=attr)
    raise ValueError(f"Unsupported entity or wrong point count: {kind}")


def build(source, recipe, output):
    if output.suffix.lower() not in {".dwg", ".dxf"}:
        raise ValueError("Choose a DWG or DXF output")
    overlay = output.with_name(output.stem + "-overlay.png")
    if source.resolve() in {overlay.resolve(), output.resolve()}:
        raise ValueError("Output overlaps source")
    model = json.loads(recipe.read_text(encoding="utf-8-sig"))
    with Image.open(source) as img:
        if list(img.size) != model["image_size"]:
            raise ValueError("Image dimensions changed since point selection")
        review = img.convert("RGB")
    reg = Registration(model)
    dimensions(model, reg)
    doc = ezdxf.new("R2018", units=ezdxf.units.MM)
    for name, props in model["layers"].items():
        doc.layers.add(name, color=props["aci"], lineweight=props["weight"])
    for name, shapes in model.get("blocks", {}).items():
        block = doc.blocks.new(name)
        for shape in shapes:
            if "mm" not in shape or shape["type"] == "insert":
                raise ValueError("Block shapes require local millimetres and primitive geometry")
            add_entity(block, dict(shape, layer="0"), reg)
    space = doc.modelspace()
    draw = ImageDraw.Draw(review)
    for item in model["entities"]:
        if item["layer"] not in model["layers"]:
            raise ValueError(f'Undefined layer: {item["layer"]}')
        ent = add_entity(space, item, reg)
        shapes = ent.virtual_entities() if ent.dxftype() == "INSERT" else [ent]
        for shape in shapes:
            vertices = cad_path.make_path(shape).flattening(abs(reg.factor) * 0.25)
            pixels = [reg.pixel((pt.x, pt.y)) for pt in vertices]
            draw.line(pixels, fill=model["layers"][item["layer"]]["overlay"], width=2)
    if not len(space):
        raise ValueError("Empty drawing")
    output.parent.mkdir(parents=True, exist_ok=True)
    from ezdxf import zoom
    zoom.extents(space)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        folder = Path(temp)
        dxf = folder / "model.dxf"
        doc.saveas(dxf)
        review.save(folder / "overlay.png")
        if output.suffix.lower() == ".dwg":
            from verify_dwg import save_verified
            save_verified(dxf, output, folder / "readback.dxf")
        else:
            os.replace(dxf, output)
        os.replace(folder / "overlay.png", overlay)
    print(f"{len(space)} editable entities; {output.name}; {overlay.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("source", type=Path)
    p.add_argument("folder", type=Path)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=int, default=300)
    b = sub.add_parser("build")
    b.add_argument("scan", type=Path)
    b.add_argument("recipe", type=Path)
    b.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.source, args.page, args.dpi, args.folder)
    else:
        build(args.scan, args.recipe, args.output)


if __name__ == "__main__":
    main()
