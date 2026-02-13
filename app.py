import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random
import math

# --- 0. 页面配置与 CSS 美化 ---
st.set_page_config(page_title="智能排班 V9.0 (完全掌控版)", layout="wide", page_icon="🎛️")

# 注入 CSS：卡片式布局与居中优化
st.markdown("""
    <style>
    .stApp {font-family: "Microsoft YaHei", sans-serif; background-color: #f5f7f9;}
    
    /* 表格居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"] {
        justify-content: center !important; text-align: center !important;
    }
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important; text-align: center !important;
    }
    
    /* 卡片容器样式 */
    .css-card {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 20px;
        border: 1px solid #e0e0e0;
    }
    
    /* 标题微调 */
    h5 {color: #333; font-weight: 600;}
    
    /* 顶部参数区的样式 */
    .stExpander {
        background-color: #fff;
        border-radius: 8px;
        border: 1px solid #ddd;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🎛️ 智能排班系统 V9.0 - 完全掌控版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 顶部：底层参数配置 (防阉割/上帝视角) ---
with st.expander("🛠️ 点击展开/调整底层逻辑参数 (上帝视角)", expanded=False):
    st.markdown("在这里，你可以查看并调整 AI 的决策权重。**权重越高，AI 越不敢违反该规则。**")
    
    p_c1, p_c2, p_c3 = st.columns(3)
    
    with p_c1:
        st.markdown("**⚖️ 平衡性阈值 (V7功能回归)**")
        diff_daily_threshold = st.number_input("允许每日在岗人数波动 (人)", 0, 5, 1, help="例如设为1：周一5人，周二4人是允许的。")
        diff_period_threshold = st.number_input("允许周期内班次数量差异 (次)", 0, 5, 2, help="例如设为2：张三上5个早班，李四上3个是允许的。")
        
    with p_c2:
        st.markdown("**🏋️ 惩罚权重 (越重要分越高)**")
        w_refuse = st.number_input("权重：拒绝班次", value=500000, step=10000)
        w_activity = st.number_input("权重：活动强制", value=1000000, step=10000, disabled=True, help="活动是最高指令，不可改")
        w_fairness = st.number_input("权重：公平性波动", value=50, step=10)
        
    with p_c3:
        st.markdown("**⚡ 求解器设置**")
        max_time = st.number_input("最大计算时间 (秒)", 5, 60, 20)
        enable_soft_fatigue = st.checkbox("活动期间允许晚转早 (软约束)", value=True, disabled=True)

# --- 2. 侧边栏：基础数据 ---
with st.sidebar:
    st.header("1. 人员与班次")
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十,郑十一,王十二"
    emp_input = st.text_area("员工名单", default_employees, height=100)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    shifts_input = st.text_input("班次定义 (须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except:
        st.error("❌ 班次中必须包含'休'字！"); st.stop()
    shift_work = [s for s in shifts if s != off_shift_name] 

    st.markdown("---")
    st.header("2. 基础限制")
    # 晚转早
    enable_no_night_to_day = st.toggle("🚫 禁止晚转早 (活动可覆盖)", value=True)
    if enable_no_night_to_day:
        c_n, c_d = st.columns(2)
        with c_n: night_shift = st.selectbox("晚班是", shift_work, index=len(shift_work)-1)
        with c_d: day_shift = st.selectbox("早班是", shift_work, index=0)

# --- 3. 主控制台：日期与模式 ---
# 使用容器模拟卡片
with st.container():
    # 日期选择
    c1, c2, c3 = st.columns(3)
    with c1: start_date = st.date_input("开始日期", datetime.date.today())
    with c2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
    with c3:
        num_days = (end_date - start_date).days + 1
        rest_mode = st.selectbox("休息模式 (影响建议值)", ["做6休1", "做5休2", "自定义"], index=0)
        
        if rest_mode == "做6休1": min_off_days = num_days // 7
        elif rest_mode == "做5休2": min_off_days = (num_days // 7) * 2
        else: min_off_days = st.number_input(f"周期最少休几天?", min_value=0, value=1)
        
        max_consecutive = st.number_input("最大连班天数", 1, 14, 6)

    if start_date > end_date: st.error("日期设置错误"); st.stop()
    
    date_tuples = get_date_tuple(start_date, end_date)
    date_headers_simple = [f"{d} {w}" for d, w in date_tuples]

# --- 4. 人力分析看板 ---
st.markdown("---")
total_man_days = len(employees) * num_days
required_rest_days = len(employees) * min_off_days
available_man_days = total_man_days - required_rest_days
avg_daily_staff = available_man_days / num_days
suggested_per_shift = math.floor(avg_daily_staff / len(shift_work))

m1, m2, m3, m4 = st.columns(4)
m1.metric("总投入人力", f"{len(employees)} 人")
m2.metric("理论可用工时", f"{available_man_days} 人天")
m3.metric("日均运力 (预估)", f"{avg_daily_staff:.1f} 人")
m4.metric("建议单班最少", f"{suggested_per_shift} 人", delta="动态计算")

# --- 5. 核心配置区 (左：规则，右：需求) ---
st.markdown("###")
col_rule, col_space, col_table = st.columns([1, 0.1, 2]) # 中间加个空列做分隔

with col_rule:
    st.markdown('<div class="css-card">', unsafe_allow_html=True) # 开始卡片
    st.markdown("##### 🧱 每日班次基线")
    st.caption("这是平时日子的最低要求。")
    
    min_staff_per_shift = {}
    for s in shift_work:
        val = st.number_input(f"{s}", min_value=0, value=suggested_per_shift, 
                              key=f"min_{s}_{suggested_per_shift}")
        min_staff_per_shift[s] = val
    st.markdown('</div>', unsafe_allow_html=True) # 结束卡片

with col_table:
    # --- 员工个性化需求 ---
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown("##### 1. 🙋‍♂️ 员工个性化需求")
    
    init_data = {
        "姓名": employees,
        "上期末班": [off_shift_name for _ in employees],
        "指定休息日": ["" for _ in employees],
        "拒绝班次(强)": ["" for _ in employees],
        "减少班次(弱)": ["" for _ in employees]
    }
    
    edited_df = st.data_editor(
        pd.DataFrame(init_data),
        column_config={
            "姓名": st.column_config.TextColumn(disabled=True),
            "上期末班": st.column_config.SelectboxColumn(options=shifts),
            "指定休息日": st.column_config.TextColumn(help="填数字如 1,3"),
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""] + shift_work),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""] + shift_work)
        },
        hide_index=True,
        use_container_width=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

    # --- 活动需求 (放在这里) ---
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
    st.markdown("##### 2. 🔥 活动/大促需求 (优先级最高)")
    st.caption("在此指定某天全员上岗，系统将自动允许'晚转早'以满足活动。")
    
    activity_data = {
        "活动名称": ["大促预热", "双11爆发"],
        "日期": [date_headers_simple[0], date_headers_simple[1] if num_days>1 else date_headers_simple[0]],
        "指定班次": [shift_work[0], shift_work[0]], 
        "所需人数": [len(employees), len(employees)]
    }
    
    edited_activity = st.data_editor(
        pd.DataFrame(activity_data),
        num_rows="dynamic",
        column_config={
            "日期": st.column_config.SelectboxColumn(options=date_headers_simple),
            "指定班次": st.column_config.SelectboxColumn(options=shift_work),
            "所需人数": st.column_config.NumberColumn(min_value=0, max_value=len(employees))
        },
        use_container_width=True,
        key="activity_editor"
    )
    st.markdown('</div>', unsafe_allow_html=True)

# --- 核心算法 V9 ---
def solve_schedule_v9():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    penalties = [] 
    
    # 1. 变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # --- 硬约束 ---
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    for e in range(len(employees)): 
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) >= min_off_days)

    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        for d in range(num_days - max_consecutive):
            window = [shift_vars[(e, d+k, w)] for k in range(max_consecutive + 1) for w in work_indices]
            model.Add(sum(window) <= max_consecutive)

    # --- 基础最少人数 (被活动覆盖) ---
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            if min_val > 0:
                model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= min_val)

    # --- 活动需求 (硬约束) ---
    activity_conflicts = []
    for idx, row in edited_activity.iterrows():
        if not row["日期"] or not row["指定班次"]: continue
        try:
            d_idx = date_headers_simple.index(row["日期"])
            s_idx = s_map[row["指定班次"]]
            req = row["所需人数"]
            if req and req > 0:
                model.Add(sum(shift_vars[(e, d_idx, s_idx)] for e in range(len(employees))) >= int(req))
                activity_conflicts.append({"d": d_idx, "name": row["活动名称"]})
        except: continue

    # --- 晚转早 (带权重的软约束) ---
    warnings_fatigue = []
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                violation = model.NewBoolVar(f'fatigue_{e}_{d}')
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1 + violation)
                penalties.append(violation * w_activity) # 使用配置的权重
                warnings_fatigue.append({
                    "e": employees[e], "d": d, "v": violation, 
                    "date_trigger": date_headers_simple[d+1] 
                })
        # 历史衔接
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                violation_h = model.NewBoolVar(f'fatigue_hist_{idx}')
                model.Add(shift_vars[(idx, 0, d_idx)] <= violation_h)
                penalties.append(violation_h * w_activity)
                warnings_fatigue.append({
                    "e": employees[idx], "d": -1, "v": violation_h, 
                    "date_trigger": date_headers_simple[0]
                })

    # --- 阈值控制 (V7功能回归) ---
    
    # 1. 每日在岗波动 (Stability)
    for s_name in shift_work:
        if min_staff_per_shift.get(s_name, 0) == 0: continue
        s_idx = s_map[s_name]
        daily_counts = [sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) for d in range(num_days)]
        
        max_d, min_d = model.NewIntVar(0, len(employees), ''), model.NewIntVar(0, len(employees), '')
        model.AddMaxEquality(max_d, daily_counts)
        model.AddMinEquality(min_d, daily_counts)
        
        diff_d = model.NewIntVar(0, len(employees), '')
        model.Add(diff_d == max_d - min_d)
        
        excess_d = model.NewIntVar(0, len(employees), '')
        model.Add(excess_d >= diff_d - diff_daily_threshold)
        penalties.append(excess_d * 50) # 稳定性权重固定较高

    # 2. 员工工时公平 (Fairness)
    for s_name in shift_work:
        s_idx = s_map[s_name]
        emp_counts = [sum(shift_vars[(e, d, s_idx)] for d in range(num_days)) for e in range(len(employees))]
        
        max_e, min_e = model.NewIntVar(0, num_days, ''), model.NewIntVar(0, num_days, '')
        model.AddMaxEquality(max_e, emp_counts)
        model.AddMinEquality(min_e, emp_counts)
        
        diff_e = model.NewIntVar(0, num_days, '')
        model.Add(diff_e == max_e - min_e)
        
        excess_e = model.NewIntVar(0, num_days, '')
        model.Add(excess_e >= diff_e - diff_period_threshold)
        penalties.append(excess_e * w_fairness) # 使用配置的公平权重

    # --- 个人需求 ---
    warnings_personal = []
    for idx, row in edited_df.iterrows():
        # 拒绝班次
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * w_refuse) # 使用配置的拒绝权重
                warnings_personal.append({"t": "拒", "e": employees[idx], "d": d, "v": is_s, "s": ref})
        
        # 减少班次
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * 5)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time)
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # 数据组装
        data_rows = []
        msgs = []
        
        # 冲突归因
        for w in warnings_fatigue:
            if solver.Value(w['v']) == 1:
                reason = "资源紧张"
                for act in activity_conflicts:
                    act_d = date_headers_simple[act['d']]
                    if act_d == w['date_trigger']: reason = f"活动【{act['name']}】需求"
                msgs.append(f"🔴 **疲劳预警**: {w['e']} 在 {w['date_trigger']} 晚转早。归因: {reason}")
        
        for w in warnings_personal:
            if solver.Value(w['v']) == 1:
                msgs.append(f"🟠 个人冲突: {w['e']} {date_headers_simple[w['d']]} 上了拒绝的 {w['s']}")

        # 构建 DataFrame
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
    
    return None, ["❌ 排班失败：硬性冲突无法解决，请检查最少在岗人数是否超过总人数。"]

# --- 运行 ---
st.markdown("###")
if st.button("🚀 生成排班表", type="primary"):
    with st.spinner("AI 正在计算最佳方案..."):
        df_res, msgs = solve_schedule_v9()
        
        if df_res is not None:
            if msgs:
                with st.expander("⚠️ 冲突报告", expanded=True):
                    for m in msgs: st.markdown(m)
            else:
                st.success("✅ 完美排班：无冲突")
            
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
            st.download_button("📥 下载 Excel", output.getvalue(), "排班表_V9.xlsx")
        else:
            st.error(msgs[0])
