import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io

# --- 页面配置 ---
st.set_page_config(page_title="智能排班 V3.0 (表格版)", layout="wide", page_icon="📅")
st.title("📅 智能排班系统 V3.0 - 批量管理版")

# --- 1. 基础数据配置 ---
with st.sidebar:
    st.header("1. 基础设置")
    
    # 员工名单录入
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十,郑十一,王十二"
    emp_input = st.text_area("输入员工名单 (用逗号分隔)", default_employees, height=100)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    # 班次设置
    st.subheader("班次定义")
    shifts_input = st.text_input("班次名称 (用逗号分隔)", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    
    # 自动识别“休”
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
        st.success(f"已识别休息班次为: **{off_shift_name}**")
    except StopIteration:
        st.error("❌ 班次中必须包含'休'字！")
        st.stop()

    # 时间范围
    num_days = st.slider("排班周期 (天)", 7, 31, 7)

# --- 2. 约束规则 (表格化) ---
st.header("⚙️ 规则与需求管理")

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("全局硬性规则")
    # 休息天数限制
    target_off_days = st.number_input("每人每周期必须休息几天？", min_value=0, max_value=num_days, value=2)
    
    st.markdown("---")
    # 每日最少人数
    st.caption("各班次最少在岗人数")
    min_staff_per_shift = {}
    for s in shifts:
        if s != off_shift_name:
            min_staff_per_shift[s] = st.number_input(f"【{s}】最少人数", min_value=0, value=1, key=f"min_{s}")
    
    # 晚转早
    st.markdown("---")
    enable_no_night_to_day = st.checkbox("🚫 禁止'晚转早'", value=True)
    if enable_no_night_to_day:
        night_shift = st.selectbox("晚班是?", [s for s in shifts if s != off_shift_name], index=len(shifts)-2)
        day_shift = st.selectbox("早班是?", [s for s in shifts if s != off_shift_name], index=0)

with col2:
    st.subheader("🙋‍♂️ 员工个性化需求 (直接编辑表格)")
    st.caption("在下方表格填入员工的具体要求。数字代表第几天（如 '1,7' 代表第1天和第7天必休）。")
    
    # 创建初始数据框
    init_data = {
        "姓名": employees,
        "指定休息日 (如: 1,3)": ["" for _ in employees],
        "拒绝班次 (如: 晚班)": ["" for _ in employees]
    }
    df_requests = pd.DataFrame(init_data)
    
    # 这是一个可编辑的表格！
    edited_df = st.data_editor(
        df_requests, 
        num_rows="dynamic",
        column_config={
            "指定休息日 (如: 1,3)": st.column_config.TextColumn(help="输入数字，逗号分隔。例如：1,7 代表周一和周日休息"),
            "拒绝班次 (如: 晚班)": st.column_config.SelectboxColumn(options=[s for s in shifts if s != off_shift_name], help="该员工绝对不上的班次")
        },
        hide_index=True
    )

# --- 核心算法 ---
def solve_schedule_v3():
    model = cp_model.CpModel()
    shift_vars = {}
    
    # 索引映射
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]

    # 1. 创建变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # 2. 基础硬约束：每天每人只能 1 个班
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # 3. 基础硬约束：最少人数 (排除休息班次)
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= min_val)

    # 4. 重点升级：每人休息天数必须达标
    # 强制每个人在 num_days 里的“休”班次总数 == target_off_days
    for e in range(len(employees)):
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) == target_off_days)

    # 5. 重点升级：处理表格里的个性化需求
    # 遍历用户在网页表格里填的数据
    for index, row in edited_df.iterrows():
        name = row["姓名"]
        if name not in employees: continue # 防止名字改错了
        e_idx = employees.index(name)
        
        # 处理指定休息日
        req_days_str = str(row["指定休息日 (如: 1,3)"])
        if req_days_str and req_days_str.strip():
            # 将 "1, 3, 5" 变成 [0, 2, 4] (注意程序里是 0 开始)
            try:
                days_list = [int(x.strip()) - 1 for x in req_days_str.replace("，", ",").split(",") if x.strip().isdigit()]
                for d_idx in days_list:
                    if 0 <= d_idx < num_days:
                        # 强制这一天必须是“休”
                        model.Add(shift_vars[(e_idx, d_idx, off_idx)] == 1)
            except:
                st.warning(f"员工 {name} 的休息日格式输入有误，已跳过。")

        # 处理拒绝班次
        reject_shift = row["拒绝班次 (如: 晚班)"]
        if reject_shift and reject_shift in shifts:
            reject_idx = s_map[reject_shift]
            for d in range(num_days):
                # 强制这一天绝对不能是这个班
                model.Add(shift_vars[(e_idx, d, reject_idx)] == 0)

    # 6. 晚转早限制
    if enable_no_night_to_day:
        n_idx = s_map[night_shift]
        d_idx = s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1)

    # 求解
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        data = []
        for e in range(len(employees)):
            row_data = {"姓名": employees[e]}
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row_data[f"第{d+1}天"] = shifts[s]
            data.append(row_data)
        return pd.DataFrame(data), "成功"
    else:
        return None, "冲突"

# --- 运行区 ---
st.markdown("###")
if st.button("🚀 生成 V3 排班表", type="primary"):
    with st.spinner("AI 正在根据表格需求进行精密计算..."):
        result_df, msg = solve_schedule_v3()
        
        if result_df is not None:
            st.success(f"✅ 排班完成！所有人的休息天数都已确保为 {target_off_days} 天。")
            
            # 颜色标记
            def color_map(val):
                if off_shift_name in str(val): return 'background-color: #d1e7dd; color: #0f5132' # 绿色
                if "晚" in str(val): return 'background-color: #fff3cd; color: #664d03' # 黄色
                return ''
                
            st.dataframe(result_df.style.applymap(color_map), use_container_width=True)
            
            # 导出
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                result_df.to_excel(writer, index=False)
            st.download_button("📥 下载 Excel", output.getvalue(), "排班表_V3.xlsx")
        else:
            st.error("❌ 排班失败：约束冲突！")
            st.warning("""
            可能的原因：
            1. 指定的休息日太多，导致没法凑够上班人数。
            2. 某个员工拒绝了所有班次。
            3. 请检查'指定休息日'是否和'最少在岗人数'打架了。
            """)
