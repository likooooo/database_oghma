#!/usr/bin/env python3
"""Export Oghma materials (n.csv/alpha.csv) and spectra to simulation_database format."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.csv_io import read_material_nk_table, read_tabulated_xy_um
from common.interpolation import merge_n_alpha_to_nk
from common.logging_util import MaterialLogger
from common.name_sanitize import sanitize_path_segment
from common.yml_emit import write_tabulated_nk_yml, write_tabulated_spectra_yml

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


def _validate_nk(wl_um: np.ndarray, n_vals: np.ndarray, k_vals: np.ndarray, log: MaterialLogger) -> None:
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
        _validate_nk(wl_um, n_out, k_out, log)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Oghma materials and spectra.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/home/like/repos/simulation_toykits/simulation_core/assets/database"),
        help="Source database root (contains materials/ and spectra/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output root for this submodule",
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

    source = args.source.resolve()
    output = args.output.resolve()
    log_dir = (args.log_dir or output / "logs").resolve()
    materials_out = output / "materials"
    spectra_out = output / "spectra"

    src_materials = source / "materials"
    src_spectra = source / "spectra"
    if not src_materials.is_dir():
        print(f"error: materials directory not found: {src_materials}", file=sys.stderr)
        return 1

    _cleanup_stale_generic_1e(materials_out)

    leaves = find_leaf_materials(src_materials)
    if args.limit is not None:
        leaves = leaves[: max(args.limit, 0)]

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


if __name__ == "__main__":
    raise SystemExit(main())
