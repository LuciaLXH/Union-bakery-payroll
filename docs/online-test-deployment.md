# Union Bakery 在线测试部署记录

日期：2026-10-12。环境用途：老板使用虚构数据体验网页流程。

测试网址：https://union-bakery-payroll-test.onrender.com

[Render 服务管理](https://dashboard.render.com/web/srv-db65t6id0e5s739gn5d0) · [Neon 项目管理](https://console.neon.tech/app/projects/plain-leaf-07182352)

## 资源与结构

- 网页：Render Free Web Service，新加坡区域，Python 3.12.14 + Streamlit。
- 数据库：Neon Free，PostgreSQL 18，新加坡区域；独立项目 `union-bakery-payroll-test`，分支 `boss-test`，数据库 `bakery_payroll_test`。
- 页面访问使用共享测试口令；口令只以 PBKDF2 SHA-256 哈希存入 Render。数据库连接要求 TLS。
- 应用使用专用 `bakery_app` 账号与连接池，仅有现有表的查询、新增和更新权限，以及序列使用权限；没有删表、建表、删除记录或管理其他账号的权限。
- 只有虚构门店、虚构员工和示例考勤。没有上传本地现有员工或工资记录。
- GitHub 保存代码和版本化 SQL；数据库连接、测试口令、本地会话历史及验收截图不提交到公开仓库。

浏览器 → Render（页面、Python 计算）→ Neon（员工、月度输入、核算版本）。本地电脑关机不影响在线测试；未保存的网页编辑仍会丢失。

## 初始化与运行

空库使用 `python -m scripts.initialize_test_database`，通过 `CLOUD_ADMIN_URL` 指定直连管理员地址，通过 `CLOUD_APP_PASSWORD` 设置受限应用账号。脚本只接受名为 `bakery_payroll_test` 的空库；检查发现已有表立即停止，避免重复初始化。

脚本按顺序应用 001、002、003；在同一事务中建立五张表、账号授权和虚构案例，核对实发 5263 元后提交。它不是通用升级脚本，不可用于覆盖已有数据库。

Render 的构建命令为 `pip install -r requirements.txt`，启动命令为 `sh scripts/run_cloud.sh`。环境变量包括 `PYTHON_VERSION`、`APP_MODE`、`DATABASE_URL`、`TEST_PASSWORD_HASH`。

修复了连接池不支持连接启动参数 `lock_timeout` 的问题：改为每次数据库操作在事务中设置 `SET LOCAL`，保留 5 秒锁等待和 30 秒语句超时。

自动部署关闭；以后修改源代码并推送后，需要手动触发 Render 部署。仓库提供 `render.yaml` 供重建参考。当前服务通过 MCP 直接创建；该工具不支持设置 HTTP 健康路径，Render 使用默认检查，部署验收额外请求 `/_stcore/health`。

## 已完成的验证

- 本地包含真实 PostgreSQL 隔离结构的 76 项测试通过。
- 云端应用账号通过连接池读取五张表与示例结果。
- 已核对应用账号不具备 DELETE、schema CREATE、超级用户和角色管理权限。
- 重复初始化被安全拒绝，已有记录保留。
- 示例：工资 4500、绩效 600、全勤 300，缺勤 1 天、迟到 7 分钟、加班 2 小时，实发 5263 元。

线上服务已显示 Live，`/_stcore/health` 返回 `ok`。浏览器实际验收：

- 未登录仅出现口令入口；错误口令无法进入，正确口令可进入云端工作台。
- 把虚构员工迟到从 7 改为 10 分钟：未保存状态隐藏旧下载入口；保存后实发从 5263 变为 5260。
- 成功下载 Excel，并读取下载文件核对工资扣款 150、迟到扣款 10、加班费 50、实发 5260。
- 通过弹窗设置本月上限，保存后出现已达到上限提示；主页面不显示上限金额。
- 核算历史保留 5263 与 5260 两个版本，只有后者标为当前有效。
- 刷新页面后需重新输入测试口令；重新登录后迟到 10 分钟、实发 5260 和已设置上限状态仍然保留。
- 部署后至本次验收结束，Render 错误级别日志查询为空。

首次上线运行代码：`6eddb06`。后续文档与 SQL 文件尾空白整理不改变运行逻辑。

## 测试方式与限制

操作流程见 [老板测试说明](boss-test-guide.md)。测试口令通过本地私密文件单独交付，不能放进公开文档。

[Render 免费服务说明](https://render.com/docs/free)：空闲约 15 分钟后休眠，再次打开约需一分钟。免费服务适合此次体验测试，不承诺持续在线或正式生产服务质量。

此版本没有独立老板/员工账号和正式权限隔离，也没有正式发薪确认、锁定或个人工资条。工资上限会提示晋升限制，但尚未实现晋升审批与强制阻止职级修改。现在仅供虚构数据测试，正式录入真实数据前需要补齐身份权限、备份与恢复方案。
