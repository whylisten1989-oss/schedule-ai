import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io

# --- 页面配置 ---
st.set_page_config(page_title="智能排班系统", layout="wide")

st.title("🤖 智能排班助手 (AI Scheduling)")
st.markdown("### 专为您定制的自动化排班工具")

# --- 侧边栏：输入与配置 ---
with st.sidebar:
    st.header("1. 人员与班次设置")
    
    # 上传员工名单
    uploaded_file = st.file_uploader("上传员工名单 (Excel/CSV)", type=['xlsx', 'csv'])
    employees = []
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            if "姓名" in df.columns:
                employees = df["姓名"].tolist()
                st.success(f"已加载 {len(employees)} 名员工")
            else:
                st.error("表格中必须包含'姓名'这一列")
        except Exception as e:
            st.error(f"文件读取失败: {e}")

    # 定义班次
    shifts_input = st.text_input("输入班次名称 (用逗号分隔)", "早班, 中班, 晚班, 休")
    shifts = [s.strip() for s in shifts_input.split(",")]
    days = 7  # 默认排一周
    num_days = st.slider("排班天数", 1, 31, 7)

    st.header("2. 约束规则")
    # 每天每班次所需人数
    min_staff = st.number_input("每个班次最少人数", min_value=1, value=2)
    
    # 简单的个人偏好示例
    st.subheader("个人偏好")
    if employees:
        selected_emp = st.selectbox("选择员工设置偏好", employees)
        off_days = st.multiselect(f"选择 {selected_emp} 想要休息的日子", [f"第{i+1}天" for i in range(num_days)])
        # 这里只是演示，实际逻辑需要更复杂的存储结构

# --- 核心算法：AI 排班引擎 ---
def solve_schedule(employees, shifts, num_days, min_staff):
    model = cp_model.CpModel()
    shift_vars = {}

    # 创建变量：员工 e 在第 d 天是否上班次 s
    for e in range(len(employees)):
        for d in range(num_days):
            for s in range(len(shifts)):
                shift_vars[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')

    # 约束 1: 每天每人只能安排 1 个班次 (包括休息)
    for e in range(len(employees)):
        for d in range(num_days):
            model.Add(sum(shift_vars[(e, d, s)] for s in range(len(shifts))) == 1)

    # 约束 2: 每天除“休”以外的班次，必须满足最少人数
    # 假设输入的班次最后一个是“休”，或者用户明确指定
    # 这里简化处理：默认非“休”的班次都需要人
    work_shifts = [s for s in range(len(shifts)) if "休" not in shifts[s]]
    
    for d in range(num_days):
        for s in work_shifts:
            model.Add(sum(shift_vars[(e, d, s)] for e in range(len(employees))) >= min_staff)

    # (高级约束如“晚转早”可以在此继续添加...)

    # 求解
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        data = []
        for e in range(len(employees)):
            row = {"姓名": employees[e]}
            for d in range(num_days):
                for s in range(len(shifts)):
                    if solver.Value(shift_vars[(e, d, s)]):
                        row[f"第{d+1}天"] = shifts[s]
            data.append(row)
        return pd.DataFrame(data)
    else:
        return None

# --- 主界面：生成与展示 ---
if st.button("🚀 开始 AI 排班"):
    if not employees:
        st.warning("请先上传员工名单！")
    else:
        with st.spinner("AI 正在计算最佳排班方案..."):
            result_df = solve_schedule(employees, shifts, num_days, min_staff)
            
            if result_df is not None:
                st.success("✅ 排班成功！")
                st.dataframe(result_df)
                
                # 下载按钮
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    result_df.to_excel(writer, index=False)
                st.download_button(
                    label="📥 下载 Excel 排班表",
                    data=output.getvalue(),
                    file_name="排班表.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.error("❌ 无法找到满足条件的排班，请尝试降低约束条件（如减少每班人数）。")
