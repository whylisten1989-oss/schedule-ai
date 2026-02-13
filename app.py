import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random
import math

# --- 0. 页面与深度 CSS 美化 ---
st.set_page_config(page_title="智能排班 V11.0 (最终交付版)", layout="wide", page_icon="💎")

# 初始化 Session State (防止表格刷新消失)
if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'msgs' not in st.session_state:
    st.session_state.msgs = []

st.markdown("""
    <style>
    /* 全局字体与背景 */
    .stApp {font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background-color: #f0f2f5;}
    
    /* 侧边栏美化 - 卡片式 */
    section[data-testid="stSidebar"] > div {padding-top: 2rem;}
    .sidebar-card {
        background-color: white; border: 1px solid #d1d5db; 
        border-radius: 8px; padding: 15px; margin-bottom: 15px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    .sidebar-title {font-weight: bold; color: #374151; margin-bottom: 10px; border-bottom: 2px solid #e5e7eb; padding-bottom: 5px;}

    /* 主区域卡片 */
    .main-card {
        background-color: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); 
        border: 1px solid #e5e7eb; margin-bottom: 20px;
    }
    .card-header {font-size: 1.1em; font-weight: 700; color: #1f2937; margin-bottom: 15px;}
    
    /* 指标卡片 (Metrics) 紧凑化 */
    div[data-testid="metric-container"] {
        background-color: #f9fafb; border: 1px solid #e5e7eb;
        padding: 10px; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    
    /* 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    
    /* 生成按钮 - 悬浮动效 */
    .stButton > button {
        width: 100%; 
        background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
        color: white !important; font-size: 18px !important; font-weight: bold !important;
        border: none !important; border-radius: 10px !important;
        padding: 12px 0 !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 6px rgba(16, 185, 129, 0.3);
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 15px rgba(16, 185, 129, 0.4);
    }
    .stButton > button:active {transform: translateY(1px);}
    
    /* 顶部逻辑按钮微调 */
    .stExpander {border: 1px solid #e5e7eb; background-color: white; border-radius: 8px;}
    </style>
""", unsafe_allow_html=True)

st.title("💎 智能排班系统 V11.0 - 最终交付版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏 (带边框美化) ---
with st.sidebar:
    # 基础档案卡片
    st.markdown('<div class="sidebar-card"><div class="sidebar-title">📂 基础档案</div>', unsafe_allow_html=True)
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十,郑十一,王十二"
    emp_input = st.text_area("员工名单", default_employees, height=100)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 
    st.markdown('</div>', unsafe_allow_html=True)

    # 基础规则卡片
    st.markdown('<div class="sidebar-card"><div class="sidebar-title">📏 基础规则</div>', unsafe_allow_html=True)
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1)
        with c2: day_shift = st.selectbox("早班", shift_work, index=0)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 2. 顶部逻辑总览 (与代码一致) ---
with st.expander("📜 系统底层逻辑总览 (权重已更新)", expanded=False):
    st.markdown("""
    **后台逻辑优先级 (权重从高到低):**
    1.  🔥 **活动/大促需求** (权重: ∞) - *最高指令，覆盖一切*
    2.  🛌 **休息模式达标** (权重: 200,000) - *强制执行休息标准*
    3.  🚫 **禁止晚转早** (权重: 100,000) - *除非活动强制，否则禁止*
    4.  🧱 **每日班次基线** (权重: 50,000) - *保公司：必须满足每日最低人力*
    5.  ❌ **拒绝班次** (权重: 10,000) - *保个人：尽量不排拒绝的班，但人手不够时让位于基线*
    6.  ⚖️ **平衡性** (权重: 1,000) - *保公平：尽量大家一样多*
    7.  🔻 **减少班次** (权重: 10) - *软需求*
    """)

# --- 3. 紧凑布局区 (左控右显) ---
st.markdown("###")
col_ctrl, col_data = st.columns([1, 1.2]) # 左 1 : 右 1.2 比例

