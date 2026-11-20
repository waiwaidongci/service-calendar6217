"""抽样算法自检：小规模随机实例上与暴力枚举对比（python -m scripts.check_sampling）。

验证：两套名额同时凑齐；超时单最多；同分取冻结行号字典序最小的整份名单；
凑不齐时抛出 Infeasible 且与暴力判定一致。plan_sample 本身不枚举组合。
"""
import itertools,random
from collections import Counter
from src.services.sampling import plan_sample,Infeasible

def brute(rows,gq,pq):
 gq={k:v for k,v in gq.items() if v>0};pq={k:v for k,v in pq.items() if v>0}
 total=sum(gq.values())
 if total!=sum(pq.values()) or total==0: return None
 best=None
 for combo in itertools.combinations(rows,total):
  if Counter(r['department'] for r in combo)!=gq: continue
  if Counter(r['priority'] for r in combo)!=pq: continue
  key=(sum(int(r['overdue']) for r in combo),tuple(sorted(r['row'] for r in combo)))
  if best is None or key[0]>best[0] or (key[0]==best[0] and key[1]<best[1]): best=key
 return best

def check(rows,gq,pq):
 expected=brute(rows,gq,pq)
 try:
  got=plan_sample(rows,gq,pq)
 except Infeasible:
  assert expected is None,f'算法判定凑不齐，但暴力找到解：'+str((rows,gq,pq,expected))
  return
 assert expected is not None,f'算法找到解，但暴力判定凑不齐：'+str((rows,gq,pq))
 key=(sum(int(r['overdue']) for r in got),tuple(r['row'] for r in got))
 assert key==expected,f'名单不一致：算法{key} 暴力{expected} '+str((rows,gq,pq))

def random_cases():
 rng=random.Random(42)
 for _ in range(3000):
  n=rng.randint(1,11)
  rows=[{'row':i,'order_no':f'o{i}','department':rng.choice('甲乙丙'),'priority':rng.choice('急缓'),'overdue':rng.random()<0.5} for i in range(n)]
  gq={g:rng.randint(0,3) for g in '甲乙丙'}
  pq={p:rng.randint(0,3) for p in '急缓'}
  check(rows,gq,pq)

def scenarios():
 rows=[{'row':0,'order_no':'a','department':'甲','priority':'急','overdue':False},
       {'row':1,'order_no':'b','department':'甲','priority':'急','overdue':True},
       {'row':2,'order_no':'c','department':'甲','priority':'缓','overdue':False}]
 assert [r['row'] for r in plan_sample(rows,{'甲':1},{'急':1})]==[1]  # 超时优先
 rows2=rows+[{'row':3,'order_no':'d','department':'甲','priority':'急','overdue':True}]
 assert [r['row'] for r in plan_sample(rows2,{'甲':2},{'急':1,'缓':1})]==[1,2]  # 凑齐两套后超时最多
 tie=[dict(r,order_no=f't{i}') for i,r in enumerate(rows2[:3])]
 for r in tie: r['priority']='急';r['overdue']=True
 assert [r['row'] for r in plan_sample(tie,{'甲':2},{'急':2})]==[0,1]  # 同分取行号最前
 try: plan_sample(rows,{'甲':2},{'急':1}); assert False
 except Infeasible as e: assert '总数不一致' in e.reasons[0]  # 两套总数互相卡住
 try: plan_sample(rows,{'甲':5},{'急':5}); assert False
 except Infeasible as e: assert any('甲' in x and '剩余可抽仅' in x for x in e.reasons)  # 单项缺口
 block=[{'row':0,'order_no':'x0','department':'甲','priority':'急','overdue':False},
        {'row':1,'order_no':'x1','department':'甲','priority':'急','overdue':True},
        {'row':2,'order_no':'x2','department':'乙','priority':'急','overdue':False},
        {'row':3,'order_no':'x3','department':'乙','priority':'急','overdue':False},
        {'row':4,'order_no':'x4','department':'丙','priority':'缓','overdue':False},
        {'row':5,'order_no':'x5','department':'丙','priority':'缓','overdue':False}]
 try: plan_sample(block,{'甲':2,'乙':2,'丙':1},{'急':3,'缓':2}); assert False
 except Infeasible as e:  # 单项名额都够，但甲乙只能占「急」而「急」名额不足：联合卡住
  assert not any('剩余可抽仅' in x for x in e.reasons),e.reasons
  assert any('互相卡住' in x and '甲' in x and '急' in x for x in e.reasons),e.reasons
 ignored=[{'row':0,'order_no':'z','department':'丙','priority':'急','overdue':True}]+block[:2]
 assert [r['row'] for r in plan_sample(ignored,{'甲':1},{'急':1})]==[1]  # 名额外小组不参与

if __name__=='__main__':
 scenarios();random_cases();print('check_sampling ok')
