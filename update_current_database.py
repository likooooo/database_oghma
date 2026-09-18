#!/usr/bin/env python3
"""Download Oghma materials from oghma-nano.com and export normative YAML.

重导出需能下载 zip；日常交付物为已提交的 ``materials/**/*.yml``。
不依赖 ``SIMULATION_BASELINE_TOOLS_DIR``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

MODULE_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = MODULE_DIR.parents[1]
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from framework.emit_material_yaml import isotropic_from_separate_n_k, write_material_yaml
from framework.material_tags import build_oghma_tags

OGHMA_MATERIALS_URL = "https://www.oghma-nano.com/downloads/updates/materials.zip"
_CACHE_DIR = MODULE_DIR / ".cache"

INSTALL_HINTS: dict[str, dict[str, str]] = {
    OGHMA_MATERIALS_URL: {"subpath": ".", "src_prefix": ""},
}


def _manifest_url(zip_url: str) -> str:
    return zip_url if zip_url.endswith(".json") else f"{zip_url}.json"


def fetch_manifest(zip_url: str) -> dict[str, Any]:
    manifest_url = _manifest_url(zip_url)
    with urllib.request.urlopen(manifest_url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _install_spec(manifest: dict[str, Any], zip_url: str) -> tuple[str, str]:
    targets = manifest.get("targets") or {}
    seg = targets.get("segment0") or {}
    target = str(seg.get("target", "")).strip()
    src = str(seg.get("src", "/")).strip()
    subpath = "."
    if target:
        t = target.replace("\\", "/").strip("/")
        if t.startswith("materials/"):
            subpath = t[len("materials/") :]
        elif t == "materials":
            subpath = "."
        elif t:
            subpath = t
    src_prefix = src.lstrip("/")
    if not target and zip_url in INSTALL_HINTS:
        hint = INSTALL_HINTS[zip_url]
        subpath = hint["subpath"]
        src_prefix = hint["src_prefix"]
    return subpath, src_prefix


def md5_file(path: str | Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_update(zip_url: str, manifest_path: str) -> bool:
    """Return True when remote checksum differs from local manifest."""
    remote = fetch_manifest(zip_url)
    remote_checksum = str(remote.get("remote_checksum", "")).lower()
    if not remote_checksum:
        return True
    mp = Path(manifest_path)
    if not mp.is_file():
        return True
    local = json.loads(mp.read_text(encoding="utf-8"))
    local_checksum = str(local.get("local_checksum", local.get("remote_checksum", ""))).lower()
    return remote_checksum != local_checksum


def download_url(zip_url: str, dest_zip: str, manifest_path: str) -> dict[str, Any]:
    """Download zip and write local manifest snapshot."""
    dest = Path(dest_zip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = fetch_manifest(zip_url)
    with urllib.request.urlopen(zip_url, timeout=600) as resp:
        dest.write_bytes(resp.read())
    local_checksum = md5_file(dest)
    remote_checksum = str(manifest.get("remote_checksum", local_checksum))
    out = {
        "name": manifest.get("name", Path(dest).stem),
        "remote_path": zip_url,
        "remote_checksum": remote_checksum,
        "local_checksum": local_checksum,
        "remote_time": manifest.get("remote_time", -1),
        "remote_size": manifest.get("remote_size", dest.stat().st_size),
        "installed": "false",
        "targets": manifest.get("targets", {}),
    }
    mp = Path(manifest_path)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(out, indent="\t"), encoding="utf-8")
    return out


def merge_install_zip(zip_path: str, dest_dir: str, zip_url: str) -> None:
    """Extract zip into dest_dir incrementally; never delete dest_dir."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = str(Path(zip_path).with_suffix(".json"))
    manifest = {}
    if Path(manifest_path).is_file():
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    elif zip_url:
        manifest = fetch_manifest(zip_url)
    subpath, src_prefix = _install_spec(manifest, zip_url)
    target_root = dest if subpath in (".", "") else dest / subpath
    target_root.mkdir(parents=True, exist_ok=True)
    prefix = src_prefix.replace("\\", "/").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            rel = name
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix) :]
            elif prefix:
                alt = re.sub(
                    r"refractiveindex\.info-database-[0-9a-f]+/",
                    "refractiveindex.info-database/",
                    prefix,
                )
                if alt != prefix and name.startswith(alt):
                    rel = name[len(alt) :]
                else:
                    continue
            rel = rel.lstrip("/")
            if not rel:
                continue
            out_path = target_root / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

    if Path(manifest_path).is_file():
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        data["installed"] = "true"
        data["local_checksum"] = data.get("local_checksum") or md5_file(zip_path)
        Path(manifest_path).write_text(json.dumps(data, indent="\t"), encoding="utf-8")


