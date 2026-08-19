"""
Скрапер публикаций Новосибирскстата по форме 2-наука.

ЧТО ЭТО ЗА ДАННЫЕ. Форма № 2-наука («Сведения о выполнении научных
исследований и разработок», ОКУД 0604011) — годовой отчёт организаций в
Росстат. Сами отчёты респондентов не публикуются (ст. 4 п. 5 / ст. 9 п. 1
282-ФЗ, конфиденциальность первичных данных), поэтому скрапить нечего на уровне
организаций. Но её СВОДЫ по Новосибирской области территориальный орган
выкладывает открыто, разделом «Наука и инновации»:

    https://54.rosstat.gov.ru/folder/32625

Там 12 файлов, и это ровно разрезы формы 2-наука за 2008-2024 гг.: число
организаций, численность работников, затраты по видам работ / видам затрат /
источникам финансирования / приоритетным направлениям, плюс инновационная
деятельность за 2024 г. по видам экономической деятельности. В росстатовском
parquet этих разрезов нет — там только итоговые показатели по региону.

Зачем нам «источники финансирования». Это единственный найденный источник, где
видно «средства бюджетов всех уровней» отдельной строкой — то есть пункт ТЗ
«объём поддержки организаций ВТНО через порталы по бюджету», который иначе
закрывается только суррогатом (Y477110102, все затраты на инновации).

ПОЧЕМУ verify=False ПО УМОЛЧАНИЮ. Сайты rosstat.gov.ru отдают сертификат
российского УЦ (Минцифры), которого нет ни в одном стандартном trust store:
`curl https://54.rosstat.gov.ru` падает с «unable to verify the first
certificate», `curl -k` — работает. Запросы здесь — анонимные GET публичных
файлов, без cookie, токенов и персональных данных, так что понижать проверку
безопасно. Если корневой сертификат Минцифры установлен — запускайте с
--ca-bundle <path>, и проверка включится.

robots.txt сайта запрещает только /search, /admin/, /wps/wcm/ и /audiocaptcha —
раздел /folder/ скрапить можно. Между запросами пауза (--delay), чтобы не
изображать нагрузочное тестирование.

ЧТО НА ВЫХОДЕ (--out, по умолчанию data/raw/nauka_nso):
  raw/<имя файла>       — исходники как есть (ничего не теряем);
  nauka_nso_long.csv    — длинная таблица: source, table, unit, section, label,
                          indent, year, value — для временных рядов. section
                          восстановлен по отступам ячеек, поэтому сумма по
                          разрезу не двоится (см. sheet_grid);
  wide/<имя>.csv        — прямоугольный дамп листа для таблиц, у которых нет
                          строки с годами (инновационная деятельность за один
                          год по ОКВЭД: многоуровневая шапка со слитыми
                          ячейками, единой схемы у них нет);
  manifest.csv          — что скачано: url, файл, байты, sha256, формат, статус.

Запуск (в контейнере etl репозиторий монтируется, scripts/ в образ не входит):
  docker compose run --rm -v "$PWD:/work" -w /work etl \
      python -m scripts.scrape_nauka_nso
  python -m scripts.scrape_nauka_nso --selftest   # парсер без сети

ponytail: .DOC и старый .xls (OLE2) складываются в raw/ и в длинную таблицу не
идут — .DOC это вообще текст с таблицами Word, а не сетка. Разбирать их стоит
только если оттуда реально понадобятся цифры; xlrd для .xls уже в requirements.
"""

import argparse
import csv
import hashlib
import io
import os
import re
import sys
import time
import urllib.parse
from html import unescape

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://54.rosstat.gov.ru"
FOLDER = 32625  # «Наука и инновации» Новосибирскстата
HEADERS = {"User-Agent": "mipt-deaggr-research/1.0 (spatial disaggregation practicum)"}
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
UNIT_RE = re.compile(r"^\s*(млн\s|тыс\.?\s|в\s*%|процент|человек|единиц|штук|"
                     r"миллион|рубл)", re.I)
# «из них:», «в том числе:» — не строки данных, а заголовок вложенного списка;
# запоминаем его как section для следующих строк, иначе иерархия теряется
SECTION_RE = re.compile(r"^(из них|в том числе|из общего числа|из этого)\s*:?\s*$", re.I)
# Росстат пишет отсутствие данных так; пустое значение честнее нуля
MISSING = {"", "-", "–", "—", "…", "...", "х", "x"}


