import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import math

# --- 0. 页面配置与 UI 重构 (完全回归 V14 的高颜值风格) ---
st.set_page_config(page_title="智能排班 V16.0 (终极修正版)", layout="wide", page_icon="💎")

if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_report' not in st.session_state:
    st.session_state.audit_report = []

st.markdown("""
    <style>
    /* 1. 全局字体与背景 (回归清爽) */
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        background-color: #f7f9fc;
    }
    
    /* 2. 卡片式布局 (V14 风格回归) */
    .css-card {
        background-color: white;
        padding: 24px;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        margin-bottom: 20px;
        border: 1px solid #edf2f7;
    }
    .card-title {
        font-size: 16px;
        font-weight: 700;
        color: #1a202c;
        margin-bottom: 16px;
        border-left: 4px solid #3182ce;
        padding-left: 10px;
    }
    
    /* 3. 输入框美化 */
    .stTextInput>div>div>input, .stNumberInput>div>div>input, .stSelectbox>div>div, .stTextArea>div>div>textarea {
        border-radius: 6px;
        border: 1px solid #cbd5e0;
    }
    
    /* 4. 生成按钮 (全宽、悬浮感、大圆角) */
    .stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #3182ce 0%, #2b6cb0 100%) !important;
        color: white !important;
        font-size: 20px !important;
        font-weight: 600 !important;
        padding: 16px 0 !important;
        border-radius: 10px !important;
        border: none !important;
        box-shadow: 0 4px 6px rgba(49, 130, 206, 0.3);
        transition: all 0.2s;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(49, 130, 206, 0.4);
    }
    
    /* 5. 审计日志区 (美化版) */
    .audit-container {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 15px;
        max-height: 400px;
        overflow-y: auto;
    }
    .log-item {
        padding: 8px 12px;
        margin-bottom: 6px;
        border-radius: 6px;
        font-size: 14px;
        display: flex;
        align-items: center;
    }
    .log-err {background-color: #fff5f5; color: #c53030; border-left: 4px solid #c53030;}
    .log-warn {background-color: #fffaf0; color: #c05621; border-left: 4px solid #c05621;}
    .log-pass {background-color: #f0fff4; color: #2f855a; border-left: 4px solid #2f855a;}
    .log-header {font-weight: bold; margin-top: 15px; margin-bottom: 5px; color: #4a5568; border-bottom: 1px dashed #cbd5e0;}

    /* 6. 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("💎 智能排班 V16.0 - 终极修正版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏：基础档案 (保留美观样式) ---
with st.sidebar:
    st.markdown('<div class="css-card"><div class="card-title">📂 基础档案</div>', unsafe_allow_html=True)
    
    default_employees = "张三\n李四\n王五\n赵六\n钱七\n孙八\n周九\n吴十\n郑十一\n王十二"
    emp_input = st.text_area("员工名单 (Excel直接粘贴)", default_employees, height=150)
    employees = [e.strip() for e in emp_input.replace('\n', ',').replace('，', ',').split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 
    
    st.markdown("---")
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1)
        with c2: day_shift = st.selectbox("早班", shift_work, index=0)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 2. 顶部：逻辑透明化 (绝不阉割) ---
col_logic_1, col_logic_2 = st.columns(2)

with col_logic_1:
    with st.expander("⚖️ 平衡性与波动阈值 (V16修正)", expanded=True):
        st.info("💡 如果排班结果差值超过设定，系统会在日志中报错。")
        p1, p2 = st.columns(2)
        with p1: diff_daily_threshold = st.number_input("每日人数允许差值", 0, 5, 0, help="设为0表示必须完全平。")
        with p2: diff_period_threshold = st.number_input("员工工时允许差值", 0, 5, 2, help="员工之间工作天数最大差距。")

with col_logic_2:
    with st.expander("📜 查看底层逻辑权重"):
        st.markdown("""
        **后台逻辑优先级 (权重从高到低):**
        1.  **🔥 活动需求** (硬约束) - *绝对优先*
        2.  **🚫 0排班禁令** (硬约束) - *绝对不排*
        3.  **⚖️ 每日人数波动** (权重: 5,000,000) - *强制拉平*
        4.  **🔄 最大连班** (权重: 2,000,000) - *红线指标*
        5.  **🧱 每日基线** (权重: 1,000,000) - *保运营*
        6.  **🛌 休息模式** (权重: 500,000) - *保休息*
        7.  **❌ 个人拒绝/指定休** (权重: 50,000) - *尽量满足*
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
    
    rest_mode = st.selectbox("休息模式 (硬指标)", ["做6休1", "做5休2", "自定义"], index=0)
    if rest_mode == "做6休1": target_off_days = num_days // 7
    elif rest_mode == "做5休2": target_off_days = (num_days // 7) * 2
    else: target_off_days = st.number_input(f"周期内应休几天?", min_value=0, value=1)
    
    max_consecutive = st.number_input("最大连班限制", 1, 14, 6)
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
    st.markdown('<div class="card-title">🧱 每日班次基线</div>', unsafe_allow_html=True)
    st.caption("注：设为 0 = 🚫 绝对禁止排班")
    
    min_staff_per_shift = {}
    for s in shift_work:
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("###")
    generate_btn = st.button("🚀 立即执行智能排班 (自检版)")

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
            "上期末班": st.column_config.SelectboxColumn(options=shifts),
            "指定休息日": st.column_config.TextColumn(help="填数字如 1,3"),
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""]+shift_work),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""]+shift_work)
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