def sync_oghma_source(source_root: Path, cache_dir: Path, force: bool) -> Path:
    """Download Oghma materials zip into source_root."""
    source_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    url = OGHMA_MATERIALS_URL
    dest_dir = source_root / "materials"
    zip_name = url.rsplit("/", 1)[-1]
    zip_path = cache_dir / zip_name
    manifest_path = cache_dir / f"{Path(zip_name).stem}.json"
    if force or check_update(url, str(manifest_path)) or not zip_path.is_file():
        print(f"og: downloading {url}")
        download_url(url, str(zip_path), str(manifest_path))
    else:
        print(f"og: using cached {zip_path}")
    merge_install_zip(str(zip_path), str(dest_dir), url)

    return source_root


class MaterialLogger:
    def __init__(self, name: str, log_dir: Path | None = None) -> None:
        self.name = name
        self.warnings: list[str] = []
        self.log_dir = log_dir

    def warn(self, message: str) -> None:
        line = f"[{self.name}] {message}"
        self.warnings.append(message)
        print(line, file=sys.stderr)

    def flush(self) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.name.replace("/", "_")
        log_path = self.log_dir / f"{safe_name}.log"
        if not self.warnings:
            if log_path.is_file():
                log_path.unlink()
            return
        log_path.write_text("\n".join(self.warnings) + "\n", encoding="utf-8")