def norm(s):
    """Схлопывает переносы внутри подписей: в xlsx они реальные \n."""
    return re.sub(r"\s+", " ", str(s)).strip()


def fetch(session, url, ca, delay, timeout=120):
    r = session.get(url, headers=HEADERS, timeout=timeout, verify=ca)
    r.raise_for_status()
    time.sleep(delay)
    return r


def list_files(session, folder, ca, delay):
    """-> [(url, имя файла)] всех вложений раздела.

    Имена файлов на сайте меняются («(1)», «(3)» — следы перезаливок), поэтому
    ссылки берём со страницы, а не хардкодим.
    """
    html = fetch(session, f"{BASE}/folder/{folder}", ca, delay).text
    seen, out = set(), []
    for href in re.findall(r'href="(/storage/mediabank/[^"]+)"', html):
        href = unescape(href)
        if href in seen:
            continue
        seen.add(href)
        name = os.path.basename(href)
        # пробел в имени -> %20; кириллицу quote() тоже закодирует
        out.append((BASE + urllib.parse.quote(href), name))
    return out


def sheet_grid(data, name):
    """Первый лист файла -> (строки, отступы); None, если это не таблица.

    Отступы (alignment.indent первой ячейки) — это и есть иерархия: Росстат
    вкладывает подпункты не текстом, а отступом ячейки («из них:» = 1,
    «собственные средства» = 2, «в том числе:» = 3, «федерального бюджета» = 4).
    Без них «средства организаций госсектора» (отступ 2, сосед «средств
    бюджетов») выглядит как подпункт предыдущего «в том числе:» — и сумма по
    разрезу считается дважды. Поэтому read_only здесь не используем: файлы по
    10-12 КБ, стили читать не дорого.

    xlsx читает openpyxl, старый OLE2 .xls — xlrd. Оба уже в
    etl/requirements.txt; ставить ради этого что-то ещё не нужно.
    """
    if data[:4] == b"PK\x03\x04":
        if name.lower().endswith((".docx", ".doc")):
            return None
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
        grid, ind = [], []
        for row in ws.iter_rows():
            grid.append(["" if c.value is None else norm(c.value) for c in row])
            ind.append(int((row[0].alignment.indent or 0)) if row else 0)
        return grid, ind
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" and name.lower().endswith(".xls"):
        try:
            import xlrd
        except ImportError:
            return None
        sh = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        grid = [[norm(sh.cell_value(r, c)) for c in range(sh.ncols)]
                for r in range(sh.nrows)]
        return grid, [0] * len(grid)   # отступы .xls не читаем: эти файлы идут в wide/
    return None


def num(raw):
    """'23 528,8' -> 23528.8; None, если это не число (прочерк, «…», текст)."""
    if raw in MISSING:
        return None
    try:
        return float(str(raw).replace(",", ".").replace("\xa0", "").replace(" ", ""))
    except ValueError:
        return None


def year_header(row):
    """{индекс столбца: год}, если строка — шапка с годами; иначе None.

    Одного «три и более четырёхзначных числа вида 19xx/20xx» НЕ хватает:
    численность техников по годам — это 1986, 2056, 2047 человек, и такая
    строка данных сама выглядела шапкой (после чего годы в выгрузке доходили до
    2056). Поэтому два признака от самого файла: у шапки пустая ячейка в столбце
    подписей (у строки данных там название), а годы строго возрастают.
    """
    if row and row[0]:
        return None
    ys = {j: int(v) for j, v in enumerate(row) if YEAR_RE.match(v)}
    if len(ys) < 3:
        return None
    seq = [ys[j] for j in sorted(ys)]
    return ys if seq == sorted(set(seq)) else None


