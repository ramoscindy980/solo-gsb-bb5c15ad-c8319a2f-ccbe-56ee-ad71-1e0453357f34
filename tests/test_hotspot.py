"""热点归并核心算法单元测试。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.hotspot import build_hotspots, connected_components
from app.staypoint import StaySegment, haversine_m

BASE = datetime(2026, 9, 21, 8, 0, 0, tzinfo=timezone.utc)


def make_segment(
    idx,
    lat,
    lon,
    start_offset,
    duration_s=300.0,
):
    """构造单段停留（下标、中心、时间跨度任意指定）。"""
    start = BASE + timedelta(seconds=start_offset)
    end = start + timedelta(seconds=duration_s)
    return StaySegment(
        start_index=idx,
        end_index=idx,
        start_time=start,
        end_time=end,
        center_lat=lat,
        center_lon=lon,
    )


def offset_lat(lat: float, meters: float) -> float:
    """纬度方向平移 meters 米对应的纬度值。"""
    return lat + meters / 111_320.0


def test_transitive_chain_merges_non_adjacent_centers():
    # 甲-乙-丙 链式相邻，甲丙相距超过半径，但必须归并为同一分量
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000),          # 甲
        make_segment(1, offset_lat(39.9000, 300), 116.4000, 2000),  # 乙（300m）
        make_segment(2, offset_lat(39.9000, 600), 116.4000, 3000),  # 丙（600m）
    ]
    assert haversine_m(seg[0].center_lat, 116.4, seg[2].center_lat, 116.4) > 500

    labels, hotspots = build_hotspots(seg, hotspot_radius_m=500, min_visits=3)
    assert labels == [1, 1, 1]
    assert len(hotspots) == 1
    h = hotspots[0]
    assert h.hotspot_id == 1
    assert h.visit_count == 3
    assert h.member_indices == [0, 1, 2]
    assert h.total_duration_s == 900.0
    assert h.first_visit_time == seg[0].start_time
    assert h.last_visit_time == seg[2].end_time


def test_components_independent_of_traversal_order():
    # 同一场景以不同顺序输入，分量成员集合划分必须一致；
    # 热点编号按最早访问时间稳定分配，因此标签也应一致。
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000),
        make_segment(1, offset_lat(39.9000, 300), 116.4000, 2000),
        make_segment(2, offset_lat(39.9000, 600), 116.4000, 3000),
        make_segment(3, 40.0000, 116.5000, 4000),
        make_segment(4, offset_lat(40.0000, 200), 116.5000, 5000),
    ]
    components_a = connected_components(seg, 500.0)
    components_b = connected_components(list(reversed(seg)), 500.0)

    sets_a = {frozenset(c) for c in components_a}
    sets_b = {
        frozenset(len(seg) - 1 - i for i in c) for c in components_b
    }
    assert sets_a == sets_b

    labels_a, _ = build_hotspots(seg, 500.0, min_visits=2)
    labels_b, _ = build_hotspots(list(reversed(seg)), 500.0, min_visits=2)
    assert labels_a == list(reversed(labels_b))


def test_min_visits_filters_components_as_noise():
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000),
        make_segment(1, offset_lat(39.9000, 100), 116.4000, 2000),
        make_segment(2, 40.0000, 116.5000, 3000),
    ]
    # 半径 200m：前两段一个分量（2 次访问），第三段孤立（1 次访问）
    labels, hotspots = build_hotspots(seg, hotspot_radius_m=200, min_visits=2)
    assert labels == [1, 1, None]
    assert len(hotspots) == 1
    assert hotspots[0].member_indices == [0, 1]

    # 门槛提高到 3：所有停留都成为噪声
    labels2, hotspots2 = build_hotspots(seg, hotspot_radius_m=200, min_visits=3)
    assert labels2 == [None, None, None]
    assert hotspots2 == []

    # min_visits=1 时孤立停留自身也是热点
    labels3, hotspots3 = build_hotspots(seg, hotspot_radius_m=200, min_visits=1)
    assert labels3 == [1, 1, 2]
    assert [h.visit_count for h in hotspots3] == [2, 1]


def test_hotspot_id_ordered_by_first_visit_time():
    # 输入顺序故意打乱：先给出位置靠近但时间更晚的两个停留，
    # 再给出更早的孤立热点；编号仍须按最早访问时间。
    later = [
        make_segment(0, 39.9000, 116.4000, 5000),
        make_segment(1, offset_lat(39.9000, 100), 116.4000, 6000),
    ]
    earlier = [make_segment(2, 40.0000, 116.5000, 1000)]
    seg = later + earlier
    labels, hotspots = build_hotspots(seg, hotspot_radius_m=200, min_visits=1)
    # 更早的孤立分量得编号 1，更晚的双访问分量得编号 2
    assert labels == [2, 2, 1]
    assert [h.hotspot_id for h in hotspots] == [1, 2]
    assert hotspots[0].first_visit_time == earlier[0].start_time
    assert hotspots[1].first_visit_time == later[0].start_time


def test_center_weighted_by_duration():
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000, duration_s=600.0),
        make_segment(1, 39.9010, 116.4010, 2000, duration_s=300.0),
        make_segment(2, 39.9020, 116.4020, 3000, duration_s=100.0),
    ]
    _, hotspots = build_hotspots(seg, hotspot_radius_m=5000, min_visits=1)
    h = hotspots[0]
    assert h.total_duration_s == 1000.0
    assert h.center_lat == pytest.approx(
        (39.9000 * 600 + 39.9010 * 300 + 39.9020 * 100) / 1000
    )
    assert h.center_lon == pytest.approx(
        (116.4000 * 600 + 116.4010 * 300 + 116.4020 * 100) / 1000
    )


def test_zero_duration_falls_back_to_equal_weight():
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000, duration_s=0.0),
        make_segment(1, 39.9020, 116.4020, 2000, duration_s=0.0),
    ]
    _, hotspots = build_hotspots(seg, hotspot_radius_m=5000, min_visits=2)
    h = hotspots[0]
    assert h.total_duration_s == 0.0
    assert h.center_lat == pytest.approx(39.9010)
    assert h.center_lon == pytest.approx(116.4010)


def test_boundary_distance_equals_radius_connected():
    # 两点中心距离恰好（浮点误差范围内）等于半径时必须连边（<= 判定）
    seg = [
        make_segment(0, 39.9000, 116.4000, 1000),
        make_segment(1, 39.9100, 116.4000, 2000),
    ]
    exact = haversine_m(39.9000, 116.4, 39.9100, 116.4)
    labels, hotspots = build_hotspots(seg, hotspot_radius_m=exact, min_visits=2)
    assert labels == [1, 1]
    assert len(hotspots) == 1


def test_empty_input():
    labels, hotspots = build_hotspots([], hotspot_radius_m=100, min_visits=2)
    assert labels == []
    assert hotspots == []
