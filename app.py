import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random

# --- 页面配置 ---
st.set_page_config(page_title="智能排班 V6.0 (旗舰版)", layout="wide", page_icon="🧩")
st.title("🧩 智能排班系统 V6.0 - 旗舰体验版")

# --- CSS 样式注入：强制表格居中 ---
st.markdown("""
    <style>
    .stDataFrame {text-align: center !important;}
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        text-align: center !important;
        justify-content: center !important;
    }
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"] {
        text-align: center !important;
        justify-content: center !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    """生成 (日期, 周几) 的元组列表，用于多层表头"""
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    # 返回格式: [('02-01', '周四'), ('02-02', '周五')...]
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 基础数据配置 (侧边栏) ---
with st.sidebar:
    st.header("1. 人员与班次")
    
    # 员工名单
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十,郑十一,王十二"
    emp_input = st.text_area("员工名单", default_employees, height=80)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    # 班次设置
    shifts_input = st.text_input("班次定义 (必须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except:
        st.error("❌ 班次中必须包含'休'字！")
        st.stop()
        
    shift_work = [s for s in shifts if s != off_shift_name] # 工作班次列表

    st.markdown("---")
    st.header("2. 模式与限制")
    
    # 休息模式
    rest_mode = st.selectbox("休息模式", ["做6休1 (标准)", "做5休2 (双休)", "自定义天数"])
    
    # 晚转早
    enable_no_night_to_day = st.checkbox("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        night_shift = st.selectbox("晚班是?", shift_work, index=len(shift_work)-1)
        day_shift = st.selectbox("早班是?", shift_work, index=0)

# --- 主界面 ---
st.subheader("⚙️ 排班控制台")

# 日期选择
c1, c2, c3 = st.columns(3)
with c1:
    start_date = st.date_input("开始日期", datetime.date.today())
with c2:
    end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
with c3:
    num_days = (end_date - start_date).days + 1
    if rest_mode == "做6休1 (标准)":
        min_off_days = num_days // 7
    elif rest_mode == "做5休2 (双休)":
        min_off_days = (num_days // 7) * 2
    else:
        min_off_days = st.number_input(f"{num_days}天内最少休息几天?", min_value=0, value=1)
    
    max_consecutive_work = st.number_input("最大连续上班天数", min_value=1, max_value=12, value=6)

if start_date > end_date:
    st.error("日期无效")
    st.stop()

# 获取双层表头所需的元组
date_tuples = get_date_tuple(start_date, end_date)
# 为了方便索引，我们也需要一个简单的 list
date_headers_simple = [f"{d} {w}" for d, w in date_tuples]

# --- 规则配置区 ---
col_rule, col_table = st.columns([1, 3])

with col_rule:
    st.info(f"周期: {num_days}天 | 最少休: {min_off_days}天")
    st.markdown("##### 每日最少在岗")
    min_staff_per_shift = {}
    for s in shift_work:
        min_staff_per_shift[s] = st.number_input(f"{s}", min_value=0, value=1, key=f"min_{s}")

with col_table:
    st.markdown("##### 🙋‍♂️ 员工个性化需求表")
    
    init_data = {
        "姓名": employees,
        "上期末班": [off_shift_name for _ in employees],
        "指定休息日": ["" for _ in employees],
        "拒绝班次(强)": ["" for _ in employees],
        "减少班次(弱)": ["" for _ in employees]
    }
    
    df_requests = pd.DataFrame(init_data)
    
    # 更加美观的 Column Config
    edited_df = st.data_editor(
        df_requests,
        column_config={
            "姓名": st.column_config.TextColumn(disabled=True),
            "上期末班": st.column_config.SelectboxColumn(
                options=shifts, width="medium", help="昨天上的什么班？用于衔接"
            ),
            "指定休息日": st.column_config.TextColumn(
                width="medium", help="输入数字(如 1,3)，逗号隔开"
            ),
            "拒绝班次(强)": st.column_config.SelectboxColumn(
                options=[""] + shift_work, width="small", help="坚决不上，除非没人"
            ),
            "减少班次(弱)": st.column_config.SelectboxColumn(
                options=[""] + shift_work, width="small", help="尽量不上"
            )
        },
        hide_index=True,
        use_container_width=True
    )

# --- 核心算法 V6 ---
def solve_schedule_v6():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    
    penalties = []
    
    # 1. 创建变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # --- 硬约束 ---
    # H1. 每天每人1班
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)
            
    # H2. 每日最少人数
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= min_val)

    # H3. 最少休息天数
    for e in range(len(employees)):
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) >= min_off_days)
        
    # H4. 最大连续工作 (滑动窗口)
    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        for d in range(num_days - max_consecutive_work):
            window_vars = [shift_vars[(e, d + k, w)] for k in range(max_consecutive_work + 1) for w in work_indices]
            model.Add(sum(window_vars) <= max_consecutive_work)

    # H5. 晚转早 + 历史衔接
    if enable_no_night_to_day:
        n_idx = s_map[night_shift]
        d_idx = s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1)
        
        # 历史衔接
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                model.Add(shift_vars[(idx, 0, d_idx)] == 0)

    # --- 软约束 (带随机因子) ---
    
    warnings_check = [] 
    
    for index, row in edited_df.iterrows():
        e_idx = index
        
        # 1. 指定休息日 (权重 1000)
        req_days_str = str(row["指定休息日"])
        if req_days_str.strip():
            try:
                days_list = [int(x.strip()) - 1 for x in req_days_str.replace("，", ",").split(",") if x.strip().isdigit()]
                for day_req in days_list:
                    if 0 <= day_req < num_days:
                        is_off = shift_vars[(e_idx, day_req, off_idx)]
                        not_off = model.NewBoolVar(f'violate_rest_{e_idx}_{day_req}')
                        model.Add(is_off + not_off == 1) 
                        # 随机因子 0-5，打破平局
                        penalties.append(not_off * (1000 + random.randint(0, 5))) 
                        warnings_check.append({"type": "休息", "emp": employees[e_idx], "day": day_req, "var": is_off})
            except: pass

        # 2. 拒绝班次 (权重 100,000 - 极高，相当于软性硬约束)
        refuse = row["拒绝班次(强)"]
        if refuse and refuse in shift_work:
            r_idx = s_map[refuse]
            for d in range(num_days):
                is_shift = shift_vars[(e_idx, d, r_idx)]
                # 如果排了这个班，惩罚 100,000
                penalties.append(is_shift * (100000 + random.randint(0, 10)))
                warnings_check.append({"type": "拒绝", "emp": employees[e_idx], "day": d, "var": is_shift, "shift": refuse})

        # 3. 减少班次 (权重 10 - 较低)
        reduce_s = row["减少班次(弱)"]
        if reduce_s and reduce_s in shift_work:
            red_idx = s_map[reduce_s]
            count_red = sum(shift_vars[(e_idx, d, red_idx)] for d in range(num_days))
            penalties.append(count_red * (10 + random.randint(0, 2)))

    # 4. 公平性 (方差最小化)
    for s_name in shift_work:
        s_idx = s_map[s_name]
        counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        # 简易方差惩罚: sum((count - avg)^2) 较难实现，改用 minimize max - min
        max_c = model.NewIntVar(0, num_days, f'max_{s_name}')
        min_c = model.NewIntVar(0, num_days, f'min_{s_name}')
        model.AddMaxEquality(max_c, counts)
        model.AddMinEquality(min_c, counts)
        penalties.append((max_c - min_c) * 5) # 权重 5

    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # --- 结果构建 ---
        
        # 1. 警告收集
        warning_msgs = []
        for check in warnings_check:
            if check["type"] == "休息" and solver.Value(check["var"]) == 0:
                day_str = date_headers_simple[check["day"]]
                warning_msgs.append(f"⚠️ {check['emp']} {day_str} 的休息申请未满足")
            if check["type"] == "拒绝" and solver.Value(check["var"]) == 1:
                day_str = date_headers_simple[check["day"]]
                warning_msgs.append(f"🔴 严重冲突: {check['emp']} {day_str} 被迫安排了 {check['shift']} (人手不足)")

        # 2. 数据表构建
        data_rows = []
        for e in range(len(employees)):
            # 基础信息
            row = [employees[e]]
            stats = {s: 0 for s in shifts}
            
            # 每日排班
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        stats[shifts[s]] += 1
            
            # 右侧统计
            for s in shift_work:
                row.append(stats[s])
            row.append(stats[off_shift_name]) # 休息天数
            data_rows.append(row)

        # 3. 底部统计构建
        # 统计每一列(每一天) 各个班次的人数
        footer_rows = []
        
        # 在岗总人数
        row_total = ["【在岗总数】"]
        for d in range(num_days):
            count = sum(1 for r in data_rows if r[d+1] != off_shift_name)
            row_total.append(count)
        row_total.extend([""] * (len(shift_work) + 1)) # 补齐右侧空白
        footer_rows.append(row_total)
        
        # 各班次统计
        for s in shifts: # 包含休息
            row_s = [f"【{s}人数】"]
            for d in range(num_days):
                count = sum(1 for r in data_rows if r[d+1] == s)
                row_s.append(count)
            row_s.extend([""] * (len(shift_work) + 1))
            footer_rows.append(row_s)

        # --- DataFrame 组装 (MultiIndex) ---
        
        # 列头设计: 姓名 + [日期, 周几]... + [统计, 早班]...
        columns = [("基本信息", "姓名")]
        for d_str, w_str in date_tuples:
            columns.append((d_str, w_str))
        for s in shift_work:
            columns.append(("班次统计", s))
        columns.append(("班次统计", "休息"))
        
        # 创建 MultiIndex
        multi_columns = pd.MultiIndex.from_tuples(columns)
        
        # 合并数据
        all_data = data_rows + footer_rows
        df_final = pd.DataFrame(all_data, columns=multi_columns)
        
        return df_final, warning_msgs
    else:
        return None, ["❌ 无法生成排班，请检查硬性约束（如每日最少人数是否大于总人数）。"]

# --- 运行 ---
st.markdown("###")
if st.button("🚀 生成 V6 旗舰排班表", type="primary"):
    with st.spinner("AI 正在进行随机冲突检测与优化..."):
        result_df, msgs = solve_schedule_v6()
        
        if result_df is not None:
            if msgs:
                with st.expander("⚠️ 冲突报告", expanded=True):
                    for m in msgs: 
                        if "🔴" in m: st.error(m)
                        else: st.warning(m)
            else:
                st.success("✅ 排班完美，无冲突！")
            
            # 样式设置
            def color_code(val):
                s_val = str(val)
                if off_shift_name in s_val: return 'background-color: #f0f2f6; color: #ccc'
                if "晚" in s_val: return 'background-color: #fff3cd; color: #856404'
                if "【" in s_val: return 'font-weight: bold; background-color: #e6f3ff'
                return ''

            st.dataframe(
                result_df.style.applymap(color_code).set_properties(**{'text-align': 'center'}), 
                use_container_width=True,
                height=600 # 增加高度
            )
            
            # 导出 (扁平化表头以便 Excel 读取)
            output = io.BytesIO()
            # 导出时把 MultiIndex 压扁，变成 "02-01 (周四)" 格式
            export_df = result_df.copy()
            new_cols = []
            for c in export_df.columns:
                if c[0] == "基本信息" or c[0] == "班次统计":
                    new_cols.append(c[1])
                else:
                    new_cols.append(f"{c[0]}\n{c[1]}")
            export_df.columns = new_cols
            
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                export_df.to_excel(writer, index=False)
            st.download_button("📥 下载 Excel", output.getvalue(), "排班表_V6.xlsx")
        else:
            st.error("无法生成排班，请放宽条件。")
