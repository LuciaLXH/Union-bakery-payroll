# 后续功能路线图 / Product roadmap

状态：规划中；下列功能尚未上线。先收集老板对当前版本的反馈，再按可独立验收的小阶段交付。

## 1. 个人工资条：先预览和下载，再考虑邮件

**老板看到的效果：** 选门店和月份，查看每人的工资组成、逐项扣款、加班费、实发和备注；确认后下载单份 PDF 或整店压缩包。

**需要做的工作：** 以某一次确定的核算版本生成工资条，保存规则版本、输入版本、生成时间和工资条版本。修改考勤后，旧工资条标记为已被新版本替代；不能将旧金额当作当前金额再次发出。

**拟增加的数据：** `payslips`（核算记录 ID、版本、生成状态、文件位置、内容校验值、生成时间）。不把永久公开的文件地址写进页面；真实数据阶段使用受保护存储和短期下载链接。

**验收：** 工资条金额与核算明细一致；中文姓名、长备注和分页清楚；一人一份不混入其他员工内容；重复生成可追踪，旧版本不冒充新版本。

## 2. 身份、门店权限与出勤提交

**店长看到的效果：** 先提供适合手机的专属网页链接，登录后只看到自己门店和当月员工，填写考勤、保存草稿、提交给老板。先验证网页流程，再决定是否需要微信小程序。

**老板看到的效果：** 每家门店显示“未开始 / 草稿 / 已提交 / 待修改 / 已确认”，可以查看、退回并给出修改意见。店长默认只填写考勤和备注，是否显示工资金额由老板决定。

**需要做的工作：** 独立账号、门店成员关系、服务端权限校验、提交与退回流程、修改记录。不能只靠隐藏按钮或在网址里写门店编号来隔离数据。确认后的出勤或发薪记录修改需要重新打开并保留原因。

**拟增加的数据：** `users` 或外部身份 ID、`store_memberships`、`attendance_submissions`、`audit_events`；必要时将纯考勤输入与工资快照权限拆分。

**验收：** A 店店长不能通过改链接访问 B 店数据，也不能访问受限工资接口；移动端可完成录入；重复提交不重复建记录；老板确认前可以退回修改。

## 3. 邮件发送：在确认和权限流程之后接入

**老板看到的效果：** 先预览收件名单、工资条版本和邮件内容，确认发薪记录后再发送。页面显示待发送、发送中、服务商已接受、失败和退信；可以对失败项重试。

**需要做的工作：** 验证员工邮箱与员工编号的对应关系，设置门店发件地址；接入邮件服务商和后台任务；敏感文件使用受保护访问。前期先用测试邮箱和虚构 PDF 验证流程。是否在批准后自动发送，作为单独设置。

**拟增加的数据：** 员工联系信息与验证状态、`email_outbox`、`delivery_attempts`、幂等键（员工＋月份＋工资条版本＋渠道）、服务商回执 ID。发送重试必须复用幂等标识，避免重复投递。

**验收：** 未确认月份不能发送；一人对应一份已确认工资条；重复点击不重复发送；错误邮箱被拦截或可追踪；失败重试不会更换附件；“服务商已接受”不等同于员工已经收到或阅读。

## 4. 老板的数据分析 Dashboard

**先交付的效果：** 按门店、月份筛选，查看月度实发、工资组成、加班费、迟到与缺勤、人数变化以及环比趋势。每张图可追溯到对应月份和当前有效核算版本。

**口径要求：** 同一员工同一月份只取当前有效版本；旧核算记录不重复相加。草稿、待核算和已确认月份分别标识。没有历史月份时显示“暂无历史”，不能伪装成 0 或编造增长率。

**拟增加的数据：** 首版复用现有五张表建立查询或视图。等实际数据量需要时才增加汇总表。若希望看人工成本率，需要另行提供口径一致的门店月营业额与其他用工成本；目前工资数据本身无法算出可信的成本率。

**验收：** 图表合计与月度工资页相等；改动并重算后自动取新版本；未核算员工有清楚提示；无上期数据不计算环比；门店权限同样适用于导出和分析接口。

## 建议顺序

1. 收集本轮老板反馈，先修真实使用中的阻碍。
2. 交付个人工资条预览与下载，可独立验收。
3. 建立账号、门店权限与确认流程，然后交付店长手机填报链接。
4. 接入经过确认的邮件发送与失败重试。
5. 用已稳定的月份数据搭建 Dashboard；基础趋势可与第 3 阶段并行。

现在不需要再发一长串问题给老板。进入各阶段前只确认对应事项：工资条必要字段与版式；店长是否能看金额、谁确认；员工邮箱由谁维护与发件人；Dashboard 最重要的三个指标。

## English summary

| Phase | User outcome | Dependency |
|---|---|---|
| Payslip preview and PDF download | Explain one employee’s pay from a specific calculation version | Stable calculations and version references |
| Identity and scoped manager attendance | Managers submit only their own store’s attendance | Server-side roles, store membership and review workflow |
| Reviewed email delivery | Send the approved payslip to the verified recipient with delivery tracking | Approved payroll, verified contact data, outbox and idempotency |
| Monthly analytics | Compare current payroll totals, overtime, absence and headcount | Defined metrics, version-aware queries and enough history |

These are planned capabilities. The public English demo sends no emails and has no manager accounts or analytics dashboard. Payroll cost ratios require revenue and additional cost data that the current system does not collect.
