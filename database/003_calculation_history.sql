-- 已应用到开发库 public，2026-10-12 只读核对确认；现有库不要重复执行。
-- 已有数据保留。全部结构变更放在同一事务内，出错时不提交部分变更。
\set ON_ERROR_STOP on
BEGIN;

-- 1. 月度输入版本：修改输入后递增，用于识别旧结果。
ALTER TABLE public.payroll_entries
    ADD COLUMN input_version BIGINT NOT NULL DEFAULT 1
        CHECK (input_version > 0);

-- 2. 自动维护版本，用户不需要在每次 UPDATE 时手动加一。
CREATE FUNCTION public.track_payroll_input_version()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.input_version := 1;
    ELSIF (to_jsonb(NEW) - 'input_version' - 'created_at')
          IS DISTINCT FROM
          (to_jsonb(OLD) - 'input_version' - 'created_at') THEN
        NEW.input_version := OLD.input_version + 1;
    ELSE
        NEW.input_version := OLD.input_version;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER payroll_entries_track_input_version
BEFORE INSERT OR UPDATE ON public.payroll_entries
FOR EACH ROW EXECUTE FUNCTION public.track_payroll_input_version();

-- 3. 一次核算一条记录，保留它使用的输入和公式版本。
-- 此表保存结果，不在数据库中另写一套工资公式。
CREATE TABLE public.payroll_calculations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payroll_entry_id BIGINT NOT NULL REFERENCES public.payroll_entries(id),
    input_version BIGINT NOT NULL CHECK (input_version > 0),
    rule_version TEXT NOT NULL CHECK (length(trim(rule_version)) > 0),
    input_snapshot JSONB NOT NULL
        CHECK (jsonb_typeof(input_snapshot) = 'object'),

    salary_deduction NUMERIC(20, 2) NOT NULL,
    performance_deduction NUMERIC(20, 2) NOT NULL,
    attendance_deduction NUMERIC(20, 2) NOT NULL,
    late_deduction NUMERIC(20, 2) NOT NULL,
    overtime_pay NUMERIC(20, 2) NOT NULL,
    gross_pay NUMERIC(20, 2) NOT NULL,
    total_deductions NUMERIC(20, 2) NOT NULL,
    net_pay NUMERIC(20, 2) NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (payroll_entry_id, input_version, rule_version),
    CHECK (LEAST(salary_deduction, performance_deduction,
                 attendance_deduction, late_deduction,
                 overtime_pay, gross_pay, total_deductions) >= 0),
    CHECK (GREATEST(salary_deduction, performance_deduction,
                    attendance_deduction, late_deduction, overtime_pay,
                    gross_pay, total_deductions, net_pay) < 'Infinity'::numeric),
    CHECK (total_deductions = salary_deduction + performance_deduction
                            + attendance_deduction + late_deduction),
    CHECK (net_pay = gross_pay - total_deductions)
);

-- 负数实发允许留作异常核算证据；程序后续必须阻止其确认和正式导出。
-- 结果保存程序见 core/payroll_storage.py；确认后锁定及历史只增不改权限尚待实现。
-- 当前结果查询需要同时匹配 input_version 和程序采用的 rule_version。
COMMIT;
