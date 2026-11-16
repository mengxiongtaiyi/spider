# FreeLaserFile 采集器

对应 V1.5 **任务四 / Craftseek Task 3**：从 [FreeLaserFile](https://freelaserfile.com/) 采集激光相关设计公开字段，并**落盘高清图**。

本 PR **仅含 FreeLaserFile**，不含 Ameede / Instructables / Robotime / Creative Fabrica。

站点为 Next.js，本采集器优先走公开 JSON API（`/api/designs`、`/api/designs/{slug}`），不依赖浏览器渲染。

## 字段映射（V1.5 → camelCase）

| V1.5 说明书 | CSV/Excel 字段 | 说明 |
| --- | --- | --- |
| 采集入口URL | `sourcePageUrl` | 列表页；分页为 `/?page=N`（或分类页） |
| 项目详情页 URL | `detailUrl` | `https://freelaserfile.com/design/{slug}` |
| 项目名称 | `title` | 详情完整标题 |
| 高清大图 URL | `coverUrl` + `imageUrls` | 正文主图/设计图；**排除**详情 `similar` 侧栏推荐 |
| （合同增补）本地路径 | `coverUrlRelative` + `imagePaths` | 如 `data/freelaserfile/images/...` |
| 项目分类及标签 | `categories` + `tags` | Category 名 + 标签（如 Art & Decor、laser cut、cnc） |

附加列：`description`、`slug`、`designId`、`entryType`、`crawlStatus`、`skipReason`、`seq`。

### 广告 / 侧栏

详情 API 的 `similar`（页面 MOST DOWNLOADED / 侧栏推荐）**不当作本条图片来源**，也不拆成独立源站记录（全站列表 API 已覆盖这些设计）。

## 安装

```bash
cd freelaserfile
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

样例：

```bash
cd freelaserfile
python crawl_freelaserfile.py --sample 5 -v
```

全量（断点续跑；API 当前约 3k 条，高于说明书估 1.8k）：

```bash
python crawl_freelaserfile.py --min-delay 0.5 --max-delay 1.2
python crawl_freelaserfile.py --min-delay 0.5 --max-delay 1.2   # resume
```

可选：`--category art-decor`、`--tag laser-cut`、`--max-pages N`。

## 输出路径

相对 `freelaserfile/`：

| 路径 | 内容 |
| --- | --- |
| `data/freelaserfile/freelaserfile.csv` | 主表 |
| `data/freelaserfile/freelaserfile.xlsx` | Excel |
| `data/freelaserfile/images/` | 本地图片 |
| `data/freelaserfile/checkpoints/state.json` | 断点 |
| `data/freelaserfile/run_summary.json` | 摘要 |

## 与 V1.5 FreeLaserFile 表的已知差异 / 缺口

1. **图片落盘**：说明书「仅提取 URL」；合同要求下载 → `coverUrlRelative` / `imagePaths`。
2. **高清图源**：CDN `originals/` 常 404；实际下载 **watermarked → medium**（与站点主图一致）。
3. **条数**：说明书估 ~1800；API `pagination.total` 现约 **3065**（全站激光/CNC 向设计）。默认采全量 published；可用 `--tag laser-cut` 收窄。
4. **分类标签**：拆成 `categories` + `tags` 两列，合起来对应说明书「项目分类及标签」。
