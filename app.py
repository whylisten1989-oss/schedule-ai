import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random
import math

# --- 0. 页面与CSS配置 ---
st.set_page_config(page_title="智能排班 V10.0 (指挥官版)", layout="wide", page_icon="🚀")

# 注入 CSS：卡片式布局、按钮美化、居中优化
st.markdown("""
    <style>
    .stApp {font-family: "Microsoft YaHei", sans-serif; background-color: #f7f9fc;}
    
    /* 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    
    /* 卡片容器 */
    .css-card {
        background-color: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 15px; border: 1px solid #e1e4e8;
    }
    .css-card-header { font-size: 1.1em; font-weight: bold; color: #2c3e50; margin-bottom: 10px; border-bottom: 2px solid #eee; padding-bottom: 5px;}
    
    /* 侧边栏优化 */
    section[data-testid="stSidebar"] {background-color: #ffffff; border-right: 1px solid #eee;}
    
    /* 生成按钮美化 - 巨大、绿色 */
    div.stButton > button {
        width: 100%; font-size: 20px !important; font-weight: bold !important;
        background-color: #00C853 !important; color: white !important;
        border: none; border-radius: 8px; padding: 15px 0; transition: 0.3s;
    }
    div.stButton > button:hover {background-color: #009624 !important; box-shadow: 0 4px 12px rgba(0,200,83,0.4);}
    
    /* 顶部逻辑按钮样式 */
    .logic-btn {border: 1px solid #4CAF50; color: #4CAF50; padding: 5px 10px; border-radius: 5px; font-size: 0.8em; margin-right: 5px;}
    </style>
""", unsafe_allow_html=True)

st.title("🚀 智能排班系统 V10.0 - 指挥官版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏：基础数据 ---
with st.sidebar:
    st.header("1. 基础档案")
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十,郑十一,王十二"
    emp_input = st.text_area("员工名单", default_employees, height=120)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except: st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 

    st.markdown("---")
    st.header("2. 基础规则")
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        c1, c2 = st.columns(2)
        with c1: night_shift = st.selectbox("晚班", shift_work, index=len(shift_work)-1)
        with c2: day_shift = st.selectbox("早班", shift_work, index=0)

# --- 2. 顶部：逻辑控制台 (独立按钮区) ---
col_logic_1, col_logic_2 = st.columns(2)

# A. 平衡性阈值按钮
with col_logic_1:
    with st.expander("⚖️ 平衡性阈值设置 (点击调整)", expanded=False):
        st.info("当没有硬性冲突时，AI 将尽量满足以下平衡标准：")
        p1, p2 = st.columns(2)
        with p1:
            diff_daily_threshold = st.number_input("每日人数允许波动", 0, 5, 1, help="周一5人，周二4人，波动为1。")
        with p2:
            diff_period_threshold = st.number_input("员工工时允许差异", 0, 5, 2, help="张三上5天，李四上3天，差异为2。")

# B. 系统底层逻辑总览按钮
with col_logic_2:
    with st.expander("📜 系统底层逻辑总览 (查看后台逻辑)", expanded=False):
        st.markdown("""
        **后台逻辑优先级 (权重从高到低):**
        1.  🔥 **活动/大促需求** (权重: ∞ / 硬约束) - *绝对优先，可覆盖休息与晚转早*
        2.  🚫 **物理限制** (权重: ∞) - *一人一天只能上一班*
        3.  🛌 **休息模式达标** (权重: 200,000) - *做6休1就是做6休1，严禁多休或少休*
        4.  🚫 **禁止晚转早** (权重: 100,000) - *除非活动强制，否则禁止*
        5.  ❌ **拒绝班次** (权重: 50,000) - *尽量满足员工拒绝的需求*
        6.  🧱 **每日班次基线** (权重: 10,000) - *满足日常最低人力*
        7.  ⚖️ **平衡性** (权重: 50-100) - *让大家干活一样多*
        """)

# --- 3. 主控制台：日期与模式 ---
with st.container():
    c1, c2, c3 = st.columns(3)
    with c1: start_date = st.date_input("开始日期", datetime.date.today())
    with c2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
    with c3:
        num_days = (end_date - start_date).days + 1
        rest_mode = st.selectbox("休息模式 (硬指标)", ["做6休1", "做5休2", "自定义"], index=0)
        if rest_mode == "做6休1": target_off_days = num_days // 7
        elif rest_mode == "做5休2": target_off_days = (num_days // 7) * 2
        else: target_off_days = st.number_input(f"周期内应休几天?", min_value=0, value=1)
        
        max_consecutive = st.number_input("最大连班限制", 1, 14, 6)

    if start_date > end_date: st.error("日期设置错误"); st.stop()
    
    date_tuples = get_date_tuple(start_date, end_date)
    date_headers_simple = [f"{d} {w}" for d, w in date_tuples]

# --- 4. 人力分析 ---
st.markdown("---")
# 智能计算建议值
total_capacity = len(employees) * (num_days - target_off_days)
daily_capacity = total_capacity / num_days
suggested_min = math.floor(daily_capacity / len(shift_work))

m1, m2, m3, m4 = st.columns(4)
m1.metric("总人力", f"{len(employees)} 人")
m2.metric("必须工作", f"{total_capacity} 人天", help="扣除休息后的总工时")
m3.metric("日均运力", f"{daily_capacity:.1f} 人")
m4.metric("建议单班基线", f"{suggested_min} 人")

# --- 5. 核心配置卡片区 ---
st.markdown("###")
col_base, col_emp = st.columns([1, 2.5])

# 左侧：每日基线
with col_base:
    st.markdown('<div class="css-card"><div class="css-card-header">🧱 每日班次基线</div>', unsafe_allow_html=True)
    st.caption("日常运营最低要求 (优先级 < 休息模式)")
    min_staff_per_shift = {}
    for s in shift_work:
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}_{suggested_min}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True)

