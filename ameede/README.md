# Ameede（Vector CNC-Laser）采集器

对应 V1.5 **任务二 / 锁定合同 Task 1**：从 [Ameede Vector CNC-Laser](https://www.ameede.com/amee/vector-cnc-laser/) 采集激光相关项目公开字段，并**落盘高清图**。

本 PR **不包含** Instructables / FreeLaserFile / Robotime / Creative Fabrica。

## 字段映射（V1.5 → camelCase）

| V1.5 说明书 | CSV/Excel 字段 | 说明 |
| --- | --- | --- |
| 采集入口URL | `sourcePageUrl` | 分类列表页；分页保留实际 `/page/N/` URL |
| 项目详情页 URL | `detailUrl` | 项目详情；不含广告外链 |
| 项目名称 | `title` | 详情页完整标题（含项目编号） |
| 高清大图 URL | `coverUrl` | 详情主图直链 |
| （合同增补）本地图片路径 | `coverUrlRelative` | 如 `data/ameede/images/E0025665.webp` |

附加列（便于验收与区分入口，非 V1.5 强制四字段）：

- `projectCode`：从标题/URL 解析的编号（如 `E0025665`）
- `downloadUrl`：详情页上的 `/download/...` 入口（**不当作源站记录**，仅标注）
- `categories` / `tags`：详情页公开分类与标签
- `entryType`：`detail`（入库） / 跳过侧见 `ameede_skipped.csv`
- `crawlStatus` / `skipReason` / `seq`

### 入口区分规则

| 类型 | 判定 | 是否写入主表 |
| --- | --- | --- |
| 详情页 | `ameede.com` 项目 slug 页（`type-post`） | 是 |
| 下载入口 | `/download/{code}/`（WPDM `wpdmpro`） | 否（记入 skipped） |
| 广告/外链 | 非 ameede 域名或广告路径 | 否（记入 skipped） |

## 安装

在仓库内进入本目录：

```bash
cd ameede
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

样例（约 5 条，证明 列表→详情→图片落盘）：

```bash
cd ameede
python crawl_ameede.py --sample 5 -v
```

小批量（前 2 页，约 40 条）：

```bash
python crawl_ameede.py --max-pages 2 --fresh
```

全量（约 1433 页 / ~25k+，可断点续跑）：

```bash
python crawl_ameede.py --min-delay 1.0 --max-delay 2.0
# 中断后再次执行同一命令即可 resume
python crawl_ameede.py --min-delay 1.0 --max-delay 2.0
```

常用参数：

- `--max-items N` / `--max-pages N` / `--start-page N`
- `--fresh` 清空 checkpoint 与输出后重跑
- `--no-resume` 不读 checkpoint
- `--no-images` 仅调试解析（交付勿用）

## 输出路径

均相对于 `ameede/` 目录：

| 路径 | 内容 |
| --- | --- |
| `data/ameede/ameede.csv` | 主数据 CSV |
| `data/ameede/ameede.xlsx` | 主数据 Excel |
| `data/ameede/ameede_skipped.csv` | 下载入口/广告等非源站 |
| `data/ameede/images/` | 高清图文件 |
| `data/ameede/checkpoints/state.json` | 断点（页码 + 已处理 URL） |
| `data/ameede/run_summary.json` | 最近一次运行摘要 |

样例跑数产物已提交在 `data/ameede/`，用于验收字段与双图片路径。

## 与 V1.5 Ameede 表的已知差异 / 缺口

1. **图片落盘**：V1.5 原文写「仅提取 URL，无需下载」；**本锁定合同要求必须下载**，故增加 `coverUrlRelative`，并保留 `coverUrl`。
2. **Craftseek 代码交付字段表**：Project store 中未见单独 Craftseek 字段 PDF；英文字段按既有交付风格（如 3axis 的 `sourcePageUrl` / `detailUrl` / `coverUrl`）与上表映射命名。
3. **广告**：列表页当前以分类 `article.post` 为主；站外广告链按域名/路径规则过滤。若站点后续插入站内推广卡片，需按抽检结果补规则。
4. **下载入口**：仅记录 `downloadUrl`，不把 `/download/` 页当作项目源站行。
5. **全量条数**：分类分页可见约 **1433** 页 × ~20 条；以实际可列表量为准，目标量级 ~25k。
