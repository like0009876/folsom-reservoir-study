"""Reproducible daily water-balance scenario experiment. No LLM required."""
from pathlib import Path
import json, hashlib, platform
from dataclasses import dataclass, asdict, replace
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/research'; FIG=ROOT/'figures/research'
OUT.mkdir(parents=True,exist_ok=True); FIG.mkdir(parents=True,exist_ok=True)
CFS=86.4/43560
TRAIN=[2000,2003,2007]; TEST=[2011,2012,2015,2016]
LABEL={'A':'Demand-first','B':'Fixed hedging','C':'Optimized hedging'}
COLORS={'A':'#215A8E','B':'#D58B27','C':'#2A896F'}

@dataclass(frozen=True)
class Config:
    capacity: float=975.
    reserve: float=90.
    initial: float=466.1
    inflow_scale: float=1.
    demand_scale: float=1.
    tocs_offset: float=0.
    eco_scale: float=1.
    safe_cfs: float=130000.
    substeps: int=24

raw=pd.read_csv(ROOT/'data/processed/cdec_daily_quality_controlled.csv',parse_dates=['date'])
profile=pd.read_csv(ROOT/'data/processed/demand_scenario_365_day_profile.csv')
lookup={(int(r.month),int(r.day)):float(r.demand_taf_per_day) for r in profile.itertuples()}
lookup[(2,29)]=(lookup[(2,28)]+lookup[(3,1)])/2

def prepare(a):
    a=a.copy().reset_index(drop=True)
    for f in ['inflow_taf_per_day','evap_taf_per_day']:
        assert a[f].notna().all() and a[f].ge(0).all()
        assert a[f+'_source_flag'].fillna('').eq('').all()
        assert not a[f+'_obs_day_mismatch'].any()
    assert a.date.diff().dropna().eq(pd.Timedelta(days=1)).all()
    a['demand_base']= [lookup[(t.month,t.day)] for t in a.date]
    a['eco_base']= [CFS*(500 if (t.month>=9 and (t.month>9 or t.day>=15)) else 250) for t in a.date]
    # October-December also require the historical 500cfs proxy.
    a['dowy']=[(t-pd.Timestamp(year=t.year if t.month>=10 else t.year-1,month=10,day=1)).days+1 for t in a.date]
    a['fixed_tocs']=np.interp(a.dowy,[0,50,151,200,243,366],[975,400,400,750,975,975])
    return a

YEARS={wy:prepare(raw[raw.water_year.eq(wy)]) for wy in TRAIN+TEST}
for wy,a in YEARS.items():
    assert a.date.iloc[0]==pd.Timestamp(wy-1,10,1) and a.date.iloc[-1]==pd.Timestamp(wy,9,30)
CONT=prepare(raw[raw.date.between('2009-12-16','2013-05-11')])
for wy,a in YEARS.items(): a.to_csv(OUT/f'input_WY{wy}.csv',index=False,encoding='utf-8-sig')