# --- 5. 核心算法 V16 (修复审计漏报 Bug) ---
def solve_schedule_v16():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # === 权重体系 ===
    W_ACTIVITY = 10000000
    W_DAILY_BALANCE = 5000000 # 每日平衡
    W_CONSECUTIVE = 2000000   # 连班
    W_BASELINE = 1000000      # 基线
    W_REST_STRICT = 500000    # 休息
    W_PERIOD_BALANCE = 100000 # 工时平衡
    W_FATIGUE = 50000
    W_REFUSE = 20000

    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')

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

    # S1. 每日基线
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue
            s_idx = s_map[s_name]
            actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
            model.Add(shortage >= min_val - actual)
            model.Add(shortage >= 0)
            penalties.append(shortage * W_BASELINE)

    # S2. 休息模式
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        diff_rest = model.NewIntVar(0, num_days, f'diff_r_{e}')
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        penalties.append(diff_rest * W_REST_STRICT)

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

        # 指定休息日 (添加惩罚)
        req_off = str(row["指定休息日"])
        if req_off.strip():
            try:
                days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                for d in days:
                    if 0 <= d < num_days:
                        # 没休则罚 5万 (与拒绝同级)
                        is_work = model.NewBoolVar(f'vio_off_{idx}_{d}')
                        model.Add(shift_vars[(idx, d, off_idx)] == 0).OnlyEnforceIf(is_work)
                        model.Add(shift_vars[(idx, d, off_idx)] == 1).OnlyEnforceIf(is_work.Not())
                        penalties.append(is_work * 50000)
            except: pass

    # S6. 强力平衡 (Max - Min <= Threshold)
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        
        # 每日波动
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d, min_d = model.NewIntVar(0, len(employees), ''), model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        excess_d = model.NewIntVar(0, len(employees), '')
        model.Add(excess_d >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess_d * W_DAILY_BALANCE)

        # 员工差异
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        max_e, min_e = model.NewIntVar(0, num_days, ''), model.NewIntVar(0, num_days, '')
        model.AddMaxEquality(max_e, e_counts)
        model.AddMinEquality(min_e, e_counts)
        excess_e = model.NewIntVar(0, num_days, '')
        model.Add(excess_e >= (max_e - min_e) - diff_period_threshold)
        penalties.append(excess_e * W_PERIOD_BALANCE)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 25.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # --- 6. 严苛审计逻辑 (Python Side Audit - FIX BUG) ---
        audit_logs = []
        
        # 构建结果矩阵
        res_matrix = [] # [employee][day] -> shift_name
        for e in range(len(employees)):
            row = []
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        break
            res_matrix.append(row)
            
        # 审计1: 指定休息日 (修复漏报问题)
        audit_logs.append("<div class='log-header'>1. 指定休息日检测 (Specific Rest)</div>")
        off_fail_count = 0
        for idx, row in edited_df.iterrows():
            req_off = str(row["指定休息日"])
            if req_off.strip():
                try:
                    days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                    for d in days:
                        if 0 <= d < num_days:
                            actual_shift = res_matrix[idx][d]
                            if actual_shift != off_shift_name:
                                # 之前这里漏报了，现在修复
                                audit_logs.append(f"<div class='log-item log-err'>❌ {employees[idx]} 指定第{d+1}天休，但排了: {actual_shift} (资源冲突)</div>")
                                off_fail_count += 1
                except: pass
        if off_fail_count == 0: audit_logs.append("<div class='log-item log-pass'>✅ 所有指定休息请求均已满足</div>")

        # 审计2: 每日人数波动
        audit_logs.append("<div class='log-header'>2. 每日人数波动 (Daily Balance)</div>")
        for s_name in shift_work:
            if min_staff_per_shift.get(s_name, 0) == 0: continue
            counts = []
            for d in range(num_days):
                c = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                counts.append(c)
            diff = max(counts) - min(counts)
            if diff > diff_daily_threshold:
                audit_logs.append(f"<div class='log-item log-err'>❌ {s_name}: 波动 {diff} (最大{max(counts)}/最小{min(counts)}) > 阈值 {diff_daily_threshold}</div>")
            else:
                audit_logs.append(f"<div class='log-item log-pass'>✅ {s_name}: 波动 {diff} (达标)</div>")

        # 审计3: 员工工时差异
        audit_logs.append("<div class='log-header'>3. 员工工时公平 (Worker Fairness)</div>")
        for s_name in shift_work:
            e_counts = []
            for e in range(len(employees)):
                c = sum(1 for d in range(num_days) if res_matrix[e][d] == s_name)
                e_counts.append(c)
            diff = max(e_counts) - min(e_counts)
            if diff > diff_period_threshold:
                audit_logs.append(f"<div class='log-item log-err'>❌ {s_name}: 差异 {diff} (最忙{max(e_counts)}/最闲{min(e_counts)}) > 阈值 {diff_period_threshold}</div>")
            else:
                audit_logs.append(f"<div class='log-item log-pass'>✅ {s_name}: 差异 {diff} (达标)</div>")

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
    
    return None, ["❌ 求解失败：硬性冲突无法解决 (如每日基线 > 总人数)。"]

# --- 6. 执行与显示 ---
if generate_btn:
    with st.spinner("🚀 AI 正在运算 (V16 Core)..."):
        df, logs = solve_schedule_v16()
        st.session_state.result_df = df
        st.session_state.audit_report = logs

if st.session_state.result_df is not None:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 审计日志 & 排班结果</div>', unsafe_allow_html=True)
    
    # 审计日志
    log_html = "<div class='audit-container'>" + "".join(st.session_state.audit_report) + "</div>"
    st.markdown(log_html, unsafe_allow_html=True)
    st.markdown("###")
    
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f8f9fa; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #ebf8ff; color: #2b6cb0'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出 Excel", output.getvalue(), "智能排班_V16.xlsx")
    
    st.markdown('</div>', unsafe_allow_html=True)
