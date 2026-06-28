# og

Export Oghma-native optical materials and spectra from the simulation toolkits database into refractiveindex.info-compatible YAML.

## Source

Reads `materials/` and `spectra/` from an Oghma database root (default: `simulation_toykits/simulation_core/assets/database`).

Leaf materials are directories containing `n.csv`, optional `alpha.csv`, and `data.json` with `"item_type": "material"`. These categories are skipped:

- `refractive_index_info`
- `chemnitz`
- `blends`
- `gas`

## Output

| Path | Contents |
|------|----------|
| `materials/{category}/.../{name}.yml` | Tabulated n,k on a wavelength union (µm), cubic interpolation when n and α grids differ |
| `spectra/{category}/.../{name}.yml` | Tabulated spectra (wavelength µm + intensity) |
| `logs/` | Per-material warning logs |

## Install

From the parent `simulation_database` repo (recommended):

```bash
cd ~/repos/simulation_database
pip install -r requirements.txt
```

Or install only this submodule's dependencies:

```bash
pip install -r og/requirements.txt
```

The export script adds the parent repo root to `sys.path` so it can import `common/` (CSV I/O, interpolation, YAML emit, logging).

## Usage

```bash
# Full export (writes into this submodule directory)
python og/export.py

# Custom source and output
python og/export.py \
  --source /path/to/simulation_core/assets/database \
  --output /path/to/og

# Quick test (first N materials only)
python og/export.py --limit 3

# Via parent update script
python update_all.py --only oghma
```

Exit code is 0 when all exported materials succeed, 1 if any fail.
