# QuantumMicroscopy

PyQt6 GUI for a confocal microscope: galvanometer XY scanning, Thorlabs piezo Z-axis, NI-DAQmx APD photon counting, and live camera preview.

> **Branch:** development happens on `Confocal-Only`. Do not merge from `master` (which contains other modalities like AWG / PicoHarp / FLIM).

---

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh                  # macOS / Linux
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"       # Windows

# Install Python deps (creates .venv automatically)
uv sync

# Windows only — adds pythonnet for the Thorlabs piezo
uv sync --extra windows
```

**Hardware drivers are separate from pip** — see [`driver.md`](./driver.md) for NI-DAQmx and Thorlabs Kinesis installation per OS.

## Running

```bash
uv run python gui/confocal_gui.py
```

## OS Support at a Glance

| | Windows | Linux | macOS |
|---|:---:|:---:|:---:|
| Full experiment (scan + Z + APD) | ✅ | ⚠️ no Z | ❌ |
| Offline data analysis (MAT browser, fitting) | ✅ | ✅ | ✅ |

See [`driver.md`](./driver.md) for the per-instrument breakdown.

## Project Layout

```
gui/                       PyQt6 windows, widgets, browsers
experiments/               QThread workers (Confocal, Tracker, APD, TaskHandler)
instruments/               Hardware adapters (ThorlabsPiezo)
exp_config/                YAML hardware config + persisted GUI state
fitters.py                 Curve-fitting framework
akl_image_processing.py    Stripe-pattern detection
file_utils.py              YAML / MAT / CSV I/O
```

For architecture details and developer notes see [`CLAUDE.md`](./CLAUDE.md).