def parse_tables(grid, indents):
    """Лист -> [(заголовок, единица, записи)]; [] если строки с годами нет.

    На одном листе Росстат кладёт НЕСКОЛЬКО таблиц друг под другом: в файле по
    численности работников это «всего по категориям персонала» и «доктора и
    кандидаты наук», у каждой свой заголовок, единица и своя строка с годами.
    Пока блоки не разделялись, вторая строка с годами разбиралась как строка
    данных — в выгрузку попадала запись с подписью «2008» и значением 2024.
    Поэтому: находим все строки-шапки (year_header), режем лист по ним,
    преамбула блока = строки без чисел прямо над его шапкой.
    """
    heads = [(i, ys) for i, row in enumerate(grid) if (ys := year_header(row))]
    if not heads:
        return []

    tables = []
    for k, (head_i, cols) in enumerate(heads):
        # преамбула блока — непрерывный хвост строк БЕЗ чисел прямо над шапкой.
        # Именно непрерывный: если брать все нечисловые ячейки от предыдущей
        # шапки, в заголовок второго блока попадают подписи строк первого
        # («Всего», «в том числе:», «исследователи», …).
        pre_from = 0 if k == 0 else heads[k - 1][0] + 1
        i = head_i - 1
        while i >= pre_from and not any(num(c) is not None for c in grid[i]):
            i -= 1
        pre = [c for row in grid[i + 1:head_i] for c in row if c]
        title = " ".join(c for c in pre if not UNIT_RE.match(c))
        units = [c for c in pre if UNIT_RE.match(c)]
        body_to = heads[k + 1][0] if k + 1 < len(heads) else len(grid)

        recs, stack = [], []      # stack: [(отступ, заголовок-связка)]
        for row, ind in zip(grid[head_i + 1:body_to], indents[head_i + 1:body_to]):
            label = next((c for c in row if c), "")
            if not label:
                continue
            while stack and stack[-1][0] >= ind:   # вышли из вложенного списка
                stack.pop()
            if SECTION_RE.match(label):
                stack.append((ind, label.rstrip(": ")))
                continue
            section = stack[-1][1] if stack else ""
            for j, year in cols.items():
                val = num(row[j] if j < len(row) else "")
                if val is None:
                    continue
                # 19326.599999999999 -> 19326.6: источник печатает один знак,
                # это шум двоичного float, а не значащие цифры
                recs.append((section, label, ind, year, f"{val:.10g}"))
        if recs:
            tables.append((title, (units[0] if units else ""), recs))
    return tables


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--folder", type=int, default=FOLDER)
    ap.add_argument("--out", default="data/raw/nauka_nso")
    ap.add_argument("--ca-bundle", help="путь к корневому сертификату УЦ Минцифры; "
                                        "без него проверка TLS отключается")
    ap.add_argument("--delay", type=float, default=1.0, help="пауза между запросами, с")
    ap.add_argument("--force", action="store_true", help="перекачать уже скачанное")
    ap.add_argument("--selftest", action="store_true", help="проверить парсер без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    ca = a.ca_bundle or False
    if ca is False:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        print("TLS-проверка отключена: сертификат УЦ Минцифры не в trust store "
              "(см. докстринг). Запросы — анонимные GET публичных файлов.")

    out = os.path.join(ROOT, a.out)
    raw, wide = os.path.join(out, "raw"), os.path.join(out, "wide")
    os.makedirs(raw, exist_ok=True)
    os.makedirs(wide, exist_ok=True)

    session = requests.Session()
    files = list_files(session, a.folder, ca, a.delay)
    print(f"раздел /folder/{a.folder}: вложений {len(files)}")

    manifest, long_rows = [], []
    for url, name in files:
        path = os.path.join(raw, name)
        if os.path.exists(path) and not a.force:
            data = open(path, "rb").read()
            got = "кэш"
        else:
            data = fetch(session, url, ca, a.delay).content
            open(path, "wb").write(data)
            got = "скачан"

        parsed = sheet_grid(data, name)
        if parsed is None:
            status = "не таблица (OLE2/Word) — только raw/"
        else:
            grid, indents = parsed
            tables = parse_tables(grid, indents)
            if tables:
                for title, unit, recs in tables:
                    long_rows += [(name, title, unit, sec, lb, i, y, v)
                                  for sec, lb, i, y, v in recs]
                n = sum(len(r) for _, _, r in tables)
                status = (f"{n} значений, таблиц {len(tables)}, "
                          f"{tables[0][0][:38]}")
            else:
                wpath = os.path.join(wide, os.path.splitext(name)[0] + ".csv")
                with open(wpath, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerows(grid)
                status = f"нет строки с годами -> wide/ ({len(grid)} строк)"
        manifest.append((url, name, len(data), hashlib.sha256(data).hexdigest()[:16],
                         name.rsplit(".", 1)[-1].lower(), got, status))
        print(f"  {name[:62]:64} {status}")

    lp = os.path.join(out, "nauka_nso_long.csv")
    with open(lp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "table", "unit", "section", "label", "indent",
                    "year", "value"])
        w.writerows(long_rows)
    mp = os.path.join(out, "manifest.csv")
    with open(mp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "file", "bytes", "sha256_16", "ext", "fetch", "status"])
        w.writerows(manifest)

    if long_rows:
        years = sorted({int(r[6]) for r in long_rows})
        print(f"\n-> {lp}: {len(long_rows)} значений, "
              f"{len({r[1] for r in long_rows})} таблиц, годы {years[0]}-{years[-1]}")
    else:
        print(f"\n-> {lp}: пусто — ни в одном файле не нашлось строки с годами")
    print(f"-> {mp}")


