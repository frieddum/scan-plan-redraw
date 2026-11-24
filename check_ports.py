"""Check placed device ports against explicit vertices on selected circuit layers."""
import argparse
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import ezdxf
from ezdxf.math import Vec3


def connection_points(entity):
    if entity.dxftype() == "LWPOLYLINE":
        return list(entity.vertices_in_wcs())
    if entity.dxftype() == "POLYLINE":
        return list(entity.points_in_wcs())
    if entity.dxftype() == "LINE":
        return [entity.dxf.start, entity.dxf.end]
    if entity.dxftype() == "ARC":
        return [entity.start_point, entity.end_point]
    return []


def inspect(space, spec):
    tolerance = float(spec["tolerance"])
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be a finite nonnegative number")
    if not spec["rules"]:
        raise ValueError("At least one device rule is required")
    failures = []
    for rule in spec["rules"]:
        name = rule["block"]
        layers = {layer.casefold() for layer in rule["wires"]}
        devices = [e for e in space.query("INSERT") if e.dxf.name.casefold() == name.casefold()
                   and e.dxf.layer.casefold() == rule["layer"].casefold()]
        if len(devices) != rule["count"] or not devices:
            failures.append(f'{name}: found {len(devices)}, expected {rule["count"]}')
        vertices = [Vec3(v) for e in space if e.dxf.layer.casefold() in layers
                    for v in connection_points(e)]
        for device in devices:
            terminal = device.matrix44().transform(Vec3(rule["port"]))
            gap = min((p.distance(terminal) for p in vertices), default=math.inf)
            if gap > tolerance:
                failures.append(f"{name} {device.dxf.handle}, port {terminal}: gap {gap:.3f}")
        print(f"{name}: inspected {len(devices)} inserts")
    if failures:
        raise ValueError("\n".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing", type=Path)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--layout", default="Model")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    with TemporaryDirectory() as tmp:
        path = args.drawing
        if path.suffix.lower() == ".dwg":
            from acad import dwg2dxf
            path = Path(tmp) / "readback.dxf"
            dwg2dxf(args.drawing, path)
        inspect(ezdxf.readfile(path).layouts.get(args.layout), spec)


if __name__ == "__main__":
    main()
