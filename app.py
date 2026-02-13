import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random
import math

# --- 0. 页面与深度 CSS 美化 ---
st.set_page_config(page_title="智能排班 V12.0 (完美交付版)", layout="wide", page_icon="💎")

# 初始化 Session State
if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_logs' not in st.session_state: # 专门存运行日志
    st.session_state.audit_logs = []

st.markdown("""
    <style>
    /* 1. 全局字体与背景 */
    .stApp {font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background-color: #f4f6f9;}
    
    /* 2. 输入框边框强化 (解决看不清的问题) */
    input, .stSelectbox div[data-baseweb="select"] > div, textarea {
        border: 1px solid #ced4da !important;
        border-radius: 6px !important;
        background-color: white !important;
    }
    
    /* 3. 侧边栏卡片美化 */
    section[data-testid="stSidebar"] > div {padding-top: 1rem;}
    .sidebar-card {
        background-color: white; border: 1px solid #e0e0e0; 
        border-radius: 8px; padding: 15px; margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .sidebar-title {font-weight: 700; color: #2c3e50; margin-bottom: 10px; border-bottom: 2px solid #f0f0f0; padding-bottom: 5px;}

    /* 4. 主区域卡片 */
    .main-card {
        background-color: white; padding: 25px; border-radius: 12px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.05); 
        border: 1px solid #e0e0e0; margin-bottom: 20px;
    }
    .card-header {font-size: 1.15em; font-weight: 700; color: #1f2937; margin-bottom: 15px; display: flex; align-items: center;}
    
    /* 5. 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    
    /* 6. 超大号 3D 生成按钮 */
    .stButton > button {
        width: 100%; 
        background: linear-gradient(145deg, #2ecc71, #27ae60) !important;
        color: white !important; 
        font-size: 22px !important; 
        font-weight: 800 !important;
        border: none !important; 
        border-radius: 12px !important;
        padding: 18px 0 !important;
        margin-top: 20px;
        box-shadow: 0 6px 0 #1e8449, 0 10px 10px rgba(0,0,0,0.2) !important; /* 3D效果 */
        transition: all 0.2s ease !important;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 0 #1e8449, 0 12px 15px rgba(0,0,0,0.3) !important;
    }
    .stButton > button:active {
        transform: translateY(4px); /* 按压效果 */
        box-shadow: 0 2px 0 #1e8449, 0 4px 5px rgba(0,0,0,0.2) !important;
    }

    /* 7. 日志区域样式 */
    .audit-pass {color: #2e7d32; font-weight: bold; padding: 2px 0;}
    .audit-fail {color: #c62828; font-weight: bold; background-color: #ffebee; padding: 5px; border-radius: 4px;}
    .audit-info {color: #1565c0;}
    </style>
""", unsafe_allow_html=True)

st.title("💎 智能排班 V12.0 - 完美交付版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏 (Excel 粘贴优化) ---
with st.sidebar:
    st.markdown('<div class="sidebar-card"><div class="sidebar-title">📂 基础档案</div>', unsafe_allow_html=True)
    
    # 员工名单优化：支持换行符，方便 Excel 粘贴
    default_employees = "张三\n李四\n王五\n赵六\n钱七\n孙八\n周九\n吴十\n郑十一\n王十二"
    emp_input = st.text_area("员工名单 (支持从Excel直接复制粘贴)", default_employees, height=150, 
                             help="直接复制一列名字粘贴进来，支持逗号或换行分隔。")
    # 处理逻辑：同时支持逗号和换行
    employees = [e.strip() for e in emp_input.replace('\n', ',').replace('，', ',').split(",") if e.strip()]
    
    st.caption(f"当前识别人数：{len(employees)} 人")
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休", help="用逗号分隔，必须包含'休'字")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card"><div class="sidebar-title">📏 基础规则</div>', unsafe_allow_html=True)
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True, help="如果昨天是晚班，今天不能是早班。活动期间可能会被迫打破。")
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1, help="定义哪个是晚班")
        with c2: day_shift = st.selectbox("早班", shift_work, index=0, help="定义哪个是早班")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 2. 顶部逻辑按钮 ---
col_logic_1, col_logic_2 = st.columns(2)
with col_logic_1:
    with st.expander("⚖️ 平衡性阈值设置 (点击调整)"):
        st.info("当没有硬性冲突时，AI 将尽量满足以下平衡标准：")
        p1, p2 = st.columns(2)
        with p1: diff_daily_threshold = st.number_input("每日人数允许波动", 0, 5, 1, help="周一5人，周二4人，波动为1。")
        with p2: diff_period_threshold = st.number_input("员工工时允许差异", 0, 5, 2, help="张三上5天，李四上3天，差异为2。")

