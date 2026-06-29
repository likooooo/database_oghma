# og

Download Oghma materials/spectra from [oghma-nano.com](https://www.oghma-nano.com) and export to refractiveindex.info-compatible YAML.

## Source

Remote packages (downloaded automatically):

- `materials.zip` — leaf dirs with `n.csv`, optional `alpha.csv`, `data.json` (`item_type: material`)
- `spectra.zip` — leaf dirs with `spectra.csv`, `data.json` (`item_type: spectra`)

Skipped material categories: `refractive_index_info`, `chemnitz`, `blends`, `gas`.

## Output

| Path | Contents |
|------|----------|
| `materials/{category}/.../{name}.yml` | Tabulated n,k (µm), cubic interpolation when n/α grids differ |
| `spectra/{category}/.../{name}.yml` | Tabulated spectra |
| `logs/` | Per-material warning logs |
| `.cache/` | Downloaded zips and extracted CSV source (gitignored) |

## Usage

```bash
pip install -r requirements.txt

# Full update (download + export)
python update_current_database.py

# Quick test
python update_current_database.py --limit 3

# Force re-download
python update_current_database.py --force

# Via parent
python update_all.py --only oghma
```