def simulate(a,policy,theta=.5,alpha=.6,cfg=Config(),official_tocs=False):
    n=len(a); rec=np.empty((n,13));s=cfg.initial
    qs=a.inflow_taf_per_day.to_numpy()*cfg.inflow_scale
    es=a.evap_taf_per_day.to_numpy()
    ds=a.demand_base.to_numpy()*cfg.demand_scale
    fs=a.eco_base.to_numpy()*cfg.eco_scale
    limits=(a.tocs_taf.to_numpy() if official_tocs else a.fixed_tocs.to_numpy())+cfg.tocs_offset
    limits=np.clip(limits,cfg.reserve,cfg.capacity)
    assert np.isfinite(limits).all()
    for i,(q,e,d,f,tocs) in enumerate(zip(qs,es,ds,fs,limits)):
        prior=s
        ratio=max(0.,min(1.,(prior-cfg.reserve)/max(tocs-cfg.reserve,1.)))
        if policy=='A': fraction=1.
        elif policy=='B': fraction=.6 if ratio<.5 else 1.
        elif policy=='C': fraction=alpha+(1-alpha)*min(1.,ratio/theta)
        else: raise ValueError(policy)
        target=max(d*fraction,f)
        # Daily data are distributed uniformly within the day, not treated as
        # observed hourly data. Update the head-dependent outlet capacity hourly
        # to avoid the old daily explicit-step empty/refill oscillation at 90 TAF.
        assert cfg.substeps>=1
        dt=1./cfg.substeps
        controlled=spill=cap=peak=0.
        for _ in range(cfg.substeps):
            available=s+(q-e)*dt
            if available<0:raise ValueError('Evaporation exceeds all available water')
            if s<=90: rate=0.
            elif s<100: rate=(s-90)*3500
            elif s<400: rate=35000+(s-100)*(5000/300)
            elif s<600: rate=40000+(s-400)*375
            elif s<975: rate=115000+(s-600)*40
            else:rate=130000.
            capacity_volume=min(rate,cfg.safe_cfs)*CFS*dt
            flood_volume=max(available-tocs,0.)
            r_step=min(max(target*dt,flood_volume),capacity_volume,max(available-cfg.reserve,0.))
            spill_step=max(available-r_step-cfg.capacity,0.)
            s=available-r_step-spill_step
            controlled+=r_step;spill+=spill_step;cap+=capacity_volume
            peak=max(peak,(r_step+spill_step)/(CFS*dt))
        release=controlled+spill
        service=min(d,release)
        deficit=d-service
        residual=s-prior-q+e+release
        rec[i]=[prior,s,controlled,spill,release,service,deficit,max(s-tocs,0.),max(f-release,0.),residual,fraction,cap,peak]
    z=a[['date','water_year']].copy()
    z[['storage_begin','storage_end','controlled_release','spill','total_release','service','deficit','tocs_excess','eco_deficit','balance_error','command_fraction','controlled_capacity','max_substep_release_cfs']]=rec
    z['inflow']=qs;z['evap']=es;z['demand']=ds;z['eco_target']=fs;z['tocs']=limits
    assert abs(z.balance_error).max()<1e-8
    assert z.storage_end.between(-1e-8,cfg.capacity+1e-8).all()
    assert (z.controlled_release<=z.controlled_capacity+1e-8).all()
    return z

def metrics(z,cfg=Config()):
    frac=z.deficit/z.demand
    fail=z.deficit.to_numpy()>1e-7
    runs=[];current=0
    for x in fail:
        current=current+1 if x else 0
        if current:runs.append(current)
    transitions=int((fail[:-1]&~fail[1:]).sum())
    at_risk=int(fail[:-1].sum())
    return dict(days=len(z),inflow_taf=z.inflow.sum(),demand_taf=z.demand.sum(),deficit_taf=z.deficit.sum(),
                volume_satisfaction=1-z.deficit.sum()/z.demand.sum(),time_reliability=1-fail.mean(),
                reliability_95pct=float((frac<=.05+1e-9).mean()),reliability_90pct=float((frac<=.1+1e-9).mean()),
                mean_squared_relative_deficit=float((frac**2).mean()),max_daily_deficit_ratio=frac.max(),
                max_shortage_run_days=max(runs,default=0),resilience=transitions/at_risk if at_risk else np.nan,
                basic_50pct_shortage_days=int((z.service<.5*z.demand-1e-7).sum()),
                end_storage_taf=z.storage_end.iloc[-1],min_storage_taf=z.storage_end.min(),
                spill_taf=z.spill.sum(),tocs_excess_days=int(z.tocs_excess.gt(1e-7).sum()),
                max_tocs_excess_taf=z.tocs_excess.max(),max_release_cfs=z.total_release.max()/CFS,
                release_over_safe_days=int(z.total_release.gt(cfg.safe_cfs*CFS+1e-7).sum()),
                substep_release_over_safe_days=int(z.max_substep_release_cfs.gt(cfg.safe_cfs+1e-6).sum()),
                peak_substep_release_cfs=float(z.max_substep_release_cfs.max()),
                eco_proxy_shortage_days=int(z.eco_deficit.gt(1e-7).sum()),eco_proxy_deficit_taf=z.eco_deficit.sum(),
                low_storage_below_200_days=int(z.storage_end.lt(200).sum()),
                reserve_violation_days=int(z.storage_end.lt(cfg.reserve-1e-7).sum()),
                max_balance_error_taf=z.balance_error.abs().max())