with col_ctrl:
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">📅 排班设定</div>', unsafe_allow_html=True)
    
    c_d1, c_d2 = st.columns(2)
    with c_d1: start_date = st.date_input("开始日期", datetime.date.today())
    with c_d2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
    
    if start_date > end_date: st.error("日期错"); st.stop()
    num_days = (end_date - start_date).days + 1
    
    rest_mode = st.selectbox("休息模式 (强制目标)", ["做6休1", "做5休2", "自定义"], index=0)
    if rest_mode == "做6休1": target_off_days = num_days // 7
    elif rest_mode == "做5休2": target_off_days = (num_days // 7) * 2
    else: target_off_days = st.number_input(f"周期内应休几天?", min_value=0, value=1)
    
    max_consecutive = st.number_input("最大连班限制", 1, 14, 6)
    
    # 阈值设置放入左侧
    with st.expander("⚖️ 平衡阈值设置"):
        diff_daily_threshold = st.number_input("每日人数允许波动", 0, 5, 1)
        diff_period_threshold = st.number_input("员工工时允许差异", 0, 5, 2)
    
    st.markdown('</div>', unsafe_allow_html=True)

# 智能计算建议值
total_capacity = len(employees) * (num_days - target_off_days)
daily_capacity = total_capacity / num_days
suggested_min = math.floor(daily_capacity / len(shift_work))

with col_data:
    st.markdown('<div class="main-card" style="height: 100%;">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">📊 人力资源看板</div>', unsafe_allow_html=True)
    
    m1, m2 = st.columns(2)
    m1.metric("总人力规模", f"{len(employees)} 人")
    m2.metric("周期总工时", f"{total_capacity} 人天")
    
    m3, m4 = st.columns(2)
    m3.metric("日均运力 (预估)", f"{daily_capacity:.1f} 人")
    m4.metric("建议单班基线", f"{suggested_min} 人", delta="基线参考")
    
    st.caption("注：'建议基线' 是基于总工时平摊到每个班次的理论值。")
    st.markdown('</div>', unsafe_allow_html=True)


# --- 4. 核心配置区 (每日基线 + 员工需求) ---
col_base, col_req = st.columns([1, 2.5])

# 左下方：每日基线
with col_base:
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">🧱 每日班次基线</div>', unsafe_allow_html=True)
    st.caption("优先级：高 (50,000分)")
    
    min_staff_per_shift = {}
    for s in shift_work:
        # 使用 key 强制刷新建议值
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    # --- 生成按钮放在基线下方 (视觉焦点) ---
    st.markdown("###")
    generate_btn = st.button("🚀 立即生成智能排班表")

# 右侧：详细需求
with col_req:
    # 员工个性化
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">1. 🙋‍♂️ 员工个性化需求</div>', unsafe_allow_html=True)
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
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="权重 10,000"),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="权重 10")
        }, hide_index=True, use_container_width=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

    # 活动需求
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">2. 🔥 活动/大促需求 (优先级最高)</div>', unsafe_allow_html=True)
    
    activity_data = {
        "活动名称": ["大促预热", "双11爆发"],
        "日期": [None, None], # 默认不填，由用户选
        "指定班次": [shift_work[0], shift_work[0]], 
        "所需人数": [len(employees), len(employees)]
    }
    # 预处理表头
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


