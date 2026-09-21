"""FastAPI 应用入口。"""
from __future__ import annotations

from typing import List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .hotspot import build_hotspots
from .models import (
    Hotspot as HotspotModel,
    HotspotRequest,
    HotspotResponse,
    HotspotStay,
    HotspotStats,
    Stats,
    Stay,
    StayRequest,
    StayResponse,
)
from .staypoint import Point, StaySegment, detect_stays, total_distance_m

app = FastAPI(
    title="轨迹停留分析 API",
    version="1.1.0",
    description=(
        "提交带时间戳的有序轨迹，按 Haversine 距离与最短停留时长阈值流式合并连续点，"
        "识别停留区段（含跨采样间断切分），返回停留时长、中心点、原始点范围，"
        "并提供 GeoJSON 结果与总里程、停留次数统计；"
        "另支持跨时段热点归并：以停留中心连边求连通分量，"
        "按访问次数门槛识别热点、其余停留标记为噪声。"
    ),
)


def _to_points(req: StayRequest) -> List[Point]:
    return [(p.lat, p.lon, p.timestamp) for p in req.points]


def _detect_segments(req: StayRequest) -> List[StaySegment]:
    max_gap_s = (
        req.max_gap_s if req.max_gap_s is not None else 4 * req.min_stay_duration_s
    )
    return detect_stays(
        _to_points(req),
        distance_threshold_m=req.distance_threshold_m,
        min_stay_duration_s=req.min_stay_duration_s,
        max_gap_s=max_gap_s,
    )


def _build_stays(segments: List[StaySegment]) -> List[Stay]:
    return [
        Stay(
            stay_id=i + 1,
            center_lat=seg.center_lat,
            center_lon=seg.center_lon,
            start_time=seg.start_time,
            end_time=seg.end_time,
            duration_s=seg.duration_s,
            start_index=seg.start_index,
            end_index=seg.end_index,
            point_count=seg.point_count,
        )
        for i, seg in enumerate(segments)
    ]


def _build_stats(points: List[Point], stays: List[Stay]) -> Stats:
    total_duration_s = (
        (points[-1][2] - points[0][2]).total_seconds() if len(points) > 1 else 0.0
    )
    return Stats(
        point_count=len(points),
        total_distance_m=round(total_distance_m(points), 3),
        total_duration_s=total_duration_s,
        stay_count=len(stays),
        total_stay_duration_s=sum(s.duration_s for s in stays),
    )


def _analyze(req: StayRequest) -> StayResponse:
    points = _to_points(req)
    segments = _detect_segments(req)
    stays = _build_stays(segments)
    return StayResponse(stays=stays, stats=_build_stats(points, stays))


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/stays",
    response_model=StayResponse,
    summary="停留分析（JSON 结果 + 统计）",
)
def analyze_stays(req: StayRequest) -> StayResponse:
    return _analyze(req)


@app.post(
    "/api/v1/hotspots",
    response_model=HotspotResponse,
    summary="跨时段热点归并（停留 -> 连通分量 -> 热点 / 噪声）",
)
def analyze_hotspots(req: HotspotRequest) -> HotspotResponse:
    points = _to_points(req)
    segments = _detect_segments(req)
    stays = _build_stays(segments)

    labels, hotspots = build_hotspots(
        segments,
        hotspot_radius_m=req.hotspot_radius_m,
        min_visits=req.min_visits,
    )

    hotspot_stays = [
        HotspotStay(**s.model_dump(), hotspot_id=labels[i])
        for i, s in enumerate(stays)
    ]
    hotspot_models: List[HotspotModel] = [
        HotspotModel(
            hotspot_id=h.hotspot_id,
            center_lat=h.center_lat,
            center_lon=h.center_lon,
            visit_count=h.visit_count,
            total_duration_s=h.total_duration_s,
            first_visit_time=h.first_visit_time,
            last_visit_time=h.last_visit_time,
            stay_ids=[idx + 1 for idx in h.member_indices],
        )
        for h in hotspots
    ]

    base_stats = _build_stats(points, stays)
    stats = HotspotStats(
        **base_stats.model_dump(),
        hotspot_count=len(hotspot_models),
        noise_count=sum(1 for label in labels if label is None),
    )
    return HotspotResponse(stays=hotspot_stays, hotspots=hotspot_models, stats=stats)


@app.post(
    "/api/v1/stays/geojson",
    summary="停留分析（GeoJSON FeatureCollection）",
)
def analyze_stays_geojson(req: StayRequest) -> JSONResponse:
    result = _analyze(req)

    features: List[dict] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[p.lon, p.lat] for p in req.points],
            },
            "properties": {
                "kind": "trajectory",
                "point_count": result.stats.point_count,
                "total_distance_m": result.stats.total_distance_m,
                "total_duration_s": result.stats.total_duration_s,
            },
        }
    ]
    for s in result.stays:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [s.center_lon, s.center_lat],
                },
                "properties": {
                    "kind": "stay",
                    "stay_id": s.stay_id,
                    "start_time": s.start_time.isoformat(),
                    "end_time": s.end_time.isoformat(),
                    "duration_s": s.duration_s,
                    "start_index": s.start_index,
                    "end_index": s.end_index,
                    "point_count": s.point_count,
                },
            }
        )

    return JSONResponse(
        content={"type": "FeatureCollection", "features": features},
        media_type="application/geo+json",
    )
