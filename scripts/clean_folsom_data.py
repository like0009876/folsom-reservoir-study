"""Data preparation only. No operation simulation, policy fitting or comparison."""
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT/'data/raw', ROOT/'data/processed'
OUT.mkdir(parents=True, exist_ok=True)
QA = ROOT/'results/data_quality'
QA.mkdir(parents=True, exist_ok=True)
CFS_TO_TAF_DAY = 86.4 / 43560
TAF_TO_M3 = 1233481.83754752

def save(df, name, folder=OUT):
    df.to_csv(folder/name, index=False, encoding='utf-8-sig', date_format='%Y-%m-%d')

g = pd.read_csv(RAW/'folsom_daily.csv')
assert g.columns.tolist() == ['date','inflow','outflow','storage','evap']
g['date'] = pd.to_datetime(g['date'], format='%Y-%m-%d', errors='raise')
assert not g.date.duplicated().any()
assert g.date.is_monotonic_increasing
assert len(pd.date_range(g.date.min(),g.date.max()).difference(g.date)) == 0
for col in ['inflow','outflow','storage','evap']:
    g[col] = pd.to_numeric(g[col], errors='raise')
    assert np.isfinite(g[col].dropna()).all()
g['water_year'] = g.date.dt.year + g.date.dt.month.ge(10).astype(int)
g['balance_residual_taf'] = g.storage.diff()-g.inflow+g.outflow+g.evap
g['missing_storage_or_release'] = g[['storage','outflow']].isna().any(axis=1)
g['inflow_at_0_001_taf_floor'] = g.inflow.eq(.001)
g['balance_review_gt_0_1_taf'] = g.balance_residual_taf.abs().gt(.1)
g['above_repository_capacity_975_taf'] = g.storage.gt(975)
g['provenance'] = 'ResLLM public project input; not certified as independent observations'
save(g, 'github_daily_quality_controlled.csv')
save(g[g.missing_storage_or_release | g.inflow_at_0_001_taf_floor |
       g.balance_review_gt_0_1_taf | g.above_repository_capacity_975_taf],
     'github_review_records.csv', QA)

# Official series: retain original value/flags/timestamps, and add normalized values.
l = pd.read_csv(RAW/'cdec_FOL_daily_1994_2016.csv', keep_default_na=False)
l.insert(0, 'raw_record_number', np.arange(1,len(l)+1))
assert set(l.STATION_ID) == {'FOL'} and set(l.DURATION) == {'D'}
spec = {15:('storage_taf','AF',.001),76:('inflow_taf_per_day','CFS',CFS_TO_TAF_DAY),
        23:('outflow_taf_per_day','CFS',CFS_TO_TAF_DAY),
        74:('evap_taf_per_day','CFS',CFS_TO_TAF_DAY),94:('tocs_taf','AF',.001)}
assert set(l.SENSOR_NUMBER) == set(spec)
l['date'] = pd.to_datetime(l['DATE TIME'],format='%Y%m%d %H%M',errors='raise')
l['obs_datetime'] = pd.to_datetime(l['OBS DATE'],format='%Y%m%d %H%M',errors='coerce')
l['numeric_original'] = pd.to_numeric(l.VALUE,errors='coerce')
l['source_flag'] = l.DATA_FLAG.str.strip()
l['missing_or_non_numeric'] = l.numeric_original.isna()
l['negative_value'] = l.numeric_original.lt(0)
l['obs_day_mismatch'] = l.numeric_original.notna() & (
    l.obs_datetime.isna() | l.obs_datetime.dt.normalize().ne(l.date.dt.normalize()))
l['normalized_field'] = l.SENSOR_NUMBER.map(lambda n:spec[n][0])
l['value_taf_or_taf_per_day'] = l.numeric_original * l.SENSOR_NUMBER.map(lambda n:spec[n][2])
for sensor,(_,unit,_) in spec.items():
    assert set(l.loc[l.SENSOR_NUMBER.eq(sensor),'UNITS']) == {unit}
assert not l.duplicated(['SENSOR_NUMBER','date']).any(), 'Duplicate series/date: reconcile first'
save(l, 'cdec_normalized_long.csv')
save(l[l.missing_or_non_numeric | l.negative_value | l.obs_day_mismatch |
       l.source_flag.ne('')], 'cdec_review_records.csv', QA)

idx = pd.date_range('1994-01-01','2016-09-30')
w = pd.DataFrame(index=idx)
for sensor,(field,_,_) in spec.items():
    a = l[l.SENSOR_NUMBER.eq(sensor)].set_index('date')
    w[field] = a.value_taf_or_taf_per_day.reindex(idx)
    w[field+'_source_flag'] = a.source_flag.reindex(idx).fillna('NO_RECORD')
    w[field+'_obs_day_mismatch'] = a.obs_day_mismatch.reindex(idx).astype('boolean').fillna(False).astype(bool)
