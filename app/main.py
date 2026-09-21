"""FastAPI 应用入口。"""
from __future__ import annotations

from typing import List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .models import Stay, StayRequest, StayResponse, Stats
from .staypoint import Point, detect_stays, total_distance_m

app = FastAPI(
    title="轨迹停留分析 API",
    version="1.0.0",
    description=(
        "提交带时间戳的有序轨迹，按 Haversine 距离与最短停留时长阈值流式合并连续点，"
        "识别停留区段（含跨采样间断切分），返回停留时长、中心点、原始点范围，"
        "并提供 GeoJSON 结果与总里程、停留次数统计。"
    ),
)


def _to_points(req: StayRequest) -> List[Point]:
    return [(p.lat, p.lon, p.timestamp) for p in req.points]


def _analyze(req: StayRequest) -> StayResponse:
    points = _to_points(req)
    max_gap_s = (
        req.max_gap_s if req.max_gap_s is not None else 4 * req.min_stay_duration_s
    )
    segments = detect_stays(
        points,
        distance_threshold_m=req.distance_threshold_m,
        min_stay_duration_s=req.min_stay_duration_s,
        max_gap_s=max_gap_s,
    )
    stays = [
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
    total_duration_s = (
        (points[-1][2] - points[0][2]).total_seconds() if len(points) > 1 else 0.0
    )
    stats = Stats(
        point_count=len(points),
        total_distance_m=round(total_distance_m(points), 3),
        total_duration_s=total_duration_s,
        stay_count=len(stays),
        total_stay_duration_s=sum(s.duration_s for s in stays),
    )
    return StayResponse(stays=stays, stats=stats)


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
