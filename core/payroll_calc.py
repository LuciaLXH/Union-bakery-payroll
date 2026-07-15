"""
核心薪酬计算模块
实现社保调整、试用期折算、加班费判定、客诉扣款与复杂的报损分摊计算逻辑。
"""

import pandas as pd
import numpy as np
import config

def calculate_payroll(
    employees_df: pd.DataFrame, 
    loss_df: pd.DataFrame, 
    store_sales: dict,
    social_security_std: float = config.SOCIAL_SECURITY_DEDUCTION
) -> pd.DataFrame:
    """
    核心薪酬核算引擎
    
    参数:
    - employees_df: 员工基本数据及当月考勤、微调数据 DataFrame
        必须包含列: 
        - '姓名' (str, 唯一键)
        - '分店' (str, 如 '分店A', '分店B', '分店C')
        - '职位' (str, '店长'/'前台'/'后厨'/'学徒')
        - '是否上社保' (bool/str, True/Yes/是)
        - '是否处于试用期' (bool/str, True/Yes/是)
        - '出勤天数' (float, 默认 30)
        - '加班小时数' (float, 后厨有效)
        - '绩效奖金' (float, 可为空，为空则取配置默认值)
        - '全勤奖' (float, 可为空，为空则取配置默认值)
        - '手动福利' (float, 默认为 0)
        - '投诉扣款' (float, 默认为 0)
    - loss_df: 当月报损明细数据 DataFrame
        必须包含列:
        - '日期' (str/datetime)
        - '分店' (str)
        - '责任人' (str, 为空或 '所有人'/'全体'/'公摊' 表示公摊)
        - '物品名称' (str)
        - '物品类别' (str, '成品'/'半成品'/'原材料')
        - '金额' (float)
    - store_sales: 字典，键为分店名称，值为当月营业额（元）
    - social_security_std: 当月实际缴纳的社保标准，默认从 config 读取
    
    返回:
    - 计算好各项薪资明细的 DataFrame，包含所有计算中间项及最终'实发工资'
    """
    df = employees_df.copy()
    
    # 1. 规范化输入数据中的布尔值
    def to_bool(val):
        if isinstance(val, bool):
            return val
        if pd.isna(val):
            return False
        val_str = str(val).strip().lower()
        return val_str in ('yes', 'y', 'true', '1', '是', 'y')

    df['是否上社保'] = df['是否上社保'].apply(to_bool)
    df['是否处于试用期'] = df['是否处于试用期'].apply(to_bool)

    # 2. 计算调整后底薪
    # 根据职位匹配默认底薪
    df['标准底薪'] = df['职位'].apply(lambda x: config.DEFAULT_BASE_SALARY.get(x, 0.0))
    
    # 社保判定与折算
    # 上社保：扣减社保标准（约 1000 元）
    # 不上社保：基本工资正常发放（注意：店长不上社保时，底薪已包含 600 元，所以这里直接以标准底薪为基准不扣减即可）
    df['社保扣减额'] = df.apply(
        lambda row: social_security_std if row['是否上社保'] else 0.0, 
        axis=1
    )
    
    # 试用期折算：未转正员工底薪打折（通常是 80%）
    df['试用期折扣'] = df['是否处于试用期'].apply(
        lambda x: config.PROBATION_SALARY_RATIO if x else 1.0
    )
    
    # 调整后底薪 = (标准底薪 - 社保扣减额) * 试用期折扣
    df['调整后底薪'] = (df['标准底薪'] - df['社保扣减额']) * df['试用期折扣']
    # 确保底薪不为负数
    df['调整后底薪'] = df['调整后底薪'].clip(lower=0.0)

    # 3. 统计店铺总人数与人效，用于后厨加班费门槛校验
    # 店铺人数统计 (按花名册中各分店的总人数)
    store_counts = df['分店'].value_counts().to_dict()
    
    # 计算各店的平均人效： 门店营业额 / (门店总人数 * 当月天数)
    # 假设标准计薪天数采用 30 天
    days_in_month = config.OVERTIME_RULES["STANDARD_WORK_DAYS"]
    store_efficiency = {}
    for store, sales in store_sales.items():
        count = store_counts.get(store, 0)
        if count > 0:
            store_efficiency[store] = sales / (count * days_in_month)
        else:
            store_efficiency[store] = 0.0
            
    # 将人效指标映射回员工表
    df['店铺平均人效'] = df['分店'].apply(lambda x: store_efficiency.get(x, 0.0))
    
    # 4. 计算加班费（仅限后厨，且店铺平均人效需 > 1800）
    def calc_overtime(row):
        if row['职位'] != '后厨':
            return 0.0
        
        # 判断平均人效门槛
        efficiency = row['店铺平均人效']
        if efficiency <= config.OVERTIME_RULES["EFFICIENCY_THRESHOLD"]:
            return 0.0
        
        # 加班小时数
        ot_hours = row.get('加班小时数', 0.0)
        if pd.isna(ot_hours) or ot_hours <= 0:
            return 0.0
            
        # 公式：当月基本工资 ÷ 30天 ÷ 9小时 × 1.5倍 × 加班小时数
        # 这里“当月基本工资”按员工的标准底薪还是调整后底薪？
        # 规则说明：“当月基本工资 ÷ 30天 ÷ 9小时 × 1.5倍 × 加班小时数”。
        # 按照常理，基本工资采用员工的标准底薪（或调整后底薪）。我们这里采用“调整后底薪”作为基数，防止社保扣除后计薪基数不一致；
        # 同时为了安全，在代码中以“调整后底薪”进行计算，并做好防空处理。
        base_rate = row['调整后底薪'] / days_in_month / config.OVERTIME_RULES["STANDARD_DAILY_HOURS"]
        ot_pay = base_rate * config.OVERTIME_RULES["MULTIPLIER"] * ot_hours
        return round(ot_pay, 2)
        
    df['加班费'] = df.apply(calc_overtime, axis=1)

    # 5. 绩效与全勤奖（若输入为空，则取默认配置值）
    df['全勤奖'] = df['全勤奖'].fillna(config.DEFAULT_ATTENDANCE_BONUS).astype(float)
    df['绩效奖金'] = df['绩效奖金'].fillna(config.DEFAULT_PERFORMANCE_BONUS).astype(float)
    df['加班小时数'] = df['加班小时数'].fillna(0.0).astype(float)
    
    # 手动福利与投诉扣款初始化
    df['手动福利'] = df['手动福利'].fillna(0.0).astype(float)
    df['投诉扣款'] = df['投诉扣款'].fillna(0.0).astype(float)

    # 6. 计算报损扣款（核心难点）
    # 准备存储每位员工的报损扣款明细
    # 结构: { 员工姓名: [ {金额, 物品, 类别, 责任类型(个人/公摊), 原始损失, 实际分摊金额} ] }
    employee_loss_records = {name: [] for name in df['姓名']}
    
    # 初始化每个人分摊前的报损扣款
    df['报损扣款_未免责'] = 0.0
    
    if loss_df is not None and not loss_df.empty:
        # 对报损记录逐条进行折合与分摊
        for idx, row in loss_df.iterrows():
            date = row.get('日期')
            store = row.get('分店')
            responsible = row.get('责任人')
            item_name = row.get('物品名称', '未知')
            item_type = row.get('物品类别', '成品')
            raw_amount = float(row.get('金额', 0.0))
            
            if raw_amount <= 0:
                continue
                
            # 折算规则：成品扣 50%，半成品/原材料扣 100%
            rate = config.LOSS_DEDUCTION_RATES.get(item_type, 1.0)
            net_loss = raw_amount * rate
            
            # 判断是否有明确责任人
            is_individual = False
            # 责任人如果不为空且在员工列表中
            if pd.notna(responsible) and str(responsible).strip() != '' and str(responsible).strip() not in ('所有人', '全体', '公摊', '无'):
                resp_name = str(responsible).strip()
                if resp_name in employee_loss_records:
                    is_individual = True
                    # 个人全额承担
                    employee_loss_records[resp_name].append({
                        'date': date,
                        'item_name': item_name,
                        'item_type': item_type,
                        'raw_amount': raw_amount,
                        'net_loss': net_loss,
                        'share_amount': net_loss,
                        'is_shared': False,
                        'gross_event_amount': raw_amount # 事件原发生金额
                    })
            
            # 找不到责任人，由当班的所有人员平摊（即该店所有员工）
            if not is_individual:
                # 获取该分店的员工名单
                store_employees = df[df['分店'] == store]['姓名'].tolist()
                if store_employees:
                    share_count = len(store_employees)
                    share_amount = net_loss / share_count
                    for emp in store_employees:
                        employee_loss_records[emp].append({
                            'date': date,
                            'item_name': item_name,
                            'item_type': item_type,
                            'raw_amount': raw_amount,
                            'net_loss': net_loss,
                            'share_amount': share_amount,
                            'is_shared': True,
                            'gross_event_amount': raw_amount # 事件原发生金额
                        })

    # 计算学徒免责条款后的报损扣款
    def calc_final_loss_deduction(row):
        name = row['姓名']
        position = row['职位']
        records = employee_loss_records.get(name, [])
        
        # 计算未免责时的总扣款金额
        gross_deduction = sum(r['share_amount'] for r in records)
        
        if not records:
            return 0.0
            
        # 若是学徒，执行免责条款判断
        if position == '学徒':
            total_occurrences = len(records)
            
            # 一票否决判定：检查是否有任意一次报损的原始事件金额 > 200 元
            # 注意：这里是“单次报损数额巨大（如单次超过 200 元）”。
            # 我们按照报损表中的原始发生“金额”是否大于 200 元来判断。
            has_veto = any(r['gross_event_amount'] > config.APPRENTICE_LOSS_RULES["SINGLE_VETO_THRESHOLD"] for r in records)
            
            # 免责条件：累计次数在 2 次以内（含2次），且没有触发一票否决
            if total_occurrences <= config.APPRENTICE_LOSS_RULES["MAX_FREE_COUNT"] and not has_veto:
                # 享受全额免责，扣款归零
                return 0.0
            else:
                # 触发一票否决或次数超限，不予免责，全额扣款
                return round(gross_deduction, 2)
        else:
            # 非学徒员工，不享受免责，全额扣款
            return round(gross_deduction, 2)

    # 存下未免责前分摊的和免责后的最终扣款
    df['报损扣款_未免责'] = df['姓名'].apply(lambda n: round(sum(r['share_amount'] for r in employee_loss_records.get(n, [])), 2))
    df['报损扣款'] = df.apply(calc_final_loss_deduction, axis=1)

    # 7. 汇总最终工资
    # 实发工资 = 调整后底薪 + 全勤 + 绩效 + 加班费 + 手动福利 - 报损扣款 - 投诉扣款
    df['实发工资'] = (
        df['调整后底薪'] + 
        df['全勤奖'] + 
        df['绩效奖金'] + 
        df['加班费'] + 
        df['手动福利'] - 
        df['报损扣款'] - 
        df['投诉扣款']
    )
    
    # 确保实发工资不为负数
    df['实发工资'] = df['实发工资'].clip(lower=0.0).round(2)
    
    return df

