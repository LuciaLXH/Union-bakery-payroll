-- 用户已手动执行；2026-10-06 只读核对通过。现有开发库不要重复执行。
-- ALTER TABLE 是修改现有表结构，不会重建表或删除工资快照。
-- 单条语句：任一新增列或约束失败，整条语句都不生效。
ALTER TABLE public.payroll_entries
    ADD COLUMN ordinary_absence_days INTEGER NOT NULL DEFAULT 0
        CHECK (ordinary_absence_days BETWEEN 0 AND 30),
    ADD COLUMN unauthorized_absence_days INTEGER NOT NULL DEFAULT 0
        CHECK (unauthorized_absence_days BETWEEN 0 AND 30),
    ADD COLUMN late_minutes INTEGER NOT NULL DEFAULT 0
        CHECK (late_minutes >= 0),
    ADD COLUMN overtime_hours NUMERIC NOT NULL DEFAULT 0
        CHECK (overtime_hours >= 0 AND overtime_hours < 'Infinity'::numeric),
    ADD COLUMN work_error_notes TEXT NOT NULL DEFAULT '',
    ADD CONSTRAINT payroll_entries_absence_days_check
        CHECK (ordinary_absence_days + unauthorized_absence_days <= 30);

-- NUMERIC 不限定加班小数位，避免在入库时悄悄舍入有效小时。
-- 小于 Infinity 同时排除正无穷和 PostgreSQL 排序中更大的 NaN。
