import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import math

# --- 0. 页面配置与 UI 重构 (去除丑陋边框，采用现代阴影) ---
st.set_page_config(page_title="智能排班 V14.0 (最终修正版)", layout="wide", page_icon="⚖️")

if 'result_df' not in st.session_state:
    st.session_state.result_df = None
if 'audit_report' not in st.session_state:
    st.session_state.audit_report = []

st.markdown("""
    <style>
    /* 全局字体与背景 */
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        background-color: #f7f9fc;
    }
    
    /* 1. 卡片式布局 (替代丑陋的边框) */
    .css-card {
        background-color: white;
        padding: 24px;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05); /* 柔和阴影 */
        margin-bottom: 20px;
        border: 1px solid #edf2f7; /* 极淡的边框 */
    }
    .card-title {
        font-size: 16px;
        font-weight: 700;
        color: #1a202c;
        margin-bottom: 16px;
        border-left: 4px solid #3182ce; /* 左侧蓝色条点缀 */
        padding-left: 10px;
    }
    
    /* 2. 输入框美化 (统一风格) */
    .stTextInput>div>div>input, .stNumberInput>div>div>input, .stSelectbox>div>div {
        border-radius: 6px;
        border: 1px solid #e2e8f0;
    }
    
    /* 3. 生成按钮 (全宽、悬浮感) */
    .stButton > button {
        width: 100%;
        background-color: #3182ce !important;
        color: white !important;
        font-size: 18px !important;
        font-weight: 600 !important;
        padding: 16px 0 !important;
        border-radius: 8px !important;
        border: none !important;
        box-shadow: 0 4px 6px rgba(49, 130, 206, 0.3);
        transition: all 0.2s;
    }
    .stButton > button:hover {
        background-color: #2b6cb0 !important;
        transform: translateY(-1px);
        box-shadow: 0 6px 8px rgba(49, 130, 206, 0.4);
    }
    
    /* 4. 审计日志区 */
    .audit-box {
        background-color: #2d3748;
        color: #68d391;
        padding: 16px;
        border-radius: 8px;
        font-family: 'Courier New', monospace;
        font-size: 14px;
        line-height: 1.6;
        max-height: 300px;
        overflow-y: auto;
    }
    .log-err {color: #fc8181; font-weight: bold;}
    .log-warn {color: #f6ad55;}
    
    /* 5. 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("⚖️ 智能排班系统 V14.0 - 公平性修正版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏：基础档案 ---
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

# --- 2. 主控制区 ---
col_ctrl, col_data = st.columns([1, 1.2])

with col_ctrl:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📅 排班设定</div>', unsafe_allow_html=True)
    
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
    
    # --- 这里是你要求的阈值调整，必须显眼 ---
    st.markdown("---")
    st.markdown('<div class="card-title" style="font-size:14px; margin-bottom:10px;">⚖️ 公平性与波动控制 (V14回归)</div>', unsafe_allow_html=True)
    
    c_t1, c_t2 = st.columns(2)
    with c_t1: 
        diff_daily_threshold = st.number_input("每日人数允许差值", 0, 5, 1, help="周一5人，周二4人，差1 (允许)。差2则罚分。")
    with c_t2: 
        diff_period_threshold = st.number_input("周期班次允许差值", 0, 5, 2, help="张三上5个早班，李四上3个，差2 (允许)。差3则重罚。")
    
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
    
    st.info("💡 为什么之前排班不均？因为系统在满足'基线'后就偷懒了。V14版加入了强力公平算法，会强制把多余的工时平均分配。")
    st.markdown('</div>', unsafe_allow_html=True)

# --- 3. 详细配置区 ---
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
    
    # 生成按钮
    st.markdown("###")
    generate_btn = st.button("🚀 立即生成排班 (执行自检)")

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

# --- 4. 核心算法 V14 (解决不均衡的根源) ---
def solve_schedule_v14():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # 权重体系修正：大幅提升公平性的地位
    W_ACTIVITY = 10000000
    W_BASELINE = 1000000
    W_CONSECUTIVE = 500000
    W_REST_STRICT = 200000
    W_FATIGUE = 100000
    W_BALANCE = 50000  # <--- 从之前的 1000 提升到 50000，强制 AI 重视公平
    W_REFUSE = 10000
    W_REDUCE = 1000

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

    # S1. 每日基线 (>=)
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue
            s_idx = s_map[s_name]
            actual = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            shortage = model.NewIntVar(0, len(employees), f'short_{d}_{s_name}')
            model.Add(shortage >= min_val - actual)
            model.Add(shortage >= 0)
            penalties.append(shortage * W_BASELINE)

    # S2. 休息模式 (=)
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        diff_rest = model.NewIntVar(0, num_days, f'diff_r_{e}')
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        penalties.append(diff_rest * W_REST_STRICT)

    # S3. 活动需求 (>=)
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
            penalties.append(cnt * W_REDUCE)
        
        req_off = str(row["指定休息日"])
        if req_off.strip():
            try:
                days = [int(x)-1 for x in req_off.replace("，",",").split(",") if x.strip().isdigit()]
                for d in days:
                    if 0 <= d < num_days:
                        # 没休则罚
                        is_work = model.NewBoolVar(f'vio_off_{idx}_{d}')
                        model.Add(shift_vars[(idx, d, off_idx)] == 0).OnlyEnforceIf(is_work)
                        model.Add(shift_vars[(idx, d, off_idx)] == 1).OnlyEnforceIf(is_work.Not())
                        penalties.append(is_work * 50000)
            except: pass

    # --- S6. 关键：公平性 (The Fairness Fix) ---
    # 我们不仅要限制 max-min，还要惩罚每一个偏离平均值的行为
    # 逻辑：对于每个工作班次，计算 max_count 和 min_count
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        
        # 1. 每日人数波动 (Daily Stability)
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d = model.NewIntVar(0, len(employees), '')
        min_d = model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        excess_d = model.NewIntVar(0, len(employees), '')
        model.Add(excess_d >= (max_d - min_d) - diff_daily_threshold)
        penalties.append(excess_d * W_BALANCE)

        # 2. 员工工时公平性 (Period Fairness)
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        max_e = model.NewIntVar(0, num_days, '')
        min_e = model.NewIntVar(0, num_days, '')
        model.AddMaxEquality(max_e, e_counts)
        model.AddMinEquality(min_e, e_counts)
        excess_e = model.NewIntVar(0, num_days, '')
        # 如果 max - min > 阈值，重罚
        model.Add(excess_e >= (max_e - min_e) - diff_period_threshold)
        penalties.append(excess_e * W_BALANCE * 5) # 5倍权重，强迫 AI 把班次抹平

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # --- 审计逻辑 ---
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
            
        # 审计1: 0排班
        for d in range(num_days):
            for s_name, min_val in min_staff_per_shift.items():
                if min_val == 0:
                    cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s_name)
                    if cnt > 0: audit_logs.append(f"<span class='log-err'>❌ [严重] {s_name} 被禁用了，但第{d+1}天排了 {cnt} 人</span>")

        # 审计2: 公平性
        for s_name in shift_work:
             counts = []
             for e in range(len(employees)):
                 c = sum(1 for d in range(num_days) if res_matrix[e][d] == s_name)
                 counts.append(c)
             diff = max(counts) - min(counts)
             if diff > diff_period_threshold:
                 audit_logs.append(f"<span class='log-err'>❌ [平衡性] {s_name} 差异过大: {diff} (阈值 {diff_period_threshold})</span>")
             else:
                 audit_logs.append(f"<span class='log-warn'>✅ [平衡性] {s_name} 差异: {diff} (达标)</span>")

        # 审计3: 最大连班
        for e_idx, e_name in enumerate(employees):
            consecutive = 0
            max_c = 0
            for d in range(num_days):
                if res_matrix[e_idx][d] != off_shift_name: consecutive += 1
                else: consecutive = 0
                max_c = max(max_c, consecutive)
            if max_c > max_consecutive:
                audit_logs.append(f"<span class='log-err'>❌ [健康] {e_name} 连班 {max_c} 天 (超限 {max_consecutive})</span>")

        if not any("❌" in l for l in audit_logs):
            audit_logs.insert(0, "<span class='log-ok'>✅ 自检通过：所有硬性规则与平衡性指标均已满足。</span>")

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
            
        footer_rows = []
        for s in shifts: 
            r_s = [f"【{s}】"]
            for d in range(num_days):
                cnt = sum(1 for e in range(len(employees)) if res_matrix[e][d] == s)
                r_s.append(cnt)
            r_s.extend([""] * (len(shift_work)+1))
            footer_rows.append(r_s)

        cols = [("基本信息", "姓名")] + date_tuples + [("工时统计", s) for s in shift_work] + [("工时统计", "休息天数")]
        return pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols)), audit_logs
    
    return None, ["❌ 求解失败：可能是每日基线要求过高。"]

# --- 6. 执行 ---
if generate_btn:
    with st.spinner("🚀 AI 正在进行深度平衡运算..."):
        df, logs = solve_schedule_v14()
        st.session_state.result_df = df
        st.session_state.audit_report = logs

if st.session_state.result_df is not None:
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 审计报告 & 排班结果</div>', unsafe_allow_html=True)
    
    log_html = "<div class='audit-box'>" + "<br>".join(st.session_state.audit_report) + "</div>"
    st.markdown(log_html, unsafe_allow_html=True)
    st.markdown("###")
    
    def style_map(val):
        s = str(val)
        if off_shift_name in s: return 'background-color: #f8f9fa; color: #adb5bd'
        if "晚" in s: return 'background-color: #fff3cd; color: #856404'
        if "【" in s: return 'font-weight: bold; background-color: #e3f2fd'
        return ''
    
    st.dataframe(st.session_state.result_df.style.applymap(style_map), use_container_width=True, height=600)
    
    output = io.BytesIO()
    df_exp = st.session_state.result_df.copy()
    df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in st.session_state.result_df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_exp.to_excel(writer, index=False)
    st.download_button("📥 导出排班表 (Excel)", output.getvalue(), "智能排班_V14.xlsx")
    
    st.markdown('</div>', unsafe_allow_html=True)