def get_employee_loss_details(
    employees_df: pd.DataFrame, 
    loss_df: pd.DataFrame
) -> dict:
    """
    辅助函数：获取每位员工的报损分摊明细列表，用于在前端“报损分摊明细”页面中展示
    """
    employee_loss_records = {name: [] for name in employees_df['姓名']}
    
    if loss_df is None or loss_df.empty:
        return employee_loss_records
        
    for idx, row in loss_df.iterrows():
        date = row.get('日期')
        store = row.get('分店')
        responsible = row.get('责任人')
        item_name = row.get('物品名称', '未知')
        item_type = row.get('物品类别', '成品')
        raw_amount = float(row.get('金额', 0.0))
        
        if raw_amount <= 0:
            continue
            
        rate = config.LOSS_DEDUCTION_RATES.get(item_type, 1.0)
        net_loss = raw_amount * rate
        
        is_individual = False
        if pd.notna(responsible) and str(responsible).strip() != '' and str(responsible).strip() not in ('所有人', '全体', '公摊', '无'):
            resp_name = str(responsible).strip()
            if resp_name in employee_loss_records:
                is_individual = True
                employee_loss_records[resp_name].append({
                    'date': date,
                    'store': store,
                    'item_name': item_name,
                    'item_type': item_type,
                    'raw_amount': raw_amount,
                    'net_loss': net_loss,
                    'share_amount': net_loss,
                    'is_shared': False,
                    'gross_event_amount': raw_amount
                })
        
        if not is_individual:
            store_employees = employees_df[employees_df['分店'] == store]['姓名'].tolist()
            if store_employees:
                share_count = len(store_employees)
                share_amount = net_loss / share_count
                for emp in store_employees:
                    employee_loss_records[emp].append({
                        'date': date,
                        'store': store,
                        'item_name': item_name,
                        'item_type': item_type,
                        'raw_amount': raw_amount,
                        'net_loss': net_loss,
                        'share_amount': share_amount,
                        'is_shared': True,
                        'gross_event_amount': raw_amount
                    })
                    
    return employee_loss_records
