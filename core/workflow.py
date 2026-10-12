"""第一阶段的会话草稿。接口接受映射，方便独立测试。"""
from core.payroll_calc import calculate_payroll


def initialize(state):
    if state.get("payroll_schema_version") != 2:
        state["payroll_schema_version"] = 2
        state["draft_df"] = None
        state["payroll_df"] = None
        state["editor_revision"] = 0
        state["input_source"] = ""


def load_inputs(state, df, source):
    state["draft_df"] = df.copy(deep=True)
    state["payroll_df"] = None
    state["input_source"] = source
    state["editor_revision"] += 1


def apply_edits(state, edits):
    """从稳定输入应用当前控件补丁，刷新控件身份，避免二次编辑回退。"""
    df = state["draft_df"].copy(deep=True)
    for row_index, values in edits.items():
        for col, value in values.items():
            df.at[df.index[int(row_index)], col] = value
    state["draft_df"] = df
    state["payroll_df"] = None
    state["editor_revision"] += 1


def recalculate(state):
    # 失败时也必须使先前结果失效。
    state["payroll_df"] = None
    state["payroll_df"] = calculate_payroll(state["draft_df"])
    return state["payroll_df"]
