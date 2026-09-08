#!/usr/bin/env bash
# Сквозная воспроизводимая демонстрация платформы на двух показателях:
#   Y477110196 — коэффициент естественного прироста населения (на 1000 чел.)
#   Y477110197 — коэффициент миграционного прироста (на 10000 чел.)
#
# Путь: parquet Росстата -> миграции -> PostGIS -> API -> тайл карты. Каждый шаг
# проверяется,
# любой сбой роняет скрипт (set -e). Идемпотентен: регион переливается заново.
#
#   ./scripts/demo_e2e.sh [slug]     # по умолчанию moscow
#
# Требуется: docker compose, curl, jq, python3. Сеть НЕ нужна — все входные
# данные (parquet, сетки, csv-маски) уже лежат в data/.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${1:-moscow}"
CODES=(Y477110196 Y477110197)

[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a
API="http://localhost:${API_PORT}"
TILES="http://localhost:${MARTIN_PORT}"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "1/7 Инварианты разнесения + сырые значения из parquet Росстата"
# образ etl несёт код ингеста — пересобираем, иначе run возьмёт старый слой
docker compose build etl
# Σ = rv для объёмных, взвешенное среднее = rv для ставок (миграция 0013)
docker compose run --rm --no-deps -T etl python -m src.masks.composition
# Тем же кодом, что использует ингест (фильтр сентинелов -77777777/-99999999,
# откат на последний доступный год по §4.2) — не «похожим» запросом.
docker compose run --rm --no-deps -T etl python - "$REGION" "${CODES[@]}" <<'PY'
import sys, yaml
from etl.ingest import regional_value_lookup
slug, codes = sys.argv[1], sys.argv[2:]
cfg = yaml.safe_load(open('etl/config.yaml', encoding='utf-8'))
reg = next(r for r in cfg['regions'] if r['slug'] == slug)
pq, year = cfg['rosstat_parquet'], cfg['year']
print(f"  регион parquet: {reg['object_name']}, год {year}")
for c in codes:
    m = cfg['indicators'][c]
    rv = regional_value_lookup(pq, reg['object_name'], c, year, m.get('subsection'))
    nat = regional_value_lookup(pq, 'Российская Федерация', c, year,
                                m.get('subsection'), object_level='Страна')
    print(f"  {c}: регион={rv} РФ={nat} [{m['unit']}] — {m['name']}")
    assert rv is not None, f"{c}: нет значения в parquet"
PY

step "2/7 Платформа: db + tiles + api + web"
docker compose up -d --build db tiles api web
for i in $(seq 60); do
  curl -fsS "$API/health" >/dev/null 2>&1 && break
  [ "$i" = 60 ] && { echo "API не поднялся"; docker compose logs --tail=40 api; exit 1; }
  sleep 2
done
echo "  API: $(curl -fsS "$API/health")"

step "3/7 Миграции схемы (идемпотентны, тот же набор, что pre-upgrade хук Helm)"
# Файлы монтируются в docker-entrypoint-initdb.d и применяются ТОЛЬКО при первой
# инициализации тома — на существующем томе новые миграции надо накатить явно.
for f in deploy/helm/spatial-masks/files/migrations/*.sql; do
  docker compose exec -T db psql -q -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    -v ON_ERROR_STOP=1 -f - < "$f" >/dev/null || { echo "  сбой на $f"; exit 1; }
done
echo "  применено: $(ls deploy/helm/spatial-masks/files/migrations/*.sql | wc -l | tr -d ' ') файлов"

step "4/7 Ингест: сетка, 11 масок и композиции региона $REGION"
docker compose run --rm -T etl python -m etl.ingest --config etl/config.yaml \
  --region "$REGION" "${CODES[@]/#/--indicator=}"

step "5/7 БД: что легло в таблицы"
docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "
  SELECT i.code, i.unit, i.rate_base, c.regional_value, i.national_total,
         count(DISTINCT c.id) AS ablation_rows,
         bool_and(c.sum_preserved) AS invariant_ok,
         round(max(CASE WHEN q.metric = 'gini' THEN q.value END)::numeric, 4) AS gini_max,
         string_agg(DISTINCT q.metric, ', ') AS metrics
  FROM composition c JOIN indicator i ON i.code = c.indicator_code
  JOIN region r ON r.id = c.region_id
  LEFT JOIN quality_metric q ON q.composition_id = c.id
  WHERE r.slug = '$REGION' AND i.code IN ('${CODES[0]}','${CODES[1]}')
  GROUP BY i.code, i.unit, i.rate_base, c.regional_value, i.national_total
  ORDER BY i.code;"

step "6/7 API: тот же путь, которым ходит веб-приложение"
RID=$(curl -fsS "$API/api/regions" | jq -r ".[] | select(.slug==\"$REGION\") | .id")
[ -n "$RID" ] || { echo "регион $REGION не найден в /api/regions"; exit 1; }
BBOX=$(curl -fsS "$API/api/regions" | jq -c ".[] | select(.slug==\"$REGION\") | .bbox")
echo "  region_id=$RID bbox=$BBOX"

for CODE in "${CODES[@]}"; do
  echo
  curl -fsS "$API/api/indicators" | jq -r ".[] | select(.code==\"$CODE\") |
    \"  /api/indicators      \(.code)  \(.name) [\(.unit)]\""
  W=$(curl -fsS "$API/api/default-weights?indicator=$CODE" | jq -c .)
  echo "  /api/default-weights $W"
  R=$(curl -fsS -X POST "$API/api/recompute" -H 'content-type: application/json' \
        -d "{\"region_id\":$RID,\"indicator\":\"$CODE\",\"weights\":$W}")
  echo "  /api/recompute       rv=$(jq -r .regional_value <<<"$R")" \
       "rate_base=$(jq -r '.rate_base // "—"' <<<"$R")" \
       "value=[$(jq -r .value_min <<<"$R")…$(jq -r .value_max <<<"$R")]" \
       "peak=$(jq -r .peak_threshold <<<"$R")" \
       "gini=$(jq -r .metrics.gini <<<"$R")"
  # Доля ячеек выше порога пиков: должна быть ~5%, а не 95% (миграция 0013).
  # Множитель rv/scale берём из самого тайл-URL — ровно то, чем красит SQL.
  RV=$(jq -r .regional_value <<<"$R")
  SCALE=$(jq -r .tile_url <<<"$R" | sed -n 's/.*[?&]total=\([^&]*\).*/\1/p')
  THR=$(jq -r '.peak_threshold // 0' <<<"$R")
  docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -A -c "
    WITH wt AS (SELECT m.id mask_id, (e.value)::float8 w
                FROM json_each_text('$W'::json) e JOIN mask m ON m.slug = e.key),
    cell AS (SELECT mcv.cell_id, sum(wt.w*mcv.weight) raw
             FROM mask_cell_value mcv JOIN wt ON wt.mask_id = mcv.mask_id
             JOIN grid_cell g ON g.id = mcv.cell_id
             WHERE g.region_id = $RID AND mcv.indicator_code IN ('', '$CODE')
             GROUP BY 1)
    SELECT '  ячеек выше порога пиков: ' || round(
             100.0 * count(*) FILTER (WHERE raw * $RV / $SCALE >= $THR) / count(*), 1
           ) || '%   (scale=$SCALE)' FROM cell;"
  # тайл карты по координатам центра региона — доказательство, что слой рисуется
  read -r Z X Y < <(python3 -c "
import math, sys, json
minx, miny, maxx, maxy = json.loads('$BBOX')
z = 8; lon = (minx+maxx)/2; lat = (miny+maxy)/2
n = 2**z
x = int((lon+180)/360*n)
y = int((1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n)
print(z, x, y)")
  URL=$(jq -r .tile_url <<<"$R" | sed "s/{z}/$Z/;s/{x}/$X/;s/{y}/$Y/")
  BYTES=$(curl -fsS "$URL" | wc -c | tr -d ' ')
  echo "  тайл $Z/$X/$Y          $BYTES байт MVT"
  [ "$BYTES" -gt 0 ] || { echo "  ПУСТОЙ ТАЙЛ"; exit 1; }
  # структура концентрации: пики + линии MST (ТЗ п.7)
  CS=$(curl -fsS --get "$API/api/concentration-structure" \
        --data-urlencode "region_id=$RID" --data-urlencode "indicator=$CODE" \
        --data-urlencode "weights=$W")
  echo "  /api/concentration-structure пиков=$(jq '[.features[]|select(.geometry.type=="Point")]|length' <<<"$CS")" \
       "линий=$(jq '[.features[]|select(.geometry.type=="LineString")]|length' <<<"$CS")"
done

step "7/7 Результирующий слой (свод по 15 показателям, оба кода входят)"
curl -fsS "$API/api/composite?region_id=$RID" | jq "{
  layers: [.layers[] | select(.indicator==\"${CODES[0]}\" or .indicator==\"${CODES[1]}\")],
  skipped, missing_masks, offset}"

printf '\n\033[1mГотово.\033[0m Карта: http://localhost:%s (регион «%s», показатели %s)\n' \
  "$WEB_PORT" "$REGION" "${CODES[*]}"