def score(theta,alpha,weight=.05):
    terms=[]
    for wy in TRAIN:
        for scale in [1.,.8]:
            z=simulate(YEARS[wy],'C',theta,alpha,replace(Config(),inflow_scale=scale))
            terms.append(float(((z.deficit/z.demand)**2).mean())+weight*(max(300-z.storage_end.iloc[-1],0)/300)**2)
    return np.mean(terms)

grid=[]
for theta in [.2,.35,.5,.65,.8,1.]:
    for alpha in [.1,.25,.4,.55,.7,.85,1.]:
        grid.append({'theta':theta,'alpha':alpha,'training_score':score(theta,alpha)})
grid=pd.DataFrame(grid).sort_values(['training_score','theta','alpha'])
grid.to_csv(OUT/'training_parameter_grid.csv',index=False)
best=grid.iloc[0]; THETA=float(best.theta); ALPHA=float(best.alpha)
(OUT/'frozen_parameters.json').write_text(json.dumps({'theta':THETA,'alpha':ALPHA,'training_score':float(best.training_score),'training_years':TRAIN,'test_years':TEST,'config':asdict(Config()),'grid_size':len(grid)},indent=2),encoding='utf-8')
print('Frozen parameters',THETA,ALPHA,flush=True)

allrows=[];series={}
for wy in TRAIN+TEST:
 for policy in ['A','B','C']:
    z=simulate(YEARS[wy],policy,THETA,ALPHA)
    m=metrics(z);allrows.append({'water_year':wy,'split':'train' if wy in TRAIN else 'test','policy':policy,**m})
    z.to_csv(OUT/f'daily_WY{wy}_{policy}.csv',index=False,float_format='%.10g')
    series[(wy,policy)]=z
base=pd.DataFrame(allrows);base.to_csv(OUT/'annual_baseline_metrics.csv',index=False)
summary=base[base.split.eq('test')].groupby('policy').mean(numeric_only=True).drop(columns='water_year').reset_index()
summary.to_csv(OUT/'test_mean_annual_metrics.csv',index=False)

cases={
 'inflow_60pct':replace(Config(),inflow_scale=.6),'inflow_80pct':replace(Config(),inflow_scale=.8),
 'demand_80pct':replace(Config(),demand_scale=.8),'demand_120pct':replace(Config(),demand_scale=1.2),
 'initial_250':replace(Config(),initial=250),'initial_700':replace(Config(),initial=700),
 'tocs_plus100':replace(Config(),tocs_offset=100),'tocs_plus177':replace(Config(),tocs_offset=177),
 'capacity_977':replace(Config(),capacity=977),'ecofloor_zero':replace(Config(),eco_scale=0),
 'ecofloor_double':replace(Config(),eco_scale=2),
}
sens=[]
for name,cfg in cases.items():
 for wy in TEST:
  for policy in ['A','B','C']:
    z=simulate(YEARS[wy],policy,THETA,ALPHA,cfg)
    sens.append({'scenario':name,'water_year':wy,'policy':policy,**metrics(z,cfg)})
pd.DataFrame(sens).to_csv(OUT/'sensitivity_annual_metrics.csv',index=False)

# Actual 2011 TOCS as a contemporaneous constraint replay, not a forecastable rule.
off=[]
for policy in ['A','B','C']:
 z=simulate(YEARS[2011],policy,THETA,ALPHA,official_tocs=True)
 off.append({'policy':policy,**metrics(z)})
 z.to_csv(OUT/f'daily_WY2011_official_tocs_{policy}.csv',index=False)
pd.DataFrame(off).to_csv(OUT/'official_tocs_2011_metrics.csv',index=False)

continuous=[]
for policy in ['A','B','C']:
 z=simulate(CONT,policy,THETA,ALPHA)
 continuous.append({'policy':policy,**metrics(z)})
 z.to_csv(OUT/f'continuous_1243days_{policy}.csv',index=False)
pd.DataFrame(continuous).to_csv(OUT/'continuous_carryover_metrics.csv',index=False)

drought=prepare(raw[raw.date.between('2013-11-30','2016-09-30')])
drought_initial=float(raw.loc[raw.date.eq('2013-11-29'),'storage_taf'].iloc[0])
droughtrows=[];droughtseries={}
for policy in ['A','B','C']:
 cfg=replace(Config(),initial=drought_initial)
 z=simulate(drought,policy,THETA,ALPHA,cfg)
 droughtrows.append({'policy':policy,'initial_storage_taf':drought_initial,**metrics(z,cfg)})
 droughtseries[policy]=z
 z.to_csv(OUT/f'drought_carryover_1036days_{policy}.csv',index=False)
