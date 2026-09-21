"""跨时段热点归并核心算法（纯函数，无 Web 依赖）。

在停留检测（``staypoint.detect_stays``）结果之上做第二级聚类：

1. 以每段停留的中心点为图节点，两个中心的 Haversine 距离
   <= ``hotspot_radius_m`` 时连一条**无向边**；
2. 用并查集求连通分量——连边具有传递性，因此支持
   “甲邻乙、乙邻丙、甲不邻丙”的链式扩展；无向图的连通分量是图的
   固有划分，与边的遍历 / 合并顺序无关；
3. 分量内停留段数（即访问次数）>= ``min_visits`` 判为**热点**，
   其余分量中的全部停留标记为**噪声**；
4. 热点编号按分量内最早访问时间升序稳定分配（时间相同按最早成员下标）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .staypoint import StaySegment, haversine_m


@dataclass
class Hotspot:
    """一个热点（访问次数达标的连通分量）。"""

    hotspot_id: int
    member_indices: List[int]
    visit_count: int
    total_duration_s: float
    center_lat: float
    center_lon: float
    first_visit_time: datetime
    last_visit_time: datetime


def connected_components(
    stays: Sequence[StaySegment], radius_m: float
) -> List[List[int]]:
    """按中心距离 <= ``radius_m`` 建无向图，返回连通分量。

    每个分量以停留下标列表表示；分量按其最小成员下标升序返回，
    分量内下标亦升序。使用并查集合并所有满足距离条件的节点对，
    连通分量划分与边的合并顺序无关。
    """
    n = len(stays)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # 始终让较小根成为父节点，保证合并过程可复现
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for i in range(n):
        si = stays[i]
        for j in range(i + 1, n):
            sj = stays[j]
            if (
                haversine_m(
                    si.center_lat, si.center_lon, sj.center_lat, sj.center_lon
                )
                <= radius_m
            ):
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [groups[root] for root in sorted(groups)]


def build_hotspots(
    stays: Sequence[StaySegment],
    hotspot_radius_m: float,
    min_visits: int,
) -> Tuple[List[Optional[int]], List[Hotspot]]:
    """在停留序列上归并热点。

    :param stays: 停留区段序列（通常即 ``detect_stays`` 的结果，按时间升序）
    :param hotspot_radius_m: 热点归并半径（米），中心距离不超过该值则连边
    :param min_visits: 最少访问次数，分量内停留段数达到该值才判为热点
    :returns: ``(labels, hotspots)``——``labels[i]`` 为第 i 段停留所属的
        热点编号（从 1 开始），不属于任何热点时为 ``None``（噪声）；
        ``hotspots`` 按编号升序排列。
    """
    components = connected_components(stays, hotspot_radius_m)

    candidates = [members for members in components if len(members) >= min_visits]
    # 稳定编号：最早访问时间升序，同分按分量内最小下标
    candidates.sort(
        key=lambda members: (
            min(stays[m].start_time for m in members),
            members[0],
        )
    )

    labels: List[Optional[int]] = [None] * len(stays)
    hotspots: List[Hotspot] = []

    for hotspot_id, members in enumerate(candidates, start=1):
        duration_weight = sum(stays[m].duration_s for m in members)
        if duration_weight > 0:
            center_lat = (
                sum(stays[m].center_lat * stays[m].duration_s for m in members)
                / duration_weight
            )
            center_lon = (
                sum(stays[m].center_lon * stays[m].duration_s for m in members)
                / duration_weight
            )
        else:
            # 所有停留时长均为 0（min_stay_duration_s=0 的单点簇）时退化为等权均值
            center_lat = sum(stays[m].center_lat for m in members) / len(members)
            center_lon = sum(stays[m].center_lon for m in members) / len(members)

        first_visit_time = min(stays[m].start_time for m in members)
        last_visit_time = max(stays[m].end_time for m in members)

        for m in members:
            labels[m] = hotspot_id

        hotspots.append(
            Hotspot(
                hotspot_id=hotspot_id,
                member_indices=list(members),
                visit_count=len(members),
                total_duration_s=duration_weight,
                center_lat=center_lat,
                center_lon=center_lon,
                first_visit_time=first_visit_time,
                last_visit_time=last_visit_time,
            )
        )

    return labels, hotspots
