"""Pydantic 请求 / 响应模型。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TrackPoint(BaseModel):
    """轨迹点：经纬度 + 时间戳。

    timestamp 支持三种形式：
    - ISO 8601 字符串，如 "2026-09-21T08:00:00Z"、"2026-09-21T08:00:00+08:00"
    - Unix 时间戳（秒，int/float），如 1758432000
    - 不带时区的 ISO 字符串按 UTC 处理
    """

    lat: float = Field(..., ge=-90, le=90, description="纬度，[-90, 90]")
    lon: float = Field(..., ge=-180, le=180, description="经度，[-180, 180]")
    timestamp: datetime = Field(..., description="ISO 8601 字符串或 Unix 秒时间戳")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v):
        if isinstance(v, bool):
            raise ValueError("非法时间戳")
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, str):
            s = v.strip()
            try:
                return datetime.fromtimestamp(float(s), tz=timezone.utc)
            except ValueError:
                pass
            if s.endswith(("Z", "z")):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return v


class StayRequest(BaseModel):
    """停留分析请求。"""

    points: List[TrackPoint] = Field(
        ..., min_length=1, description="按时间升序排列的轨迹点"
    )
    distance_threshold_m: float = Field(
        100.0, gt=0, description="停留距离阈值（米）：连续点距锚点不超过该值则合并为同一停留"
    )
    min_stay_duration_s: float = Field(
        300.0, ge=0, description="最短停留时长（秒）：区段时间跨度达到该值才判定为停留"
    )
    max_gap_s: Optional[float] = Field(
        None,
        gt=0,
        description="最大采样间隔（秒）：相邻点间隔超过该值视为采样间断，强制切分段落；"
        "为空时默认取 4 * min_stay_duration_s",
    )


class Stay(BaseModel):
    """一个停留区段。"""

    stay_id: int = Field(..., description="停留序号，从 1 开始")
    center_lat: float = Field(..., description="停留中心纬度（区段内点均值）")
    center_lon: float = Field(..., description="停留中心经度（区段内点均值）")
    start_time: datetime = Field(..., description="进入停留区的时间（区段首点时间）")
    end_time: datetime = Field(..., description="离开停留区的时间（区段末点时间）")
    duration_s: float = Field(..., description="停留时长（秒）")
    start_index: int = Field(..., description="区段首点在请求 points 中的下标（含）")
    end_index: int = Field(..., description="区段末点在请求 points 中的下标（含）")
    point_count: int = Field(..., description="区段包含的轨迹点数")


class Stats(BaseModel):
    """整体统计。"""

    point_count: int = Field(..., description="轨迹点总数")
    total_distance_m: float = Field(..., description="总里程（米），相邻点 Haversine 距离之和")
    total_duration_s: float = Field(..., description="轨迹总时长（秒），首末点时间差")
    stay_count: int = Field(..., description="识别出的停留次数")
    total_stay_duration_s: float = Field(..., description="停留总时长（秒）")


class StayResponse(BaseModel):
    stays: List[Stay]
    stats: Stats
