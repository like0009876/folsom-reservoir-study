from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'results/research'
checks=[]
def check(name,condition,detail):
 checks.append({'check':name,'pass':bool(condition),'evidence':detail})
 if not condition:raise AssertionError((name,detail))
daily=sorted(set(R.glob('daily_*.csv'))|set(R.glob('continuous_1243days_*.csv'))|set(R.glob('drought_carryover_1036days_*.csv'))|set(R.glob('flood_event_[0-9]*_*.csv')))
maxerr=0.
for f in daily:
 d=pd.read_csv(f,parse_dates=['date'])
 err=(d.storage_end-d.storage_begin-d.inflow+d.evap+d.total_release).abs().max();maxerr=max(maxerr,float(err))
 check('saved_balance_'+f.name,err<2e-6,f'max absolute residual={err:.9g} TAF; rounded saved CSV')
 check('saved_constraints_'+f.name,
       d.date.diff().dropna().eq(pd.Timedelta(days=1)).all() and
       np.allclose(d.storage_begin.iloc[1:],d.storage_end.iloc[:-1],atol=1e-6) and
       d.storage_end.between(-1e-7,975+1e-6).all() and
       d[['controlled_release','spill','service','deficit']].ge(-1e-7).all().all() and
       d.controlled_release.le(d.controlled_capacity+1e-6).all() and
       np.allclose(d.service+d.deficit,d.demand,atol=1e-7) and
       np.allclose(d.controlled_release+d.spill,d.total_release,atol=1e-6),str(f.relative_to(ROOT)))
check('saved_daily_case_count',len(daily)==39,len(daily))
expected={'annual_baseline_metrics.csv':21,'training_parameter_grid.csv':42,'sensitivity_annual_metrics.csv':132,
          'official_tocs_2011_metrics.csv':3,'continuous_carryover_metrics.csv':3,'drought_carryover_metrics.csv':3,
          'preference_sensitivity.csv':8,'flood_event_metrics.csv':9}
for name,n in expected.items():
 d=pd.read_csv(R/name);check('case_count_'+name,len(d)==n,{'actual':len(d),'expected':n})
b=pd.read_csv(R/'annual_baseline_metrics.csv')
for row in b.itertuples():
 d=pd.read_csv(R/f'daily_WY{row.water_year}_{row.policy}.csv')
 satisfaction=1-d.deficit.sum()/d.demand.sum()
 mse=((d.deficit/d.demand)**2).mean()
 check(f'summary_recalculation_{row.water_year}_{row.policy}',abs(satisfaction-row.volume_satisfaction)<1e-8 and abs(mse-row.mean_squared_relative_deficit)<1e-8,{'volume_satisfaction':satisfaction,'mean_squared_relative_deficit':mse})
p=json.loads((R/'frozen_parameters.json').read_text())
grid=pd.read_csv(R/'training_parameter_grid.csv').sort_values(['training_score','theta','alpha'])
check('grid_selection',np.allclose([p['theta'],p['alpha'],p['training_score']],grid.iloc[0][['theta','alpha','training_score']].to_numpy(dtype=float)),p)
check('disjoint_calibration_and_test',not set(p['training_years'])&set(p['test_years']),{'training':p['training_years'],'test':p['test_years']})
s=pd.read_csv(R/'test_mean_annual_metrics.csv').set_index('policy')
for k in ['A','B','C']:
 ref=b[b.split.eq('test')&b.policy.eq(k)].volume_satisfaction.mean()
 check('reported_test_annual_mean_'+k,abs(ref-s.loc[k,'volume_satisfaction'])<1e-12,ref)
for name,h in json.loads((ROOT/'docs/migration_sha256.json').read_text()).items():
 check('original_raw_hash_'+name,hashlib.sha256((ROOT/'data/raw'/name).read_bytes()).hexdigest()==h,h)
for fig in sorted((ROOT/'figures/research').glob('*.png')):
 check('figure_export_'+fig.stem,fig.stat().st_size>10000 and fig.with_suffix('.pdf').exists(),str(fig.relative_to(ROOT)))
text=(ROOT/'report/Folsom水库调度研究报告.md').read_text(encoding='utf-8')
for term in ['利益相关者','可计算模型','三种管理策略','敏感性','公平','生态','局限','管理建议','参考资料']:
 check('report_requirement_'+term,term in text,'report/Folsom水库调度研究报告.md')
check('model_internal_checks',json.loads((R/'verification.json').read_text())['all_pass'],'results/research/verification.json')
result={'all_pass':all(c['pass'] for c in checks),'checks':checks,'count':len(checks),'saved_max_balance_error_taf':maxerr,
        'scope':'Independent recomputation of exported cases, artifact coverage and declared protocol; no claim of real-world validation'}
(R/'independent_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False,indent=2))
