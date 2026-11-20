"""月度报告抽样算法（纯函数，不依赖数据库与配置）。

输入候选单：{'row','order_no','department','priority','overdue',...}，row 为冻结行号（0 起始）。
目标：同时凑齐小组名额与优先级名额；在此前提下最大化超时单数量；
同分时取按冻结行号排序后字典序最小的整份名单。
实现：单元格（小组×优先级）聚合 + 最小费用最大流求最大超时数，
再按冻结行号升序贪心“能选就选”（每次以残量网络上的最小费用最大流判定可行性），
复杂度 O(候选数 × 名额 × (小组数×优先级数) )，十万单不枚举组合。
"""
from collections import defaultdict
import heapq


class Infeasible(Exception):
    """名额凑不齐；reasons 为互相卡住的名额说明。"""

    def __init__(self, reasons):
        super().__init__('；'.join(reasons))
        self.reasons = reasons


def _mcmf(n, arcs, s, t, limit):
    """最小费用最大流（SSP + 势能保证非负约化费用）。返回 (流量, 费用, 残量网络中源可达集)。"""
    graph = [[] for _ in range(n)]
    for u, v, cap, cost in arcs:
        graph[u].append([v, cap, cost, len(graph[v])])
        graph[v].append([u, 0, -cost, len(graph[u]) - 1])
    flow = cost = 0
    pot = [0] * n
    inf = float('inf')
    while flow < limit:
        dist = [inf] * n
        dist[s] = 0
        prev = [None] * n
        heap = [(0, s)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for i, e in enumerate(graph[u]):
                if e[1] > 0:
                    nd = d + e[2] + pot[u] - pot[e[0]]
                    if nd < dist[e[0]]:
                        dist[e[0]] = nd
                        prev[e[0]] = (u, i)
                        heapq.heappush(heap, (nd, e[0]))
        if dist[t] == inf:
            break
        for v in range(n):
            if dist[v] < inf:
                pot[v] += dist[v]
        add = limit - flow
        v = t
        while v != s:
            u, i = prev[v]
            add = min(add, graph[u][i][1])
            v = u
        v = t
        while v != s:
            u, i = prev[v]
            e = graph[u][i]
            e[1] -= add
            graph[v][e[3]][1] += add
            cost += add * e[2]
            v = u
        flow += add
    reach = [False] * n
    reach[s] = True
    stack = [s]
    while stack:
        u = stack.pop()
        for e in graph[u]:
            if e[1] > 0 and not reach[e[0]]:
                reach[e[0]] = True
                stack.append(e[0])
    return flow, cost, reach


def _solve(gq, pq, cap, over):
    """构造名额流网络：源→小组(名额)→[超时容量0费,普通容量1费]→优先级(名额)→汇。

    返回 (最大流量, 最大流下最少普通单数, 源可达集, 节点编号映射)。
    流量==名额合计 即两套名额可凑齐；此时 名额合计-费用 即最多超时单数。
    """
    groups = sorted(gq)
    prios = sorted(pq)
    gi = {g: 1 + i for i, g in enumerate(groups)}
    pi = {p: 1 + len(groups) + i for i, p in enumerate(prios)}
    t = 1 + len(groups) + len(prios)
    arcs = [(0, gi[g], gq[g], 0) for g in groups]
    for (g, p), c in cap.items():
        o = min(over.get((g, p), 0), c)
        if o:
            arcs.append((gi[g], pi[p], o, 0))
        if c - o:
            arcs.append((gi[g], pi[p], c - o, 1))
    arcs += [(pi[p], t, pq[p], 0) for p in prios]
    flow, cost, reach = _mcmf(t + 1, arcs, 0, t, sum(gq.values()))
    return flow, cost, reach, (gi, pi)


def _reasons(gq, pq, cells, reach, gi, pi):
    """凑不齐时说明哪些名额互相卡住：单项缺口 + 最小割证书（Hall 式联合约束）。"""
    reasons = []
    avail_g = defaultdict(int)
    avail_p = defaultdict(int)
    for (g, p), lst in cells.items():
        avail_g[g] += len(lst)
        avail_p[p] += len(lst)
    for g, q in sorted(gq.items()):
        if avail_g[g] < q:
            reasons.append(f'小组「{g}」需 {q} 单，剩余可抽仅 {avail_g[g]} 单')
    for p, q in sorted(pq.items()):
        if avail_p[p] < q:
            reasons.append(f'优先级「{p}」需 {q} 单，剩余可抽仅 {avail_p[p]} 单')
    a_groups = [g for g in sorted(gq) if reach[gi[g]]]
    b_prios = [p for p in sorted(pq) if reach[pi[p]]]
    need_a = sum(gq[g] for g in a_groups)
    cap_b = sum(pq[p] for p in b_prios)
    cross = sum(len(lst) for (g, p), lst in cells.items() if g in a_groups and p not in b_prios)
    gap = need_a - cap_b - cross
    if gap > 0:
        trivial = (len(a_groups) == 1 and not b_prios) or (len(a_groups) == len(gq) and len(b_prios) == len(pq) - 1)
        if not trivial:  # 平凡情形已由单项缺口说明，联合证书只解释真正互相卡住的名额
            if not b_prios:
                joint = f'小组「{"、".join(a_groups)}」合计需 {need_a} 单，剩余底稿中仅 {cross} 单可抽，缺 {gap} 单'
            else:
                joint = (f'小组「{"、".join(a_groups)}」的名额（合计 {need_a} 单）与优先级「{"、".join(b_prios)}」的名额（合计 {cap_b} 单）互相卡住：'
                         f'这些小组的单据计入该优先级名额的至多 {cap_b} 单，其余 {need_a - cap_b} 单只能来自这些小组的其他优先级底稿，'
                         f'但剩余底稿中仅 {cross} 单符合，缺 {gap} 单')
            if joint not in reasons:
                reasons.append(joint)
    return reasons


def plan_sample(rows, group_quotas, priority_quotas):
    """返回按冻结行号升序的选中单列表；凑不齐时抛出 Infeasible(reasons)。

    rows：候选单（已剔除同月已占用原单、已按原单编号去重）。
    名额表只列正数名额；未列出的小组/优先级名额为 0，其单据不参与抽取。
    """
    gq = {k: int(v) for k, v in group_quotas.items() if v > 0}
    pq = {k: int(v) for k, v in priority_quotas.items() if v > 0}
    total = sum(gq.values())
    ptotal = sum(pq.values())
    if total != ptotal:
        raise Infeasible([f'小组名额合计 {total} 单，优先级名额合计 {ptotal} 单，总数不一致，两套名额互相卡住，无法凑齐'])
    if total == 0:
        raise Infeasible(['小组与优先级名额至少各填一项且合计大于 0'])
    cells = defaultdict(list)
    for r in rows:
        if r['department'] in gq and r['priority'] in pq:
            cells[(r['department'], r['priority'])].append(r)
    cap0 = {k: len(v) for k, v in cells.items()}
    over0 = {k: sum(1 for x in v if x['overdue']) for k, v in cells.items()}
    flow, cost, reach, (gi, pi) = _solve(gq, pq, cap0, over0)
    if flow < total:
        raise Infeasible(_reasons(gq, pq, cells, reach, gi, pi))
    target = total - cost  # 最多超时单数
    # 同分取冻结行号最前的整份名单：按行号升序，能选就选（保持存在最优完成）。
    forced = defaultdict(int)
    forced_over = defaultdict(int)
    dropped = defaultdict(int)
    dropped_over = defaultdict(int)
    gq_res = dict(gq)
    pq_res = dict(pq)
    need_over = target
    selected = []
    for r in sorted(rows, key=lambda x: x['row']):
        if len(selected) == total:
            break
        g, p = r['department'], r['priority']
        if g not in gq or p not in pq:
            continue
        cell = (g, p)
        if gq_res[g] == 0 or pq_res[p] == 0:
            dropped[cell] += 1
            dropped_over[cell] += int(bool(r['overdue']))
            continue
        cap = {k: len(v) - forced[k] - dropped[k] for k, v in cells.items()}
        over = {k: over0[k] - forced_over[k] - dropped_over[k] for k in cells}
        cap[cell] -= 1
        od = int(bool(r['overdue']))
        over[cell] -= od
        gq_try = {**gq_res, g: gq_res[g] - 1}
        pq_try = {**pq_res, p: pq_res[p] - 1}
        rest = total - len(selected) - 1
        f2, c2, _, _ = _solve(gq_try, pq_try, cap, over)
        if f2 == rest and rest - c2 >= need_over - od:
            selected.append(r)
            forced[cell] += 1
            forced_over[cell] += od
            gq_res = gq_try
            pq_res = pq_try
            need_over -= od
        else:
            dropped[cell] += 1
            dropped_over[cell] += od
    return sorted(selected, key=lambda x: x['row'])