w.index.name = 'date'
w['water_year'] = w.index.year+(w.index.month>=10)
core = ['storage_taf','inflow_taf_per_day','outflow_taf_per_day','evap_taf_per_day']
w['core_available'] = w[core].notna().all(axis=1)
w['forcing_available_nonnegative'] = w[['inflow_taf_per_day','evap_taf_per_day']].notna().all(axis=1) & w.inflow_taf_per_day.ge(0) & w.evap_taf_per_day.ge(0)
w['five_fields_available'] = w[core+['tocs_taf']].notna().all(axis=1)
w['negative_any'] = w[core+['tocs_taf']].lt(0).any(axis=1)
w['any_obs_day_mismatch'] = w[[c for c in w if c.endswith('_obs_day_mismatch')]].any(axis=1)
flagcols=[c for c in w if c.endswith('_source_flag')]
w['any_nonblank_source_flag'] = w[flagcols].ne('').any(axis=1)
w['balance_residual_taf'] = w.storage_taf.diff()-w.inflow_taf_per_day+w.outflow_taf_per_day+w.evap_taf_per_day
w['balance_review_gt_0_1_taf'] = w.balance_residual_taf.abs().gt(.1)
w['requires_review'] = ~w.core_available | w.negative_any | w.any_obs_day_mismatch | w.any_nonblank_source_flag | w.balance_review_gt_0_1_taf
save(w.reset_index(), 'cdec_daily_quality_controlled.csv')

# Cross-source differences are not automatically corrections. Do not mix sources.
cross = g.set_index('date')[['inflow','outflow','storage','evap']].join(w[core],how='inner')
summ=[]
for github,official in zip(['inflow','outflow','storage','evap'],['inflow_taf_per_day','outflow_taf_per_day','storage_taf','evap_taf_per_day']):
    cross[github+'_official_minus_github'] = cross[official]-cross[github]
    a=cross[github+'_official_minus_github'].dropna()
    summ.append({'variable':github,'paired_days':len(a),'within_0_01_taf':int(a.abs().le(.01).sum()),
                 'median_absolute_difference_taf':a.abs().median(),'mean_absolute_difference_taf':a.abs().mean(),'max_absolute_difference_taf':a.abs().max()})
save(cross.reset_index(), 'official_github_crosscheck.csv', QA)
save(pd.DataFrame(summ),'crosscheck_summary.csv', QA)

year=[]
for wy,a in w.groupby('water_year'):
    expected=len(pd.date_range(f'{wy-1}-10-01',f'{wy}-09-30'))
    year.append({'water_year':wy,'days_in_download':len(a),'expected_full_year_days':expected,
                 **{field+'_missing_days':int(a[field].isna().sum()) for field in core+['tocs_taf']},
                 'negative_days':int(a.negative_any.sum()),'obs_day_mismatch_days':int(a.any_obs_day_mismatch.sum()),
                 'balance_review_days':int(a.balance_review_gt_0_1_taf.sum()),
                 'forcing_complete_full_water_year':bool(len(a)==expected and a.forcing_available_nonnegative.all()),
                 'all_five_complete_full_water_year':bool(len(a)==expected and a.five_fields_available.all() and not a.negative_any.any())})
year=pd.DataFrame(year);save(year,'water_year_coverage.csv',QA)

# Do not concatenate separated valid days into a continuous simulation input.
segments=[]
mask=w.forcing_available_nonnegative
groups=mask.ne(mask.shift()).cumsum()
for _,a in w[mask].groupby(groups[mask]):
    segments.append({'start_date':a.index.min(),'end_date':a.index.max(),'days':len(a)})
seg=pd.DataFrame(segments).sort_values('days',ascending=False)
save(seg,'nonnegative_forcing_continuous_segments.csv',QA)

# A screened, complete water year for method development, without any infilling.
primary = w[w.water_year.eq(2011)].copy()
assert len(primary)==365 and primary.five_fields_available.all()
assert not primary.requires_review.any()
assert primary.index.min()==pd.Timestamp('2010-10-01')
assert primary.index.max()==pd.Timestamp('2011-09-30')
save(primary.reset_index(), 'PRIMARY_cdec_WY2011_screened.csv')
strict = w.five_fields_available & ~w.requires_review
strict_groups = strict.ne(strict.shift()).cumsum()
strict_segments=[]
for _,a in w[strict].groupby(strict_groups[strict]):
    strict_segments.append({'start_date':a.index.min(),'end_date':a.index.max(),'days':len(a)})
strict_seg=pd.DataFrame(strict_segments).sort_values('days',ascending=False)
save(strict_seg,'all_five_screen_pass_continuous_segments.csv',QA)
longest=strict_seg.iloc[0]
save(w.loc[longest.start_date:longest.end_date].reset_index(), 'EXTENDED_longest_screen_pass_525days.csv')

