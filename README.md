# 轨迹停留分析 API（Trajectory Stay Analysis）

基于 **FastAPI** 的轨迹停留点（Stay Point）分析服务。客户端提交带时间戳、经纬度的**有序轨迹**，
以及距离阈值、最短停留时长阈值；服务按 **Haversine** 距离和时间间隔**流式合并**连续点，
识别停留区段的**进入 / 离开**时间与**跨采样间断**，返回每个停留的**时长、中心点、原始点下标范围**，
并附带 **GeoJSON** 结果与**总里程、停留次数**等统计。

此外提供**跨时段热点归并**接口 `POST /api/v1/hotspots`：在停留检测结果之上，以停留中心间
Haversine 距离连边构建无向图，连通分量中访问次数达标者判为**热点**、其余停留标记为**噪声**；
热点中心按停留时长加权，编号按最早访问时间稳定分配。

## 功能特性

- 流式停留检测：逐点扫描合并，经典 stay-point 算法（锚点距离判定），时间复杂度 O(n)
- 跨采样间断处理：相邻点间隔超过 `max_gap_s` 强制切分段落，间断时间不计入停留时长
- 每个停留返回：进入 / 离开时间、停留时长、中心点（簇内均值）、原始点下标闭区间、点数
- 跨时段热点归并：停留中心无向图连通分量（支持传递扩展、结果与遍历顺序无关），
  按 `min_visits` 区分热点与噪声；热点返回访问次数、累计停留时长、时长加权中心、成员 stay_id
- 热点编号按分量内最早访问时间升序稳定分配
- 整体统计：总里程（Haversine 累计）、轨迹总时长、停留次数、停留总时长（热点接口另含热点数 / 噪声数）
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

### 3. `POST /api/v1/hotspots` — 跨时段热点归并

**两步处理**：先沿用 `/api/v1/stays` 的全部停留检测参数识别停留；再以每段停留的中心点为节点，
两中心 Haversine 距离 ≤ `hotspot_radius_m` 时连一条无向边，对连通分量做判定：

- 分量内停留段数（访问次数）≥ `min_visits` → **热点**；
- 其余分量中的停留 → **噪声**（响应中 `hotspot_id` 为 `null`）。

**请求体**：包含 `/api/v1/stays` 的所有字段（`points`、`distance_threshold_m`、
`min_stay_duration_s`、`max_gap_s`），另加：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `hotspot_radius_m` | float | 是 | — | 热点归并半径（米）：两个停留中心距离不超过该值则连边，`> 0` |
| `min_visits` | int | 是 | — | 最少访问次数：连通分量内停留段数达到该值才判为热点，`≥ 1` |

**请求示例**

```bash
curl -X POST http://localhost:8000/api/v1/hotspots \
  -H 'Content-Type: application/json' \
  -d @examples/hotspots.json | jq .
```

`examples/hotspots.json` 描述 9 次访问：在 A → B → C 三个相邻约 300 m 的位置各访问一次
（A 与 C 相距约 600 m，不直接相邻，经 B 传递归并）→ 约 11 km 外的 D 访问一次（噪声）
→ 再访 A、B、C（并入同一分量）→ 约 5.6 km 外的 E 访问两次。半径 500 m、`min_visits=2` 时，
A-B-C 链（6 次访问）为热点 1，E-E（2 次访问）为热点 2，D 为噪声。

**响应示例**（摘要）