# 右侧：需求板块 (上下布局)
with col_emp:
    # 员工个性化
    st.markdown('<div class="css-card"><div class="css-card-header">1. 🙋‍♂️ 员工个性化需求</div>', unsafe_allow_html=True)
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

    # 活动需求
    st.markdown('<div class="css-card"><div class="css-card-header">2. 🔥 活动/大促需求 (优先级最高)</div>', unsafe_allow_html=True)
    st.info("💡 如果指定了活动人数，系统将**自动打破** '休息模式' 和 '晚转早' 限制以确保有人上班。")
    activity_data = {
        "活动名称": ["大促预热", "双11爆发"],
        "日期": [date_headers_simple[0], date_headers_simple[1] if num_days>1 else date_headers_simple[0]],
        "指定班次": [shift_work[0], shift_work[0]], 
        "所需人数": [len(employees), len(employees)]
    }
    edited_activity = st.data_editor(
        pd.DataFrame(activity_data), num_rows="dynamic",
        column_config={
            "日期": st.column_config.SelectboxColumn(options=date_headers_simple),
            "指定班次": st.column_config.SelectboxColumn(options=shift_work),
            "所需人数": st.column_config.NumberColumn(min_value=0, max_value=len(employees))
        }, use_container_width=True, key="activity_editor"
    )
    st.markdown('</div>', unsafe_allow_html=True)