pd.DataFrame(droughtrows).to_csv(OUT/'drought_carryover_metrics.csv',index=False)

# Parameter preference sensitivity: tune only training data, then evaluate held-out years.
pref=[]
for weight in [0.,.2]:
 candidates=[(score(float(r.theta),float(r.alpha),weight),float(r.theta),float(r.alpha)) for r in grid.itertuples()]
 v,t,a=min(candidates)
 for wy in TEST:
  pref.append({'reserve_weight':weight,'theta':t,'alpha':a,'water_year':wy,**metrics(simulate(YEARS[wy],'C',t,a))})
pd.DataFrame(pref).to_csv(OUT/'preference_sensitivity.csv',index=False)

# Step-size convergence uses unchanged observed daily inputs and fixed parameters.
convergence=[]
for steps in [1,4,12,24,48,96]:
 for policy in ['A','B','C']:
  cfg=replace(Config(),substeps=steps)
  z=simulate(YEARS[2015],policy,THETA,ALPHA,cfg)
  convergence.append({'substeps':steps,'policy':policy,**metrics(z,cfg)})
  if steps in [1,24,96]:z.to_csv(OUT/f'diagnostic_WY2015_steps{steps}_{policy}.csv',index=False)
pd.DataFrame(convergence).to_csv(OUT/'timestep_convergence.csv',index=False)

# Separate historical flood event, screened with the same forcing rules.
# Added after the initial baseline run; frozen hedging parameters are unchanged.
event=prepare(raw[raw.date.between('1996-12-15','1997-02-15')])
event_initial=float(raw.loc[raw.date.eq('1996-12-14'),'storage_taf'].iloc[0])
event.to_csv(OUT/'input_flood_event_1997.csv',index=False)
flood=[];floodseries={}
for mult in [1.,1.25,1.5]:
 for policy in ['A','B','C']:
  cfg=replace(Config(),initial=event_initial,inflow_scale=mult)
  z=simulate(event,policy,THETA,ALPHA,cfg)
  flood.append({'inflow_multiplier':mult,'policy':policy,'initial_storage_taf':event_initial,**metrics(z,cfg)})
  floodseries[(mult,policy)]=z
  z.to_csv(OUT/f'flood_event_{mult}_{policy}.csv',index=False)
pd.DataFrame(flood).to_csv(OUT/'flood_event_metrics.csv',index=False)

# Scientific figures, each exported as PNG and PDF with the same data.
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.bbox':'tight'})
def savefig(name):
 plt.savefig(FIG/(name+'.png'),dpi=180);plt.savefig(FIG/(name+'.pdf'));plt.close()

fig,axs=plt.subplots(1,2,figsize=(11,4))
for split,years,color in [('train',TRAIN,'#738D9C'),('test',TEST,'#215A8E')]:
 axs[0].bar([str(y) for y in years],[YEARS[y].inflow_taf_per_day.sum() for y in years],color=color,label=split)
axs[0].axhline(lookup and profile.demand_taf_per_day.sum(),color='#9C3A37',ls='--',label='365-day release target')
axs[0].set(ylabel='Annual volume (TAF)',xlabel='Independent water year');axs[0].legend(fontsize=8)
ref=YEARS[2011];axs[1].plot(ref.date,ref.fixed_tocs,label='Project fixed curve');axs[1].plot(ref.date,ref.tocs_taf,label='Reported official TOCS',alpha=.8)
axs[1].set(ylabel='Storage target (TAF)',title='Constraint definitions differ');axs[1].legend(fontsize=8);fig.autofmt_xdate();fig.tight_layout();savefig('01_inputs_and_constraints')

fig,axs=plt.subplots(2,2,figsize=(11,7))
for ax,(field,title) in zip(axs.ravel(),[('volume_satisfaction','Volume satisfaction'),('time_reliability','Full-demand days'),('max_daily_deficit_ratio','Maximum daily deficit ratio'),('end_storage_taf','End-of-year storage (TAF)')]):
 for j,p in enumerate(['A','B','C']):
  a=base[base.water_year.isin(TEST)&base.policy.eq(p)].set_index('water_year').loc[TEST]
  ax.bar(np.arange(4)+(j-1)*.24,a[field],width=.23,label=LABEL[p],color=COLORS[p])
 ax.set_xticks(range(4),TEST);ax.set_title(title)