def selftest():
    """Парсер на синтетической таблице того же вида, что публикует Росстат."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    # отступы (indent) — как в публикациях Росстата, иерархия задана ими
    for row, indent in [(["Внутренние затраты на научные исследования и разработки"], 0),
                        (["Млн рублей"], 0),
                        (["", 2022, 2023, 2024], 0),
                        (["Всего", 34070.699999999997, 41612.5, 49789.1], 0),
                        (["из них:"], 1),
                        (["средства бюджетов всех уровней", 20941, 23528.8, 23844.6], 2),
                        (["в том числе:"], 3),
                        (["федерального бюджета", 20829, 23301.1, 23688.7], 4),
                        # сосед «средств бюджетов», НЕ подпункт «в том числе»
                        (["средства организаций госсектора", 8000, 9000, 12897.3], 2),
                        (["зарубежные источники", "…", "-", "…"], 2),
                        # второй блок на том же листе — как в файле по
                        # численности работников («доктора и кандидаты наук»)
                        (["Списочная численность работников"], 0),
                        (["Человек"], 0),
                        (["", 2022, 2023, 2024], 0),
                        (["Доктора наук", 1300, 1320, 1339], 0)]:
        ws.append(row)
        ws.cell(row=ws.max_row, column=1).alignment = openpyxl.styles.Alignment(indent=indent)
    buf = io.BytesIO()
    wb.save(buf)

    grid, indents = sheet_grid(buf.getvalue(), "t.xlsx")
    tables = parse_tables(grid, indents)
    assert len(tables) == 2, f"должно быть два блока, а не {len(tables)}"
    title, unit, recs = tables[0]
    assert "Внутренние затраты" in title, title
    assert unit == "Млн рублей", unit
    got = {(s, lb, y): v for s, lb, _i, y, v in recs}
    # float-шум обрезан, а не округлён в мусор
    assert got[("", "Всего", 2022)] == "34070.7", got
    # «из них:» стало section, а не строкой данных
    assert got[("из них", "средства бюджетов всех уровней", 2023)] == "23528.8", got
    assert not [k for k in got if k[1] == "из них"], "connector попал в данные"
    # главное, зачем читаются отступы: после «в том числе:» (отступ 3) строка с
    # отступом 2 возвращается на уровень «из них», иначе сумма разреза двоится
    assert got[("в том числе", "федерального бюджета", 2024)] == "23688.7", got
    assert got[("из них", "средства организаций госсектора", 2024)] == "12897.3", got
    # второй блок разобран отдельно, а его шапка не стала строкой данных
    t2, u2, r2 = tables[1]
    assert u2 == "Человек", u2
    assert ("", "Доктора наук", 2024, "1339") in [(s_, l_, y_, v_) for s_, l_, _i, y_, v_ in r2], r2
    assert not [1 for s_, l_, *_ in recs + r2 if YEAR_RE.match(l_)], "шапка попала в данные"
    # «…» и «-» — пропуск, а не ноль
    assert not [k for k in got if k[1] == "зарубежные источники"], got
    print(f"selftest ok: {len(recs)} значений, единица «{unit}»")


if __name__ == "__main__":
    sys.exit(main())
