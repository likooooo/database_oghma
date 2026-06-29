#!/usr/bin/env python3
"""Download Oghma materials/spectra from oghma-nano.com and export to YAML."""

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
from scipy.interpolate import CubicSpline

MODULE_DIR = Path(__file__).resolve().parent

OGHMA_MATERIALS_URL = "https://www.oghma-nano.com/downloads/updates/materials.zip"
OGHMA_SPECTRA_URL = "https://www.oghma-nano.com/downloads/updates/spectra.zip"

INSTALL_HINTS: dict[str, dict[str, str]] = {
    OGHMA_MATERIALS_URL: {"subpath": ".", "src_prefix": ""},
    OGHMA_SPECTRA_URL: {"subpath": ".", "src_prefix": ""},
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
    """Download and merge Oghma materials/spectra zips into source_root."""
    source_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    packages = [
        (OGHMA_MATERIALS_URL, source_root / "materials"),
        (OGHMA_SPECTRA_URL, source_root),
    ]
    for url, dest_dir in packages:
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


def read_tabulated_xy_um(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read two-column CSV; return sorted (wl_um, value)."""
    wl_raw, val, meta = read_oghma_csv(path)
    if meta:
        wl_um = oghma_axis_to_um(wl_raw, meta, axis="y")
    else:
        wl_raw_arr = np.asarray(wl_raw, dtype=float)
        if wl_raw_arr.size == 0:
            wl_um = wl_raw_arr
        elif np.nanmax(wl_raw_arr) < 1e-2:
            wl_um = wl_raw_arr * 1e6
        else:
            wl_um = wl_raw_arr
    val_arr = np.asarray(val, dtype=float)
    order = np.argsort(wl_um)
    return wl_um[order], val_arr[order]


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


def _dedupe_sorted(wl: np.ndarray, vals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if wl.size == 0:
        return wl, vals
    order = np.argsort(wl)
    wl = wl[order]
    vals = vals[order]
    unique_wl: list[float] = []
    unique_vals: list[float] = []
    for w, v in zip(wl, vals):
        if unique_wl and np.isclose(w, unique_wl[-1]):
            unique_vals[-1] = float(v)
        else:
            unique_wl.append(float(w))
            unique_vals.append(float(v))
    return np.asarray(unique_wl, dtype=float), np.asarray(unique_vals, dtype=float)


def cubic_interp_extrap(
    x_new: np.ndarray,
    x_src: np.ndarray,
    y_src: np.ndarray,
) -> np.ndarray:
    if x_src.size == 0:
        return np.zeros_like(x_new, dtype=float)
    if x_src.size == 1:
        return np.full_like(x_new, float(y_src[0]), dtype=float)
    spline = CubicSpline(x_src, y_src, extrapolate=True)
    return np.asarray(spline(x_new), dtype=float)


def merge_n_alpha_to_nk(
    wl_n_um: np.ndarray,
    n_vals: np.ndarray,
    wl_alpha_um: np.ndarray,
    alpha_vals: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Merge n and alpha tables onto the wavelength union with cubic interpolation.

    Returns (wl_um, n, k) on the union grid.
    """
    wl_n_um, n_vals = _dedupe_sorted(np.asarray(wl_n_um, dtype=float), np.asarray(n_vals, dtype=float))
    if wl_alpha_um.size:
        wl_alpha_um, alpha_vals = _dedupe_sorted(
            np.asarray(wl_alpha_um, dtype=float),
            np.asarray(alpha_vals, dtype=float),
        )

    grids = [wl_n_um]
    if wl_alpha_um.size:
        grids.append(wl_alpha_um)
    wl_union = np.unique(np.concatenate(grids))

    n_on_union = cubic_interp_extrap(wl_union, wl_n_um, n_vals)
    if wl_alpha_um.size:
        alpha_on_union = cubic_interp_extrap(wl_union, wl_alpha_um, alpha_vals)
    else:
        alpha_on_union = np.zeros_like(wl_union, dtype=float)

    k_on_union = k_from_alpha_on_wl_um(wl_union, alpha_on_union)
    return wl_union, n_on_union, k_on_union


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


def _format_value(value: float) -> str:
    """Format numeric columns like refractiveindex.info tabulated data."""
    if value == 0.0:
        return "0"
    abs_val = abs(value)
    if abs_val >= 1e-3 and abs_val < 1e4:
        text = f"{value:.6g}"
    else:
        text = f"{value:.6E}".replace("e", "E")
    return text


def _format_coeff(value: float) -> str:
    if value == 0.0:
        return "0"
    return _format_value(value)


def _block_scalar(key: str, text: str) -> str:
    if not text.strip():
        return f"{key}: |\n"
    lines = text.rstrip("\n").split("\n")
    body = "\n".join(f"    {line}" for line in lines)
    return f"{key}: |\n{body}\n"


def _render_data_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["DATA:"]
    for block in blocks:
        btype = block["type"]
        lines.append(f"  - type: {btype}")
        if btype.startswith("formula"):
            lines.append(f"    wavelength_range: {block['wl_min']:.6g} {block['wl_max']:.6g}")
            coeff_text = " ".join(_format_coeff(c) for c in block["coefficients"])
            lines.append(f"    coefficients: {coeff_text}")
        elif btype.startswith("tabulated"):
            lines.append("    data: |")
            for row in block["rows"]:
                if len(row) == 2:
                    w, v = row
                    lines.append(f"        {_format_value(w)} {_format_value(v)}")
                else:
                    w, n, k = row
                    lines.append(
                        f"        {_format_value(w)} {_format_value(n)} {_format_value(k)}"
                    )
    return lines


def write_material_yml(
    path: Path,
    data_blocks: list[dict[str, Any]],
    *,
    references: str = "",
    comments: str = "",
    conditions: str = "",
) -> None:
    """Write YAML with one or more DATA blocks (formula, tabulated nk/k, etc.)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        _block_scalar("REFERENCES", references),
        _block_scalar("COMMENTS", comments),
        _block_scalar("CONDITIONS", conditions),
        *_render_data_blocks(data_blocks),
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


def _tabulated_nk_rows(wl_um: np.ndarray, n_vals: np.ndarray, k_vals: np.ndarray) -> list[tuple]:
    wl_um = np.asarray(wl_um, dtype=float)
    n_vals = np.asarray(n_vals, dtype=float)
    k_vals = np.asarray(k_vals, dtype=float)
    return [(float(w), float(n), float(k)) for w, n, k in zip(wl_um, n_vals, k_vals)]


def _tabulated_spectra_rows(wl_um: np.ndarray, values: np.ndarray) -> list[tuple]:
    wl_um = np.asarray(wl_um, dtype=float)
    values = np.asarray(values, dtype=float)
    return [(float(w), float(v)) for w, v in zip(wl_um, values)]


def tabulated_nk_block(wl_um: np.ndarray, n_vals: np.ndarray, k_vals: np.ndarray) -> dict[str, Any]:
    return {"type": "tabulated nk", "rows": _tabulated_nk_rows(wl_um, n_vals, k_vals)}


def tabulated_spectra_block(wl_um: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    return {"type": "tabulated spectra", "rows": _tabulated_spectra_rows(wl_um, values)}


def write_tabulated_spectra_yml(
    path: Path,
    wl_um: np.ndarray,
    values: np.ndarray,
    *,
    references: str = "",
    comments: str = "",
    conditions: str = "",
) -> None:
    write_material_yml(
        path,
        [tabulated_spectra_block(wl_um, values)],
        references=references,
        comments=comments,
        conditions=conditions,
    )


def write_tabulated_nk_yml(
    path: Path,
    wl_um: np.ndarray,
    n_vals: np.ndarray,
    k_vals: np.ndarray,
    *,
    references: str = "",
    comments: str = "",
    conditions: str = "",
) -> None:
    write_material_yml(
        path,
        [tabulated_nk_block(wl_um, n_vals, k_vals)],
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


def _changelog_from_data_json(src_dir: Path) -> str:
    data_json = src_dir / "data.json"
    if not data_json.is_file():
        return ""
    try:
        meta = json.loads(data_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("changelog") or "").strip()


def _remove_stale_leaf_dir(stale_dir: Path) -> None:
    if stale_dir.is_dir():
        shutil.rmtree(stale_dir)


def _cleanup_stale_generic_1e(materials_out: Path) -> None:
    generic_dir = materials_out / "generic"
    if not generic_dir.is_dir():
        return
    for child in generic_dir.iterdir():
        if child.is_dir() and child.name.startswith("1e"):
            shutil.rmtree(child)


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


def is_leaf_spectrum(dir_path: Path) -> bool:
    if not dir_path.is_dir() or _has_subdirectories(dir_path):
        return False
    return _data_json_item_type(dir_path) == "spectra"


def export_material(src_dir: Path, out_yml: Path, log_dir: Path) -> bool:
    rel = src_dir.name
    log = MaterialLogger(rel, log_dir)
    try:
        wl_n, n_vals, wl_alpha, alpha_vals = read_material_nk_table(src_dir)
        if wl_n.size == 0:
            log.warn("n.csv has no data points")
            log.flush()
            return False

        if wl_alpha.size and wl_alpha.size != wl_n.size:
            log.warn(
                f"n grid ({wl_n.size} pts) and alpha grid ({wl_alpha.size} pts) differ; "
                "using wavelength union with cubic interpolation"
            )
            n_min, n_max = float(wl_n[0]), float(wl_n[-1])
            a_min, a_max = float(wl_alpha[0]), float(wl_alpha[-1])
            if a_min > n_max or a_max < n_min:
                log.warn(
                    f"alpha wavelength range [{a_min:.6g}, {a_max:.6g}] um "
                    f"does not overlap n range [{n_min:.6g}, {n_max:.6g}] um"
                )

        wl_um, n_out, k_out = merge_n_alpha_to_nk(wl_n, n_vals, wl_alpha, alpha_vals)
        validate_tabulated_nk(wl_um, n_out, k_out, log)

        out_yml.parent.mkdir(parents=True, exist_ok=True)
        write_tabulated_nk_yml(
            out_yml,
            wl_um,
            n_out,
            k_out,
            comments=_format_comments(log.warnings),
        )
        log.flush()
        return True
    except Exception as exc:
        log.warn(f"export failed: {exc}")
        log.flush()
        return False


def export_spectrum(src_dir: Path, out_yml: Path) -> bool:
    try:
        csv_path = src_dir / "spectra.csv"
        if not csv_path.is_file():
            return False
        wl_um, val = read_tabulated_xy_um(csv_path)
        if wl_um.size == 0:
            return False
        comments = _changelog_from_data_json(src_dir)
        out_yml.parent.mkdir(parents=True, exist_ok=True)
        write_tabulated_spectra_yml(out_yml, wl_um, val, comments=comments)
        return True
    except Exception:
        return False


def _sanitized_rel_path(rel: Path) -> Path:
    return Path(*[sanitize_path_segment(part) for part in rel.parts])


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


def find_leaf_spectra(spectra_root: Path) -> list[Path]:
    return sorted(p for p in spectra_root.rglob("*") if p.is_dir() and is_leaf_spectrum(p))


def export_database(source: Path, output: Path, log_dir: Path, limit: int | None) -> int:
    materials_out = output / "materials"
    spectra_out = output / "spectra"

    src_materials = source / "materials"
    src_spectra = source / "spectra"
    if not src_materials.is_dir():
        print(f"error: materials directory not found: {src_materials}", file=sys.stderr)
        return 1

    _cleanup_stale_generic_1e(materials_out)

    leaves = find_leaf_materials(src_materials)
    if limit is not None:
        leaves = leaves[: max(limit, 0)]

    ok = 0
    fail = 0
    for leaf in leaves:
        rel = leaf.relative_to(src_materials)
        safe_rel = _sanitized_rel_path(rel)
        out_yml = materials_out / safe_rel.with_suffix(".yml")
        stale_dir = materials_out / safe_rel
        if export_material(leaf, out_yml, log_dir):
            _remove_stale_leaf_dir(stale_dir)
            ok += 1
        else:
            fail += 1

    spectra_ok = 0
    spectra_fail = 0
    if src_spectra.is_dir():
        for leaf in find_leaf_spectra(src_spectra):
            rel = leaf.relative_to(src_spectra)
            safe_rel = _sanitized_rel_path(rel)
            out_yml = spectra_out / safe_rel.with_suffix(".yml")
            stale_dir = spectra_out / safe_rel
            if export_spectrum(leaf, out_yml):
                _remove_stale_leaf_dir(stale_dir)
                spectra_ok += 1
            else:
                spectra_fail += 1

    print(
        f"og: materials ok={ok} fail={fail}, "
        f"spectra ok={spectra_ok} fail={spectra_fail}"
    )
    return 1 if fail or spectra_fail else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Oghma materials/spectra and export to YAML."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=MODULE_DIR,
        help="Output root for this submodule",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory to cache downloaded zips (default: <output>/.cache)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download zips even when remote checksum matches cache",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory for per-material warning logs (default: <output>/logs)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Export at most N materials (for testing)",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    cache_dir = (args.cache_dir or output / ".cache").resolve()
    log_dir = (args.log_dir or output / "logs").resolve()
    source = cache_dir / "source"

    try:
        sync_oghma_source(source, cache_dir, args.force)
    except Exception as exc:
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1

    return export_database(source, output, log_dir, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