with col_logic_2:
    with st.expander("📜 查看底层逻辑优先级"):
        st.markdown("""
        1.  🔥 **活动需求** (硬约束) - *绝对优先*
        2.  🚫 **0排班禁令** (硬约束) - *设为0则绝对不排*
        3.  🛌 **休息模式** (权重: 20w) - *强制达标*
        4.  🧱 **每日基线** (权重: 5w) - *保日常运营*
        5.  ❌ **个人拒绝** (权重: 1w) - *尽量满足*
        """)

# --- 3. 主控制区 ---
st.markdown("###")
col_ctrl, col_data = st.columns([1, 1.2])

with col_ctrl:
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">📅 排班设定</div>', unsafe_allow_html=True)
    
    c_d1, c_d2 = st.columns(2)
    with c_d1: start_date = st.date_input("开始日期", datetime.date.today())
    with c_d2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
    
    if start_date > end_date: st.error("日期错"); st.stop()
    num_days = (end_date - start_date).days + 1
    
    rest_mode = st.selectbox("休息模式 (硬指标)", ["做6休1", "做5休2", "自定义"], index=0, help="系统会强制每个人休够这么多天，少一天都会报错或重罚。")
    if rest_mode == "做6休1": target_off_days = num_days // 7
    elif rest_mode == "做5休2": target_off_days = (num_days // 7) * 2
    else: target_off_days = st.number_input(f"周期内应休几天?", min_value=0, value=1)
    
    max_consecutive = st.number_input("最大连班限制", 1, 14, 6, help="连续上班超过这个天数，系统会强制安排休息。")
    st.markdown('</div>', unsafe_allow_html=True)

# 智能计算
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

# --- 4. 详细配置区 ---
col_base, col_req = st.columns([1, 2.5])

with col_base:
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">🧱 每日班次基线</div>', unsafe_allow_html=True)
    st.caption("注：若设为 0，系统将**绝对禁止**排该班次。")
    
    min_staff_per_shift = {}
    for s in shift_work:
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}",
                              help=f"每天【{s}】至少需要几个人？设为0则完全不排。")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    # === 超大生成按钮 ===
    st.markdown("###")
    generate_btn = st.button("🚀 生成智能排班表")

with col_req:
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
            "上期末班": st.column_config.SelectboxColumn(options=shifts, help="用于衔接上一周期的排班，防止晚转早"),
            "指定休息日": st.column_config.TextColumn(help="填数字如 1,3。系统会尽力满足。"),
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="坚决不上。如果人手不够，系统会在日志里报错。"),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""]+shift_work, help="尽量不上。")
        }, hide_index=True, use_container_width=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">2. 🔥 活动/大促需求 (优先级最高)</div>', unsafe_allow_html=True)
    
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

