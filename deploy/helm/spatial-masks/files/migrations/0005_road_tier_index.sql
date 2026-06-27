-- 0005_road_tier_index.sql
-- Частичные GiST-индексы по классу дороги — чтобы KNN-поиск ближайшей дороги
-- тира (road_network_mask) шёл по индексу, а не сканировал все дороги с фильтром
-- по regex (на Краснодаре это давало запрос на десятки минут).
-- Предикаты ДОЛЖНЫ дословно совпадать с WHERE в load_road_network_mask.

CREATE INDEX IF NOT EXISTS road_geom_fed_gix ON road USING gist (geom)
    WHERE highway ~ 'motorway|trunk';
CREATE INDEX IF NOT EXISTS road_geom_reg_gix ON road USING gist (geom)
    WHERE highway ~ 'primary';
CREATE INDEX IF NOT EXISTS road_geom_loc_gix ON road USING gist (geom)
    WHERE highway ~ 'secondary';
