# Сервис: БД, тайлы, API, фронт

Production-ready обёртка над системой масок: данные в PostGIS, тайлы через Martin,
API на FastAPI, карта на MapLibre. Тоггл слоёв на фронте — без пересчёта.

## Компоненты

| Сервис | Образ/стек | Порт | Назначение |
|---|---|---|---|
| `db` | postgis/postgis:16-3.4 | 5432 | хранилище сетки, масок, композиций |
| `tiles` | martin | 3000 | векторные тайлы (MVT) из PostGIS |
| `api` | FastAPI | 8000 | метаданные, контракты масок, метрики |
| `web` | MapLibre + Vite | 5173 | интерактивная карта |

## Схема данных

`db/migrations/0001_init.sql`, `0002_tiles.sql` — применяются автоматически при
первой инициализации тома Postgres.

- `region`, `indicator`, `regional_value` — справочники и официальные значения
- `grid_cell` — сетка 1×1 км (геометрия + статичные фичи)
- `mask` — реестр масок с контрактом из §6 ТЗ (source/signal/influence/formula/…)
- `mask_cell_value` — вес маски 0..1 по ячейке (источник тоггл-слоёв)
- `composition` + `distribution_cell` — итоговое распределение, `SUM(value)=official`
- `quality_metric` — метрики качества (§9)

## Запуск (этап 2 — БД + ETL)

```bash
cp .env.example .env
docker compose up -d db          # поднять Postgres+PostGIS, миграции применятся сами

# залить данные (нужен Python-окружение с зависимостями из requirements.txt)
pip install -r requirements.txt
DATABASE_URL=postgresql+psycopg://masks:masks@localhost:5432/masks \
    python -m etl.ingest --config etl/config.yaml
```

ETL идемпотентен: повторный запуск пере-заливает регионы (каскадное удаление по slug).

Перед запуском положить в `data/`:
- `data/raw/.../Показатели по регионам.parquet` (Росстат)
- `data/processed/grid_*_1km_features.gpkg`, `border_*.gpkg`

## Тайлы (после ETL)

- маска: `http://localhost:3000/tile_mask/{z}/{x}/{y}?mask=worldpop_mask`
- маска по показателю: `…?mask=regression_mask&indicator=Y477090007`
- распределение: `http://localhost:3000/tile_distribution/{z}/{x}/{y}?composition=1`

## API (этап 3)

```bash
docker compose up -d db tiles api
# или локально:
DATABASE_URL=postgresql+psycopg://masks:masks@localhost:5432/masks \
TILES_BASE_URL=http://localhost:3000 \
    uvicorn app.main:app --app-dir apps/api --reload
```

Эндпоинты (`http://localhost:8000`):

| Метод | Назначение |
|---|---|
| `GET /health` | проверка живости + коннект к БД |
| `GET /api/config` | базовый URL тайлов для фронта |
| `GET /api/regions` | регионы + bbox + число ячеек |
| `GET /api/indicators` | показатели (эластичность, R²) |
| `GET /api/masks` | контракты масок (§6) + шаблон URL тайла |
| `GET /api/compositions?region_id=&indicator=` | слои распределения + метрики + URL тайла |

Swagger: `http://localhost:8000/docs`.

## Фронт (этап 4)

```bash
docker compose up -d            # весь стек
# или локально для разработки:
cd apps/web && npm install && npm run dev   # http://localhost:5173
```

Карта на MapLibre GL:
- выбор региона (центрирование по bbox) и показателя;
- селектор итоговой композиции + панель метрик (сохранение суммы, Джини, top-10%);
- панель масок — чекбокс + opacity-слайдер на каждый слой (тоггл предрасчётных тайлов);
- кнопка «i» — контракт маски (§6): источник, сигнал, тип влияния, формула, ограничения;
- легенда цветовой шкалы распределения.

Базовый URL API — runtime-конфиг `public/config.js` (`window.__APP_CONFIG__.apiBase`);
в проде заменяется смонтированным ConfigMap, образ собирается один раз.

## Деплой в Kubernetes (этап 5)

