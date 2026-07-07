-- 0005_road_tier_index.sql
-- Частичные composite GiST-индексы по региону и классу дороги — чтобы
-- KNN-поиск ближайшей дороги тира (road_network_mask) шёл по индексу.
-- Предикаты ДОЛЖНЫ дословно совпадать с WHERE в load_road_network_mask.

CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP INDEX IF EXISTS road_geom_fed_gix;
DROP INDEX IF EXISTS road_geom_reg_gix;
DROP INDEX IF EXISTS road_geom_loc_gix;

CREATE INDEX IF NOT EXISTS road_region_geom_fed_gix ON road USING gist (region_id, geom)
    WHERE highway ~ 'motorway|trunk';
CREATE INDEX IF NOT EXISTS road_region_geom_reg_gix ON road USING gist (region_id, geom)
    WHERE highway ~ 'primary';
CREATE INDEX IF NOT EXISTS road_region_geom_loc_gix ON road USING gist (region_id, geom)
    WHERE highway ~ 'secondary';