axs[0,0].legend(fontsize=8);fig.tight_layout();savefig('02_holdout_comparison')

fig,axs=plt.subplots(3,1,figsize=(11,8),sharex=True)
for p in ['A','B','C']:
 z=series[(2015,p)];axs[0].plot(z.date,z.storage_end,label=LABEL[p],color=COLORS[p]);axs[1].plot(z.date,z.service/z.demand,color=COLORS[p]);axs[2].plot(z.date,z.deficit.cumsum(),color=COLORS[p])
axs[0].plot(z.date,z.tocs,'--',color='grey',label='TOCS');axs[0].set_ylabel('Storage (TAF)');axs[0].legend(ncol=2,fontsize=8)
axs[1].set_ylabel('Release / target');axs[2].set_ylabel('Cumulative deficit (TAF)');fig.autofmt_xdate();fig.tight_layout();savefig('03_dry_WY2015')

sg=pd.DataFrame(sens)
fig,axs=plt.subplots(1,2,figsize=(11,4))
for p in ['A','B','C']:
 rows=[]
 for name,xval in [('inflow_60pct',.6),('inflow_80pct',.8),('baseline',1.)]:
  a=base[base.split.eq('test')&base.policy.eq(p)] if name=='baseline' else sg[sg.scenario.eq(name)&sg.policy.eq(p)]
  rows.append((xval,a.volume_satisfaction.mean(),a.max_daily_deficit_ratio.mean()))
 axs[0].plot([r[0] for r in rows],[r[1] for r in rows],'-o',label=LABEL[p],color=COLORS[p]);axs[1].plot([r[0] for r in rows],[r[2] for r in rows],'-o',color=COLORS[p])
axs[0].set(ylabel='Mean annual volume satisfaction',xlabel='Inflow multiplier');axs[1].set(ylabel='Mean annual maximum deficit ratio',xlabel='Inflow multiplier');axs[0].legend(fontsize=8);fig.tight_layout();savefig('04_inflow_sensitivity')

fig,ax=plt.subplots(figsize=(7,5))
ax.scatter(grid.theta,grid.alpha,c=grid.training_score,cmap='viridis_r',s=100)
ax.scatter([THETA],[ALPHA],marker='*',s=280,color='#D84335',edgecolor='white',label='Selected on training years')
cb=fig.colorbar(ax.collections[0],ax=ax);cb.set_label('Training objective')
ax.set(xlabel='Hedging threshold',ylabel='Minimum delivery fraction');ax.legend(fontsize=8);savefig('05_parameter_search')

fig,axs=plt.subplots(2,1,figsize=(11,6),sharex=True)
for mult,style in [(1.,'-'),(1.5,'--')]:
 z=floodseries[(mult,'A')]
 axs[0].plot(z.date,z.storage_end,style,label=f'Inflow x{mult}')
 axs[1].plot(z.date,z.total_release/CFS,style,label=f'Inflow x{mult}')
axs[0].plot(z.date,z.tocs,':',color='grey',label='Fixed TOCS');axs[0].axhline(975,color='#9C3A37',ls=':',label='Capacity')
axs[1].axhline(130000,color='#9C3A37',ls=':',label='Simplified channel threshold')
axs[0].set_ylabel('Storage (TAF)');axs[1].set_ylabel('Release (cfs)');axs[0].legend(fontsize=8,ncol=2);axs[1].legend(fontsize=8);fig.autofmt_xdate();fig.tight_layout();savefig('06_flood_event_stress')

fig,axs=plt.subplots(2,1,figsize=(11,6),sharex=True)
for p,z in droughtseries.items():
 axs[0].plot(z.date,z.storage_end,label=LABEL[p],color=COLORS[p]);axs[1].plot(z.date,(z.deficit/z.demand).rolling(30,min_periods=1).mean(),color=COLORS[p])
axs[0].axhline(90,color='grey',ls=':');axs[0].set_ylabel('Storage (TAF)');axs[0].legend(fontsize=8)
axs[1].set_ylabel('30-day mean deficit ratio');fig.autofmt_xdate();fig.tight_layout();savefig('07_multiyear_drought')

