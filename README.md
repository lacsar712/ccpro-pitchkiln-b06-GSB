# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

## 界面

- 首页：**灶台值守看板** — 相位筛选 chips + 相位图例 + 对账行 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 产地筛选 + 对账行 + 卡片时间线，非宽表 CRUD

## 对账口径（四项复算）

看板与来脂批页共四项对账数字，**一律按无筛全量复算**，不随筛选条件变化：

| 对账项 | 位置 | 对齐方式 |
|--------|------|----------|
| 灶台总数 | 看板对账行 | = 无筛全量瓦片数 |
| 各相位图例数 | 看板图例 | = 无筛全量下各相位瓦片数 |
| 来脂批总数 | 来脂批页对账行 | = 无筛全量卡片行数 |
| 未收灶值守数 | 看板对账行 | = 无筛全量瓦片中带进行中值守的灶数 |

规则：

- 相位筛（看板）与产地筛（来脂批）只改变展示的瓦片 / 卡片子集；四项对账与图例计数始终按无筛全量复算，对账行已注明口径。
- 对账数字与瓦片 / 卡片同源：同一趟查询得到的内存列表既渲染瓦片（卡片）又累计对账，不另起第二趟聚合查询，杜绝看板与列表各算出现差 1。
- HTMX 局部网格 `floor/grid/` 与整页 `/` 共用同一上下文函数 `_board_context()`，两种入口的瓦片集合必然一致。
- 模板不硬编码任何对账数字，全部来自视图上下文。
- 种子数据保证四项对账与各相位图例计数均非零（五个相位全覆盖，来脂批含重复产地以便演示产地筛）。

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
