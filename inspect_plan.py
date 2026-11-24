"""Показать состав конкретного листа или определения блока AutoCAD."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import ezdxf
from ezdxf.lldxf.const import VSF_VIEWPORT_ZOOM_LOCKING


def print_plot_info(space, layers, explicit_colors):
    print('Свойства слоёв в DWG:')
    for name in sorted(layers):
        if name not in space.doc.layers:
            print(f'  {name}: определение слоя отсутствует')
            continue
        layer = space.doc.layers.get(name)
        props = [f'ACI {layer.color}', layer.dxf.linetype, f'вес DXF {layer.dxf.lineweight}']
        if layer.rgb is not None:
            props.append(f'RGB {tuple(layer.rgb)}')
        if layer.is_off():
            props.append('выключен')
        if layer.is_frozen():
            props.append('заморожен')
        if not layer.dxf.plot:
            props.append('печать отключена')
        if explicit_colors[name]:
            props.append(f'явный цвет у {explicit_colors[name]} объектов')
        print(f'  {name}: ' + ', '.join(props))
    if not space.is_any_paperspace or space.is_block_layout:
        return
    d = space.dxf
    styles = 'включены' if d.plot_layout_flags & space.PLOT_PLOTSTYLES else 'выключены'
    print(f'Лист: {d.plot_configuration_file or "устройство не задано"}, '
          f'{d.paper_size or "формат не задан"}, таблица стилей: '
          f'{d.current_style_sheet or "не задана"}, стили печати {styles}.')
    for vp in space.query('VIEWPORT'):
        if vp.dxf.id == 1:  # системный экран пространства листа
            continue
        frozen = ', '.join(sorted(vp.frozen_layers)) or 'нет'
        print(f'  Видовой экран {vp.dxf.handle}: замороженные слои — {frozen}')
        lock = 'да' if vp.dxf.flags & VSF_VIEWPORT_ZOOM_LOCKING else 'нет'
        print(f'    лист/модель = {vp.get_scale():.8g}, поворот вида = {vp.dxf.view_twist_angle:g}°, '
              f'масштаб заблокирован: {lock}')


def inspect(space, plot=False):
    layers = defaultdict(Counter)
    inserts = Counter()
    explicit_colors = Counter()
    for entity in space:
        kind = entity.dxftype()
        counts = layers[entity.dxf.layer]
        counts[kind] += 1
        if plot and (entity.dxf.hasattr('true_color') or entity.dxf.get('color', 256) not in (0, 256)):
            explicit_colors[entity.dxf.layer] += 1
        if kind == 'HATCH':
            counts['контуров HATCH'] += len(entity.paths)
        elif kind == 'INSERT':
            inserts[entity.dxf.name] += 1
    print(f'{space.name} — {len(space)} объектов непосредственно в листе или определении блока.')
    for layer, counts in sorted(layers.items()):
        print(f'  {layer}: ' + ', '.join(f'{k}={n}' for k, n in counts.most_common()))
    if inserts:
        print('Вложенные вставки. Для проверки содержимого используйте --block с именем:')
        for name, count in inserts.most_common():
            print(f'  {count} × {name}')
    if any(e.dxftype() == 'VIEWPORT' and e.dxf.id > 1 for e in space):
        print('План в видовом экране проверяйте в соответствующей области модели.')
    if plot:
        print_plot_info(space, layers, explicit_colors)


def print_linear_dimensions(space):
    dimensions = [d for d in space.query('DIMENSION') if d.dimtype in (0, 1)]
    print(f'{space.name}: линейных и параллельных размеров — {len(dimensions)}.')
    for dimension in dimensions:
        d = dimension.dxf
        override = d.get('text', '')
        if not override or override == '<>':
            label = 'автоматический текст'
        elif not override.strip():
            label = 'текст скрыт'
        elif '<>' in override:
            label = f'шаблон {override!r}'
        else:
            label = f'текст задан вручную {override!r}'
        geometry = dimension.get_geometry_block()
        strings = []
        if geometry is not None:
            for entity in geometry.query('TEXT MTEXT'):
                strings.append(entity.plain_text())
        text = ' | '.join(strings) or 'нет текста в графике размера'
        try:
            factor = dimension.override().get('dimlfac', 1.0)
        except ezdxf.DXFTableEntryError:
            factor = 'стиль отсутствует'
        print(f'  {d.handle} [{d.layer}]: измерено {dimension.get_measurement():.12g} ед.; '
              f'стиль {d.dimstyle}, DIMLFAC={factor}; {label}; в DWG: {text}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drawing', type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--layout', help='имя листа или Model')
    group.add_argument('--block', help='имя определения блока с планом')
    parser.add_argument('--plot', action='store_true', help='показать свойства слоёв и параметры печати листа')
    parser.add_argument('--dims', action='store_true', help='сверить измерения и сохранённые надписи линейных размеров')
    parser.add_argument('--route', nargs='+', metavar='HANDLE', help='проверить объекты трассы в порядке обхода')
    parser.add_argument('--start', nargs=2, type=float, metavar=('X', 'Y'), help='начальная точка трассы')
    parser.add_argument('--end', nargs=2, type=float, metavar=('X', 'Y'), help='конечная точка трассы')
    parser.add_argument('--tol', type=float, help='допуск стыка в единицах проверяемого чертежа')
    args = parser.parse_args()
    if args.route and (args.start is None or args.end is None or args.tol is None):
        parser.error('Для --route задайте --start X Y, --end X Y и --tol')
    if not args.route and any(arg is not None for arg in (args.start, args.end, args.tol)):
        parser.error('--start, --end и --tol относятся к --route')
    with TemporaryDirectory(prefix='eom_inspect_') as temp:
        path = args.drawing
        if path.suffix.lower() == '.dwg':
            import acad
            path = Path(temp) / 'copy.dxf'
            acad.dwg2dxf(args.drawing, path)
        doc = ezdxf.readfile(path)
        if args.block is not None:
            space = doc.blocks.get(args.block)
            if space is None:
                parser.error(f'Блок «{args.block}» отсутствует')
        else:
            try:
                space = doc.layouts.get(args.layout)
            except KeyError:
                parser.error('Доступные листы: ' + ', '.join(doc.layout_names()))
        if args.plot or not (args.route or args.dims):
            inspect(space, plot=args.plot)
        if args.dims:
            print_linear_dimensions(space)
        if args.route:
            from route_check import check_route
            try:
                check_route(space, args.route, args.start, args.end, args.tol)
            except ValueError as error:
                parser.exit(1, f'Трасса не подтверждена: {error}\n')


if __name__ == '__main__':
    main()
