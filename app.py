import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime

# --- 页面配置 ---
st.set_page_config(page_title="智能排班 V4.0 (专业版)", layout="wide", page_icon="🗓️")
st.title("🗓️ 智能排班系统 V4.0 - 日期与统计增强版")

# --- 工具函数：生成日期列表 ---
def get_date_headers(start_date, end_date):
    """生成带有周几的日期列表，例如 '10-01 (周日)'"""
    delta = end_date - start_date
    date_list = []
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    for i in range(delta.days + 1):
        day = start_date + datetime.timedelta(days=i)
        date_str = f"{day.strftime('%m-%d')} ({week_map[day.weekday()]})"
        date_list.append(date_str)
    return date_list

# --- 1. 基础数据配置 ---
with st.sidebar:
    st.header("1. 基础设置")
    
    # 员工名单
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十"
    emp_input = st.text_area("输入员工名单", default_employees, height=100)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    # 班次设置
    st.subheader("班次定义")
    shifts_input = st.text_input("班次名称 (逗号分隔)", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    
    # 识别休息班次
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except:
        st.error("班次中必须包含'休'字！")
        st.stop()

# --- 2. 核心控制台 ---
st.header("⚙️ 排班控制台")

# 日期选择器
col_date1, col_date2 = st.columns(2)
with col_date1:
    start_date = st.date_input("开始日期", datetime.date.today())
with col_date2:
    end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))

if start_date > end_date:
    st.error("结束日期必须晚于开始日期")
    st.stop()

date_headers = get_date_headers(start_date, end_date)
num_days = len(date_headers)
st.caption(f"当前排班周期：共 {num_days} 天")

st.markdown("---")

col_rule1, col_rule2 = st.columns([1, 2])

