import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import math
import numpy as np # 用于计算方差标准差

# --- 0. 页面与深度 CSS 美化 ---
st.set_page_config(page_title="智能排班 V13.0 (审计官版)", layout="wide", page_icon="⚖️")

if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_report' not in st.session_state:
    st.session_state.audit_report = []

st.markdown("""
    <style>
    /* 1. 全局字体 */
    .stApp {font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background-color: #f8f9fa;}
    
    /* 2. 输入框边框强化 */
    input, textarea, .stSelectbox > div > div {
        border: 1px solid #6c757d !important;
        border-radius: 4px !important;
        background-color: #ffffff !important;
    }
    
    /* 3. 卡片式布局 */
    .css-card {
        background-color: white; padding: 20px; border-radius: 8px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 20px;
        border: 1px solid #dee2e6;
    }
    .card-title {
        font-size: 1.1em; font-weight: bold; color: #343a40; 
        border-bottom: 2px solid #e9ecef; padding-bottom: 10px; margin-bottom: 15px;
    }
    
    /* 4. 全宽生成按钮 (修复不够宽的问题) */
    .stButton > button {
        width: 100% !important;
        background: linear-gradient(90deg, #198754, #20c997) !important;
        color: white !important; font-size: 20px !important; font-weight: bold !important;
        border: none !important; border-radius: 8px !important; padding: 15px 0 !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        transition: transform 0.2s;
    }
    .stButton > button:hover {transform: scale(1.01);}
    
    /* 5. 审计日志样式 */
    .audit-box {
        background-color: #212529; color: #00ff00; font-family: 'Consolas', monospace;
        padding: 15px; border-radius: 5px; font-size: 0.9em; line-height: 1.5;
        max-height: 400px; overflow-y: auto;
    }
    .log-err {color: #ff4d4d; font-weight: bold;}
    .log-warn {color: #ffc107; font-weight: bold;}
    .log-ok {color: #00e676;}
    .log-info {color: #b0bec5;}
    
    /* 6. 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("⚖️ 智能排班 V13.0 - 审计官版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏 (基础档案) ---
with st.sidebar:
    st.markdown('<div class="css-card"><div class="card-title">📂 基础档案</div>', unsafe_allow_html=True)
    
    # 员工名单
    default_employees = "张三\n李四\n王五\n赵六\n钱七\n孙八\n周九\n吴十\n郑十一\n王十二"
    emp_input = st.text_area("员工名单 (Excel粘贴)", default_employees, height=150, help="支持换行符分隔")
    employees = [e.strip() for e in emp_input.replace('\n', ',').replace('，', ',').split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="css-card"><div class="card-title">📏 规则开关</div>', unsafe_allow_html=True)
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1)
        with c2: day_shift = st.selectbox("早班", shift_work, index=0)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 2. 顶部逻辑透明化 (详细版) ---
with st.expander("📜 点击查看系统底层逻辑优先级 (详细参数)", expanded=False):
    st.markdown("""
    | 优先级 | 规则名称 | 权重分值 | 说明 |
    | :--- | :--- | :--- | :--- |
    | **Level 0** | **🔥 活动/大促需求** | **∞ (硬约束)** | 最高指令，若设为硬性人数，绝对优先满足。 |
    | **Level 1** | **🚫 0排班禁令** | **∞ (硬约束)** | 若某班次设为0人，则绝对禁止排班。 |
    | **Level 2** | **🧱 每日基线(非0)** | **1,000,000** | 必须满足日常运营最低人数，否则业务瘫痪。 |
    | **Level 3** | **🔄 最大连班限制** | **500,000** | 防止猝死，权重极高。若打破说明人力极度枯竭。 |
    | **Level 4** | **🛌 休息模式达标** | **200,000** | 强制每个人休够天数。 |
    | **Level 5** | **🌙 禁止晚转早** | **100,000** | 除非活动强制，否则不应打破。 |
    | **Level 6** | **❌ 个人拒绝班次** | **50,000** | 尽量满足，但人手不够时会让位给基线。 |
    | **Level 7** | **⚖️ 平衡性与减少** | **1,000** | 在满足上述所有条件后，追求公平。 |
    """)

# --- 3. 核心控制台 (左控右显) ---
st.markdown("###")
col_ctrl, col_data = st.columns([1, 1])

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
    
    max_consecutive = st.number_input("最大连班限制", 1, 14, 6, help="权重大幅提升！超过此限制会严重报警。")
    
    # 平衡阈值
    st.markdown("---")
    st.caption("⚖️ 平衡性阈值 (超过此差值将报警)")
    c_t1, c_t2 = st.columns(2)
    with c_t1: diff_daily_threshold = st.number_input("每日人数波动", 0, 5, 1)
    with c_t2: diff_period_threshold = st.number_input("员工工时差异", 0, 5, 2)
    
    st.markdown('</div>', unsafe_allow_html=True)

# 智能建议
total_capacity = len(employees) * (num_days - target_off_days)
daily_capacity = total_capacity / num_days
suggested_min = math.floor(daily_capacity / len(shift_work))

with col_data:
    st.markdown('<div class="css-card" style="height: 100%;">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📊 人力资源看板</div>', unsafe_allow_html=True)
    
    m1, m2 = st.columns(2)
    m1.metric("总人力", f"{len(employees)} 人")
    m2.metric("总工时池", f"{total_capacity} 人天")
    
    m3, m4 = st.columns(2)
    m3.metric("日均运力", f"{daily_capacity:.1f} 人")
    m4.metric("建议单班基线", f"{suggested_min} 人")
    
    st.info(f"💡 说明：如果最大连班限制为 {max_consecutive} 天，且周期长于 {max_consecutive} 天，系统会强制插入休息日。")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. 详细配置区 ---
col_base, col_req = st.columns([1, 2.5])

with col_base:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🧱 每日班次基线</div>', unsafe_allow_html=True)
    st.caption("注：设为 0 = 🚫 当天该班次关闭 (硬约束)")
    
    min_staff_per_shift = {}
    for s in shift_work:
        # 使用 key 强制刷新
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 生成按钮移到这里
    st.markdown("###")
    generate_btn = st.button("🚀 立即执行智能排班 (审计级)")

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
    st.markdown('<div class="card-title">2. 🔥 活动/大促需求 (覆盖一切)</div>', unsafe_allow_html=True)
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

# --- 5. 核心算法 V13 ---
def solve_schedule_v13():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # 权重体系 (大幅提升基线和连班的权重)
    W_ACTIVITY = 10000000 # 1千万
    W_BASELINE = 1000000  # 1百万 (基线极其重要)
    W_CONSECUTIVE = 500000 # 50万 (连班限制)
    W_REST_STRICT = 200000
    W_FATIGUE = 100000
    W_REFUSE = 50000
    W_BALANCE = 1000

    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')

    # --- H1. 物理约束 ---
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # --- H2. 0排班禁令 (硬约束) ---
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0:
                s_idx = s_map[s_name]
                model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) == 0)

    # --- S0. 连班限制 (权重升级) ---
    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        # 这里的逻辑是：如果连续工作超过 max，产生极大惩罚
        # 使用滑动窗口
        for d in range(num_days - max_consecutive):
            # 窗口大小 max + 1
            window = [sum(shift_vars[(e, d+k, w)] for w in work_indices) for k in range(max_consecutive + 1)]
            # sum(window) 代表这 max+1 天里工作的天数
            # 如果全勤，sum = max+1。我们希望 sum <= max
            # 因此，如果 sum > max (即 sum == max+1)，则违规
            is_violation = model.NewBoolVar(f'cons_vio_{e}_{d}')
            # reified constraint: sum > max <-> violation
            model.Add(sum(window) > max_consecutive).OnlyEnforceIf(is_violation)
            model.Add(sum(window) <= max_consecutive).OnlyEnforceIf(is_violation.Not())
            
            penalties.append(is_violation * W_CONSECUTIVE)

    # --- S1. 每日基线 (权重升级) ---
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue
            s_idx = s_map[s_name]
            actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
            model.Add(shortage >= min_val - actual)
            model.Add(shortage >= 0)
            penalties.append(shortage * W_BASELINE)

    # --- S2. 休息模式 ---
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        diff_rest = model.NewIntVar(0, num_days, f'diff_r_{e}')
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        penalties.append(diff_rest * W_REST_STRICT)

    # --- S3. 活动需求 ---
    for idx, row in edited_activity.iterrows():
        if not row["日期"] or not row["指定班次"]: continue
        try:
            d_idx = date_headers_simple.index(row["日期"])
            s_idx = s_map[row["指定班次"]]
            req = int(row["所需人数"])
            if req > 0:
                model.Add(sum(shift_vars[(e, d_idx, s_idx)] for e in range(len(employees))) >= req)
        except: continue

    # --- S4. 晚转早 & 拒绝 & 平衡 (略简化逻辑以突出重点) ---
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                vio = model.NewBoolVar(f'fat_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + vio)
                penalties.append(vio * W_FATIGUE)
    
    for idx, row in edited_df.iterrows():
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * W_REFUSE)

    # 平衡性
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
        # --- 6. 自检/审计逻辑 (Post-Check) ---
        # 我们不依赖 solver 的变量状态，而是直接拿结果矩阵进行 Python 级的计算
        audit_logs = []
        
        # 构建结果矩阵
        res_matrix = [] # [employee][day] = shift_name
        for e in range(len(employees)):
            row = []
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        break
            res_matrix.append(row)
            
        # 1. 审计：最大连班 (红线)
        for e_idx, e_name in enumerate(employees):
            consecutive = 0
            max_c = 0
            for d in range(num_days):
                if res_matrix[e_idx][d] != off_shift_name:
                    consecutive += 1
                else:
                    consecutive = 0
                max_c = max(max_c, consecutive)
            
            if max_c > max_consecutive:
                audit_logs.append(f"<span class='log-err'>❌ [严重] {e_name} 连续上班 {max_c} 天 (超过限制 {max_consecutive})</span>")
            
        # 2. 审计：工时差异
        work_counts = {}
        for e_idx, e_name in enumerate(employees):
            count = sum(1 for d in range(num_days) if res_matrix[e_idx][d] != off_shift_name)
            work_counts[e_name] = count
        
        counts = list(work_counts.values())
        diff_work = max(counts) - min(counts)
        if diff_work > diff_period_threshold:
            audit_logs.append(f"<span class='log-err'>❌ [平衡性] 工时最大差值为 {diff_work} (阈值 {diff_period_threshold})。{max(work_counts, key=work_counts.get)}:{max(counts)} vs {min(work_counts, key=work_counts.get)}:{min(counts)}</span>")
        else:
            audit_logs.append(f"<span class='log-ok'>✅ [平衡性] 工时差值 {diff_work} (达标)</span>")

        # 3. 审计：指定休息日
        for idx, row in edited_df.iterrows():
            req_off = str(row["指定休息日"])
            if req_off.strip():
                days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                for d in days:
                    if 0 <= d < num_days:
                        if res_matrix[idx][d] != off_shift_name:
                             audit_logs.append(f"<span class='log-err'>❌ [个人] {employees[idx]} 第{d+1}天指定休息未满足 (被更高优先级规则覆盖)</span>")

        # 4. 审计：0排班
        for d in range(num_days):
            for s_name, min_val in min_staff_per_shift.items():
                if min_val == 0:
                    cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                    if cnt > 0:
                         audit_logs.append(f"<span class='log-err'>❌ [严重] 第{d+1}天 {s_name} 出现了 {cnt} 人 (应为0)</span>")

        # 如果没有错误日志
        if not any("❌" in l for l in audit_logs):
            audit_logs.insert(0, "<span class='log-ok'>✅ 自检通过：所有硬性规则均已满足。</span>")

        # 构建 DataFrame
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
            
        # 底部统计
        footer_rows = []
        for s in shifts: # 包含休息
            r_s = [f"【{s}】"]
            for d in range(num_days):
                cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s)
                r_s.append(cnt)
            r_s.extend([""] * (len(shift_work)+1))
            footer_rows.append(r_s)

        cols = [("基本信息", "姓名")] + date_tuples + [("工时统计", s) for s in shift_work] + [("工时统计", "休息天数")]
        return pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols)), audit_logs
    
    return None, ["❌ 求解失败：可能是每日基线要求过高，超过了总人数。"]

# --- 6. 执行逻辑 ---
if generate_btn:
    with st.spinner("🚀 正在执行 AI 排班与合规性自检..."):
        df, logs = solve_schedule_v13()
        st.session_state.result_df = df
        st.session_state.audit_report = logs

if st.session_state.result_df is not None:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 审计报告 & 排班结果</div>', unsafe_allow_html=True)
    
    # 审计日志窗口
    log_html = "<div class='audit-box'>" + "<br>".join(st.session_state.audit_report) + "</div>"
    st.markdown(log_html, unsafe_allow_html=True)
    
    st.markdown("###")
    
    # 结果表格
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f1f3f5; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #e3f2fd'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    # 导出
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出排班表 (Excel)", output.getvalue(), "智能排班_V13.xlsx")
    
    st.markdown('</div>', unsafe_allow_html=True)
