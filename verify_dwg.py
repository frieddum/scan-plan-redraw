"""Сверка модели после экспорта. CLI: python verify_dwg.py result.dxf result_check.dxf"""
from collections import Counter
from pathlib import Path
import os
import sys
import tempfile

import ezdxf


def numbers(values):
    return tuple(round(float(v), 6) for v in values)


def signature(entity):
    d = entity.dxf
    kind = entity.dxftype()
    key = (kind, d.layer.casefold())
    if kind == 'INSERT':
        matrix = entity.matrix44()
        axes = tuple(numbers(matrix.transform_direction(axis))
                     for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        attributes = tuple(sorted((a.dxf.tag.casefold(), a.dxf.text) for a in entity.attribs))
        return (*key, d.name.casefold(), numbers(matrix.transform((0, 0, 0))), axes, attributes)
    if kind == 'LWPOLYLINE':
        vertices = tuple(numbers(p) for p in entity.get_points('xyseb'))
        return (*key, entity.closed, vertices, numbers((d.elevation, d.const_width)), numbers(d.extrusion),
                d.color, d.linetype.casefold())
    if kind == 'LINE':
        return (*key, tuple(sorted((numbers(d.start), numbers(d.end)))))
    if kind in ('ARC', 'CIRCLE'):
        key = (*key, numbers(entity.ocs().to_wcs(d.center)), round(d.radius, 6))
        if kind == 'ARC':
            return (*key, numbers(entity.start_point), numbers(entity.end_point), numbers(d.extrusion))
        return key
    if kind == 'TEXT':
        align, first, second = entity.get_placement()
        return (*key, d.text, align.name, numbers(first), numbers(second) if second is not None else (),
                numbers((d.height, d.rotation % 360)))
    if kind == 'MTEXT':
        return (*key, entity.plain_text(), numbers(d.insert))
    return key


def compare(expected_file, actual_file):
    expected, actual = (ezdxf.readfile(p) for p in (expected_file, actual_file))
    if expected.units != actual.units:
        raise ValueError(f'Изменились единицы модели: {expected.units} → {actual.units}')
    before, after = (Counter(signature(e) for e in doc.modelspace()) for doc in (expected, actual))
    if before != after:
        for label, diff in [('до', before - after), ('после', after - before)]:
            for entry, count in diff.most_common(3):
                print(f'{label}: {count} × {str(entry)[:320]}')
        raise ValueError('Состав или параметры объектов модели изменились при записи DWG')
    names = {e.dxf.name for e in expected.modelspace().query("INSERT")}
    for name in names:
        left = Counter(signature(e) for e in expected.blocks[name])
        right = Counter(signature(e) for e in actual.blocks[name])
        if left != right:
            raise ValueError(f"Геометрия блока {name} изменилась при записи DWG")
    print(f'Проверка DWG: совпали единицы и проверяемые параметры {after.total()} объектов модели.')


def save_verified(dxf, output, check):
    import acad

    output = Path(output)
    # Размещение рядом с результатом позволяет заменить его одной операцией.
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        candidate = Path(folder) / 'result.dwg'
        acad.dxf2dwg(dxf, candidate)
        acad.dwg2dxf(candidate, check)
        compare(dxf, check)
        os.replace(candidate, output)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    compare(*sys.argv[1:])
