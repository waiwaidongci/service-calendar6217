"""抽样服务自检（python -m scripts.check_sample_service）：sqlite 内存库 + 假配置，不依赖 docker。

覆盖：整批占用、请求键幂等找回、同月原单（含更正单）不再抽、并发撞单后用剩余底稿重算、
凑不齐说明、旧报告缺原单编号不可抽但可查看。
"""
import os,sys,types
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/sample_service_test.db')
os.environ.setdefault('JWT_SECRET','test')
fake=types.ModuleType('src.config.settings')
fake.CONFIG={'fields':{}}
fake.DATABASE_URL=os.environ['DATABASE_URL']
fake.REDIS_URL='redis://localhost:6379/0'
fake.SECRET='test'
sys.modules['src.config.settings']=fake
if os.path.exists('/tmp/sample_service_test.db'): os.remove('/tmp/sample_service_test.db')
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.config.database import Base,engine,Session
from src.models import identity,run,sampling
from src.models.run import Run
from src.models.sampling import SampleBatch,SampleOccupation
from src.services.sample import sample_run,list_samples
from src.services.sampling import plan_sample

Base.metadata.create_all(engine)
USER=SimpleNamespace(email='analyst@demo.local')
POLICY={'version':'v1','timezone':'UTC','weekdays':[0,1,2,3,4,5,6],'windows':[[0,1440]],'holidays':[],'sla_minutes':{'急':60,'缓':60}}
def src(no,dept,prio,mins,period='2026-11'):
 return {'order_no':no,'period':period,'department':dept,'opened':'2026-11-01T00:00:00+00:00','closed':f'2026-11-01T{mins//60:02d}:{mins%60:02d}:00+00:00','priority':prio}
def mk_run(sources,status='completed'):
 with Session() as db:
  r=Run(name='十一月核算',status=status,snapshot={'policy':POLICY,'sources':sources,'period':'2026-11'},checksum='x',created=1.0,approved=False)
  db.add(r);db.commit();return r.id
def body(key,g,p): return SimpleNamespace(request_key=key,groups=g,priorities=p)
def expect(code,fn):
 try: fn();assert False,'应抛出 '+str(code)
 except HTTPException as e: assert e.status_code==code,(e.status_code,e.detail);return e.detail

# 1. 基本抽样：两套名额凑齐、整批占用；更正单去重（OD-2 的更正行覆盖原行）
sources=[src('OD-1','甲','急',90),src('OD-2','甲','缓',30),src('OD-3','乙','急',10),src('OD-4','乙','缓',120),src('OD-2','甲','急',45)]
rid=mk_run(sources)
with Session() as db:
 res=sample_run(db,USER,rid,body('key-0001',{'甲':2,'乙':1},{'急':2,'缓':1}))
 picked={s['order_no'] for s in res['selected']}
 assert picked=={'OD-1','OD-2','OD-4'},picked  # OD-2 以更正行（急）计入
 assert sum(1 for s in res['selected'] if s['overdue'])==2
 assert db.query(SampleOccupation).count()==3
# 2. 失败后重试：同请求键找回同一份名单，不重复占用
with Session() as db:
 again=sample_run(db,USER,rid,body('key-0001',{'甲':2,'乙':1},{'急':2,'缓':1}))
 assert again['id']==res['id'] and db.query(SampleOccupation).count()==3
# 3. 同月已抽中的原单不能再选：第二批复算避开第一批
with Session() as db:
 sources2=sources+[src('OD-5','甲','急',80),src('OD-6','乙','缓',70),src('OD-7','乙','急',5)]
 rid2=mk_run(sources2)
 res2=sample_run(db,USER,rid2,body('key-0002',{'甲':1,'乙':1},{'急':1,'缓':1}))
 picked2={s['order_no'] for s in res2['selected']}
 assert not picked2&picked,(picked2,picked)
 assert db.query(SampleOccupation).count()==5
# 4. 同请求键但名额不同：拒绝
with Session() as db: expect(409,lambda:sample_run(db,USER,rid,body('key-0001',{'甲':1,'乙':1},{'急':1,'缓':1})))
# 5. 凑不齐：说明哪些名额互相卡住
with Session() as db:
 detail=expect(409,lambda:sample_run(db,USER,rid2,body('key-0003',{'甲':9,'乙':1},{'急':9,'缓':1})))
 assert '名额凑不齐' in detail and '甲' in detail,detail
# 6. 缺原单编号的旧报告：照常查看，抽样时说明不能抽
with Session() as db:
 old=mk_run([{'period':'2026-11','department':'甲','opened':'2026-11-01T00:00:00+00:00','closed':'2026-11-01T01:00:00+00:00','priority':'急'}])
 assert list_samples(db,old)==[]
 detail=expect(409,lambda:sample_run(db,USER,old,body('key-0004',{'甲':1},{'急':1})))
 assert '缺少原单编号' in detail and '照常查看' in detail,detail
# 7. 未完成报告不能抽
with Session() as db:
 queued=mk_run(sources,status='queued')
 expect(409,lambda:sample_run(db,USER,queued,body('key-0005',{'甲':1},{'急':1})))
# 8. 并发撞单：占用提交时撞上别批先占的单，用剩余底稿重算
import sqlalchemy.orm as orm
with Session() as db:
 rid3=mk_run([src('OD-8','甲','急',90),src('OD-9','甲','急',30),src('OD-10','甲','急',10)])
 pool=[{'row':i,'order_no':no,'department':'甲','priority':'急','overdue':od} for i,(no,od) in enumerate([('OD-8',True),('OD-9',False),('OD-10',False)])]
 victim=plan_sample(pool,{'甲':2},{'急':2})[0]['order_no']  # 超时单 OD-8 会被优先抽中
 real_commit,real_rollback=orm.Session.commit,orm.Session.rollback
 state={'failed':False,'occupied':False}
 def flaky_commit(self):
  if not state['failed']:
   state['failed']=True
   raise IntegrityError('INSERT',{},Exception('duplicate key uq_sample_month_order'))
  return real_commit(self)
 def patched_rollback(self):
  real_rollback(self)
  if state['failed'] and not state['occupied']:  # 别批在回滚后抢先占用
   state['occupied']=True
   with Session() as other: other.add(SampleOccupation(month='2026-11',order_no=victim,batch_id=999));other.commit()
 orm.Session.commit=flaky_commit;orm.Session.rollback=patched_rollback
 try: res3=sample_run(db,USER,rid3,body('key-0006',{'甲':2},{'急':2}))
 finally: orm.Session.commit,orm.Session.rollback=real_commit,real_rollback
 assert victim not in {s['order_no'] for s in res3['selected']},res3['selected']
 assert len(res3['selected'])==2
# 9. 名单可找回
with Session() as db:
 batches=list_samples(db,rid3)
 assert len(batches)==1 and batches[0]['id']==res3['id']
print('check_sample_service ok')
