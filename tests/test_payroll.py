"""
单元测试模块
使用 unittest 对算薪核心引擎进行多维度场景覆盖测试，以验证算法正确性。
"""

import unittest
import pandas as pd
import numpy as np
import sys
import os

# 将项目根目录添加到 python 搜索路径以方便导入
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.payroll_calc import calculate_payroll

class TestPayrollCalculation(unittest.TestCase):
    
    def setUp(self):
        # 1. 模拟花名册数据
        self.employees_data = [
            # 姓名, 分店, 职位, 是否上社保, 是否处于试用期, 出勤天数, 加班小时数, 全勤奖, 绩效奖金, 手动福利, 投诉扣款
            ["张三", "分店A", "店长", "否", "否", 30, 0, 400, 600, 0, 0],
            ["李四", "分店A", "后厨", "是", "否", 30, 15, 400, 600, 0, 0],
            ["王五", "分店A", "前台", "否", "是", 25, 0, 400, 600, 0, 0],
            ["赵六", "分店A", "学徒", "否", "否", 30, 0, 400, 600, 0, 0],
        ]
        self.employees_df = pd.DataFrame(
            self.employees_data,
            columns=["姓名", "分店", "职位", "是否上社保", "是否处于试用期", "出勤天数", "加班小时数", "全勤奖", "绩效奖金", "手动福利", "投诉扣款"]
        )
        
        # 2. 模拟营业额
        # 分店A总人数为 4 人。
        # 计薪天数默认为 30 天。
        # - 当月营业额为 240,000 元时：平均人效 = 240,000 / (4 * 30) = 2000 元/人/天 > 1800 (达标，计发加班费)
        # - 当月营业额为 180,000 元时：平均人效 = 180,000 / (4 * 30) = 1500 元/人/天 <= 1800 (不达标，加班费为 0)
        self.high_sales = {"分店A": 240000.0}
        self.low_sales = {"分店A": 180000.0}

    def test_social_security_and_probation(self):
        """测试底薪社保调整与试用期折算逻辑"""
        # 执行核算，低营业额（不计加班费）
        res_df = calculate_payroll(self.employees_df, None, self.low_sales, social_security_std=1000.0)
        
        # 1. 张三 (店长, 不上社保, 已转正)
        # 调整后底薪 = 6000
        self.assertEqual(res_df.loc[res_df["姓名"] == "张三", "调整后底薪"].values[0], 6000.0)
        
        # 2. 李四 (后厨, 上社保, 已转正)
        # 调整后底薪 = 4500 - 1000 = 3500
        self.assertEqual(res_df.loc[res_df["姓名"] == "李四", "调整后底薪"].values[0], 3500.0)
        
        # 3. 王五 (前台, 不上社保, 未转正)
        # 调整后底薪 = 3500 * 0.8 = 2800
        self.assertEqual(res_df.loc[res_df["姓名"] == "王五", "调整后底薪"].values[0], 2800.0)

    def test_kitchen_overtime_threshold(self):
        """测试后厨加班费发放门槛（平均人效判定）"""
        
        # 场景一：营业额达标 (平均人效 2000 > 1800)
        res_high = calculate_payroll(self.employees_df, None, self.high_sales, social_security_std=1000.0)
        # 李四加班小时数为 15 小时。调整后底薪为 3500。
        # 加班费公式: 3500 / 30 / 9 * 1.5 * 15 = 19.4444... * 1.5 * 15 = 291.67 元
        lisi_ot_high = res_high.loc[res_high["姓名"] == "李四", "加班费"].values[0]
        self.assertAlmostEqual(lisi_ot_high, 291.67, places=2)
        
        # 场景二：营业额未达标 (平均人效 1500 <= 1800)
        res_low = calculate_payroll(self.employees_df, None, self.low_sales, social_security_std=1000.0)
        lisi_ot_low = res_low.loc[res_low["姓名"] == "李四", "加班费"].values[0]
        self.assertEqual(lisi_ot_low, 0.0)

    def test_apprentice_loss_waiver(self):
        """测试学徒报损扣款免责与一票否决条款"""
        
        # 设定学徒赵六有报损，测试以下不同报损情况
        # 1) 发生2次小额报损（如成品30元，原价30；成品40元，原价40） -> 应免责（实扣为0）
        loss_data_1 = [
            ["2026-07-01", "分店A", "赵六", "红豆面包", "成品", 30.0],  # 个人折合损失 = 30 * 0.5 = 15
            ["2026-07-02", "分店A", "赵六", "菠萝包", "成品", 40.0]   # 个人折合损失 = 40 * 0.5 = 20
        ]
        loss_df_1 = pd.DataFrame(loss_data_1, columns=["日期", "分店", "责任人", "物品名称", "物品类别", "金额"])
        res_1 = calculate_payroll(self.employees_df, loss_df_1, self.low_sales)
        self.assertEqual(res_1.loc[res_1["姓名"] == "赵六", "报损扣款"].values[0], 0.0)
        
        # 2) 发生3次小额报损 -> 超出次数上限，全额扣除（15 + 20 + 25 = 60）
        loss_data_2 = loss_data_1 + [
            ["2026-07-03", "分店A", "赵六", "椰蓉包", "成品", 50.0]   # 个人折合损失 = 50 * 0.5 = 25
        ]
        loss_df_2 = pd.DataFrame(loss_data_2, columns=["日期", "分店", "责任人", "物品名称", "物品类别", "金额"])
        res_2 = calculate_payroll(self.employees_df, loss_df_2, self.low_sales)
        self.assertEqual(res_2.loc[res_2["姓名"] == "赵六", "报损扣款"].values[0], 60.0)

        # 3) 发生1次大额报损（金额 250 元，原材料，折合损失 250 元） -> 触发一票否决，不再免责，全扣（250）
        loss_data_3 = [
            ["2026-07-04", "分店A", "赵六", "高档进口黄油", "原材料", 250.0] # 个人折合损失 = 250
        ]
        loss_df_3 = pd.DataFrame(loss_data_3, columns=["日期", "分店", "责任人", "物品名称", "物品类别", "金额"])
        res_3 = calculate_payroll(self.employees_df, loss_df_3, self.low_sales)
        self.assertEqual(res_3.loc[res_3["姓名"] == "赵六", "报损扣款"].values[0], 250.0)

    def test_loss_sharing(self):
        """测试无法确定责任人时分店全员平摊逻辑"""
        # 发生一笔公摊报损：分店A 发生 120 元的半成品/原材料损毁（净损失 120 元，未指定责任人）
        # 分店A 共有 4 人（张三, 李四, 王五, 赵六），平摊后每人承担 120 / 4 = 30 元。
        # 非学徒员工（张三, 李四, 王五）均自负 30 元。
        # 学徒赵六（仅此1次，金额120 <= 200元，符合免责），因此扣 0 元。
        loss_data = [
            ["2026-07-02", "分店A", "公摊", "进口芝士", "原材料", 120.0]
        ]
        loss_df = pd.DataFrame(loss_data, columns=["日期", "分店", "责任人", "物品名称", "物品类别", "金额"])
        res = calculate_payroll(self.employees_df, loss_df, self.low_sales)
        
        self.assertEqual(res.loc[res["姓名"] == "张三", "报损扣款"].values[0], 30.0)
        self.assertEqual(res.loc[res["姓名"] == "李四", "报损扣款"].values[0], 30.0)
        self.assertEqual(res.loc[res["姓名"] == "王五", "报损扣款"].values[0], 30.0)
        self.assertEqual(res.loc[res["姓名"] == "赵六", "报损扣款"].values[0], 0.0) # 赵六作为学徒免责了

if __name__ == "__main__":
    unittest.main()