# --- 5. 核心算法 (V11 Weights) ---
def solve_schedule_v11():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # === 权重配置 (User Defined Hierarchy) ===
    W_ACTIVITY = 1000000     # 活动
    W_REST_STRICT = 200000   # 休息
    W_FATIGUE = 100000       # 晚转早
    W_BASELINE = 50000       # 基线 (高于拒绝)
    W_REFUSE = 10000         # 拒绝 (低于基线)
    W_BALANCE = 1000         # 平衡 (高于减少)
    W_REDUCE = 10            # 减少

    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')

    # --- H1. 物理约束 ---
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # --- S1. 休息模式 (高权软约束) ---
    rest_warnings = []
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        diff_rest = model.NewIntVar(0, num_days, f'diff_r_{e}')
        # diff = |actual - target|
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        penalties.append(diff_rest * W_REST_STRICT)
        
        is_diff = model.NewBoolVar(f'is_rd_{e}')
        model.Add(diff_rest > 0).OnlyEnforceIf(is_diff)
        model.Add(diff_rest == 0).OnlyEnforceIf(is_diff.Not())
        rest_warnings.append({"e": employees[e], "v": is_diff, "act": actual_rest, "tgt": target_off_days})

    # --- S2. 活动需求 (硬约束) ---
    activity_dates = []
    for idx, row in edited_activity.iterrows():
        if not row["日期"] or not row["指定班次"]: continue
        try:
            d_idx = date_headers_simple.index(row["日期"])
            s_idx = s_map[row["指定班次"]]
            req = int(row["所需人数"])
            if req > 0:
                model.Add(sum(shift_vars[(e, d_idx, s_idx)] for e in range(len(employees))) >= req)
                activity_dates.append(row["日期"])
        except: continue

    # --- S3. 每日基线 (权重 50k - 高于拒绝) ---
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue
            s_idx = s_map[s_name]
            actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            # 允许不足，但重罚
            shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
            model.Add(shortage >= min_val - actual)
            model.Add(shortage >= 0)
            penalties.append(shortage * W_BASELINE)

    # --- S4. 晚转早 ---
    fatigue_warnings = []
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                vio = model.NewBoolVar(f'fat_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + vio)
                penalties.append(vio * W_FATIGUE)
                fatigue_warnings.append({"e": employees[e], "d": d, "v": vio, "date": date_headers_simple[d+1]})
        # 历史衔接
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                v_h = model.NewBoolVar(f'fat_h_{idx}')
                model.Add(shift_vars[(idx, 0, d_idx)] <= v_h)
                penalties.append(v_h * W_FATIGUE)
                fatigue_warnings.append({"e": employees[idx], "d": -1, "v": v_h, "date": date_headers_simple[0]})

    # --- S5. 个人拒绝与减少 ---
    personal_warnings = []
    for idx, row in edited_df.iterrows():
        # 拒绝 (权重 10k - 低于基线)
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * W_REFUSE)
                personal_warnings.append({"t": "拒", "e": employees[idx], "d": d, "v": is_s, "s": ref})
        
        # 减少 (权重 10)
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * W_REDUCE)

    # --- S6. 平衡性 (权重 1k) ---
    # 每日波动
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d, min_d = model.NewIntVar(0, len(employees), ''), model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        excess = model.NewIntVar(0, len(employees), '')
        model.Add(excess >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess * W_BALANCE)

    # 工时公平
    for s_name in shift_work:
        s_idx = s_map[s_name]
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        max_e, min_e = model.NewIntVar(0, num_days, ''), model.NewIntVar(0, num_days, '')
        model.AddMaxEquality(max_e, e_counts)
        model.AddMinEquality(min_e, e_counts)
        excess = model.NewIntVar(0, num_days, '')
        model.Add(excess >= (max_e - min_e) - diff_period_threshold)
        penalties.append(excess * W_BALANCE)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        msgs = []
        # 收集警告
        for w in rest_warnings:
            if solver.Value(w['v']) == 1:
                msgs.append(f"🔴 **休息偏差**: {w['e']} 休了 {solver.Value(w['act'])} 天 (目标 {w['tgt']})。原因: 活动挤占或基线过高。")
        for w in fatigue_warnings:
            if solver.Value(w['v']) == 1:
                reason = "🔥 活动强制" if w['date'] in activity_dates else "基线压力"
                msgs.append(f"🟠 **疲劳**: {w['e']} 在 {w['date']} 晚转早。原因: {reason}")
        for w in personal_warnings:
            if solver.Value(w['v']) == 1:
                msgs.append(f"⚪ **妥协**: {w['e']} 上了拒绝的 {w['s']} (为满足每日基线)。")

        # 数据构建
        data_rows = []
        for e in range(len(employees)):
            row = [employees[e]]
            stats = {s: 0 for s in shifts}
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        stats[shifts[s]] += 1
            for s in shift_work: row.append(stats[s])
            row.append(stats[off_shift_name])
            data_rows.append(row)
            
        footer_rows = []
        r_tot = ["【在岗总数】"]
        for d in range(num_days):
            cnt = sum(1 for r in data_rows if r[d+1] != off_shift_name)
            r_tot.append(cnt)
        r_tot.extend([""] * (len(shift_work)+1))
        footer_rows.append(r_tot)
        
        for s in shifts: 
            r_s = [f"【{s}人数】"]
            for d in range(num_days):
                cnt = sum(1 for r in data_rows if r[d+1] == s)
                r_s.append(cnt)
            r_s.extend([""] * (len(shift_work)+1))
            footer_rows.append(r_s)

        cols = [("基本信息", "姓名")] + date_tuples + [("工时统计", s) for s in shift_work] + [("工时统计", "休息天数")]
        return pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols)), msgs
    return None, ["❌ 仍然无法排班。这通常是因为硬性约束（物理限制）被打破。"]

# --- 6. 执行与显示 (持久化逻辑) ---
if generate_btn:
    with st.spinner("🚀 AI 引擎正在运算 (V11 内核)..."):
        df, msgs = solve_schedule_v11()
        st.session_state.result_df = df
        st.session_state.msgs = msgs

# 渲染结果 (如果有)
if st.session_state.result_df is not None:
    st.markdown("---")
    st.markdown("### 📋 排班结果")
    
    if st.session_state.msgs:
        with st.expander("⚠️ 冲突与妥协报告", expanded=True):
            for m in st.session_state.msgs: st.markdown(m)
    else:
        st.success("✅ 完美排班：所有规则均已满足！")
    
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f0f2f6; color: #ccc'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #e6f3ff'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出 Excel 排班表", output.getvalue(), "排班表_V11.xlsx")
