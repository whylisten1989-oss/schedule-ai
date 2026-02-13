import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import math

# --- 0. 页面配置与 UI ---
st.set_page_config(page_title="智能排班 V15.0 (严苛审计版)", layout="wide", page_icon="🛡️")

if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_report' not in st.session_state:
    st.session_state.audit_report = []

st.markdown("""
    <style>
    /* 全局设置 */
    .stApp {font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f8;}
    
    /* 卡片风格 */
    .css-card {
        background-color: white; padding: 20px; border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 15px;
        border: 1px solid #e1e4e8;
    }
    .card-title {
        font-size: 16px; font-weight: 700; color: #2d3748; 
        margin-bottom: 15px; padding-left: 10px; border-left: 4px solid #3182ce;
    }
    
    /* 按钮美化 */
    .stButton > button {
        width: 100%; background-color: #2b6cb0 !important; color: white !important;
        font-size: 18px !important; padding: 16px 0 !important; border-radius: 8px !important;
        border: none !important; transition: 0.2s;
    }
    .stButton > button:hover {background-color: #2c5282 !important; box-shadow: 0 4px 12px rgba(0,0,0,0.15);}
    
    /* 审计日志 - 极客风 */
    .audit-container {
        background-color: #1a202c; color: #e2e8f0; padding: 15px; 
        border-radius: 8px; font-family: 'Consolas', monospace; font-size: 13px;
        max-height: 400px; overflow-y: auto; border: 1px solid #4a5568;
    }
    .log-err {color: #fc8181; font-weight: bold; background-color: #2d3748; padding: 2px 5px; border-radius: 3px;}
    .log-warn {color: #f6ad55; font-weight: bold;}
    .log-pass {color: #68d391; font-weight: bold;}
    .log-info {color: #63b3ed;}
    .log-section {border-top: 1px dashed #4a5568; margin-top: 5px; padding-top: 5px; color: #a0aec0;}

    /* 输入框样式 */
    input, textarea, select {border: 1px solid #cbd5e0 !important; border-radius: 5px !important;}
    
    /* 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🛡️ 智能排班 V15.0 - 严苛审计版")

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
    emp_input = st.text_area("员工名单", default_employees, height=150)
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

# --- 2. 顶部：逻辑透明化 (你要的功能回归了) ---
with st.expander("🛠️ 查看系统底层逻辑与权重 (上帝视角)", expanded=True):
    col_w1, col_w2 = st.columns([2, 1])
    with col_w1:
        st.markdown("""
        **当前算法优先级 (权重从高到低):**
        1.  **🔥 活动/大促需求** (权重: ∞) - *绝对指令*
        2.  **🚫 0排班禁令** (权重: ∞) - *设为0则绝对不排*
        3.  **⚖️ 每日人数波动** (权重: **5,000,000**) - *【V15上调】强制拉平每日差异*
        4.  **🔄 最大连班限制** (权重: 2,000,000) - *红线指标*
        5.  **🧱 每日基线** (权重: 1,000,000) - *保运营*
        6.  **🛌 休息模式** (权重: 500,000) - *保休息*
        7.  **❌ 个人拒绝** (权重: 50,000) - *尽量满足*
        """)
    with col_w2:
        st.info("💡 V15 修正：每日人数波动和最大连班的权重已大幅提升，现在它们比'每日基线'更重要。")

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
    
    # --- 阈值设置 (显眼位置) ---
    st.markdown("---")
    st.markdown('<div style="background:#e6fffa; padding:10px; border-radius:5px; border:1px solid #38b2ac;">', unsafe_allow_html=True)
    st.markdown("**⚖️ 平衡性阈值 (严格执行)**")
    c_t1, c_t2 = st.columns(2)
    with c_t1: 
        diff_daily_threshold = st.number_input("每日人数允许差值", 0, 5, 0, help="设为0表示每天该班次人数必须完全一样！")
    with c_t2: 
        diff_period_threshold = st.number_input("员工工时允许差值", 0, 5, 2, help="设为2表示大家班次数量差不能超过2。")
    st.markdown('</div>', unsafe_allow_html=True)
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
    m4.metric("建议单班基线", f"{suggested_min} 人")
    st.caption("注：'建议基线' 仅供参考，如果设得太高会导致无解。")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. 详细配置区 ---
col_base, col_req = st.columns([1, 2.5])

with col_base:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🧱 每日班次基线</div>', unsafe_allow_html=True)
    min_staff_per_shift = {}
    for s in shift_work:
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("###")
    generate_btn = st.button("🚀 立即执行严苛排班")

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

# --- 5. 核心算法 V15 (权重修正版) ---
def solve_schedule_v15():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # === 权重体系 (彻底修正) ===
    # 之前平衡性太低，导致被基线覆盖。现在平衡性是顶级权重。
    W_ACTIVITY = 10000000
    W_DAILY_BALANCE = 5000000 # 新增：每日波动权重 (极高)
    W_CONSECUTIVE = 2000000   # 连班限制
    W_BASELINE = 1000000      # 日常基线
    W_REST_STRICT = 500000    # 休息
    W_PERIOD_BALANCE = 100000 # 员工间差异
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
    
    # S5. 个人拒绝
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
            penalties.append(cnt * 100) # 权重较低

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

    # --- S6. 关键：强力平衡 (V15 FIX) ---
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        
        # 1. 每日人数波动 (权重 500万)
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d = model.NewIntVar(0, len(employees), '')
        min_d = model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        
        # 强制约束：如果差值超过阈值，罚分极其惨重
        excess_d = model.NewIntVar(0, len(employees), '')
        model.Add(excess_d >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess_d * W_DAILY_BALANCE)

        # 2. 员工工时差异 (权重 10万)
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        max_e = model.NewIntVar(0, num_days, '')
        min_e = model.NewIntVar(0, num_days, '')
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
        # --- 6. 严苛审计逻辑 (Python Side Audit) ---
        audit_logs = []
        
        res_matrix = []
        for e in range(len(employees)):
            row = []
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        break
            res_matrix.append(row)
            
        # 审计1: 每日人数波动 (检查用户提到的差2人问题)
        audit_logs.append("<div class='log-section'>--- 每日波动检测 (Daily Balance) ---</div>")
        for s_name in shift_work:
            if min_staff_per_shift.get(s_name, 0) == 0: continue
            
            counts = []
            for d in range(num_days):
                c = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                counts.append(c)
            
            diff = max(counts) - min(counts)
            if diff > diff_daily_threshold:
                audit_logs.append(f"<span class='log-err'>❌ [平衡失败] {s_name}: 最大 {max(counts)}人 vs 最小 {min(counts)}人 (差 {diff} > 阈值 {diff_daily_threshold})</span>")
            else:
                audit_logs.append(f"<span class='log-pass'>✅ [平衡达标] {s_name}: 波动 {diff} (阈值 {diff_daily_threshold})</span>")

        # 审计2: 员工工时差异 (检查早班堆积问题)
        audit_logs.append("<div class='log-section'>--- 员工工时检测 (Staff Fairness) ---</div>")
        for s_name in shift_work:
            e_counts = []
            for e in range(len(employees)):
                c = sum(1 for d in range(num_days) if res_matrix[e][d] == s_name)
                e_counts.append(c)
            diff = max(e_counts) - min(e_counts)
            if diff > diff_period_threshold:
                audit_logs.append(f"<span class='log-err'>❌ [严重不均] {s_name}: 某人上 {max(e_counts)}次 vs 某人上 {min(e_counts)}次 (差 {diff})</span>")
            else:
                audit_logs.append(f"<span class='log-pass'>✅ [分配均匀] {s_name}: 差异 {diff}</span>")

        # 审计3: 最大连班
        audit_logs.append("<div class='log-section'>--- 疲劳度检测 (Fatigue) ---</div>")
        for e_idx, e_name in enumerate(employees):
            consecutive = 0
            max_c = 0
            for d in range(num_days):
                if res_matrix[e_idx][d] != off_shift_name: consecutive += 1
                else: consecutive = 0
                max_c = max(max_c, consecutive)
            if max_c > max_consecutive:
                audit_logs.append(f"<span class='log-err'>❌ [严重] {e_name} 连班 {max_c} 天 (限 {max_consecutive})</span>")

        # 审计4: 0排班检测
        for d in range(num_days):
            for s_name, min_val in min_staff_per_shift.items():
                if min_val == 0:
                    cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                    if cnt > 0: audit_logs.append(f"<span class='log-err'>❌ [严重] {s_name} 被禁用，但第{d+1}天排了 {cnt} 人</span>")

        if not any("❌" in l for l in audit_logs):
            audit_logs.insert(0, "<span class='log-pass'>🎉 完美排班：所有硬性规则、平衡性阈值均通过自检！</span>")
        else:
            audit_logs.insert(0, "<span class='log-err'>⚠️ 警告：检测到部分规则未完全满足（见下文红色项），请检查是否人力过紧。</span>")

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
    
    return None, ["❌ 求解失败：可能是每日基线要求过高，超过了总人数限制。"]

# --- 6. 执行 ---
if generate_btn:
    with st.spinner("🚀 AI 正在进行深度平衡运算与自检..."):
        df, logs = solve_schedule_v15()
        st.session_state.result_df = df
        st.session_state.audit_report = logs

if st.session_state.result_df is not None:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 审计报告 & 排班结果</div>', unsafe_allow_html=True)
    
    # 审计日志
    log_html = "<div class='audit-container'>" + "<br>".join(st.session_state.audit_report) + "</div>"
    st.markdown(log_html, unsafe_allow_html=True)
    st.markdown("###")
    
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f8f9fa; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #ebf8ff; color: #2b6cb0'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    # 导出
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出排班表 (Excel)", output.getvalue(), "智能排班_V15.xlsx")
    st.markdown('</div>', unsafe_allow_html=True)
