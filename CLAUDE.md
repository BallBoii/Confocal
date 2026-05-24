# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
# Install uv (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install uv (Windows — PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Create .venv and install all dependencies
uv sync

# Windows only — adds pythonnet for Thorlabs Kinesis piezo control
uv sync --extra windows
```

After `uv sync`, the project is fully set up. Use any editor — to run, just execute `gui/confocal_gui.py` from the project root (see "Running the Application" below).

### Hardware Drivers (installed separately — not via pip)

| Hardware | Platform | Driver |
|---|---|---|
| NI DAQmx (galvos, APD counter) | Windows | [NI-DAQmx](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) |
| NI DAQmx | Linux | NI Linux Device Drivers (rpm/deb) |
| NI DAQmx | macOS | Not supported by NI |
| Thorlabs Kinesis (piezo) | Windows | [Kinesis Software](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=Motion_Control) + `uv sync --extra windows` |
| Thorlabs Kinesis | macOS / Linux | Not supported |

> **macOS note**: The app imports `ThorlabsPiezo` unconditionally at startup (`gui/confocal_gui.py:15`), which fails on macOS/Linux because `clr` (pythonnet) is unavailable. The piezo itself also uses hardcoded `C:\Program Files\Thorlabs\Kinesis\` paths. macOS is usable for GUI/data-review work only if this import is guarded.

## Running the Application

```bash
# From project root — uv handles the venv automatically
uv run python gui/confocal_gui.py
```

If the venv is already activated, the plain command works too:

```bash
PYTHONPATH=. python gui/confocal_gui.py
```

## Architecture Overview

This is a **PyQt6 GUI application** for controlling a confocal microscopy setup — galvanometer-driven XY scanning, piezo Z-axis positioning, and APD photoluminescence detection via National Instruments DAQ hardware.

### Thread Architecture

All hardware control runs in worker threads that inherit from `ExpThread` (experiments/ExpThread.py → QThread). Communication with the GUI uses PyQt6 signals/slots. Thread synchronization for scripted workflows uses `QMutex` + `QWaitCondition`.

Key threads:
- **`Confocal`** (experiments/Confocal.py) — XY/XZ/YZ scanning; generates galvo voltage waveforms, reads APD counter, fills 3D numpy arrays
- **`Tracker`** (experiments/Tracker.py) — Z autofocus; sweeps piezo, finds PL peak via parabolic fit + spline smoothing
- **`APD`** (experiments/APD.py) — Live photon count monitor running continuously
- **`TaskHandler`** (experiments/TaskHandler.py) — Scripted experiment sequencer; exposes `confocal()`, `track()`, `setval()`, etc. for automated workflows from `~/Documents/exp_scripts/`

### GUI Layer

`gui/confocal_gui.py` — `MainWindow` (~1500+ lines) is the top-level controller. It instantiates all threads, manages DAQ/piezo initialization, and coordinates the multi-tab interface. `gui/mainexp_widgets.py` holds custom PyQtGraph widgets and ROI handling.

### Configuration

Hardware parameters live in `exp_config/confocal_params.yaml` (galvo DAQ channels, voltage-to-micron scaling, piezo serial number, counter channels). GUI state is persisted to `exp_config/guisettings.config` via pickle.

### Data I/O

Scans are saved as `~/Documents/data_mat/IMG_XXXX.mat` (MATLAB format via `scipy.io.savemat`) containing `pl` (3D PL array), `xvals`/`yvals`/`zvals`. CSV metadata sidecars go alongside. Utilities in `file_utils.py`.

### Fitting / Image Processing

- `fitters.py` — wrapper-delegating framework for Gaussian, Lorentzian, Zeeman, hyperfine, saturation curve fits via `scipy.optimize.curve_fit`
- `akl_image_processing.py` — stripe pattern detection using coarse/fine angle search + variance optimization

## Key Dependencies

All managed via `pyproject.toml`. Core stack: `PyQt6`, `pyqtgraph`, `numpy`, `scipy`, `matplotlib`, `nidaqmx`, `opencv-python`, `PyYAML`. Windows-only extra (`uv sync --extra windows`): `pythonnet` for Thorlabs Kinesis piezo control.

## Branch

This project is developed and maintained on the `Confocal-Only` branch exclusively. Do not merge or port changes from `master`, which contains unrelated modalities (AWG, PicoHarp, FLIM, etc.).
