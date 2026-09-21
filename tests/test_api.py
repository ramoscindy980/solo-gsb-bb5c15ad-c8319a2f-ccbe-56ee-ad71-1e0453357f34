"""API 集成测试。"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 与 test_staypoint.SCENARIO 相同的场景，混用 epoch 与 ISO 8601 时间戳
POINTS = [
    {"lat": 39.900000, "lon": 116.400000, "timestamp": "2026-09-21T08:16:40Z"},
    {"lat": 39.900020, "lon": 116.400010, "timestamp": "2026-09-21T08:17:40Z"},
    {"lat": 39.899980, "lon": 116.399990, "timestamp": "2026-09-21T08:18:40Z"},
    {"lat": 39.900010, "lon": 116.400020, "timestamp": "2026-09-21T08:19:40Z"},
    {"lat": 39.899990, "lon": 116.399980, "timestamp": "2026-09-21T08:20:40Z"},
    {"lat": 39.910000, "lon": 116.400000, "timestamp": "2026-09-21T08:21:40Z"},
    {"lat": 39.910020, "lon": 116.400010, "timestamp": "2026-09-21T08:22:40Z"},
    {"lat": 39.909980, "lon": 116.399990, "timestamp": "2026-09-21T08:23:40Z"},
    {"lat": 39.910010, "lon": 116.400020, "timestamp": "2026-09-21T08:50:00Z"},
    {"lat": 39.910000, "lon": 116.400000, "timestamp": "2026-09-21T08:51:00Z"},
    {"lat": 39.910020, "lon": 116.400010, "timestamp": "2026-09-21T08:52:00Z"},
    {"lat": 39.909990, "lon": 116.399990, "timestamp": "2026-09-21T08:53:00Z"},
]

PAYLOAD = {
    "points": POINTS,
    "distance_threshold_m": 100,
    "min_stay_duration_s": 180,
    "max_gap_s": 600,
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_analyze_stays():
    resp = client.post("/api/v1/stays", json=PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["stays"]) == 2
    first, second = body["stays"]

    assert first["stay_id"] == 1
    assert (first["start_index"], first["end_index"]) == (0, 4)
    assert first["duration_s"] == 240.0
    assert first["point_count"] == 5
    assert abs(first["center_lat"] - 39.9) < 1e-4
    assert first["start_time"] == "2026-09-21T08:16:40Z"
    assert first["end_time"] == "2026-09-21T08:20:40Z"

    assert second["stay_id"] == 2
    assert (second["start_index"], second["end_index"]) == (8, 11)
    assert second["duration_s"] == 180.0

    stats = body["stats"]
    assert stats["point_count"] == 12
    assert stats["stay_count"] == 2
    assert stats["total_stay_duration_s"] == 420.0
    assert 1100.0 < stats["total_distance_m"] < 1200.0
    assert stats["total_duration_s"] == 2180.0


def test_analyze_stays_epoch_timestamps_and_default_gap():
    # epoch 时间戳；不传 max_gap_s 时默认 4 * min_stay_duration_s = 720s，
    # 1580s 的间断仍会被切分
    payload = {
        "points": [
            {"lat": p["lat"], "lon": p["lon"], "timestamp": 1758432000 + i * 60}
            for i, p in enumerate(POINTS[:5])
        ]
        + [
            {"lat": p["lat"], "lon": p["lon"], "timestamp": 1758432000 + 300 + i * 60}
            for i, p in enumerate(POINTS[5:8])
        ]
        + [
            {"lat": p["lat"], "lon": p["lon"], "timestamp": 1758432000 + 2000 + i * 60}
            for i, p in enumerate(POINTS[8:])
        ],
        "distance_threshold_m": 100,
        "min_stay_duration_s": 180,
    }
    resp = client.post("/api/v1/stays", json=payload)
    assert resp.status_code == 200
    stays = resp.json()["stays"]
    assert len(stays) == 2
    assert stays[0]["duration_s"] == 240.0
    assert stays[1]["duration_s"] == 180.0


def test_geojson():
    resp = client.post("/api/v1/stays/geojson", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/geo+json")
    body = resp.json()

    assert body["type"] == "FeatureCollection"
    # 1 条轨迹 LineString + 2 个停留 Point
    assert len(body["features"]) == 3

    line = body["features"][0]
    assert line["geometry"]["type"] == "LineString"
    assert len(line["geometry"]["coordinates"]) == 12
    # GeoJSON 坐标顺序为 [lon, lat]
    assert line["geometry"]["coordinates"][0] == [116.4, 39.9]
    assert line["properties"]["kind"] == "trajectory"
    assert line["properties"]["total_distance_m"] > 0

    stay_features = body["features"][1:]
    assert all(f["geometry"]["type"] == "Point" for f in stay_features)
    assert stay_features[0]["properties"]["stay_id"] == 1
    assert stay_features[0]["properties"]["duration_s"] == 240.0
    assert stay_features[1]["properties"]["stay_id"] == 2


def test_validation_errors():
    # 纬度越界
    bad = {"points": [{"lat": 91, "lon": 0, "timestamp": 0}]}
    assert client.post("/api/v1/stays", json=bad).status_code == 422
    # 空轨迹
    assert client.post("/api/v1/stays", json={"points": []}).status_code == 422
    # 非法阈值
    bad2 = {"points": POINTS, "distance_threshold_m": -1}
    assert client.post("/api/v1/stays", json=bad2).status_code == 422


# ---------------------------------------------------------------------------
# /api/v1/hotspots
# ---------------------------------------------------------------------------

# 一次访问 = 5 个点、间隔 60s，时长 240s；相邻访问间隔 1820s（> max_gap_s=600
# 强制切分，< 4*min_stay_duration 时默认 gap 也不影响本场景）。
# A（39.9000）→ B（39.9027，约 300m）→ C（39.9054，再约 300m）→ D（40.0000，
# 孤立噪声，约 11km）→ A → B → C → E（39.9500，约 5.6km 外）→ E。
# 半径 500m 时：A-B-C 经传递连边形成同一热点（A 与 C 相距约 600m，不直接相邻）；
# E-E 形成第二个热点；D 为噪声。
HOTSPOT_BASE_TS = 1758412800


def _visits() -> list:
    lat_by_place = {
        "A": 39.9000,
        "B": 39.9027,
        "C": 39.9054,
        "D": 40.0000,
        "E": 39.9500,
    }
    order = ["A", "B", "C", "D", "A", "B", "C", "E", "E"]
    pts = []
    t = HOTSPOT_BASE_TS
    for place in order:
        for k in range(5):
            pts.append(
                {
                    "lat": lat_by_place[place] + (1e-6 if k % 2 else -1e-6),
                    "lon": 116.4000 + (1e-6 if k % 2 else -1e-6),
                    "timestamp": t + k * 60,
                }
            )
        t += 1820  # 相邻访问首点间隔 1820s（末点至下次首点间隔 1580s > max_gap_s）
    return pts


def _z(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


HOTSPOT_PAYLOAD = {
    "points": _visits(),
    "distance_threshold_m": 100,
    "min_stay_duration_s": 180,
    "max_gap_s": 600,
    "hotspot_radius_m": 500,
    "min_visits": 2,
}


def test_hotspots_transitive_chain_noise_and_stable_ids():
    resp = client.post("/api/v1/hotspots", json=HOTSPOT_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()

    # 9 次访问 -> 9 段停留；前 7 次为 A/B/C/D/A/B/C，最后 2 次为 E/E
    stays = body["stays"]
    assert len(stays) == 9
    assert [s["stay_id"] for s in stays] == list(range(1, 10))
    assert all(s["duration_s"] == 240.0 for s in stays)

    # A-B-C（stay 1,2,3）传递归并；D（stay 4）噪声；A-B-C（stay 5,6,7）并入同链；
    # E-E（stay 8,9）独立热点
    assert [s["hotspot_id"] for s in stays] == [1, 1, 1, None, 1, 1, 1, 2, 2]

    hotspots = body["hotspots"]
    assert len(hotspots) == 2

    h1, h2 = hotspots
    assert h1["hotspot_id"] == 1
    assert h1["visit_count"] == 6
    assert h1["total_duration_s"] == 1440.0
    assert h1["stay_ids"] == [1, 2, 3, 5, 6, 7]
    assert h1["first_visit_time"] == _z(HOTSPOT_BASE_TS)
    # stay 7 开始于第 7 次访问：6 * 1820 秒后，末点 +240s
    assert h1["last_visit_time"] == _z(HOTSPOT_BASE_TS + 6 * 1820 + 240)
    # 加权中心 = 各停留时长相同 -> 算术平均
    assert abs(h1["center_lat"] - (39.9 + 39.9027 + 39.9054) / 3) < 1e-3
    assert abs(h1["center_lon"] - 116.4) < 1e-4

    assert h2["hotspot_id"] == 2
    assert h2["visit_count"] == 2
    assert h2["total_duration_s"] == 480.0
    assert h2["stay_ids"] == [8, 9]
    # stay 8 开始于第 8 次访问：7 * 1820 秒后
    assert h2["first_visit_time"] == _z(HOTSPOT_BASE_TS + 7 * 1820)
    assert h2["last_visit_time"] == _z(HOTSPOT_BASE_TS + 8 * 1820 + 240)
    assert abs(h2["center_lat"] - 39.95) < 1e-4

    stats = body["stats"]
    assert stats["point_count"] == 45
    assert stats["stay_count"] == 9
    assert stats["hotspot_count"] == 2
    assert stats["noise_count"] == 1
    assert stats["total_stay_duration_s"] == 9 * 240.0


def test_hotspots_no_qualifying_visits_marks_all_noise():
    payload = {**HOTSPOT_PAYLOAD, "min_visits": 4}
    # E-E 只有 2 次；A-B-C 链 6 次仍达标
    body = client.post("/api/v1/hotspots", json=payload).json()
    assert [h["visit_count"] for h in body["hotspots"]] == [6]
    assert body["stats"]["hotspot_count"] == 1
    assert body["stats"]["noise_count"] == 3

    payload = {**HOTSPOT_PAYLOAD, "min_visits": 7}
    body = client.post("/api/v1/hotspots", json=payload).json()
    assert body["hotspots"] == []
    assert all(s["hotspot_id"] is None for s in body["stays"])
    assert body["stats"]["hotspot_count"] == 0
    assert body["stats"]["noise_count"] == 9


def test_hotspots_no_stays_detected():
    # 每个点相距足够远，无法形成停留
    payload = {
        "points": [
            {"lat": 39.9 + i * 0.01, "lon": 116.4, "timestamp": 1758412800 + i * 60}
            for i in range(4)
        ],
        "hotspot_radius_m": 500,
        "min_visits": 2,
    }
    resp = client.post("/api/v1/hotspots", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stays"] == []
    assert body["hotspots"] == []
    assert body["stats"]["stay_count"] == 0
    assert body["stats"]["hotspot_count"] == 0
    assert body["stats"]["noise_count"] == 0


def test_hotspots_validation_errors():
    # radius 必须为正
    bad = {**HOTSPOT_PAYLOAD, "hotspot_radius_m": 0}
    assert client.post("/api/v1/hotspots", json=bad).status_code == 422
    # min_visits 至少为 1
    bad = {**HOTSPOT_PAYLOAD, "min_visits": 0}
    assert client.post("/api/v1/hotspots", json=bad).status_code == 422
    # 缺少必填的归并参数
    bad = {"points": POINTS, "distance_threshold_m": 100}
    assert client.post("/api/v1/hotspots", json=bad).status_code == 422
    # 沿用停留参数的校验
    bad = {"points": [], "hotspot_radius_m": 100, "min_visits": 2}
    assert client.post("/api/v1/hotspots", json=bad).status_code == 422
