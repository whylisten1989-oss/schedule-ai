import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import math

# --- 0. 页面配置 ---
st.set_page_config(page_title="AI智能排班系统 V19.0 [DAIXUAN]", layout="wide", page_icon="💎")

if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_report' not in st.session_state:
    st.session_state.audit_report = []

st.markdown("""
    <style>
    /* 全局字体 */
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        background-color: #f7f9fc;
    }

/* 隐藏底部的 "Built with Streamlit" */
    footer {
        visibility: hidden;
    }
    
    /* 卡片布局 */
    .css-card {
        background-color: white; padding: 24px; border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 20px; border: 1px solid #edf2f7;
    }
    .card-title {
        font-size: 16px; font-weight: 700; color: #1a202c; margin-bottom: 16px;
        border-left: 4px solid #3182ce; padding-left: 10px;
    }
    
    /* 输入框统一 */
    .stTextInput>div>div>input, .stNumberInput>div>div>input, .stSelectbox>div>div, .stTextArea>div>div>textarea {
        border-radius: 6px; border: 1px solid #cbd5e0;
    }

   /* 强制表头居中 (Input Table Headers) */
    div[data-testid="stDataEditor"] div[role="columnheader"] {
        justify-content: center !important;
        text-align: center !important;
    }
    
    /* 结果表 (Output Table) 强制居中 */
    div[data-testid="stDataFrame"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="columnheader"] {
        justify-content: center !important;
        text-align: center !important;
    }

    
    /* 生成按钮 */
    .stButton > button {
        width: 100%; background: linear-gradient(135deg, #3182ce 0%, #2b6cb0 100%) !important;
        color: white !important; font-size: 20px !important; font-weight: 600 !important;
        padding: 16px 0 !important; border-radius: 10px !important; border: none !important;
        box-shadow: 0 4px 6px rgba(49, 130, 206, 0.3); transition: all 0.2s;
    }
    .stButton > button:hover {
        transform: translateY(-2px); box-shadow: 0 6px 12px rgba(49, 130, 206, 0.4);
    }
    
    /* 审计日志固定高度与滚动 */
    .audit-container {
        background-color: #ffffff;
        border: 1px solid #e2e8f0; border-radius: 8px;
        padding: 15px;
        height: 300px; /* 固定高度 */
        overflow-y: auto; /* 右侧滚动条 */
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.02);
    }
    .log-item {
        padding: 6px 10px; margin-bottom: 4px; border-radius: 4px; font-size: 13px;
        display: flex; align-items: center; border-bottom: 1px solid #f7fafc;
    }
    .log-err {background-color: #fff5f5; color: #c53030; font-weight: 600; border-left: 3px solid #c53030;}
    .log-warn {background-color: #fffaf0; color: #c05621; border-left: 3px solid #c05621;}
    .log-pass {background-color: #f0fff4; color: #2f855a; border-left: 3px solid #2f855a;}
    .log-header {
        font-weight: 800; margin-top: 15px; margin-bottom: 8px; color: #2d3748; 
        background-color: #edf2f7; padding: 5px 10px; border-radius: 4px;
    }

    /* 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("💎 AI智能排班系统 V19.0 [DAIXUAN]")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏 ---
with st.sidebar:
    st.markdown('<div class="css-card"><div class="card-title">📂 基础档案</div>', unsafe_allow_html=True)
    default_employees = "张三\n李四\n王五\n赵六\n钱七\n孙八\n周九\n吴十\n郑十一\n王十二"
    emp_input = st.text_area("员工名单 (Excel直接粘贴)", default_employees, height=150, 
                             help="直接粘贴一列名字，系统会自动识别。")
    employees = [e.strip() for e in emp_input.replace('\n', ',').replace('，', ',').split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休", help="用逗号分隔，必须包含'休'字")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 
    
    st.markdown("---")
    # 小问号回归
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True, help="防止员工昨天上晚班，今天立刻上早班。")
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1, help="选择哪个是晚班")
        with c2: day_shift = st.selectbox("早班", shift_work, index=0, help="选择哪个是早班")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 2. 顶部逻辑 ---
col_logic_1, col_logic_2 = st.columns(2)
with col_logic_1:
    with st.expander("⚖️ 平衡性与波动阈值", expanded=True):
        st.info("💡 系统会尽量把差异控制在以下范围内，如果超出，审计日志会报错。")
        p1, p2 = st.columns(2)
        # 小问号回归
        with p1: diff_daily_threshold = st.number_input("每日人数允许差值", 0, 5, 0, help="例如设为0：每天的早班人数必须完全一样。")
        with p2: diff_period_threshold = st.number_input("员工工时允许差值", 0, 5, 2, help="例如设为2：张三和李四的总工时差距不能超过2天。")
with col_logic_2:
    with st.expander("📜 查看底层逻辑权重"):
        st.markdown("""
        1. 🔥 **活动/大促需求** (硬约束)
        2. 🚫 **0排班禁令** (硬约束)
        3. 🚫 **每日班次基准线** (硬约束)
        4. 🚫 **周期内休息日安排** (硬约束)
        3. ⚖️ **每日波动** (5,000,000) - *强力抹平*
        5. 🔄 **最大连班** (2,000,000) - *红线*
        6. 🧱 **每日基线** (1,000,000) - *保运营*
        7. 🛌 **休息模式** (500,000) - *保休息*
        7. ⚖️ **工时平衡** (100,000) - *强力平均*
        8. ⚖️ **指定休息日** (50,000) - *会在硬约束下失效*
        8. ⚖️ **禁止晚转早** (50,000) - *会在活动需求下失效*
        8. ⚖️ **个人拒绝班次** (20,000) - 
        8. ⚖️ **个人减少班次** (1000) - 
        """)

# --- 3. 主控制区 ---
col_ctrl, col_data = st.columns([1, 1.2])

with col_ctrl:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📅 排班设定</div>', unsafe_allow_html=True)
    
    c_d1, c_d2 = st.columns(2)
    with c_d1: start_date = st.date_input("开始日期", datetime.date.today())
    with c_d2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
    
    if start_date > end_date: st.error("日期错"); st.stop()
    num_days = (end_date - start_date).days + 1
    
    rest_mode = st.selectbox("休息模式 (硬指标)", ["做6休1", "做5休2", "自定义"], index=0, help="规定周期内必须休几天，少休或多休都会罚分。")
    if rest_mode == "做6休1": target_off_days = num_days // 7
    elif rest_mode == "做5休2": target_off_days = (num_days // 7) * 2
    else: target_off_days = st.number_input(f"周期内应休几天?", min_value=0, value=1)
    
    max_consecutive = st.number_input("最大连班限制", 1, 14, 6, help="连续工作超过此天数将触发严重警告。")
    st.markdown('</div>', unsafe_allow_html=True)

# 智能计算
total_capacity = len(employees) * (num_days - target_off_days)
daily_capacity = total_capacity / num_days
suggested_min = math.floor(daily_capacity / len(shift_work))

with col_data:
    st.markdown('<div class="css-card" style="height: 100%;">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📊 人力资源看板</div>', unsafe_allow_html=True)
    
    m1, m2 = st.columns(2)
    m1.metric("总人力", f"{len(employees)} 人")
    m2.metric("总可用工时", f"{total_capacity} 人天")
    m3, m4 = st.columns(2)
    m3.metric("日均运力", f"{daily_capacity:.1f} 人")
    m4.metric("建议单班基线", f"{suggested_min} 人", delta="推荐值")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. 详细配置区 ---
col_base, col_req = st.columns([1, 2.5])

with col_base:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🧱 每日班次基线-最低人数设定</div>', unsafe_allow_html=True)
    min_staff_per_shift = {}
    for s in shift_work:
        # 小问号回归
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}", help=f"每天【{s}】最少需要几人？设为0则完全不排。")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("###")
    generate_btn = st.button("🚀 开始AI智能排班")

with col_req:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">1. 🙋‍♂️ 员工个性化需求</div>', unsafe_allow_html=True)
    init_data = {
        "姓名": employees, "上期末班": [off_shift_name]*len(employees),
        "指定休息日": [""]*len(employees), "拒绝班次(强)": [""]*len(employees), "减少班次(弱)": [""]*len(employees)
    }
    edited_df = st.data_editor(
        pd.DataFrame(init_data),
        column_config={
            "姓名": st.column_config.TextColumn(disabled=True),
            "上期末班": st.column_config.SelectboxColumn(options=shifts, help="用于衔接昨日班次"),
            "指定休息日": st.column_config.TextColumn(help="填数字如 1,3"),
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="权重 20000"),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="权重 100")
        }, hide_index=True, use_container_width=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">2. 🔥 活动/大促需求</div>', unsafe_allow_html=True)
    activity_data = {"活动名称": ["大促预热", "双11爆发"], "日期": [None, None], "指定班次": [shift_work[0], shift_work[0]], "所需人数": [len(employees), len(employees)]}
    date_tuples = get_date_tuple(start_date, end_date)
    date_headers_simple = [f"{d} {w}" for d, w in date_tuples]
    
    edited_activity = st.data_editor(
        pd.DataFrame(activity_data), num_rows="dynamic",
        column_config={
            "日期": st.column_config.SelectboxColumn(options=date_headers_simple),
            "指定班次": st.column_config.SelectboxColumn(options=shift_work),
            "所需人数": st.column_config.NumberColumn(min_value=0, max_value=len(employees))
        }, use_container_width=True, key="activity_editor"
    )
    st.markdown('</div>', unsafe_allow_html=True)

# --- 5. 核心算法 ---
def solve_schedule_v19():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # === 权重体系 ===
    W_ACTIVITY = 10000000
    W_DAILY_BALANCE = 5000000 
    W_CONSECUTIVE = 2000000
    W_BASELINE = 1000000
    W_REST_STRICT = 500000
    W_PERIOD_BALANCE = 100000
    W_FATIGUE = 50000
    W_REFUSE = 20000

    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')
                
    # --- 0. 预处理：找出哪些天有活动 (为了实现基线智能让路) ---
    # 只要这天有活动需求，就标记为“战时状态”
    activity_day_indices = set()
    for idx, row in edited_activity.iterrows():
        if row["日期"] and row["指定班次"]:
            try:
                # 找到这天在日期列表里的索引 (0, 1, 2...)
                d_idx = date_headers_simple.index(row["日期"])
                activity_day_indices.add(d_idx)
            except: pass

    
    # H1. 物理约束
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # H2. 0排班禁令
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0:
                s_idx = s_map[s_name]
                model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) == 0)

    # S0. 连班限制
    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        for d in range(num_days - max_consecutive):
            window = [sum(shift_vars[(e, d+k, w)] for w in work_indices) for k in range(max_consecutive + 1)]
            is_violation = model.NewBoolVar(f'cons_vio_{e}_{d}')
            model.Add(sum(window) > max_consecutive).OnlyEnforceIf(is_violation)
            model.Add(sum(window) <= max_consecutive).OnlyEnforceIf(is_violation.Not())
            penalties.append(is_violation * W_CONSECUTIVE)

   # S1. 每日基线 (V23 终极版: 智能条件硬约束)
    # 逻辑：
    # 1. 平时 (无活动日): 硬约束。必须达标，少一人都不行。
    # 2. 战时 (有活动日): 软约束(权重1000万)。给活动让路，避免无解。
    # ----------------------------------------------------------
    W_BASELINE_FLEX = 10000000 # 战时权重：一千万分，依然很高，但可以被牺牲
    
    for d in range(num_days):
        # 判断今天是不是“战时”
        is_war_time = (d in activity_day_indices)
        
        for s_name, min_val in min_staff_per_shift.items():
            if min_val > 0:
                s_idx = s_map[s_name]
                # 计算实际安排的人数
                actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
                
                if not is_war_time:
                    # 【平时】：硬约束！
                    # 没有活动的日子，必须严守基线，少一个人直接报错。
                    model.Add(actual >= min_val)
                else:
                    # 【战时】：智能降级为软约束。
                    # 有活动的日子，虽然也想守基线，但如果人被活动抢光了，允许基线不达标。
                    shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
                    # shortage = 目标 - 实际 (如果实际够了，shortage就是0)
                    model.Add(shortage >= min_val - actual)
                    model.Add(shortage >= 0)
                    # 只有在缺人的时候才罚分
                    penalties.append(shortage * W_BASELINE_FLEX)

    # S2. 休息模式
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        model.Add(actual_rest == target_off_days)

    # S3. 活动需求
    for idx, row in edited_activity.iterrows():
        if not row["日期"] or not row["指定班次"]: continue
        try:
            d_idx = date_headers_simple.index(row["日期"])
            s_idx = s_map[row["指定班次"]]
            req = int(row["所需人数"])
            if req > 0:
                model.Add(sum(shift_vars[(e, d_idx, s_idx)] for e in range(len(employees))) >= req)
        except: continue

    # S4. 晚转早
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                vio = model.NewBoolVar(f'fat_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + vio)
                penalties.append(vio * W_FATIGUE)
    
    # S5. 个人拒绝与减少
    for idx, row in edited_df.iterrows():
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * W_REFUSE)
        
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * 100)

        # 指定休息日
        req_off = str(row["指定休息日"])
        if req_off.strip():
            try:
                days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                for d in days:
                    if 0 <= d < num_days:
                        is_work = model.NewBoolVar(f'vio_off_{idx}_{d}')
                        model.Add(shift_vars[(idx, d, off_idx)] == 0).OnlyEnforceIf(is_work)
                        model.Add(shift_vars[(idx, d, off_idx)] == 1).OnlyEnforceIf(is_work.Not())
                        penalties.append(is_work * 50000)
            except: pass

    # S6. 强力平衡 (BUG FIX HERE)
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        
        # 1. 每日波动修复
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        # 必须分两行定义IntVar
        max_d = model.NewIntVar(0, len(employees), f'max_d_{s_name}')
        min_d = model.NewIntVar(0, len(employees), f'min_d_{s_name}')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        
        excess_d = model.NewIntVar(0, len(employees), f'ex_d_{s_name}')
        model.Add(excess_d >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess_d * W_DAILY_BALANCE)

        # 2. 员工公平修复
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        # 必须分两行定义IntVar
        max_e = model.NewIntVar(0, num_days, f'max_e_{s_name}')
        min_e = model.NewIntVar(0, num_days, f'min_e_{s_name}')
        model.AddMaxEquality(max_e, e_counts)
        model.AddMinEquality(min_e, e_counts)
        
        excess_e = model.NewIntVar(0, num_days, f'ex_e_{s_name}')
        model.Add(excess_e >= (max_e - min_e) - diff_period_threshold)
        penalties.append(excess_e * W_PERIOD_BALANCE)

    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 25.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # --- 6. 全维度审计逻辑 ---
        audit_logs = []
        
        res_matrix = [] 
        name_map = {name: i for i, name in enumerate(employees)}

        for e in range(len(employees)):
            row = []
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        break
            res_matrix.append(row)
        
        # 1. 活动需求
        audit_logs.append("<div class='log-header'>1. 🔥 活动需求检测</div>")
        act_fail = 0
        for idx, row in edited_activity.iterrows():
            if not row["日期"] or not row["指定班次"]: continue
            try:
                d_idx = date_headers_simple.index(row["日期"])
                s_name = row["指定班次"]
                req = int(row["所需人数"])
                actual = sum(1 for e in range(len(employees)) if res_matrix[e][d_idx] == s_name)
                if actual < req:
                    audit_logs.append(f"<div class='log-item log-err'>❌ {row['日期']} {s_name}: 实到{actual} / 需{req}</div>")
                    act_fail += 1
            except: pass
        if act_fail == 0: audit_logs.append("<div class='log-item log-pass'>✅ 所有活动需求已满足</div>")

        # 2. 每日基线
        audit_logs.append("<div class='log-header'>2. 🧱 每日基线检测</div>")
        base_fail = 0
        for d in range(num_days):
            for s_name, min_val in min_staff_per_shift.items():
                if min_val == 0: continue
                cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                if cnt < min_val:
                    audit_logs.append(f"<div class='log-item log-err'>❌ 第{d+1}天 {s_name}: 实到{cnt} / 需{min_val}</div>")
                    base_fail += 1
        if base_fail == 0: audit_logs.append("<div class='log-item log-pass'>✅ 每日基线全部达标</div>")

        # 3. 休息模式
        audit_logs.append("<div class='log-header'>3. 🛌 休息模式检测</div>")
        rest_fail = 0
        for e_idx, e_name in enumerate(employees):
            cnt = sum(1 for d in range(num_days) if res_matrix[e_idx][d] == off_shift_name)
            if cnt != target_off_days:
                audit_logs.append(f"<div class='log-item log-err'>❌ {e_name}: 休了 {cnt} 天 (目标 {target_off_days})</div>")
                rest_fail += 1
        if rest_fail == 0: audit_logs.append(f"<div class='log-item log-pass'>✅ 全员休息天数达标 ({target_off_days}天)</div>")

        
        # 审计4: 指定休息日 (增强版：含越界检查)
        audit_logs.append("<div class='log-header'>4. 🧘 指定休息日检测</div>")
        spec_rest_fail = 0
        for idx, row in edited_df.iterrows():
            name = row["姓名"]
            real_idx = name_map.get(name)
            if real_idx is None: continue
            
            req_off = str(row["指定休息日"])
            if req_off.strip():
                try:
                    # 解析用户输入的数字
                    days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                    for d in days:
                        # 情况A: 用户填写的数字在当前日期范围内 (正常检查)
                        if 0 <= d < num_days:
                            actual = res_matrix[real_idx][d]
                            if actual != off_shift_name:
                                date_str = date_headers_simple[d] # 获取具体日期显示，如 "02-13 周五"
                                audit_logs.append(f"<div class='log-item log-err'>❌ {name} 指定在 {date_str} (第{d+1}天) 休息，但排了: {actual}</div>")
                                spec_rest_fail += 1
                        
                        # 情况B: 用户填写的数字超出了当前日期范围 (新增报错)
                        else:
                            audit_logs.append(f"<div class='log-item log-warn'>⚠️ {name} 指定第 {d+1} 天休息，但当前排班只有 {num_days} 天 (已忽略)</div>")
                            # 注意：这里不算 fail，但要提示用户
                except: 
                    audit_logs.append(f"<div class='log-item log-warn'>⚠️ {name} 的休息日格式输入错误</div>")
        
        if spec_rest_fail == 0: audit_logs.append("<div class='log-item log-pass'>✅ 指定休息日全部满足 (范围内)</div>")

        # 5. 每日平衡
        audit_logs.append("<div class='log-header'>5. ⚖️ 每日平衡检测</div>")
        for s_name in shift_work:
            if min_staff_per_shift.get(s_name, 0) == 0: continue
            counts = []
            for d in range(num_days):
                c = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                counts.append(c)
            diff = max(counts) - min(counts)
            if diff > diff_daily_threshold:
                 audit_logs.append(f"<div class='log-item log-err'>❌ {s_name}: 波动 {diff} (阈值 {diff_daily_threshold})</div>")
            else:
                 audit_logs.append(f"<div class='log-item log-pass'>✅ {s_name}: 波动 {diff} (达标)</div>")

        # 6. 工时公平
        audit_logs.append("<div class='log-header'>6. ⚖️ 工时公平检测</div>")
        for s_name in shift_work:
            e_counts = []
            for e in range(len(employees)):
                c = sum(1 for d in range(num_days) if res_matrix[e][d] == s_name)
                e_counts.append(c)
            diff = max(e_counts) - min(e_counts)
            if diff > diff_period_threshold:
                audit_logs.append(f"<div class='log-item log-err'>❌ {s_name}: 差异 {diff} (阈值 {diff_period_threshold})</div>")
            else:
                audit_logs.append(f"<div class='log-item log-pass'>✅ {s_name}: 差异 {diff} (达标)</div>")

        # 7. 连班检测
        audit_logs.append("<div class='log-header'>7. 🔄 连班检测</div>")
        cons_fail = 0
        for e_idx, e_name in enumerate(employees):
            curr = 0; m_c = 0
            for d in range(num_days):
                if res_matrix[e_idx][d] != off_shift_name: curr+=1
                else: curr=0
                m_c = max(m_c, curr)
            if m_c > max_consecutive:
                audit_logs.append(f"<div class='log-item log-err'>❌ {e_name} 连班 {m_c} 天 (限 {max_consecutive})</div>")
                cons_fail += 1
        if cons_fail == 0: audit_logs.append(f"<div class='log-item log-pass'>✅ 连班检测通过 (上限 {max_consecutive})</div>")
            
        # 8. 新增：晚转早检测 (疲劳审计)
        if enable_no_night_to_day: # 只有开启了这个功能才检测
            audit_logs.append("<div class='log-header'>8. 🌙 晚转早检测 (Fatigue)</div>")
            fatigue_fail = 0
            for e_idx, e_name in enumerate(employees):
                for d in range(num_days - 1):
                    today_shift = res_matrix[e_idx][d]
                    tomorrow_shift = res_matrix[e_idx][d+1]
                    
                    # 检查：今天晚班 AND 明天早班
                    if today_shift == night_shift and tomorrow_shift == day_shift:
                        audit_logs.append(f"<div class='log-item log-err'>❌ {e_name}: 第{d+1}天{night_shift} -> 第{d+2}天{day_shift} (严重疲劳 硬性条件规则导致)</div>")
                        fatigue_fail += 1
            
            if fatigue_fail == 0:
                audit_logs.append(f"<div class='log-item log-pass'>✅ 无晚转早违规</div>")
        
        # 数据构建
        data_rows = []
        for e in range(len(employees)):
            row = [employees[e]]
            stats = {s: 0 for s in shifts}
            for d in range(num_days):
                s_name = res_matrix[e][d]
                row.append(s_name)
                stats[s_name] += 1
            for s in shift_work: row.append(stats[s])
            row.append(stats[off_shift_name])
            data_rows.append(row)
            
        footer_rows = []
        for s in shifts:
            r_s = [f"【{s}】"]
            for d in range(num_days):
                cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s)
                r_s.append(cnt)
            r_s.extend([""] * (len(shift_work)+1))
            footer_rows.append(r_s)

        date_tuples = get_date_tuple(start_date, end_date)
        cols = [("基本信息", "姓名")] + date_tuples + [("工时统计", s) for s in shift_work] + [("工时统计", "休息天数")]
        return pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols)), audit_logs
    
    return None, ["❌ 求解失败：硬性冲突无法解决。"]

# --- 6. 执行 ---
if generate_btn:
    # 【新增】强制清空旧状态，防止逻辑残留
    st.session_state.result_df = None
    st.session_state.audit_report = []
if generate_btn:
    with st.spinner("🚀 AI 正在运算 (V21 Core)..."):
        df, logs = solve_schedule_v19()
        st.session_state.result_df = df
        st.session_state.audit_report = logs

if st.session_state.result_df is not None:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 审计日志 & 排班结果</div>', unsafe_allow_html=True)

    st.session_state.result_df.index = range(1, len(st.session_state.result_df) + 1)
    
    # 审计日志区
    log_html = "<div class='audit-container'>" + "".join(st.session_state.audit_report) + "</div>"
    st.markdown(log_html, unsafe_allow_html=True)
    st.markdown("###")
    
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f8f9fa; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #ebf8ff; color: #2b6cb0'
        return ''
    
    st.dataframe(st.session_state.result_df.style.map(style_map), use_container_width=True, height=600)
    
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出 Excel", output.getvalue(), "智能排班系统_V18.xlsx")
    st.markdown('</div>', unsafe_allow_html=True)