# Demand is a scenario profile: preserve full source and map 365 unique calendar days.
demand=np.loadtxt(RAW/'demand.txt')
assert len(demand)==366 and np.isfinite(demand).all() and (demand>=0).all()
assert demand[0]==demand[-1]
dates=pd.date_range('2001-10-01','2002-09-30') # non-leap reference calendar, not observation year
profile=pd.DataFrame({'water_year_day_365':range(1,366),'month':dates.month,'day':dates.day,
                      'demand_taf_per_day':demand[:365],
                      'data_role':'scenario_profile_not_observed_demand'})
save(profile,'demand_scenario_365_day_profile.csv')

stats={
 'cleaned_on':'2026-10-22',
 'github':{'total_days':len(g),'missing_storage_and_outflow_days':int(g.missing_storage_or_release.sum()),
           'floor_0_001_days':int(g.inflow_at_0_001_taf_floor.sum()),
           'balance_review_gt_0_1_taf_days':int(g.balance_review_gt_0_1_taf.sum()),
           'above_model_capacity_days':int(g.above_repository_capacity_975_taf.sum()),
           'balance_max_abs_taf':float(g.balance_residual_taf.abs().max())},
 'cdec':{'raw_records':len(l),'calendar_days':len(w),'non_numeric_records':int(l.missing_or_non_numeric.sum()),
         'negative_records':int(l.negative_value.sum()),'obs_day_mismatch_numeric_records':int(l.obs_day_mismatch.sum()),
         'nonblank_source_flag_numeric_records':int((l.source_flag.ne('')&~l.missing_or_non_numeric).sum()),
         'core_complete_days':int(w.core_available.sum()),'five_fields_complete_days':int(w.five_fields_available.sum()),
         'forcing_complete_nonnegative_days':int(w.forcing_available_nonnegative.sum()),
         'full_water_years_forcing_complete':year.loc[year.forcing_complete_full_water_year,'water_year'].tolist(),
         'full_water_years_all_five_complete':year.loc[year.all_five_complete_full_water_year,'water_year'].tolist(),
         'missing_days_by_field':{c:int(w[c].isna().sum()) for c in core+['tocs_taf']}},
 'demand':{'source_length':len(demand),'last_equals_first':bool(demand[0]==demand[-1]),'unique_calendar_profile_length':365,
           'annual_demand_taf':float(demand[:365].sum()),'role':'assumption; not validated observed demand'},
 'constants':{'cfs_to_taf_per_day':CFS_TO_TAF_DAY,'taf_to_m3':TAF_TO_M3},
 'screened_primary':{'water_year':2011,'start':'2010-10-01','end':'2011-09-30','days':365,
                     'max_abs_balance_residual_taf':float(primary.balance_residual_taf.abs().max()),
                     'remaining_limit':'Screening does not prove independence of source variables or full policy validity'},
 'rules':{'imputation':'none','outlier_deletion':'none','timestamp_shift':'none; record date retained, OBS DATE differences flagged',
          'balance_review_threshold_taf':.1,'threshold_role':'screening only; not a physical invalidity verdict',
          'cross_source_filling':'none','negative_inflow_clipping':'none','scenario_comparison_performed':False}}
(QA/'quality_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')

dictionary=[
 ('date','CDEC DATE TIME record date','date','Retain source date; OBS DATE differences remain visible'),
 ('storage_taf','CDEC sensor15; AF / 1000','TAF','State observation; not demand'),
 ('inflow_taf_per_day','CDEC sensor76; CFS * 86.4/43560','TAF/day','Daily mean flow converted to daily volume'),
 ('outflow_taf_per_day','CDEC sensor23; CFS * 86.4/43560','TAF/day','Total release is not identical to actual consumptive use'),
 ('evap_taf_per_day','CDEC sensor74; computed evaporation flow converted','TAF/day','Computed source quantity, not independent direct measurement'),
 ('tocs_taf','CDEC sensor94; AF /1000','TAF','Reported operating control; missing dates not interpolated'),
 ('balance_residual_taf','S(t)-S(t-1)-Q(t)+R(t)+E(t)','TAF','Diagnostic; other terms/time conventions may affect closure'),
 ('requires_review','missing, negative, date, source flag or balance screen','boolean','Warning; does not automatically mean erroneous'),
 ('forcing_available_nonnegative','inflow and evaporation finite and >=0','boolean','Availability only, not full quality certification'),
 ('*_source_flag','unaltered CDEC flag after stripping whitespace','text','NO_RECORD means no source record; semantics not independently resolved'),
 ('demand_taf_per_day','first365 source demand.txt values','TAF/day','Scenario only; leap-day assignment remains unresolved')]
save(pd.DataFrame(dictionary,columns=['field','definition','unit','usage_limit']),'data_dictionary.csv')

manifest=[]
for folder in [RAW,OUT,QA]:
 for f in sorted(folder.glob('*')):
  if f.is_file():manifest.append({'path':str(f.relative_to(ROOT)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
(ROOT/'docs/data_cleaning_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(stats,ensure_ascii=False,indent=2))
print('\nLongest available forcing segments:\n'+seg.head(5).to_string(index=False))
