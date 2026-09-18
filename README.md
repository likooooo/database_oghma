# database_oghma

Download Oghma materials from [oghma-nano.com](https://www.oghma-nano.com) and export normative harness YAML under `materials/` only.

Films（如 `og/film/oled_ito_al`）手写于 [`../models/`](../models/)。

## 入口

- **重导出**：脚本内固定 zip URL → `.cache/` 下载；下载失败 → 硬退出。不依赖 `SIMULATION_BASELINE_TOOLS_DIR`。
- **日常**：已提交的 `materials/**/*.yml`（ingest 只读 YAML）。

```bash
python3 update_current_database.py          # 用 cache 或按需下载
python3 update_current_database.py --force  # 强制重新下载
```

## 转换

- 主路径：分表 n / α→k（α 点上换算；禁止 wavelength union/merge）。
- `DATA.name`：`og/` + 路径最短唯一后缀。
- tags：`og` + `benchmark`（见 [`docs/tag_taxonomy.md`](../../docs/tag_taxonomy.md)）。
- **不**写 `films/`、**不**读 `filmstack_templates.json`。
- 落盘路径仍按上游目录树（`materials/{类目}/…/{叶}.yml`）。
- 门禁：`fail>0` → exit 1。