# --- 5. 核心算法 V12 (含日志生成逻辑) ---
def solve_schedule_v12():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # 权重配置
    W_ACTIVITY = 1000000 
    W_REST_STRICT = 200000
    W_FATIGUE = 100000
    W_BASELINE = 50000 
    W_REFUSE = 10000
    W_BALANCE = 1000 
    W_REDUCE = 10

    # 1. 变量创建
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')

    # --- H1. 物理约束 ---
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # --- H2. 0排班禁令 (修复BUG的关键) ---
    # 如果用户在左下角设置某班次最少人数为0，则视为“该班次本日关闭”
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0:
                s_idx = s_map[s_name]
                # 强制所有人当天该班次为0
                model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) == 0)

    # --- S1. 休息模式 ---
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        diff_rest = model.NewIntVar(0, num_days, f'diff_r_{e}')
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        penalties.append(diff_rest * W_REST_STRICT)

    # --- S2. 活动需求 ---
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

    # --- S3. 每日基线 ---
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue # 0的情况上面处理了
            s_idx = s_map[s_name]
            actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
            model.Add(shortage >= min_val - actual)
            model.Add(shortage >= 0)
            penalties.append(shortage * W_BASELINE)

    # --- S4. 晚转早 ---
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                vio = model.NewBoolVar(f'fat_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + vio)
                penalties.append(vio * W_FATIGUE)
        # 历史衔接
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                v_h = model.NewBoolVar(f'fat_h_{idx}')
                model.Add(shift_vars[(idx, 0, d_idx)] <= v_h)
                penalties.append(v_h * W_FATIGUE)

    # --- S5. 个人拒绝与减少 ---
    for idx, row in edited_df.iterrows():
        # 拒绝
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * W_REFUSE)
        # 减少
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * W_REDUCE)
        # 指定休息日 (V12新增逻辑: 尽量满足)
        req_off = str(row["指定休息日"])
        if req_off.strip():
            try:
                days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                for d in days:
                    if 0 <= d < num_days:
                        # 如果没休，扣 5万分 (跟基线差不多)
                        is_work = model.NewBoolVar(f'vio_off_{idx}_{d}')
                        model.Add(shift_vars[(idx, d, off_idx)] == 0).OnlyEnforceIf(is_work)
                        model.Add(shift_vars[(idx, d, off_idx)] == 1).OnlyEnforceIf(is_work.Not())
                        penalties.append(is_work * 50000) 
            except: pass

    # --- S6. 平衡性 ---
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d, min_d = model.NewIntVar(0, len(employees), ''), model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        excess = model.NewIntVar(0, len(employees), '')
        model.Add(excess >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess * W_BALANCE * 10)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # --- 生成审计日志 (Audit Log) ---
        logs = []
        
        # 1. 检查休息模式
        rest_ok = 0
        for e in range(len(employees)):
            act = sum(solver.Value(shift_vars[(e, d, off_idx)]) for d in range(num_days))
            if act == target_off_days: rest_ok += 1
            else: logs.append(f"<div class='audit-fail'>⚠️ 休息偏差: {employees[e]} 休了 {act} 天 (目标 {target_off_days})</div>")
        if rest_ok == len(employees): logs.append(f"<div class='audit-pass'>✅ 休息达标率: 100% ({rest_ok}/{len(employees)})</div>")
        else: logs.append(f"<div class='audit-info'>ℹ️ 休息达标率: {rest_ok}/{len(employees)}</div>")

        # 2. 检查拒绝班次
        ref_fail = 0
        for idx, row in edited_df.iterrows():
            ref = row["拒绝班次(强)"]
            if ref and ref in shift_work:
                r_idx = s_map[ref]
                for d in range(num_days):
                    if solver.Value(shift_vars[(idx, d, r_idx)]) == 1:
                        logs.append(f"<div class='audit-fail'>⚠️ 拒绝未满足: {employees[idx]} 在 {date_headers_simple[d]} 上了 {ref}</div>")
                        ref_fail += 1
        if ref_fail == 0: logs.append("<div class='audit-pass'>✅ 个人拒绝需求: 全部满足</div>")

        # 3. 检查指定休息日
        off_fail = 0
        for idx, row in edited_df.iterrows():
            req_off = str(row["指定休息日"])
            if req_off.strip():
                try:
                    days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                    for d in days:
                        if 0 <= d < num_days:
                            if solver.Value(shift_vars[(idx, d, off_idx)]) == 0:
                                logs.append(f"<div class='audit-fail'>⚠️ 指定休未满足: {employees[idx]} 在 {date_headers_simple[d]} 上班了</div>")
                                off_fail += 1
                except: pass
        if off_fail == 0: logs.append("<div class='audit-pass'>✅ 指定休息日: 全部满足</div>")
        
        # 4. 检查0排班
        zero_fail = 0
        for d in range(num_days):
            for s_name, min_val in min_staff_per_shift.items():
                if min_val == 0:
                    s_idx = s_map[s_name]
                    cnt = sum(solver.Value(shift_vars[(e, d, s_idx)]) for e in range(len(employees)))
                    if cnt > 0: zero_fail += 1
        if zero_fail == 0: logs.append("<div class='audit-pass'>✅ 0排班禁令: 全部生效 (未出现违规排班)</div>")

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
        return pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols)), logs
    
    return None, ["❌ 排班失败：硬性冲突无法解决。"]

# --- 6. 执行与显示 ---
if generate_btn:
    with st.spinner("🚀 正在执行 V12 智能排班算法..."):
        df, logs = solve_schedule_v12()
        st.session_state.result_df = df
        st.session_state.audit_logs = logs

if st.session_state.result_df is not None:
    # 结果区域卡片
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">📋 排班结果 & 审计日志</div>', unsafe_allow_html=True)
    
    # 1. 显示审计日志 (Expandable)
    with st.expander("✅ 系统运行审计日志 (点击查看详细执行情况)", expanded=True):
        for log in st.session_state.audit_logs:
            st.markdown(log, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 2. 显示表格
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f8f9fa; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #e3f2fd'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    # 3. 导出
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出 V12 排班表 (Excel)", output.getvalue(), "智能排班_V12.xlsx")
    
    st.markdown('</div>', unsafe_allow_html=True)
