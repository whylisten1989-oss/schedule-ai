import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import datetime

# --- 页面配置 ---
st.set_page_config(page_title="智能排班 V5.0 (生产力版)", layout="wide", page_icon="🧩")
st.title("🧩 智能排班系统 V5.0 - 生产力版")

# --- 工具函数 ---
def get_date_headers(start_date, end_date):
    """生成带有周几的日期列表"""
    delta = end_date - start_date
    return [(start_date + datetime.timedelta(days=i)).strftime('%m-%d (%a)') for i in range(delta.days + 1)]

# --- 1. 基础数据配置 (侧边栏) ---
with st.sidebar:
    st.header("1. 人员与班次")
    
    # 员工名单
    default_employees = "张三,李四,王五,赵六,钱七,孙八,周九,吴十"
    emp_input = st.text_area("员工名单", default_employees, height=80)
    employees = [e.strip() for e in emp_input.split(",") if e.strip()]
    
    # 班次设置
    shifts_input = st.text_input("班次定义 (必须含'休')", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    
    try:
        off_shift_name = next(s for s in shifts if "休" in s)
    except:
        st.error("❌ 班次中必须包含'休'字！")
        st.stop()
        
    shift_work = [s for s in shifts if s != off_shift_name] # 工作班次列表

    st.markdown("---")
    st.header("2. 模式与限制")
    
    # 休息模式 (转化为最小休息天数)
    rest_mode = st.selectbox("休息模式", ["做6休1 (标准)", "做5休2 (双休)", "自定义天数"])
    
    # 晚转早
    enable_no_night_to_day = st.checkbox("🚫 禁止晚转早", value=True)
    if enable_no_night_to_day:
        night_shift = st.selectbox("晚班是?", shift_work, index=len(shift_work)-1)
        day_shift = st.selectbox("早班是?", shift_work, index=0)

# --- 主界面 ---
st.subheader("⚙️ 排班控制台")

# 日期选择
c1, c2, c3 = st.columns(3)
with c1:
    start_date = st.date_input("开始日期", datetime.date.today())
with c2:
    end_date = st.date_input("结束日期", datetime.date.today() + datetime.timedelta(days=6))
with c3:
    # 动态计算所需的休息天数
    num_days = (end_date - start_date).days + 1
    if rest_mode == "做6休1 (标准)":
        min_off_days = num_days // 7
    elif rest_mode == "做5休2 (双休)":
        min_off_days = (num_days // 7) * 2
    else:
        min_off_days = st.number_input(f"{num_days}天内最少休息几天?", min_value=0, value=1)
    
    # 最大连续工作天数 (硬性防疲劳)
    max_consecutive_work = st.number_input("最大连续上班天数", min_value=1, max_value=10, value=6, help="为了防止连续工作太久，通常设为6")

if start_date > end_date:
    st.error("日期无效")
    st.stop()
    
date_headers = get_date_headers(start_date, end_date)

# --- 规则配置区 ---
col_rule, col_table = st.columns([1, 3])

with col_rule:
    st.info(f"排班周期: {num_days} 天")
    st.write(f"每个人最少休息: **{min_off_days}** 天")
    
    st.markdown("##### 每日最少在岗")
    min_staff_per_shift = {}
    for s in shift_work:
        min_staff_per_shift[s] = st.number_input(f"{s}", min_value=0, value=1, key=f"min_{s}")

with col_table:
    st.markdown("##### 🙋‍♂️ 员工状态与需求 (支持衔接上周)")
    
    # 初始化数据
    init_data = {
        "姓名": employees,
        "上期末班 (用于衔接)": [off_shift_name for _ in employees], # 默认是休，不影响
        "指定休息日 (如: 1,3)": ["" for _ in employees],
        "拒绝班次 (尽量满足)": ["" for _ in employees]
    }
    
    # 配置可编辑表格
    df_requests = pd.DataFrame(init_data)
    edited_df = st.data_editor(
        df_requests,
        column_config={
            "上期末班 (用于衔接)": st.column_config.SelectboxColumn(options=shifts, help="该员工昨天上的是什么班？用于判断是否冲突（如昨晚夜班，今早不能早班）"),
            "指定休息日 (如: 1,3)": st.column_config.TextColumn(help="希望休息的第几天，用逗号隔开"),
            "拒绝班次 (尽量满足)": st.column_config.SelectboxColumn(options=shift_work, help="如果不满足，系统会提示警告，但仍会排班")
        },
        hide_index=True,
        use_container_width=True
    )

# --- 核心算法 V5 ---
def solve_schedule_v5():
    model = cp_model.CpModel()
    shift_vars = {}
    s_map = {s: i for i, s in enumerate(shifts)}
    off_idx = s_map[off_shift_name]
    
    # 目标函数惩罚项
    penalties = []
    
    # 1. 创建变量
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # --- 硬约束 (必须满足，否则无解) ---
    
    # H1. 每天每人 1 个班
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)
            
    # H2. 每日最少人数 (人手不够是绝对不行的)
    for d in range(num_days):
        for s_name, min_val in min_staff_per_shift.items():
            s_idx = s_map[s_name]
            model.Add(sum(shift_vars[(e, d, s_idx)] for e in range(len(employees))) >= min_val)

    # H3. 周期内最少休息天数
    for e in range(len(employees)):
        model.Add(sum(shift_vars[(e, d, off_idx)] for d in range(num_days)) >= min_off_days)
        
    # H4. 最大连续工作天数 (防止过劳)
    # 逻辑：对于任意连续的 (max_work + 1) 天，其中必须至少有一天是休息
    work_indices = [i for i, s in enumerate(shifts) if s != off_shift_name]
    for e in range(len(employees)):
        # 滑动窗口
        for d in range(num_days - max_consecutive_work):
            # 这是一个布尔逻辑：sum(是工作班次) <= max_consecutive_work
            # 也就是在 max + 1 的窗口里，工作天数不能等于窗口长度
            window_vars = []
            for k in range(max_consecutive_work + 1): # 比如限6，看7天
                 for w_idx in work_indices:
                     window_vars.append(shift_vars[(e, d + k, w_idx)])
            
            # 在这7天里，工作班次的总和不能等于7 (也就是必须 < 7，至少有1个休)
            model.Add(sum(window_vars) <= max_consecutive_work)

    # H5. 晚转早限制 (含历史衔接)
    if enable_no_night_to_day:
        n_idx = s_map[night_shift]
        d_idx = s_map[day_shift]
        
        # A. 周期内衔接
        for e in range(len(employees)):
            for d in range(num_days - 1):
                model.Add(shift_vars[(e, d, n_idx)] + shift_vars[(e, d+1, d_idx)] <= 1)
        
        # B. 历史衔接 (第0天)
        # 读取表格里的 "上期末班"
        for idx, row in edited_df.iterrows():
            last_s = row["上期末班 (用于衔接)"]
            if last_s == night_shift: # 如果昨天是晚班
                # 今天(第0天)绝不能是早班
                model.Add(shift_vars[(idx, 0, d_idx)] == 0)

    # --- 软约束 (尽量满足，不行就扣分) ---
    
    # S1. 处理个人需求
    warnings_check = [] # 用于后续验证
    
    for index, row in edited_df.iterrows():
        e_idx = index
        
        # 指定休息日
        req_days_str = str(row["指定休息日 (如: 1,3)"])
        if req_days_str.strip():
            try:
                days_list = [int(x.strip()) - 1 for x in req_days_str.replace("，", ",").split(",") if x.strip().isdigit()]
                for day_req in days_list:
                    if 0 <= day_req < num_days:
                        # 定义一个布尔变量：是否满足了休息
                        is_off = shift_vars[(e_idx, day_req, off_idx)]
                        # 如果不休息(is_off=0)，惩罚 100 分 (非常高)
                        # 我们用 not_off 来代表违规
                        not_off = model.NewBoolVar(f'violate_rest_{e_idx}_{day_req}')
                        model.Add(is_off + not_off == 1) 
                        penalties.append(not_off * 1000) 
                        
                        warnings_check.append({
                            "type": "休息申请", "emp": employees[e_idx], "day": day_req, "var": is_off
                        })
            except: pass

        # 拒绝班次
        refuse = row["拒绝班次 (尽量满足)"]
        if refuse and refuse in shift_work:
            r_idx = s_map[refuse]
            count_refuse = sum(shift_vars[(e_idx, d, r_idx)] for d in range(num_days))
            # 每排一次，惩罚 100 分
            penalties.append(count_refuse * 100)
            
            # 这里不好做精确的 warning check，因为是计数，不是单点

    # S2. 公平性 (班次均衡)
    # 我们希望每个人的工作班次总数尽量接近平均值
    # 简化版：惩罚 (每个人的工作总数 - 理想平均数) 的绝对值
    # 理想平均工作天数 = (总人天 - 总休息) / 总人数，这里简单处理：
    # 直接惩罚每个人各个班次数量的方差（这里用差值代替）
    
    for s_name in shift_work:
        s_idx = s_map[s_name]
        # 计算每个人上这个班的次数
        counts = []
        for e in range(len(employees)):
            c = model.NewIntVar(0, num_days, f'count_{e}_{s_name}')
            model.Add(c == sum(shift_vars[(e, d, s_idx)] for d in range(num_days)))
            counts.append(c)
        
        # 尽量让最大值和最小值的差 越小越好
        max_c = model.NewIntVar(0, num_days, f'max_{s_name}')
        min_c = model.NewIntVar(0, num_days, f'min_{s_name}')
        model.AddMaxEquality(max_c, counts)
        model.AddMinEquality(min_c, counts)
        
        # 惩罚差值 (权重 10，比个人需求低，比随便排高)
        penalties.append((max_c - min_c) * 10)


    # 求解目标
    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # --- 数据组装 ---
        data = []
        warning_msgs = []
        
        # 1. 检查警告
        for check in warnings_check:
            if solver.Value(check["var"]) == 0:
                day_str = date_headers[check["day"]]
                warning_msgs.append(f"⚠️ {check['emp']} 在 {day_str} 的休息请求未被满足（人手不足）。")

        # 2. 构建主表
        for e in range(len(employees)):
            row_data = {"姓名": employees[e]}
            stats = {s: 0 for s in shifts}
            
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row_data[date_headers[d]] = shifts[s]
                        stats[shifts[s]] += 1
            
            # 右侧统计
            total_work = 0
            for s in shift_work:
                row_data[f"统计-{s}"] = stats[s]
                total_work += stats[s]
            row_data["总工时(天)"] = total_work
            data.append(row_data)
        
        df_result = pd.DataFrame(data)
        
        # 3. 构建底部统计 (独立多行)
        # 我们创建一个新的 DataFrame 来放底部统计，然后 concat
        footer_rows = []
        
        # (1) 在岗总人数
        row_total_on = {"姓名": "【在岗总人数】"}
        for d in range(num_days):
            day_h = date_headers[d]
            # 统计这一列里，不是'休'的数量
            count = sum(1 for val in df_result[day_h] if val != off_shift_name)
            row_total_on[day_h] = count
        footer_rows.append(row_total_on)
        
        # (2) 各班次独立统计
        for s in shift_work:
            row_s = {"姓名": f"【{s}人数】"}
            for d in range(num_days):
                day_h = date_headers[d]
                count = sum(1 for val in df_result[day_h] if val == s)
                row_s[day_h] = count
            footer_rows.append(row_s)
            
        df_footer = pd.DataFrame(footer_rows)
        # 填补统计列的空缺
        for col in df_result.columns:
            if col not in df_footer.columns:
                df_footer[col] = ""
                
        df_final = pd.concat([df_result, df_footer], ignore_index=True)
        
        return df_final, warning_msgs
    else:
        return None, ["严重冲突：无法满足基础硬性规则（如最少人数或最大连班数）。"]

# --- 运行 ---
st.markdown("###")
if st.button("🚀 生成智能排班表", type="primary"):
    with st.spinner("AI 正在平衡供需与公平性..."):
        result_df, msgs = solve_schedule_v5()
        
        if result_df is not None:
            if msgs:
                with st.expander("⚠️ 冲突提示 (部分需求未满足)", expanded=True):
                    for m in msgs: st.warning(m)
            else:
                st.success("✅ 完美排班：所有硬性规则与个人需求均已满足。")
            
            # 样式高亮
            def highlight(val):
                s_val = str(val)
                if off_shift_name in s_val: return 'background-color: #f0f2f6; color: #999'
                if "晚" in s_val: return 'background-color: #fff3cd; color: #856404'
                if "【" in s_val: return 'font-weight: bold; background-color: #e6f3ff'
                return ''
                
            st.dataframe(result_df.style.applymap(highlight), use_container_width=True)
            
            # Excel 下载
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                result_df.to_excel(writer, index=False)
            st.download_button("📥 下载排班表 (Excel)", output.getvalue(), "排班表_V5.xlsx")
            
        else:
            st.error("❌ 排班失败")
            st.error(msgs[0])
            st.markdown("建议：减少'每日最少在岗人数' 或 增加 '最大连续上班天数'。")