Helm-чарт: `deploy/helm/spatial-masks`. Один домен, маршрутизация через Ingress:
`/` → web, `/api` → api, `/tiles` → Martin (префикс вырезается rewrite'ом).

```bash
helm upgrade --install masks deploy/helm/spatial-masks \
  --namespace masks --create-namespace \
  --set ingress.host=masks.example.com \
  --set postgres.password=<secret>
```

### Внешний Postgres (в отдельном namespace)

По умолчанию `postgres.deploy=false` — чарт **не** поднимает свою БД, а
подключается к существующей по FQDN (`<svc>.<ns>.svc.cluster.local`).
`DATABASE_URL` собирается в рантайме, пароль берётся из секрета — в манифестах
его нет.

Секреты не читаются cross-namespace, поэтому пароль БД должен лежать **в ns
приложения**: либо укажите готовый секрет (`postgres.existingSecret`), либо
передайте `postgres.password` (чарт создаст секрет сам).

```bash
helm upgrade --install masks deploy/helm/spatial-masks \
  --namespace masks --create-namespace \
  --set postgres.deploy=false \
  --set postgres.host=postgres-rw.databases.svc.cluster.local \
  --set postgres.existingSecret=pg-creds \
  --set postgres.existingSecretPasswordKey=password \
  --set ingress.host=masks.realdomain.ru \
  --set ingress.tls=false --set ingress.sslRedirect=false
```

Миграции (`db/migrations/*.sql`) накатываются автоматически Helm-хуком
`spatial-masks-migrate` (pre-install/pre-upgrade Job на образе `db`, idempotent)
— и для внешнего, и для встроенного Postgres. Отключается `--set migrate.enabled=false`.
Для ручного наката вне кластера есть `db/migrate.sh`:

```bash
DATABASE_URL=postgres://masks:<pwd>@<pg-host>:5432/masks ./db/migrate.sh
```

Состав чарта:
- `db` — StatefulSet PostGIS + PVC, миграции запечены в образ (только при `postgres.deploy=true`)
- `tiles` — Martin (Deployment), конфиг из ConfigMap
- `api` — FastAPI (Deployment, 2 реплики), creds из Secret
- `web` — nginx со статикой (Deployment, 2 реплики), apiBase из ConfigMap
- `backup` — CronJob `pg_dump` в PVC с ретеншеном (`backup.enabled`)
- `etl` — Job для разовой загрузки (`etl.enabled`, нужен PVC с `data/`)

Образы собирает CI (`.github/workflows/deploy.yml`): db, api, web, etl + `helm lint`.

Загрузка данных в кластере: включить `etl.enabled=true` с `etl.dataClaim=<PVC>`,
либо запустить разово через `kubectl run` (см. вывод NOTES.txt после установки).

### За внешним reverse-proxy (другой VPS)

TLS терминируется на фронт-VPS, внутрь кластера идёт HTTP. Браузерный URL
(`apiBase` фронта и `TILES_BASE_URL` API) строится из `public.*`, а не из
внутреннего адреса — это публичный домен.

```bash
helm upgrade --install masks deploy/helm/spatial-masks \
  --set public.scheme=https \
  --set ingress.host=masks.realdomain.ru \   # такой Host шлёт фронт-VPS
  --set ingress.tls=false \                   # TLS на фронт-VPS, внутри HTTP
  --set ingress.sslRedirect=false \           # без https-редиректа за прокси
  --set postgres.password=<secret>
```

- `ingress.host` = публичный домен; фронт-VPS должен слать такой `Host`
  (`proxy_set_header Host $host`). Если домен фронта отличается — задайте
  `public.url=https://<домен>` напрямую.
- Пример конфига фронт-VPS: `deploy/reverse-proxy.nginx.conf.example`.

### CI/CD (Gitea Actions)

`.gitea/workflows/build-deploy.yaml` — на push в `main`: сборка образов (db, api,
web, etl) и пуш в registry, затем `helm upgrade --install` в кластер с тегом по
commit SHA. Подключение к внешнему Postgres и домен задаются переменными.

Нужно настроить в репозитории Gitea:
- секреты: `REGISTRY_HOST/USER/PASSWORD`, `KUBE_CONFIG`;
- переменные: `IMAGE_PREFIX`, `NAMESPACE`, `INGRESS_HOST`, `PG_HOST`, `PG_EXISTING_SECRET`.

(`.github/workflows/deploy.yml` оставлен для зеркала на GitHub — только сборка.)