```json
{
  "stays": [
    {"stay_id": 1, "center_lat": 39.9,    "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 2, "center_lat": 39.9027, "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 3, "center_lat": 39.9054, "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 4, "center_lat": 40.0,    "duration_s": 240.0, "hotspot_id": null},
    {"stay_id": 5, "center_lat": 39.9,    "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 6, "center_lat": 39.9027, "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 7, "center_lat": 39.9054, "duration_s": 240.0, "hotspot_id": 1},
    {"stay_id": 8, "center_lat": 39.95,   "duration_s": 240.0, "hotspot_id": 2},
    {"stay_id": 9, "center_lat": 39.95,   "duration_s": 240.0, "hotspot_id": 2}
  ],
  "hotspots": [
    {
      "hotspot_id": 1,
      "center_lat": 39.9027,
      "center_lon": 116.4,
      "visit_count": 6,
      "total_duration_s": 1440.0,
      "first_visit_time": "2025-09-21T00:00:00Z",
      "last_visit_time": "2025-09-21T03:06:00Z",
      "stay_ids": [1, 2, 3, 5, 6, 7]
    },
    {
      "hotspot_id": 2,
      "center_lat": 39.95,
      "center_lon": 116.4,
      "visit_count": 2,
      "total_duration_s": 480.0,
      "first_visit_time": "2025-09-21T03:32:20Z",
      "last_visit_time": "2025-09-21T04:06:40Z",
      "stay_ids": [8, 9]
    }
  ],
  "stats": {
    "point_count": 45,
    "stay_count": 9,
    "total_stay_duration_s": 2160.0,
    "hotspot_count": 2,
    "noise_count": 1
  }
}
```

**响应字段**

| 字段 | 说明 |
| --- | --- |
| `stays[]` | 与 `/api/v1/stays` 相同的停留字段，另加 `hotspot_id`（所属热点编号，从 1 开始；`null` 表示噪声） |
| `hotspots[].hotspot_id` | 热点编号，按分量内最早访问时间升序稳定分配 |
| `hotspots[].visit_count` | 访问次数（连通分量内的停留段数） |
| `hotspots[].total_duration_s` | 累计停留时长（秒），成员停留时长之和 |
| `hotspots[].center_lat / center_lon` | 热点中心：成员停留中心按各自停留时长加权平均 |
| `hotspots[].first_visit_time / last_visit_time` | 最早进入 / 最晚离开时间 |
| `hotspots[].stay_ids[]` | 成员停留的 `stay_id` 列表（按时间升序） |
| `stats.hotspot_count / noise_count` | 热点数量 / 噪声停留数量 |

### 4. `GET /health` — 健康检查

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

### 跨时段热点归并（`app/hotspot.py`）

1. 以检测出的每段停留中心为图节点，两节点中心 Haversine 距离
   ≤ `hotspot_radius_m` 时连**无向边**（O(k²)，k 为停留段数）；
2. 用**并查集**合并所有相邻节点对并求连通分量。连边天然具有传递性，因此
   **“甲邻乙、乙邻丙、甲不邻丙”** 时三者仍归入同一分量；连通分量是无向图的
   固有划分，**与节点 / 边的遍历顺序无关**（分量按最小成员下标收集，仅影响展示顺序）；
3. 分量内停留段数 ≥ `min_visits` 判为热点，其余分量的全部停留标记为噪声；
4. **稳定编号**：候选分量按“分量内最早访问时间升序、同分按最早成员下标”排序，
   从 1 开始连续编号，故编号不受输入遍历顺序影响；
5. 热点中心 = 成员停留中心以各自停留时长为权重的加权平均
   （全部时长为 0 时退化为等权均值）；累计停留时长为成员时长之和。

> 注意：输入轨迹须按时间升序排列；`start_index` / `end_index` 对应请求数组中的原始下标。
> 热点归并发生在停留层面，不会修改原始轨迹点与停留检测结果。

## 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 应用与路由
│   ├── models.py        # Pydantic 请求 / 响应模型
│   ├── staypoint.py     # Haversine 与停留检测核心算法（纯函数）
│   └── hotspot.py       # 跨时段热点归并核心算法（并查集连通分量，纯函数）
├── tests/
│   ├── test_api.py        # API 集成测试（TestClient）
│   ├── test_staypoint.py  # 停留检测算法单元测试
│   └── test_hotspot.py    # 热点归并算法单元测试
├── examples/
│   ├── request.json     # 停留分析示例请求体
│   └── hotspots.json    # 热点归并示例请求体
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

共 23 个用例，覆盖 Haversine 距离、停留合并、最短时长过滤、采样间断切分、
GeoJSON 结构、热点传递链归并、遍历顺序无关性、访问次数门槛与噪声标记、
时长加权中心、稳定编号、参数校验（422）等。