# --- 核心算法 V10 ---
def solve_schedule_v10():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = []
    
    # 权重定义 (Hierarchy of Needs)
    W_ACTIVITY = 1000000     # 活动：神圣不可侵犯
    W_REST_STRICT = 200000   # 休息模式：非常重要 (必须休够，也不能多休)
    W_FATIGUE = 100000       # 晚转早：很重要
    W_REFUSE = 50000         # 个人拒绝：重要
    W_BASELINE = 10000       # 日常基线：基础
    W_BALANCE = 100          # 平衡性：锦上添花

    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')

    # --- H1. 物理铁律 (硬约束) ---
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # --- S1. 休息模式 (软约束，但极大权重) ---
    # 为什么变软？因为如果活动需要全员上班，休息必须让路。
    # 为什么解决了“休息过多”？因为我们用 abs(实际休 - 目标休) 进行惩罚
    rest_warnings = []
    for e in range(len(employees)):
        actual_rest = sum(shift_vars[(e, d, off_idx)] for d in range(num_days))
        
        # 定义偏差变量
        diff_rest = model.NewIntVar(0, num_days, f'diff_rest_{e}')
        # 逻辑: diff >= actual - target  AND  diff >= target - actual
        # 即 diff = |actual - target|
        model.Add(diff_rest >= actual_rest - target_off_days)
        model.Add(diff_rest >= target_off_days - actual_rest)
        
        # 惩罚偏差：每多休一天或少休一天，都重罚
        penalties.append(diff_rest * W_REST_STRICT)
        
        # 记录用于报告 (如果 diff > 0)
        is_diff = model.NewBoolVar(f'is_rest_diff_{e}')
        model.Add(diff_rest > 0).OnlyEnforceIf(is_diff)
        model.Add(diff_rest == 0).OnlyEnforceIf(is_diff.Not())
        rest_warnings.append({"e": employees[e], "v": is_diff, "act": actual_rest, "tgt": target_off_days})

    # --- S2. 活动需求 (硬约束/极高权重软约束) ---
    # 为了防止无解，这里使用硬约束，但因为它是最高优先级，
    # 如果它和 H1 冲突(比如人数不够)，那就是真的无解。
    # 如果它和 S1(休息) 冲突，S1 会让路 (因为 W_ACTIVITY > W_REST)。
    activity_dates = []
    for idx, row in edited_activity.iterrows():
        if not row["日期"] or not row["指定班次"]: continue
        try:
            d_idx = date_headers_simple.index(row["日期"])
            s_idx = s_map[row["指定班次"]]
            req = int(row["所需人数"])
            if req > 0:
                # 强制要求: 当天该班次人数 >= Req
                model.Add(sum(shift_vars[(e, d_idx, s_idx)] for e in range(len(employees))) >= req)
                activity_dates.append(row["日期"])
        except: continue

    # --- S3. 每日基线 (软约束) ---
    # 权重低于休息模式。如果休息模式要求必须休，而基线要求必须上，
    # 此时 W_REST (20w) > W_BASELINE (1w)，AI 会优先保休息，基线可以稍微不达标。
    baseline_warnings = []
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            if min_val == 0: continue
            s_idx = s_map[s_name]
            
            actual_staff = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            
            # 允许少人，但要罚分
            shortage = model.NewIntVar(0, len(employees), f'shortage_{d}_{s_name}')
            model.Add(shortage >= min_val - actual_staff)
            model.Add(shortage >= 0) # 修正: 确保非负
            
            penalties.append(shortage * W_BASELINE)

    # --- S4. 晚转早 (软约束) ---
    fatigue_warnings = []
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                violation = model.NewBoolVar(f'fatigue_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + violation)
                penalties.append(violation * W_FATIGUE)
                fatigue_warnings.append({"e": employees[e], "d": d, "v": violation, "date": date_headers_simple[d+1]})
        
        # 历史
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                v_h = model.NewBoolVar(f'fat_h_{idx}')
                model.Add(shift_vars[(idx, 0, d_idx)] <= v_h)
                penalties.append(v_h * W_FATIGUE)
                fatigue_warnings.append({"e": employees[idx], "d": -1, "v": v_h, "date": date_headers_simple[0]})

    # --- S5. 个人拒绝与减少 ---
    personal_warnings = []
    for idx, row in edited_df.iterrows():
        # 拒绝
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * W_REFUSE)
                personal_warnings.append({"e": employees[idx], "d": d, "v": is_s, "s": ref})
        # 减少
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * 1000) # 适中权重

    # --- S6. 阈值与平衡 ---
    # 每日波动
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        d_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        max_d, min_d = model.NewIntVar(0, len(employees), ''), model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, d_counts)
        model.AddMinEquality(min_d, d_counts)
        diff = model.NewIntVar(0, len(employees), '')
        model.Add(diff == max_d - min_d)
        excess = model.NewIntVar(0, len(employees), '')
        model.Add(excess >= diff - diff_daily_threshold)
        penalties.append(excess * W_BALANCE * 10) # 500-1000

    # 工时公平
    for s_name in shift_work:
        s_idx = s_map[s_name]
        e_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        max_e, min_e = model.NewIntVar(0, num_days, ''), model.NewIntVar(0, num_days, '')
        model.AddMaxEquality(max_e, e_counts)
        model.AddMinEquality(min_e, e_counts)
        diff = model.NewIntVar(0, num_days, '')
        model.Add(diff == max_e - min_e)
        excess = model.NewIntVar(0, num_days, '')
        model.Add(excess >= diff - diff_period_threshold)
        penalties.append(excess * W_BALANCE * 5)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        msgs = []
        
        # 1. 休息偏差报告
        for w in rest_warnings:
            if solver.Value(w['v']) == 1:
                act = solver.Value(w['act'])
                if act < target_off_days:
                    reason = "活动/大促需求" if any(x in date_headers_simple for x in activity_dates) else "人力极度紧缺"
                    msgs.append(f"🔴 **严重牺牲**: {w['e']} 只休了 {act} 天 (目标 {target_off_days} 天)。原因: {reason}。")
                elif act > target_off_days:
                    msgs.append(f"⚠️ **资源闲置**: {w['e']} 休了 {act} 天 (目标 {target_off_days} 天)。原因: 每日基线过低，无班可排。")

        # 2. 疲劳报告
        for w in fatigue_warnings:
            if solver.Value(w['v']) == 1:
                reason = "资源紧张"
                if w['date'] in activity_dates: reason = "🔥 活动强制要求"
                msgs.append(f"🟠 **疲劳**: {w['e']} 在 {w['date']} 晚转早。原因: {reason}")
                
        # 3. 个人冲突
        for w in personal_warnings:
            if solver.Value(w['v']) == 1:
                msgs.append(f"⚪ 个人: {w['e']} 被迫上了拒绝的班次 {w['s']}。")

        # 构建数据
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
        df = pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols))
        return df, msgs
    
    return None, ["❌ 仍然无法排班。这通常是因为：\n1. 某个活动需要的总人数超过了员工总数。\n2. 每日基线设置得极其不合理。"]

# --- 运行 ---
st.markdown("###")
if st.button("🚀 立即生成排班表 (V10.0)", type="primary"):
    with st.spinner("AI 正在根据 V10 逻辑进行多维博弈..."):
        df_res, msgs = solve_schedule_v10()
        
        if df_res is not None:
            if msgs:
                with st.expander("⚠️ 排班冲突与调整报告 (必读)", expanded=True):
                    for m in msgs: st.markdown(m)
            else:
                st.success("✅ 完美排班：所有规则均已满足！")
            
            def style_map(val):
                s = str(val)
                if off_shift_name in s: return 'background-color: #f0f2f6; color: #ccc'
                if "晚" in s: return 'background-color: #fff3cd; color: #856404'
                if "【" in s: return 'font-weight: bold; background-color: #e6f3ff'
                return ''
            
            st.dataframe(df_res.style.applymap(style_map), use_container_width=True, height=600)
            
            output = io.BytesIO()
            df_exp = df_res.copy()
            df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in df_res.columns]
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_exp.to_excel(writer, index=False)
            st.download_button("📥 导出 Excel 排班表", output.getvalue(), "排班表_V10.xlsx")
        else:
            st.error(msgs[0])
