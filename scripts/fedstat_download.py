#!/usr/bin/env python3
"""Минимальный клиент ЕМИСС (fedstat.ru): выгрузка показателя в SDMX.

Порт схемы R-пакета fedstatAPIr (cran/fedstatAPIr): GET страницы показателя
даёт куки, struts-токен и дерево фильтров (inline-JS с `filters:`), данные
экспортируются POST'ом /indicator/downloadData.do?format=sdmx с формой
selectedFilterIds=<поле>_<значение>. Формат SDMX — чтобы не разбирать Excel.

Запуск (нужен RU IP):
    python -m scripts.fedstat_download --indicator 57822 --out /tmp/57822.xml \
        [--select Год=2023]        # оставить только эти значения фильтра,
                                   # остальное — «все»; без --select всё
Печать дерева фильтров: --list-filters
"""
import argparse
import json
import re
import sys
from urllib.parse import urlencode

import requests

BASE = "https://www.fedstat.ru"


def get_filters(indicator_id, session):
    r = session.get(f"{BASE}/indicator/{indicator_id}", timeout=120)
    r.raise_for_status()
    html = r.text
    # единственный <script> с `filters:` и `left_columns:` (как в fedstatAPIr)
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    cand = [s for s in scripts if re.search(r"filters\s*:", s) and re.search(r"left_columns\s*:", s)]
    if len(cand) != 1:
        sys.exit(f"ожидался один скрипт с фильтрами, найдено {len(cand)}")
    js = cand[0]
    m = re.search(r"struts\.token\.name[^\n]*?value=\"([^\"]+)\"", html) or \
        re.search(r"name=\"struts\.token\.name\" value=\"([^\"]+)\"", html)
    token_name = m.group(1) if m else ""
    v = re.search(rf'name="{re.escape(token_name)}" value="([^"]+)"', html) if token_name else None
    token = v.group(1) if v else ""
    return js, token_name, token


def _js_value(text, pos):
    """Мини-парсер JS-литерала из inline-скрипта: {...} / [..] / 'строка' /
    число / слово. Возвращает (python-объект, новая позиция)."""
    n = len(text)
    while pos < n and text[pos] in " \t\r\n,":
        pos += 1
    if pos >= n:
        return None, pos
    c = text[pos]
    if c in "{[":
        out, close = ({}, "}") if c == "{" else ([], "]")
        pos += 1
        while pos < n:
            while pos < n and text[pos] in " \t\r\n,":
                pos += 1
            if pos < n and text[pos] == close:
                return out, pos + 1
            key = None
            if isinstance(out, dict):
                key, pos = _js_value(text, pos)
                while pos < n and text[pos] in " \t\r\n":
                    pos += 1
                if pos < n and text[pos] == ":":
                    pos += 1
            val, pos = _js_value(text, pos)
            if isinstance(out, dict):
                out[key] = val
            else:
                out.append(val)
        return out, pos
    if c in "'\"":
        buf, pos = "", pos + 1
        while pos < n and text[pos] != c:
            if text[pos] == "\\" and pos + 1 < n:
                nxt = text[pos + 1]
                if nxt == "u":
                    buf += chr(int(text[pos + 2:pos + 6], 16))
                    pos += 6
                    continue
                buf += {"n": "\n", "t": "\t", "'": "'", '"': '"', "\\": "\\"}.get(nxt, nxt)
                pos += 2
                continue
            buf += text[pos]
            pos += 1
        return buf, pos + 1
    m = re.match(r"[A-Za-z0-9_.+-]+", text[pos:])
    tok = m.group(0) if m else text[pos]
    pos += len(tok)
    if tok in ("true", "false"):
        return tok == "true", pos
    try:
        return float(tok) if "." in tok else int(tok), pos
    except ValueError:
        return tok, pos


def parse_filter_tree(js):
    """`filters: {<field_id>: {title, values: {<value_id>: {title, ...}}}}`
    из inline-JS; регексы не тянут вложенность — маленький рекурсивный парсер."""
    fm = re.search(r"filters\s*:\s*", js)
    if not fm:
        sys.exit("в inline-JS нет блока filters:")
    obj, _ = _js_value(js, fm.end())
    tree = {}
    for fid, f in obj.items():
        values = {str(vid): v.get("title", "") for vid, v in (f.get("values") or {}).items()}
        if values:
            tree[str(fid)] = {"title": f.get("title", ""), "values": values}
    return tree


def download(indicator_id, only=None, fmt="sdmx"):
    s = requests.Session()
    s.headers["User-Agent"] = "mipt-deaggr-etl/1.0 (practicum research)"
    s.headers["Accept-Language"] = "ru-RU,ru;q=0.9"
    js, token_name, token = get_filters(indicator_id, s)
    tree = parse_filter_tree(js)
    form = [("title", ""), (token_name, token), ("struts.token.name", token_name),
            ("id", str(indicator_id))]
    layout = {}  # field -> line/column/filter: как на сайте, все строки/строки-фильтры
    for fid, f in tree.items():
        sel = f["values"]
        if only and f["title"] in only:
            want = [v for k, v in f["values"].items() if v in only[f["title"]]]
            sel = {k: v for k, v in f["values"].items() if v in only[f["title"]]}
            if not sel:
                sys.exit(f"фильтр «{f['title']}»: нет значений из {only[f['title']]}")
        for vid in sel:
            form.append(("selectedFilterIds", f"{fid}_{vid}"))
        layout[fid] = "lineObjectIds" if len(sel) > 1 else "filterObjectIds"
    for fid, kind in layout.items():
        form.append((kind, fid))
    r = s.post(f"{BASE}/indicator/downloadData.do?format={fmt}",
               data=urlencode(form, doseq=True),
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=300)
    if r.status_code != 200 or "<!DOCTYPE" in r.text[:200]:
        sys.exit(f"выгрузка не удалась: HTTP {r.status_code}, {r.text[:200]}")
    return tree, r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indicator", required=True)
    ap.add_argument("--out")
    ap.add_argument("--select", action="append", default=[],
                    help="Фильтр=значение[,значение]: 'Год=2023', 'Территория=Все'")
    ap.add_argument("--list-filters", action="store_true")
    a = ap.parse_args()
    only = {}
    for kv in a.select:
        k, v = kv.split("=", 1)
        only[k.strip()] = [x.strip() for x in v.split(",")]
    if a.list_filters:
        import requests as rq
        s = rq.Session()
        js, _, _ = get_filters(a.indicator, s)
        print(json.dumps(parse_filter_tree(js), ensure_ascii=False, indent=1))
        return
    tree, data = download(a.indicator, only)
    open(a.out, "wb").write(data)
    print(f"→ {a.out} ({len(data):,} байт); фильтры: "
          + ", ".join(f"{f['title']} ({len(f['values'])})" for f in tree.values()))


if __name__ == "__main__":
    main()
