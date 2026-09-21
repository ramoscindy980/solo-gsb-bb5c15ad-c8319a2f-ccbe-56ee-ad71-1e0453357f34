# 轨迹停留分析 API（Trajectory Stay Analysis）

基于 **FastAPI** 的轨迹停留点（Stay Point）分析服务。客户端提交带时间戳、经纬度的**有序轨迹**，
以及距离阈值、最短停留时长阈值；服务按 **Haversine** 距离和时间间隔**流式合并**连续点，
识别停留区段的**进入 / 离开**时间与**跨采样间断**，返回每个停留的**时长、中心点、原始点下标范围**，
并附带 **GeoJSON** 结果与**总里程、停留次数**等统计。

## 功能特性

- 流式停留检测：逐点扫描合并，经典 stay-point 算法（锚点距离判定），时间复杂度 O(n)
- 跨采样间断处理：相邻点间隔超过 `max_gap_s` 强制切分段落，间断时间不计入停留时长
- 每个停留返回：进入 / 离开时间、停留时长、中心点（簇内均值）、原始点下标闭区间、点数
- 整体统计：总里程（Haversine 累计）、轨迹总时长、停留次数、停留总时长
- GeoJSON 输出：轨迹 LineString + 停留点 Point 的 FeatureCollection，可直接上图（GeoJSON.io / Kepler 等）
- 时间戳兼容 ISO 8601 字符串与 Unix 秒（epoch）；参数校验失败返回 422

## 快速开始

### 方式一：Docker Compose（一键启动）

```bash
docker compose up --build
```

### 方式二：Dockerfile

```bash
docker build -t staypoint-api .
docker run -d -p 8000:8000 --name staypoint-api staypoint-api
```

### 方式三：本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后：

- 服务地址：http://localhost:8000
- 交互式文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：`GET /health`

## 接口说明

### 1. `POST /api/v1/stays` — 停留分析（JSON）

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `points` | array | 是 | — | 按时间升序的轨迹点，至少 1 个 |
| `points[].lat` | float | 是 | — | 纬度 `[-90, 90]` |
| `points[].lon` | float | 是 | — | 经度 `[-180, 180]` |
| `points[].timestamp` | string/number | 是 | — | ISO 8601（如 `2026-09-21T08:00:00Z`）或 Unix 秒；无时区按 UTC |
| `distance_threshold_m` | float | 否 | `100` | 距离阈值（米）：连续点距簇锚点不超过该值则合并 |
| `min_stay_duration_s` | float | 否 | `300` | 最短停留时长（秒）：簇时间跨度达到该值才判定为停留 |
| `max_gap_s` | float | 否 | `4 × min_stay_duration_s` | 最大采样间隔（秒）：超过则视为采样间断并强制切分 |

**请求示例**

```bash
curl -X POST http://localhost:8000/api/v1/stays \
  -H 'Content-Type: application/json' \
  -d @examples/request.json
```

`examples/request.json` 描述的场景：在 A 处停留 4 分钟 → 移动约 1.1 km 到 B 处短暂停留
2 分钟（低于阈值，不算停留）→ 设备离线约 26 分钟（采样间断）→ 在 C 处停留 3 分钟。

**响应示例**（实际输出）

```json
{
  "stays": [
    {
      "stay_id": 1,
      "center_lat": 39.9,
      "center_lon": 116.4,
      "start_time": "2026-09-21T08:16:40Z",
      "end_time": "2026-09-21T08:20:40Z",
      "duration_s": 240.0,
      "start_index": 0,
      "end_index": 4,
      "point_count": 5
    },
    {
      "stay_id": 2,
      "center_lat": 39.910005,
      "center_lon": 116.400005,
      "start_time": "2026-09-21T08:50:00Z",
      "end_time": "2026-09-21T08:53:00Z",
      "duration_s": 180.0,
      "start_index": 8,
      "end_index": 11,
      "point_count": 4
    }
  ],
  "stats": {
    "point_count": 12,
    "total_distance_m": 1148.002,
    "total_duration_s": 2180.0,
    "stay_count": 2,
    "total_stay_duration_s": 420.0
  }
}
```

**响应字段**

| 字段 | 说明 |
| --- | --- |
| `stays[].stay_id` | 停留序号，从 1 开始 |
| `stays[].center_lat / center_lon` | 停留中心点（区段内点坐标均值） |
| `stays[].start_time / end_time` | 进入 / 离开停留区的时间（区段首末点时间） |
| `stays[].duration_s` | 停留时长（秒） |
| `stays[].start_index / end_index` | 区段在请求 `points` 中的下标闭区间 |
| `stays[].point_count` | 区段包含的轨迹点数 |
| `stats.total_distance_m` | 总里程：相邻点 Haversine 距离之和（米） |
| `stats.total_duration_s` | 轨迹总时长：首末点时间差（秒） |
| `stats.stay_count` | 停留次数 |
| `stats.total_stay_duration_s` | 停留总时长（秒） |

### 2. `POST /api/v1/stays/geojson` — GeoJSON 结果

请求体与上相同。返回 `application/geo+json` 的 `FeatureCollection`：

- 第 1 个 Feature：整条轨迹的 `LineString`，`properties` 含里程、点数等统计
- 后续 Feature：每个停留一个 `Point`（中心点），`properties` 含停留时长、时间、下标范围

```bash
curl -X POST http://localhost:8000/api/v1/stays/geojson \
  -H 'Content-Type: application/json' \
  -d @examples/request.json | jq .
```

### 3. `GET /health` — 健康检查

```json
{"status": "ok"}
```

## 算法说明

经典 stay-point 检测（Li et al., 2008 风格）的流式实现，见 `app/staypoint.py`：

1. 按时间顺序逐点扫描，维护当前候选簇及其**锚点**（簇首点）；
2. 新点与锚点的 **Haversine** 距离 ≤ `distance_threshold_m` → 并入当前簇；
3. 距离超阈值 → 关闭当前簇：簇时间跨度 ≥ `min_stay_duration_s` 判定为停留，
   然后以新点为锚点开启新簇；
4. **采样间断**：相邻点间隔 > `max_gap_s` 时，无论距离多近都强制关闭当前簇——
   避免设备离线期间被错误计为停留（间断时间不计入任何停留时长）；
5. 停留中心取簇内所有点的经纬度均值；总里程为相邻点 Haversine 距离之和
   （地球平均半径 6371008.8 m）。

> 注意：输入轨迹须按时间升序排列；`start_index` / `end_index` 对应请求数组中的原始下标。

## 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 应用与路由
│   ├── models.py        # Pydantic 请求 / 响应模型
│   └── staypoint.py     # Haversine 与停留检测核心算法（纯函数）
├── tests/
│   ├── test_api.py        # API 集成测试（TestClient）
│   └── test_staypoint.py  # 算法单元测试
├── examples/
│   └── request.json     # 示例请求体
├── Dockerfile
├── docker-compose.yml
├── requirements.txt       # 运行依赖
└── requirements-dev.txt   # 测试依赖
```

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

共 11 个用例，覆盖 Haversine 距离、停留合并、最短时长过滤、采样间断切分、
GeoJSON 结构、参数校验（422）等。
