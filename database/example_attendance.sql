-- 用户已手动执行，2026-10-06 只读核对通过；不是结构迁移。
-- 将指定草稿的五项考勤/备注替换成以下测试值，不修改工资快照。
UPDATE payroll_entries AS d
SET ordinary_absence_days = 1,
    unauthorized_absence_days = 0,
    late_minutes = 7,
    overtime_hours = 2,
    work_error_notes = '练习整形时出现失误，已指导，不扣款'
FROM payroll_months AS p
JOIN stores AS s ON p.store_id = s.id
WHERE d.payroll_month_id = p.id
  AND s.name = '测试面包店'
  AND d.employee_code_snapshot = 'DEMO-001'
  AND p.payroll_month = DATE '2026-10-01'
  AND p.status = 'draft'
RETURNING d.employee_code_snapshot,
          d.ordinary_absence_days,
          d.unauthorized_absence_days,
          d.late_minutes,
          d.overtime_hours,
          d.work_error_notes;
