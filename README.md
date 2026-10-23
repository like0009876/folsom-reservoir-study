# Folsom Reservoir Study

Folsom水库供水保障与防洪约束下的调度策略情景研究。基于加州CDEC官方日水文输入，比较按需求供给A、固定分级限水B和参数优化连续限水C，并提供完整代码、数据、结果和研究报告。

## 报告与结果

- [Word完整研究报告（含封面与目录）](report/Folsom水库调度研究报告_完整版.docx)
- [完整报告Markdown](report/Folsom水库调度研究报告_完整版.md)
- [前版数值修正报告](report/Folsom水库调度研究报告_修正版.docx)
- [研究报告Markdown](report/Folsom水库调度研究报告.md)
- [主要结果表](results/research/test_mean_annual_metrics.csv)
- [数值振荡原因与修正](docs/numerical_fix.md)
- [数据清洗说明](docs/数据清洗报告.md)

四个独立检验年份中，A的目标下泄体积满足率约91.47%，C约88.77%；C的平均相对缺水平方较A低约10.31%，但限水天数更多。不存在全部指标都最优的策略。

## 目录

```text
scripts/                清洗、模拟、报告生成和独立校验代码
data/raw/               原始公开数据及项目输入
data/processed/         带质量标记的整理数据
results/data_quality/   数据质量检查
results/research/       参数、逐日输出、汇总和敏感性结果
figures/research/       图表（PNG、PDF）
report/                 Word、PDF、Markdown研究报告
docs/                   来源、许可证、研究假设和修正说明
```

## 运行

建议Python 3.11及以上：

```bash
pip install -r requirements.txt
python scripts/clean_folsom_data.py
python scripts/run_folsom_research.py
python scripts/build_folsom_report.py
python scripts/build_folsom_word.py
python scripts/verify_folsom_research.py
```

数值计算不需要大模型或付费API。PDF生成器当前使用Windows宋体字体；Word公式采用仓库中预先渲染的公式图像。只需重算模型时运行第二条Python命令即可。

## 图表修正与适用范围

旧版在90TAF低库容边界使用日初出库能力，产生隔日“蓄水—集中放水”伪振荡。现改为日内24子步更新出库能力，并检查至96子步的收敛；未平滑或篡改原始日数据。当前图表和统计均为修正后版本。

入流和计算蒸发来自官方资料，需水曲线及部分运行约束属于明确的情景假设。本项目不是历史实际调度复现，不能直接作为运行规程，也不能把汇总下泄量当作用户实际耗水或完整生态达标结论。

## 数据来源与致谢

- [California DWR CDEC, Folsom站](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=FOL)
- [EILab-Polimi/ResLLM](https://github.com/EILab-Polimi/ResLLM)：情景需水及简化配置
- [USBR Central Valley Project](https://www.usbr.gov/mp/cvp/)

上游项目许可证保存在docs/source_LICENSE。原始数据及上游材料保留各自权利和使用条件；本仓库未对它们另行授权。论文与官方资料完整引用见报告。

## 完整研究报告

2026-10-23更新：新增独立封面和带章节链接的目录页，重组研究问题、理论依据、实验设计与结果讨论，补充不确定性和后续研究。共18页，目录按本版实际页码校准；后续编辑正文后需重新校准页码。现有数据、模型和模拟结果未因文字修订而改变。生成入口为scripts/build_full_report.py。