fig,axs=plt.subplots(2,1,figsize=(11,6),sharex=True)
for steps,label,color in [(1,'Old daily capacity update','#A74943'),(24,'Corrected hourly capacity update','#215A8E')]:
 z=simulate(YEARS[2015],'A',THETA,ALPHA,replace(Config(),substeps=steps))
 z=z[z.date.between('2015-06-15','2015-08-15')]
 axs[0].plot(z.date,z.service/z.demand,label=label,color=color)
 axs[1].plot(z.date,z.storage_end,label=label,color=color)
axs[0].set_ylabel('Release / target');axs[1].set_ylabel('End-of-day storage (TAF)');axs[0].legend(fontsize=8)
fig.autofmt_xdate();fig.tight_layout();savefig('08_numerical_correction')

# Invariants and special cases: model correctness is separate from real-world validity.
checks=[]
for wy in TEST:
 a=YEARS[wy]
 za=simulate(a,'A');zc=simulate(a,'C',theta=.5,alpha=1.)
 checks.append({'check':f'alpha1_equals_A_WY{wy}','pass':bool(np.allclose(za.storage_end,zc.storage_end,atol=1e-9))})
 for p in ['A','B','C']:
  z=series[(wy,p)]
  checks.append({'check':f'balance_and_bounds_WY{wy}_{p}','pass':bool(z.balance_error.abs().max()<1e-8 and z.storage_end.between(-1e-8,975+1e-8).all())})
# No water, no evaporation: only initial usable storage can be released.
tiny=YEARS[2011].iloc[:3].copy();tiny['inflow_taf_per_day']=0.;tiny['evap_taf_per_day']=0.
zt=simulate(tiny,'A',cfg=replace(Config(),initial=95))
checks.append({'check':'no_water_conservation','pass':bool(abs(zt.total_release.sum()+zt.storage_end.iloc[-1]-95)<1e-9 and zt.storage_end.min()>=90-1e-9)})
checks.append({'check':'heldout_years_not_used_for_fit','pass':not bool(set(TRAIN)&set(TEST))})
cv=pd.DataFrame(convergence).set_index(['substeps','policy'])
checks.append({'check':'WY2015_hourly_vs_96step_convergence',
               'pass':all(abs(cv.loc[(24,p),'volume_satisfaction']-cv.loc[(96,p),'volume_satisfaction'])<1e-6 and
                          abs(cv.loc[(24,p),'mean_squared_relative_deficit']-cv.loc[(96,p),'mean_squared_relative_deficit'])<1e-5 for p in ['A','B','C'])})
checks.append({'check':'primary2011_official_balance_residual','pass':bool(YEARS[2011].balance_residual_taf.abs().max()<.01)})
# Extreme inflow must preserve water and expose unsafe overflow, rather than hide it.
extreme=YEARS[2011].iloc[:1].copy();extreme['inflow_taf_per_day']=1500.;extreme['evap_taf_per_day']=0.
ze=simulate(extreme,'A',cfg=replace(Config(),initial=900))
checks.append({'check':'extreme_inflow_overflow_flagged','pass':bool(ze.spill.iloc[0]>0 and ze.total_release.iloc[0]>130000*CFS and abs(ze.balance_error.iloc[0])<1e-8 and ze.storage_end.iloc[0]<=975+1e-8)})
all_daily=[*series.values(),*floodseries.values()]
checks.append({'check':'flood_target_exceedance_only_if_capacity_binds',
               'pass':all(bool(((z.controlled_capacity-z.controlled_release).abs()[z.tocs_excess.gt(1e-7)]<1e-7).all()) for z in all_daily)})
assert all(c['pass'] for c in checks)
(OUT/'verification.json').write_text(json.dumps({'checks':checks,'all_pass':True,'scope':'Numerical correctness and data eligibility, not proof of real-world optimality'},indent=2),encoding='utf-8')
(OUT/'run_metadata.json').write_text(json.dumps({'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,'train':TRAIN,'test':TEST,'selected':{'theta':THETA,'alpha':ALPHA},'sensitivity_cases':list(cases),'no_imputation':True},indent=2),encoding='utf-8')
print(summary[['policy','volume_satisfaction','time_reliability','max_daily_deficit_ratio','end_storage_taf']].to_string(index=False),flush=True)
print('All research calculations and figures completed.',flush=True)
