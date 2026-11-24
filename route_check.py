"""Проверка последовательности объектов одной трассы в координатах выбранного пространства."""
from math import isfinite

from ezdxf.math import Vec3


def route_ends(space, token):
    handle, *section = token.upper().split(':')
    entity = space.doc.entitydb.get(handle)
    if entity is None or entity.dxf.owner != space.layout_key:
        raise ValueError(f'{handle}: объект отсутствует в «{space.name}»')
    kind = entity.dxftype()
    if kind == 'LINE':
        points = [entity.dxf.start, entity.dxf.end]
    elif kind == 'ARC':
        points = [entity.start_point, entity.end_point]
    elif kind == 'LWPOLYLINE' and not entity.closed:
        points = list(entity.vertices_in_wcs())
    else:
        raise ValueError(f'{handle}: требуется LINE, ARC или открытая LWPOLYLINE')
    if len(points) < 2:
        raise ValueError(f'{handle}: недостаточно вершин')
    if not section:
        return points[0], points[-1]
    if len(section) != 2 or not all(s.isdecimal() for s in section):
        raise ValueError(f'{token}: укажите handle или handle:i:j с номерами вершин от 0')
    first, last = map(int, section)
    if first == last or max(first, last) >= len(points):
        raise ValueError(f'{token}: проверьте номера вершин (доступны 0…{len(points)-1})')
    return points[first], points[last]


def check_route(space, tokens, start, end, tolerance):
    if not isfinite(tolerance) or tolerance < 0 or not all(isfinite(v) for v in (*start, *end)):
        raise ValueError('Для точек нужны конечные координаты, для допуска — конечное число ≥ 0')
    reachable = [Vec3(start)]
    previous = 'начало'
    for token in tokens:
        a, b = route_ends(space, token)
        next_points = []
        gaps = []
        for entry, exit_point in ((a, b), (b, a)):
            gap = min(p.distance(entry) for p in reachable)
            gaps.append(gap)
            if gap <= tolerance:
                next_points.append(exit_point)
        if not next_points:
            raise ValueError(f'{previous} → {token}: расстояние между ближайшими допустимыми концами {min(gaps):.6g} ед.')
        reachable, previous = next_points, token
    gap = min(p.distance(Vec3(end)) for p in reachable)
    if gap > tolerance:
        raise ValueError(f'{previous} → конец: разрыв {gap:.6g} ед.')
    print(f'Участков в трассе: {len(tokens)}. Стыки и обе крайние точки в пределах {tolerance:g} ед.')
