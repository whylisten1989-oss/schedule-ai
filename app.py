import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io

# --- 页面配置 ---
st.set_page_config(page_title="智能排班系统 V2.0", layout="wide", page_icon="📅")

st.title("📅 智能排班系统 V2.0 - 逻辑增强版")
st.info("当前版本重点：加入了班次均衡算法（公平性）和 防疲劳逻辑（晚转早）。")

# --- 1. 基础数据配置 ---
with st.sidebar:
    st.header("1. 基础设置")
    
    # 员工名单录入
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十"
    emp_input = st.text_area("输入员工名单 (用逗号分隔)", default_employees, height=100)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    st.write(f"当前员工数: **{len(employees)}** 人")

    # 班次设置
    st.subheader("班次定义")
    shifts_input = st.text_input("班次名称 (用英文逗号分隔)", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    
    # 自动识别“休”字，用于逻辑判断
    off_shift_name = next((s for s in shifts if "休" in s), None)
    if not off_shift_name:
        st.warning("⚠️ 请确保班次中包含'休'字，否则无法正确计算休息日！")
        off_shift_indices = []
    else:
        off_shift_indices = [i for i, s in enumerate(shifts) if s == off_shift_name]

    # 时间范围
    num_days = st.slider("排班周期 (天)", 7, 31, 7)

# --- 2. 高级约束配置 (逻辑核心) ---
st.header("⚙️ 排班规则配置")
col1, col2 = st.columns(2)

with col1:
    st.subheader("🛡️ 硬约束 (必须满足)")
    # 每日每班次人数需求
    st.caption("每个班次最少需要几人？")
    min_staff_per_shift = {}
    for s in shifts:
        if "休" not in s:
            min_staff_per_shift[s] = st.number_input(f"【{s}】最少人数", min_value=0, value=1, key=f"min_{s}")

    # 晚转早限制
    st.markdown("---")
    enable_no_night_to_day = st.checkbox("🚫 禁止'晚转早' (防疲劳)", value=True, help="如果昨天是晚班，今天不能是早班")
    if enable_no_night_to_day:
        night_shift = st.selectbox("请指定哪个是'晚班'?", [s for s in shifts if "休" not in s], index=len(shifts)-2 if len(shifts)>2 else 0)
        day_shift = st.selectbox("请指定哪个是'早班'?", [s for s in shifts if "休" not in s], index=0)

with col2:
    st.subheader("⚖️ 软约束 (尽量平衡)")
    st.caption("AI 会尽力让大家的班次数量差异不超过这个值")
    
    # 班次平衡阈值
    balance_threshold = st.slider("允许的班次数量最大差异 (天)", 1, 5, 2, help="例如设为2：员工A上了5个早班，员工B最少也要上3个早班。")
    
    # 个人特殊需求 (简化版)
    st.markdown("---")
    st.caption("特殊人员照顾 (示例功能)")
    special_emp = st.selectbox("选择员工", ["无"] + employees)
    if special_emp != "无":
        avoid_shift = st.selectbox(f"尽量让 {special_emp} 少上哪个班?", [s for s in shifts if "休" not in s])
        st.info(f"系统将尝试减少 {special_emp} 的 {avoid_shift} 次数")


# --- 核心算法 ---
def solve_schedule_v2():
    model = cp_model.CpModel()
    
    # 1. 变量定义: shifts[(e, d, s)] = 1 (员工e在第d天是班次s)
    shift_vars = {}
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # 2. 硬约束：每天每人只能上 1 个班
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # 3. 硬约束：满足每日每班次最少人数
    for d in range(num_days):
        for s_idx, s_name in enumerate(shifts):
            if s_name in min_staff_per_shift:
                required = min_staff_per_shift[s_name]
                model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= required)

    # 4. 硬约束：禁止晚转早
    if enable_no_night_to_day:
        night_idx = shifts.index(night_shift)
        day_idx = shifts.index(day_shift)
        for e in range(len(employees)):
            for d in range(num_days - 1):
                # 逻辑：(昨天晚班 + 今天早班) <= 1  --> 两者不能同时为真
                model.Add(shift_vars[(e, d, night_idx)] + shift_vars[(e, d+1, day_idx)] <= 1)

    # 5. 软约束：班次均衡 (让每个人的每个班次数量尽量平均)
    # 这是一个优化目标，我们引入惩罚变量
    
    # 计算每个人各班次的总数
    for s_idx, s_name in enumerate(shifts):
        if "休" in s_name: continue # 不强制平衡休息天数，优先平衡工时
        
        counts = []
        for e in range(len(employees)):
            c = model.NewIntVar(0, num_days, f'count_{employees[e]}_{s_name}')
            model.Add(c == sum(shift_vars[(e, d, s_idx)] for d in range(num_days)))
            counts.append(c)
        
        # 核心逻辑：最大值 - 最小值 <= 阈值
        min_count = model.NewIntVar(0, num_days, f'min_{s_name}')
        max_count = model.NewIntVar(0, num_days, f'max_{s_name}')
        model.AddMinEquality(min_count, counts)
        model.AddMaxEquality(max_count, counts)
        
        # 尽量满足 (max - min <= threshold)
        # 如果无法满足，每超过 1 单位，惩罚权重增加
        # 这里为了简化，我们先尝试将其设为硬约束，如果不通再转软约束
        # 但为了用户体验，我们用 Soft Constraint 方式：
        
        diff = model.NewIntVar(0, num_days, f'diff_{s_name}')
        model.Add(diff == max_count - min_count)
        
        # 告诉求解器：尽量让 diff 小于等于 阈值
        # 这是一个技巧：我们惩罚 diff 超过 threshold 的部分
        excess = model.NewIntVar(0, num_days, f'excess_{s_name}')
        # excess >= diff - threshold
        model.Add(excess >= diff - balance_threshold)
        model.Minimize(excess * 10) # 权重设为10

    # 6. 软约束：特殊人员偏好
    if special_emp != "无":
        try:
            e_idx = employees.index(special_emp)
            s_idx = shifts.index(avoid_shift)
            # 尽量让这个 count 趋近于 0
            count_special = sum(shift_vars[(e_idx, d, s_idx)] for d in range(num_days))
            model.Minimize(count_special * 5) # 权重设为5
        except:
            pass

    # 求解
    solver = cp_model.CpSolver()
    # 设置求解时间限制 (防止死循环)
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        data = []
        for e in range(len(employees)):
            row = {"姓名": employees[e]}
            # 统计各班次数量，用于核对
            shift_counts = {s:0 for s in shifts}
            
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row[f"D{d+1}"] = shifts[s]
                        shift_counts[shifts[s]] += 1
            
            # 把统计数据加到表格后面，方便你检查是否平衡
            for s in shifts:
                 if "休" not in s:
                    row[f"{s}统计"] = shift_counts[s]
            
            data.append(row)
        return pd.DataFrame(data), solver.StatusName(status)
    else:
        return None, "无解"

# --- 运行按钮 ---
st.markdown("###")
if st.button("🚀 生成优化排班表", type="primary"):
    with st.spinner("AI 正在进行数万次组合计算..."):
        result_df, status_msg = solve_schedule_v2()
        
        if result_df is not None:
            st.success(f"✅ 排班完成！状态: {status_msg}")
            
            # 样式优化：高亮显示 '休'
            def highlight_off(val):
                color = '#d4edda' if "休" in str(val) else ''
                return f'background-color: {color}'
            
            st.dataframe(result_df.style.applymap(highlight_off), use_container_width=True)
            
            # 下载
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                result_df.to_excel(writer, index=False)
            st.download_button("📥 下载 Excel", output.getvalue(), "排班表.xlsx")
        else:
            st.error("❌ 无法找到满足所有硬约束的方案。建议：1. 增加员工人数；2. 减少每日最少值班人数；3. 允许晚转早。")
