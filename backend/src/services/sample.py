import time
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.models.run import Run
from src.models.sampling import SampleBatch,SampleOccupation
from src.services.audit import log
from src.services.sampling import plan_sample,Infeasible
from src.utils.serialize import row
def _orders(run):
 sources=run.snapshot.get('sources') or []
 missing=[i+1 for i,s in enumerate(sources) if not isinstance(s,dict) or not s.get('order_no')]
 if missing: raise HTTPException(409,f'该报告冻结底稿缺少原单编号（第 {"、".join(map(str,missing[:5]))} 行等共 {len(missing)} 行），无法确认原单身份，不能抽样；报告可照常查看与导出')
 from src.services.calculation import business_minutes
 policy=run.snapshot['policy'];sla=policy.get('sla_minutes',{});reps={}
 for i,s in enumerate(sources): reps[s['order_no']]=(i,s)  # 更正单冻结行号在后，覆盖原单，仍算同一单
 orders=[]
 for no,(i,s) in reps.items():
  mins=business_minutes(s['opened'],s['closed'],policy);limit=sla.get(s['priority'])
  orders.append({'row':i,'order_no':no,'department':s['department'],'priority':s['priority'],'period':s['period'],'minutes':mins,'overdue':bool(limit is not None and mins>limit)})
 return orders
def _quotas(body):
 def clean(d,label):
  out={}
  for k,v in d.items():
   if not isinstance(k,str) or not 1<=len(k)<=100: raise HTTPException(422,label+'名额名称不合法')
   if isinstance(v,bool) or not isinstance(v,int) or not 0<=v<=100000: raise HTTPException(422,label+'名额数值不合法')
   if v>0: out[k]=v
  return out
 gq=clean(body.groups,'小组');pq=clean(body.priorities,'优先级')
 if not gq or not pq: raise HTTPException(422,'小组与优先级名额需各至少一项大于 0')
 if sum(gq.values())>100000 or sum(pq.values())>100000: raise HTTPException(422,'名额合计过大')
 return {'groups':gq,'priorities':pq}
def sample_run(db,user,run_id,body):
 run=db.get(Run,run_id)
 if not run: raise HTTPException(404,'报告不存在')
 if run.status!='completed': raise HTTPException(409,'报告尚未完成核算，不能抽样')
 quotas=_quotas(body)
 existing=db.query(SampleBatch).filter_by(request_key=body.request_key).first()
 if existing:  # 失败后重试：找回已抽好的名单，不重复占用
  if existing.run_id!=run_id or existing.quotas!=quotas: raise HTTPException(409,'请求键已被其他抽样使用，请更换请求键')
  return row(existing)
 orders=_orders(run);months=sorted({o['period'] for o in orders})
 for _ in range(8):
  occupied={(o.month,o.order_no) for o in db.query(SampleOccupation).filter(SampleOccupation.month.in_(months)).all()}
  pool=[o for o in orders if (o['period'],o['order_no']) not in occupied]
  try: selected=plan_sample(pool,quotas['groups'],quotas['priorities'])
  except Infeasible as exc: raise HTTPException(409,'名额凑不齐：'+'；'.join(exc.reasons))
  batch=SampleBatch(run_id=run_id,request_key=body.request_key,quotas=quotas,selected=selected,overdue=sum(1 for s in selected if s['overdue']),created=time.time(),actor=user.email)
  db.add(batch)
  try:
   db.flush()  # 名额凑齐后整批占用
   db.add_all([SampleOccupation(month=s['period'],order_no=s['order_no'],batch_id=batch.id) for s in selected])
   log(db,user.email,'run.sample',{'run':run_id,'batch':batch.id,'count':len(selected),'overdue':batch.overdue})
   db.commit();return row(batch)
  except IntegrityError:
   db.rollback()
   again=db.query(SampleBatch).filter_by(request_key=body.request_key).first()
   if again: return row(again)  # 同请求键并发提交，找回已抽好的名单
   # 撞上别批先占的单：用剩余底稿重算
 raise HTTPException(409,'并发抽样冲突较多，请稍后重试')
def list_samples(db,run_id):
 if not db.get(Run,run_id): raise HTTPException(404,'报告不存在')
 return [row(b) for b in db.query(SampleBatch).filter_by(run_id=run_id).order_by(SampleBatch.id.desc()).all()]