with col_rule1:
    st.subheader("全局规则")
    # 硬规则
    target_off_days = st.number_input("每人每周期需休息天数", min_value=0, max_value=num_days, value=2)
    
    st.caption("各班次每日最少人数")
    min_staff_per_shift = {}
    for s in shifts:
        if s != off_shift_name:
            min_staff_per_shift[s] = st.number_input(f"【{s}】最少人数", min_value=0, value=1, key=f"min_{s}")
            
    enable_no_night_to_day = st.checkbox("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        night_shift = st.selectbox("晚班是?", [s for s in shifts if s != off_shift_name], index=len(shifts)-2)
        day_shift = st.selectbox("早班是?", [s for s in shifts if s != off_shift_name], index=0)

with col_rule2:
    st.subheader("🙋‍♂️ 员工个性化需求表")
    
    # 初始化表格数据
    init_data = {
        "姓名": employees,
        "指定休息日 (如: 1,3)": ["" for _ in employees],
        "拒绝班次 (硬性)": ["" for _ in employees],
        "减少班次 (软性)": ["" for _ in employees]
    }
    df_requests = pd.DataFrame(init_data)
    
    # 可编辑表格配置
    shift_options = [s for s in shifts if s != off_shift_name]
    edited_df = st.data_editor(
        df_requests,
        column_config={
            "指定休息日 (如: 1,3)": st.column_config.TextColumn(help="输入第几天的数字，如 1,7"),
            "拒绝班次 (硬性)": st.column_config.SelectboxColumn(options=shift_options, help="绝对不排这个班"),
            "减少班次 (软性)": st.column_config.SelectboxColumn(options=shift_options, help="AI 会尽量少排这个班，但人手不够时可能会排")
        },
        hide_index=True,
        use_container_width=True
    )

# --- 核心算法 ---
def solve_schedule_v4():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]

    # 1. 创建变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # 2. 基础硬约束
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= min_val)

    # 3. 休息天数约束
    for e in range(len(employees)):
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) == target_off_days)

    # 4. 个性化需求处理
    objective_terms = [] # 用于软约束的目标函数
    
    for index, row in edited_df.iterrows():
        name = row["姓名"]
        if name not in employees: continue
        e_idx = employees.index(name)
        
        # A. 指定休息日
        req_days_str = str(row["指定休息日 (如: 1,3)"])
        if req_days_str.strip():
            try:
                days_list = [int(x.strip()) - 1 for x in req_days_str.replace("，", ",").split(",") if x.strip().isdigit()]
                for d_idx in days_list:
                    if 0 <= d_idx < num_days:
                        model.Add(shift_vars[(e_idx, d_idx, off_idx)] == 1)
            except: pass

        # B. 拒绝班次 (硬约束)
        refuse = row["拒绝班次 (硬性)"]
        if refuse and refuse in shifts:
            ref_idx = s_map[refuse]
            for d in range(num_days):
                model.Add(shift_vars[(e_idx, d, ref_idx)] == 0)

        # C. 减少班次 (软约束) - 关键逻辑
        reduce_s = row["减少班次 (软性)"]
        if reduce_s and reduce_s in shifts:
            red_idx = s_map[reduce_s]
            # 计算该员工排这个班的总次数
            count_var = model.NewIntVar(0, num_days, f'count_reduce_{e_idx}')
            model.Add(count_var == sum(shift_vars[(e_idx, d, red_idx)] for d in range(num_days)))
            # 惩罚项：每排一次，惩罚分 +10
            objective_terms.append(count_var * 10)

    # 5. 班次均衡 (让大家的工时尽量平均，作为次要软约束)
    # 这里简单处理：让每个人的总工作班次尽量接近平均值，稍微加一点点惩罚，避免全部压在几个人身上
    # (为简化代码复杂度，此处暂只对“减少班次”做主要优化，均衡性由轮班逻辑自然形成)
    
    # 6. 晚转早
    if enable_no_night_to_day:
        n_idx = s_map[night_shift]
        d_idx = s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1)

    # 设置目标：最小化惩罚分 (即尽量满足大家的减少班次需求)
    if objective_terms:
        model.Minimize(sum(objective_terms))

    # 求解
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # --- 结果处理与统计 ---
        data = []
        for e in range(len(employees)):
            row_data = {"姓名": employees[e]}
            stats = {s: 0 for s in shifts} # 个人统计
            
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        # 使用生成的日期头作为列名
                        row_data[date_headers[d]] = shifts[s]
                        stats[shifts[s]] += 1
            
            # 右侧统计列
            for s in shifts:
                 if s != off_shift_name: # 只统计工作班次，休息不算
                    row_data[f"统计-{s}"] = stats[s]
            data.append(row_data)
        
        df_result = pd.DataFrame(data)
        
        # --- 底部统计行逻辑 ---
        # 统计每一天，各班次有多少人
        daily_stats_row = {"姓名": "【每日在岗统计】"}
        
        # 填充日期列的统计
        for d in range(num_days):
            day_header = date_headers[d]
            day_counts = []
            for s in shifts:
                if s == off_shift_name: continue
                # 计算当天该班次的人数
                count = sum(1 for row in data if row[day_header] == s)
                day_counts.append(f"{s[0]}:{count}") # 简写：早:2
            daily_stats_row[day_header] = " ".join(day_counts)
            
        # 填充右侧统计列的空白 (或可以放总工时)
        for s in shifts:
            if s != off_shift_name:
                daily_stats_row[f"统计-{s}"] = "-"
                
        # 将统计行追加到 DataFrame 底部
        df_final = pd.concat([df_result, pd.DataFrame([daily_stats_row])], ignore_index=True)
        
        return df_final, "成功"
    else:
        return None, "冲突"

# --- 运行区 ---
st.markdown("###")
if st.button("🚀 生成 V4 排班表", type="primary"):
    with st.spinner("AI 正在优化班次结构..."):
        result_df, msg = solve_schedule_v4()
        
        if result_df is not None:
            st.success("✅ 排班完成！已生成统计数据。")
            
            # 样式优化
            def highlight_cells(val):
                if off_shift_name in str(val): return 'background-color: #e2e3e5; color: #666'
                if "晚" in str(val): return 'background-color: #fff3cd'
                if "统计" in str(val): return 'font-weight: bold' 
                return ''

            st.dataframe(result_df.style.applymap(highlight_cells), use_container_width=True)
            
            # 导出
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                result_df.to_excel(writer, index=False)
            st.download_button("📥 下载 Excel", output.getvalue(), "智能排班表_V4.xlsx")
        else:
            st.error("❌ 排班失败：无法同时满足所有硬性条件。")
            st.warning("建议检查：是否指定了太多人休息，导致某一天达不到最少人数要求？")
