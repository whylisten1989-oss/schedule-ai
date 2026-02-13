import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime
import random
import math

# --- 0. 页面与CSS配置 ---
st.set_page_config(page_title="智能排班 V7.0 (大师版)", layout="wide", page_icon="🎨")

# 强制表格居中与UI美化的 CSS
st.markdown("""
    <style>
    /* 全局字体优化 */
    .stApp {font-family: "Microsoft YaHei", sans-serif;}
    
    /* 表格内容居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="gridcell"] {
        justify-content: center !important;
        text-align: center !important;
    }
    /* 表头居中 */
    div[data-testid="stDataFrame"] div[role="grid"] div[role="columnheader"] {
        justify-content: center !important;
        text-align: center !important;
    }
    
    /* 调整一下 Toggle 组件的样式 */
    .stToggle {
        border: 1px solid #eee;
        padding: 10px;
        border-radius: 8px;
        background-color: #f9f9f9;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🎨 智能排班系统 V7.0 - 大师体验版")

# --- 工具函数 ---
def get_date_tuple(start_date, end_date):
    """生成 (日期, 周几) 元组"""
    delta = end_date - start_date
    week_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    return [( (start_date + datetime.timedelta(days=i)).strftime('%m-%d'), 
              week_map[(start_date + datetime.timedelta(days=i)).weekday()] ) 
            for i in range(delta.days + 1)]

# --- 1. 侧边栏配置 ---
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
        st.error("❌ 班次中必须包含'休'字！")
        st.stop()
        
    shift_work = [s for s in shifts if s != off_shift_name] 

    st.markdown("---")
    st.header("2. 基础限制")
    
    # 休息模式
    rest_mode = st.selectbox("休息模式", ["做6休1 (标准)", "做5休2 (双休)", "自定义天数"])
    
    # 晚转早 UI 优化
    st.write("疲劳管理")
    enable_no_night_to_day = st.toggle("🚫 启用「禁止晚转早」保护", value=True)
    
    if enable_no_night_to_day:
        c_n, c_d = st.columns(2)
        with c_n: night_shift = st.selectbox("晚班是", shift_work, index=len(shift_work)-1)
        with c_d: day_shift = st.selectbox("早班是", shift_work, index=0)

# --- 主控制台 ---
st.subheader("⚙️ 排班控制台")

# 日期选择
c1, c2, c3 = st.columns(3)
with c1: start_date = st.date_input("开始日期", datetime.date.today())
with c2: end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
with c3:
    num_days = (end_date - start_date).days + 1
    if rest_mode == "做6休1 (标准)": min_off_days = num_days // 7
    elif rest_mode == "做5休2 (双休)": min_off_days = (num_days // 7) * 2
    else: min_off_days = st.number_input(f"周期最少休几天?", min_value=0, value=1)
    
    max_consecutive = st.number_input("最大连班天数", 1, 14, 6)

if start_date > end_date:
    st.error("日期设置错误")
    st.stop()

date_tuples = get_date_tuple(start_date, end_date)
date_headers_simple = [f"{d} {w}" for d, w in date_tuples]

# --- 智能建议与阈值设置 ---

# 计算建议值
total_capacity = len(employees) * (num_days - min_off_days) # 总可用人天
daily_capacity = total_capacity / num_days # 每天平均可用人数
suggested_min = math.floor(daily_capacity / len(shift_work)) # 平均分给每个班

col_rule, col_table = st.columns([1, 3])

with col_rule:
    st.markdown(f"**人力分析**: 共{len(employees)}人，预估日均运力 **{daily_capacity:.1f}** 人次")
    
    st.markdown("##### 每日最少在岗 (建议值已填)")
    min_staff_per_shift = {}
    for s in shift_work:
        # 智能填入建议值
        val = st.number_input(f"{s}", min_value=0, value=suggested_min, key=f"min_{s}", 
                              help="设为0表示本周期完全不排该班次")
        min_staff_per_shift[s] = val

    # --- 高级阈值设置 (隐藏式) ---
    with st.expander("🛠️ 高级平衡阈值 (点击展开)"):
        st.caption("调整由于人员差异允许产生的'不平衡'程度")
        
        # 每日稳定性
        st.markdown("**1. 每日在岗波动 (优先级: 高)**")
        diff_daily_threshold = st.slider(
            "允许每日人数最大差值", 0, 3, 1, 
            help="例如设为1：允许周一早班5人，周二早班4人。若设为0则强制每天人数必须完全一样（可能导致无解）。"
        )
        
        # 员工公平性
        st.markdown("**2. 员工工时差异 (优先级: 中)**")
        diff_period_threshold = st.slider(
            "允许周期内班次数量差值", 0, 5, 2,
            help="例如设为2：允许张三上5个早班，李四只上3个。设得越小越公平，但也越难排。"
        )

with col_table:
    st.markdown("##### 🙋‍♂️ 员工个性化需求")
    
    # 需求表数据
    init_data = {
        "姓名": employees,
        "上期末班": [off_shift_name for _ in employees],
        "指定休息日": ["" for _ in employees],
        "拒绝班次(强)": ["" for _ in employees],
        "减少班次(弱)": ["" for _ in employees]
    }
    
    # 美化配置
    edited_df = st.data_editor(
        pd.DataFrame(init_data),
        column_config={
            "姓名": st.column_config.TextColumn(disabled=True),
            "上期末班": st.column_config.SelectboxColumn(options=shifts, width="small"),
            "指定休息日": st.column_config.TextColumn(width="medium", help="填数字如 1,3"),
            "拒绝班次(强)": st.column_config.SelectboxColumn(options=[""] + shift_work, width="small"),
            "减少班次(弱)": st.column_config.SelectboxColumn(options=[""] + shift_work, width="small")
        },
        hide_index=True,
        use_container_width=True
    )

# --- 核心算法 V7 ---
def solve_schedule_v7():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    
    penalties = [] # 目标函数惩罚项
    
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

    # H2. 每日最少人数 (及 0人数逻辑)
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            total_on_shift = sum(shift_vars[(e, d, s_idx)] for e in range(len(employees)))
            
            if min_val == 0:
                # 用户设定最少0人，意味着不排这个班
                model.Add(total_on_shift == 0)
            else:
                model.Add(total_on_shift >= min_val)

    # H3. 最少休息天数
    for e in range(len(employees)):
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) >= min_off_days)
        
    # H4. 最大连续工作
    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        for d in range(num_days - max_consecutive):
            window = [shift_vars[(e, d+k, w)] for k in range(max_consecutive + 1) for w in work_indices]
            model.Add(sum(window) <= max_consecutive)

    # H5. 晚转早 + 衔接
    if enable_no_night_to_day:
        n_idx, d_idx = s_map[night_shift], s_map[day_shift]
        for e in range(len(employees)):
            for d in range(num_days - 1):
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1)
        # 历史衔接
        for idx, row in edited_df.iterrows():
            if row["上期末班"] == night_shift:
                model.Add(shift_vars[(idx, 0, d_idx)] == 0)

    # --- 软约束与阈值控制 ---
    
    # S1. 每日人数稳定性 (优先级 高)
    # 逻辑：对于每个工作班次，全周期内 Max(人数) - Min(人数) <= 阈值
    # 如果超过阈值，给予重罚
    for s_name, min_val in min_staff_per_shift.items():
        if min_val == 0: continue # 不排的班次不用管
        s_idx = s_map[s_name]
        
        daily_counts = []
        for d in range(num_days):
            c = model.NewIntVar(0, len(employees), f'd_count_{s_name}_{d}')
            model.Add(c == sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))))
            daily_counts.append(c)
            
        max_d = model.NewIntVar(0, len(employees), f'max_d_{s_name}')
        min_d = model.NewIntVar(0, len(employees), f'min_d_{s_name}')
        model.AddMaxEquality(max_d, daily_counts)
        model.AddMinEquality(min_d, daily_counts)
        
        # 差异计算
        diff_d = model.NewIntVar(0, len(employees), f'diff_d_{s_name}')
        model.Add(diff_d == max_d - min_d)
        
        # 软约束：如果 diff_d > 阈值，惩罚 = (diff - 阈值) * 权重(50)
        excess_d = model.NewIntVar(0, len(employees), f'excess_d_{s_name}')
        # excess_d >= diff_d - threshold
        model.Add(excess_d >= diff_d - diff_daily_threshold)
        penalties.append(excess_d * 50) # 权重 50 (高)

    # S2. 员工班次公平性 (优先级 中)
    # 逻辑：对于每个工作班次，所有人中 Max(次数) - Min(次数) <= 阈值
    for s_name, min_val in min_staff_per_shift.items():
        if min_val == 0: continue
        s_idx = s_map[s_name]
        
        emp_counts = []
        for e in range(len(employees)):
            c = model.NewIntVar(0, num_days, f'e_count_{e}_{s_name}')
            model.Add(c == sum(shift_vars[(e, d, s_idx)] for d in range(num_days)))
            emp_counts.append(c)
            
        max_e = model.NewIntVar(0, num_days, f'max_e_{s_name}')
        min_e = model.NewIntVar(0, num_days, f'min_e_{s_name}')
        model.AddMaxEquality(max_e, emp_counts)
        model.AddMinEquality(min_e, emp_counts)
        
        diff_e = model.NewIntVar(0, num_days, f'diff_e_{s_name}')
        model.Add(diff_e == max_e - min_e)
        
        # 软约束：超过阈值才罚
        excess_e = model.NewIntVar(0, num_days, f'excess_e_{s_name}')
        model.Add(excess_e >= diff_e - diff_period_threshold)
        penalties.append(excess_e * 20) # 权重 20 (中)

    # S3. 个人需求处理
    warnings = []
    for idx, row in edited_df.iterrows():
        # 指定休息 (权重 1000 + random)
        try:
            days = [int(x)-1 for x in str(row["指定休息日"]).replace("，",",").split(",") if x.strip().isdigit()]
            for d in days:
                if 0 <= d < num_days:
                    is_off = shift_vars[(idx, d, off_idx)]
                    not_off = model.NewBoolVar(f'vio_off_{idx}_{d}')
                    model.Add(is_off + not_off == 1)
                    penalties.append(not_off * (1000 + random.randint(0,5)))
                    warnings.append({"t": "休", "e": employees[idx], "d": d, "v": is_off})
        except: pass
        
        # 拒绝班次 (权重 100000)
        ref = row["拒绝班次(强)"]
        if ref and ref in shift_work:
            r_idx = s_map[ref]
            for d in range(num_days):
                is_s = shift_vars[(idx, d, r_idx)]
                penalties.append(is_s * 100000)
                warnings.append({"t": "拒", "e": employees[idx], "d": d, "v": is_s, "s": ref})
                
        # 减少班次 (权重 5)
        red = row["减少班次(弱)"]
        if red and red in shift_work:
            rd_idx = s_map[red]
            cnt = sum(shift_vars[(idx, d, rd_idx)] for d in range(num_days))
            penalties.append(cnt * 5)

    # 求解
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # 数据组装
        data_rows = []
        warning_msgs = []
        
        # 检查警告
        for w in warnings:
            day_s = date_headers_simple[w['d']]
            if w['t'] == "休" and solver.Value(w['v']) == 0:
                warning_msgs.append(f"⚠️ {w['e']} {day_s} 休息未满足")
            if w['t'] == "拒" and solver.Value(w['v']) == 1:
                warning_msgs.append(f"🔴 {w['e']} {day_s} 被迫排了{w['s']} (严重人手不足)")

        # 主表数据
        for e in range(len(employees)):
            row = [employees[e]]
            stats = {s: 0 for s in shifts}
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row.append(shifts[s])
                        stats[shifts[s]] += 1
            # 右侧统计
            for s in shift_work: row.append(stats[s])
            row.append(stats[off_shift_name])
            data_rows.append(row)
            
        # 底部统计 (分行)
        footer_rows = []
        # 在岗总数
        r_tot = ["【在岗总数】"]
        for d in range(num_days):
            cnt = sum(1 for r in data_rows if r[d+1] != off_shift_name)
            r_tot.append(cnt)
        r_tot.extend([""] * (len(shift_work)+1))
        footer_rows.append(r_tot)
        
        # 各班次人数
        for s in shifts: # 含休息
            r_s = [f"【{s}人数】"]
            for d in range(num_days):
                cnt = sum(1 for r in data_rows if r[d+1] == s)
                r_s.append(cnt)
            r_s.extend([""] * (len(shift_work)+1))
            footer_rows.append(r_s)

        # 构建 MultiIndex DataFrame
        cols = [("基本信息", "姓名")]
        for d, w in date_tuples: cols.append((d, w))
        for s in shift_work: cols.append(("工时统计", s))
        cols.append(("工时统计", "休息天数"))
        
        df = pd.DataFrame(data_rows + footer_rows, columns=pd.MultiIndex.from_tuples(cols))
        return df, warning_msgs
    
    return None, ["❌ 排班失败：可能是最少在岗人数设置过高，超过了总人数限制。"]

# --- 运行按钮 ---
st.markdown("###")
if st.button("🚀 生成大师排班表", type="primary"):
    with st.spinner("AI 正在进行多目标平衡计算..."):
        df_res, msgs = solve_schedule_v7()
        
        if df_res is not None:
            if msgs:
                with st.expander("⚠️ 冲突报告", expanded=True):
                    for m in msgs: st.write(m)
            else:
                st.success("✅ 完美排班：已满足所有硬性规则及阈值设定。")
            
            # 样式
            def style_map(val):
                s = str(val)
                if off_shift_name in s: return 'background-color: #f0f2f6; color: #ccc'
                if "晚" in s: return 'background-color: #fff3cd; color: #856404'
                if "【" in s: return 'font-weight: bold; background-color: #e6f3ff'
                return ''
            
            st.dataframe(
                df_res.style.applymap(style_map), 
                use_container_width=True, 
                height=600
            )
            
            # 导出处理
            output = io.BytesIO()
            df_exp = df_res.copy()
            df_exp.columns = [f"{c[0]}\n{c[1]}" if "信息" not in c[0] else c[1] for c in df_res.columns]
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_exp.to_excel(writer, index=False)
            st.download_button("📥 下载 Excel", output.getvalue(), "智能排班_V7.xlsx")
        else:
            st.error(msgs[0])
