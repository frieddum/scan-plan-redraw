"""Конвертация DWG <-> DXF через консольное ядро AutoCAD (accoreconsole.exe).

  python acad.py dwg2dxf <in.dwg> <out.dxf>
  python acad.py dxf2dwg <in.dxf> <out.dwg> [--ver 2018]

Грабли, которые тут обойдены:
  * Git Bash превращает аргументы /i и /s в пути (MSYS path conversion) — поэтому запуск
    только отсюда, через subprocess, а не напрямую из bash.
  * accoreconsole плохо переносит кириллицу/пробелы в пути скрипта — работаем в ASCII-папке
    %TEMP%\\eom_acad, файлы копируем туда и обратно.
  * после DXFOUT консоль иногда не закрывается — ждём результат с таймаутом и убиваем процесс;
    файл к этому моменту уже записан.
  * исходный файл пользователя никогда не открывается на запись: всегда работаем с копией.
"""
import glob, os, shutil, subprocess, sys, tempfile, time

def find_exe():
    c = sorted(glob.glob(r'C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe'))
    if not c:
        sys.exit('accoreconsole.exe не найден (нужен установленный AutoCAD)')
    return c[-1]

def _tmp():
    d = os.path.join(os.environ.get('TEMP', r'C:\Temp'), 'eom_acad')
    os.makedirs(d, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=d, prefix='job_')

def _run(inp, script, out, timeout):
    exe = find_exe()
    scr = os.path.join(os.path.dirname(inp), 'job.scr')
    with open(scr, 'w', encoding='ascii', newline='\r\n') as f:
        f.write(script)
    if os.path.exists(out): os.remove(out)
    p = subprocess.Popen([exe, '/i', inp, '/s', scr, '/l', 'ru-RU'],
                         cwd=os.path.dirname(inp),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time(); last = -1; stable = 0
    while time.time() - t0 < timeout:
        if p.poll() is not None: break
        if os.path.exists(out):
            sz = os.path.getsize(out)
            stable = stable + 1 if sz == last and sz > 0 else 0
            last = sz
            if stable >= 4: break          # файл записан и не растёт 2 с
        time.sleep(0.5)
    if p.poll() is None:
        p.kill()
        p.wait()
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        sys.exit('AutoCAD не создал ' + out)

def dwg2dxf(src, dst, timeout=300):
    with _tmp() as d:
        inp = os.path.join(d, 'in.dwg'); out = os.path.join(d, 'out.dxf')
        shutil.copyfile(src, inp)
        _run(inp, '_.DXFOUT\n%s\n16\n' % ('"'+out.replace('\\', '/')+'"'), out, timeout)
        shutil.copyfile(out, dst)

def dxf2dwg(src, dst, ver='2018', timeout=300):
    with _tmp() as d:
        inp = os.path.join(d, 'in.dxf'); out = os.path.join(d, 'out.dwg')
        shutil.copyfile(src, inp)
        _run(inp, '_.SAVEAS\n%s\n%s\n' % (ver, ('"'+out.replace('\\', '/')+'"')), out, timeout)
        shutil.copyfile(out, dst)

if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) < 3: sys.exit(__doc__)
    if a[0] == 'dwg2dxf': dwg2dxf(a[1], a[2])
    elif a[0] == 'dxf2dwg': dxf2dwg(a[1], a[2], a[a.index('--ver') + 1] if '--ver' in a else '2018')
    else: sys.exit(__doc__)
    print('ok', a[2], os.path.getsize(a[2]))