def k_from_alpha_on_wl_um(wl_um: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Oghma convention: k = alpha * lambda / (4*pi), with lambda in metres."""
    wl_um_arr = np.asarray(wl_um, dtype=float)
    alpha_arr = np.asarray(alpha, dtype=float)
    return alpha_arr * wl_um_arr * 1e-6 / (4.0 * np.pi)


def read_oghma_csv(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    meta: dict[str, Any] = {}
    if lines and lines[0].startswith("#"):
        match = re.search(r"\{(.+)\}", lines[0])
        if match:
            raw = "{" + match.group(1) + "}"
            raw = re.sub(r":\s*nan\b", ": null", raw, flags=re.IGNORECASE)
            raw = re.sub(r":\s*-?inf\b", ": null", raw, flags=re.IGNORECASE)
            meta = json.loads(raw)
    xs: list[float] = []
    ys: list[float] = []
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [float(x) for x in line.replace(",", " ").split()]
        if len(parts) < 2:
            continue
        xs.append(parts[0])
        ys.append(parts[1])
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), meta


def oghma_axis_to_um(
    values: np.ndarray,
    meta: dict[str, Any],
    *,
    axis: str = "y",
    heatmap: bool = False,
) -> np.ndarray:
    if heatmap:
        return np.asarray(values, dtype=float) * 1e6
    mul_key = "y_mul" if axis == "y" else "x_mul"
    units_key = "y_units" if axis == "y" else "x_units"
    fallback_mul = meta.get("y_mul" if axis == "x" else "x_mul", 1.0)
    fallback_units = meta.get("y_units" if axis == "x" else "x_units", "m")
    mul = float(meta.get(mul_key, fallback_mul))
    units = str(meta.get(units_key, fallback_units))
    pos = values * mul if mul != 1.0 else values
    if units == "nm":
        return np.asarray(pos, dtype=float) * 1e-3
    if units == "um":
        return np.asarray(pos, dtype=float)
    return np.asarray(pos, dtype=float) * 1e6


def read_material_nk_table(
    root: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Read n.csv / alpha.csv from a material directory (sorted axes in um)."""
    n_x, n_y, n_meta = read_oghma_csv(root / "n.csv")
    wl_um = oghma_axis_to_um(n_x, n_meta, axis="y")
    n_vals = np.asarray(n_y, dtype=float)

    alpha_path = root / "alpha.csv"
    if alpha_path.is_file():
        a_x, a_y, a_meta = read_oghma_csv(alpha_path)
        wl_alpha_um = oghma_axis_to_um(a_x, a_meta, axis="y")
        alpha_vals = np.asarray(a_y, dtype=float)
    else:
        wl_alpha_um = np.array([], dtype=float)
        alpha_vals = np.array([], dtype=float)

    n_order = np.argsort(wl_um)
    wl_um = wl_um[n_order]
    n_vals = n_vals[n_order]
    if wl_alpha_um.size:
        alpha_order = np.argsort(wl_alpha_um)
        wl_alpha_um = wl_alpha_um[alpha_order]
        alpha_vals = alpha_vals[alpha_order]
    return wl_um, n_vals, wl_alpha_um, alpha_vals


def validate_tabulated_nk(
    wl_um: np.ndarray,
    n_vals: np.ndarray,
    k_vals: np.ndarray,
    log: MaterialLogger,
) -> None:
    if wl_um.size == 0:
        log.warn("empty wavelength grid")
        return
    if not np.all(np.diff(wl_um) > 0):
        log.warn("wavelength grid is not strictly increasing after dedupe")
    if np.any(~np.isfinite(n_vals)):
        log.warn("non-finite n values present")
    if np.any(~np.isfinite(k_vals)):
        log.warn("non-finite k values present")
    if np.any(n_vals < 0):
        log.warn("negative n values present")
    if np.any(k_vals < 0):
        log.warn("negative k values present")


_FALLBACK = "entry"


def safe_entry_name(name: str) -> str:
    """Return a token-safe name: whitespace, parens, and illegal chars become ``_``."""
    slug = re.sub(r"\s+", "_", name.strip())
    slug = slug.replace("(", "_").replace(")", "_")
    slug = re.sub(r"[^\w.\-]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or _FALLBACK


def sanitize_path_segment(segment: str) -> str:
    """Sanitize one path component (directory name or yml stem without suffix)."""
    return safe_entry_name(segment)


def shortest_unique_suffixes(paths: list[tuple[str, ...]]) -> dict[tuple[str, ...], tuple[str, ...]]:
    """For each path, shortest right suffix unique among all paths (by suffix equality)."""
    out: dict[tuple[str, ...], tuple[str, ...]] = {}
    for path in paths:
        chosen: tuple[str, ...] | None = None
        for k in range(1, len(path) + 1):
            suffix = path[-k:]
            if sum(1 for other in paths if len(other) >= k and other[-k:] == suffix) == 1:
                chosen = suffix
                break
        out[path] = chosen if chosen is not None else path
    return out


def tags_for_rel_parts(parts: tuple[str, ...]) -> list[str]:
    """og + closed category / substance + state."""
    return build_oghma_tags(parts)


def material_name_from_suffix(suffix: tuple[str, ...]) -> str:
    if not suffix:
        raise ValueError("name suffix must be non-empty")
    return "og/" + "/".join(suffix)


def write_material_nk_separate(
    path: Path,
    wl_n: np.ndarray,
    n_vals: np.ndarray,
    wl_k: np.ndarray | None = None,
    k_vals: np.ndarray | None = None,
    *,
    name: str,
    tags: list[str],
    references: str = "",
    comments: str = "",
    conditions: str = "",
) -> None:
    write_material_yaml(
        path,
        tags=list(tags),
        data=isotropic_from_separate_n_k(
            [float(w) for w in np.asarray(wl_n, dtype=float)],
            [float(v) for v in np.asarray(n_vals, dtype=float)],
            None if wl_k is None else [float(w) for w in np.asarray(wl_k, dtype=float)],
            None if k_vals is None else [float(v) for v in np.asarray(k_vals, dtype=float)],
        ),
        name=name,
        references=references,
        comments=comments,
        conditions=conditions,
    )


SKIP_MATERIAL_PREFIXES = ("refractive_index_info", "chemnitz", "blends", "gas")


def _should_skip_material(rel_parts: tuple[str, ...]) -> bool:
    if rel_parts and rel_parts[0] in SKIP_MATERIAL_PREFIXES:
        return True
    if len(rel_parts) >= 2 and rel_parts[0] == "generic" and rel_parts[-1].startswith("1e"):
        return True
    return False


def _format_comments(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return "Export warnings:\n" + "\n".join(warnings)


def _remove_stale_leaf_dir(stale_dir: Path) -> None:
    if stale_dir.is_dir():
        shutil.rmtree(stale_dir)


def _data_json_item_type(dir_path: Path) -> str | None:
    data_json = dir_path / "data.json"
    if not data_json.is_file():
        return None
    try:
        meta = json.loads(data_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict):
        return None
    item_type = meta.get("item_type")
    return str(item_type) if item_type is not None else None


def _has_subdirectories(dir_path: Path) -> bool:
    return any(p.is_dir() for p in dir_path.iterdir())


def is_leaf_material(dir_path: Path) -> bool:
    if not dir_path.is_dir() or _has_subdirectories(dir_path):
        return False
    if not (dir_path / "n.csv").is_file():
        return False
    return _data_json_item_type(dir_path) == "material"


def export_material(
    src_dir: Path,
    out_yml: Path,
    log_dir: Path,
    *,
    name: str,
    tags: list[str],
) -> bool:
    rel = src_dir.name
    log = MaterialLogger(rel, log_dir)
    try:
        wl_n, n_vals, wl_alpha, alpha_vals = read_material_nk_table(src_dir)
        if wl_n.size == 0:
            log.warn("n.csv has no data points")
            log.flush()
            return False

        validate_tabulated_nk(wl_n, n_vals, np.zeros_like(n_vals), log)

        wl_k = None
        k_vals = None
        if wl_alpha.size:
            if wl_alpha.size != wl_n.size or not np.allclose(wl_alpha, wl_n):
                log.warn(
                    f"n grid ({wl_n.size} pts) and alpha grid ({wl_alpha.size} pts) differ; "
                    "keeping separate λ grids (no merge/resample); α→k on alpha points only"
                )
            wl_k = wl_alpha
            k_vals = k_from_alpha_on_wl_um(wl_alpha, alpha_vals)
            validate_tabulated_nk(wl_k, np.ones_like(k_vals), k_vals, log)

        out_yml.parent.mkdir(parents=True, exist_ok=True)
        write_material_nk_separate(
            out_yml,
            wl_n,
            n_vals,
            wl_k,
            k_vals,
            name=name,
            tags=tags,
            comments=_format_comments(log.warnings),
        )
        log.flush()
        return True
    except Exception as exc:
        log.warn(f"export failed: {exc}")
        log.flush()
        return False


def _sanitized_rel_path(rel: Path) -> Path:
    return Path(*[sanitize_path_segment(part) for part in rel.parts])


def _yml_path(materials_out: Path, safe_rel: Path) -> Path:
    """Append ``.yml`` without Path.with_suffix (leaf names may contain dots, e.g. ``1.0``)."""
    return materials_out.joinpath(*safe_rel.parts[:-1], safe_rel.name + ".yml")


def find_leaf_materials(materials_root: Path) -> list[Path]:
    leaves: list[Path] = []
    for path in sorted(materials_root.rglob("*")):
        if not path.is_dir():
            continue
        rel_parts = path.relative_to(materials_root).parts
        if _should_skip_material(rel_parts):
            continue
        if is_leaf_material(path):
            leaves.append(path)
    return leaves


def export_database(source: Path, log_dir: Path, limit: int | None) -> int:
    materials_out = MODULE_DIR / "materials"

    src_materials = source / "materials"
    if not src_materials.is_dir():
        print(f"error: materials directory not found: {src_materials}", file=sys.stderr)
        return 1

    if materials_out.is_dir():
        shutil.rmtree(materials_out)
    materials_out.mkdir(parents=True, exist_ok=True)

    leaves = find_leaf_materials(src_materials)
    if limit is not None:
        leaves = leaves[: max(limit, 0)]

    jobs: list[tuple[Path, Path, tuple[str, ...]]] = []
    for leaf in leaves:
        rel = leaf.relative_to(src_materials)
        safe_rel = _sanitized_rel_path(rel)
        parts = tuple(safe_rel.parts)
        jobs.append((leaf, _yml_path(materials_out, safe_rel), parts))

    out_paths = [out for _, out, _ in jobs]
    if len(out_paths) != len(set(out_paths)):
        print("error: duplicate output YAML paths after sanitize", file=sys.stderr)
        return 1

    suffixes = shortest_unique_suffixes([parts for _, _, parts in jobs])
    names = [material_name_from_suffix(suffixes[parts]) for _, _, parts in jobs]
    if len(names) != len(set(names)):
        print("error: duplicate DATA.name after shortest-unique suffix", file=sys.stderr)
        return 1

    written = fail = 0
    for leaf, out_yml, parts in jobs:
        stale_dir = materials_out.joinpath(*parts)
        if export_material(
            leaf,
            out_yml,
            log_dir,
            name=material_name_from_suffix(suffixes[parts]),
            tags=tags_for_rel_parts(parts),
        ):
            _remove_stale_leaf_dir(stale_dir)
            written += 1
        else:
            fail += 1

    print(f"og: export done written={written} skip=0 fail={fail}")
    if fail:
        return 1

    from framework.material_tags import build_benchmark_source_tags, retag_yaml_tree

    tags = build_benchmark_source_tags("og")
    n_retag = retag_yaml_tree(materials_out, tags=tags)
    print(f"og: retagged materials={n_retag}")
    print("og: films live under benchmark/models (hand YAML); this source writes materials only")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Oghma materials and export normative YAML."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download zips even when remote checksum matches cache",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Export at most N materials (for testing)",
    )
    args = parser.parse_args()

    cache_dir = _CACHE_DIR
    log_dir = MODULE_DIR / "logs"
    source = cache_dir / "source"

    try:
        sync_oghma_source(source, cache_dir, args.force)
    except Exception as exc:
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1

    return export_database(source, log_dir, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
