# Hardware Drivers

The Python packages from `pyproject.toml` are not enough on their own — the hardware below requires **system-level drivers** installed separately from pip/uv.

---

## 1. National Instruments DAQ (NI-DAQmx)

**What uses it:** Galvanometer X/Y scanning, laser/LED digital control, APD photon counter, clock generation.

The config (`exp_config/confocal_params.yaml`) shows a single device `Dev1` providing:

| Resource | Channel | Purpose |
|---|---|---|
| Analog out | `/Dev1/ao0`, `/Dev1/ao1` | Galvo Y / X mirror voltages |
| Digital out | `Dev1/port0/line0`, `line1` | Laser / LED on-off |
| Counter in | `/Dev1/ctr2` on `PFI0`/`PFI1` | APD photon counting (src + gate) |
| Counter out | `/Dev1/ctr0` on `PFI12` | Timing clock |

This means the DAQ must support **AO + DIO + 2× counter** (e.g., NI USB-6363, PCIe-6321, or similar X-series).

### Driver install

| Platform | How |
|---|---|
| **Windows** | Download **NI-DAQmx** from [ni.com](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html). Install the full runtime (not just runtime engine). |
| **Linux** | NI provides **NI Linux Device Drivers** (RPM/DEB) for RHEL/SUSE/Ubuntu. See [NI-DAQmx Linux](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html). |
| **macOS** | ❌ NI does not ship DAQmx for macOS. The Python `nidaqmx` package will install, but every DAQ call will fail at runtime. |

After installation, confirm the device is named `Dev1` in **NI MAX** (Measurement & Automation Explorer) on Windows, or via `nilsdev` on Linux. Rename it to `Dev1` if it shows up differently, or edit the YAML config to match.

---

## 2. Thorlabs Benchtop Precision Piezo (PFM450E)

**What uses it:** Z-axis sample positioning (autofocus / tracker).

`instruments/ThorlabsPiezo.py` loads three .NET DLLs:

```
C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll
C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericPiezoCLI.dll
C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.Benchtop.PrecisionPiezoCLI.dll
```

Expected serial number: **`44533394`** (set in `confocal_params.yaml`). Change if your unit differs.

### Driver install

| Platform | How |
|---|---|
| **Windows** | Install **Thorlabs Kinesis** from [thorlabs.com](https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=Motion_Control). Default install path must remain `C:\Program Files\Thorlabs\Kinesis\` (paths are hardcoded). Then run `uv sync --extra windows` to install the `pythonnet` bridge. |
| **Linux / macOS** | ❌ Not supported by Thorlabs. The `clr` import will fail and crash the GUI at startup. Use a Windows machine for live experiments. |

> If the piezo is not plugged in but Kinesis is installed, the code catches the connection error and continues with `self.channel = None` — but the **`import clr`** itself still requires Windows + Kinesis.

---

## 3. USB Camera

**What uses it:** Live optical preview only (not the science detector).

`gui/confocal_gui.py` uses OpenCV with the Windows Media Foundation backend: `enumerate_cameras(cv2.CAP_MSMF)`.

| Platform | How |
|---|---|
| **Windows** | No driver install — Windows handles UVC webcams natively via Media Foundation. |
| **Linux** | UVC is built into the kernel; `/dev/video*` is created automatically. **But:** the `CAP_MSMF` backend call won't enumerate cameras — switch to `cv2.CAP_V4L2` in code or accept that the camera tab will be empty. |
| **macOS** | UVC works system-wide; **but** again `CAP_MSMF` won't enumerate — needs `cv2.CAP_AVFOUNDATION`. |

The camera is optional — the scanning experiment runs fine without it.

---

## Instrument / Feature × OS Support

### Hardware

| Instrument | Windows | Linux | macOS | Notes |
|---|:---:|:---:|:---:|---|
| NI DAQ — Galvo X/Y (analog out) | ✅ | ✅ | ❌ | needs NI-DAQmx driver |
| NI DAQ — APD counter (counter in) | ✅ | ✅ | ❌ | needs NI-DAQmx driver |
| NI DAQ — Laser/LED (digital out) | ✅ | ✅ | ❌ | needs NI-DAQmx driver |
| NI DAQ — Clock (counter out) | ✅ | ✅ | ❌ | needs NI-DAQmx driver |
| Thorlabs PFM450E piezo (Z) | ✅ | ❌ | ❌ | needs Kinesis SDK + `pythonnet` |
| USB camera (UVC) | ✅ | ⚠️ | ⚠️ | code uses `CAP_MSMF` — change backend to use on Linux/macOS |

### Software Features

| Feature | Windows | Linux | macOS | Notes |
|---|:---:|:---:|:---:|---|
| Launch GUI | ✅ | ⚠️ | ⚠️ | `from instruments import ThorlabsPiezo` (line 15 of `confocal_gui.py`) imports `clr` — fails on non-Windows unless guarded |
| Confocal XY/XZ/YZ scan | ✅ | ⚠️ | ❌ | works on Linux **without** Z-scans (no piezo); needs DAQ |
| Z-tracker / autofocus | ✅ | ❌ | ❌ | requires piezo |
| Live APD monitor | ✅ | ✅ | ❌ | needs DAQ |
| Live camera preview | ✅ | ⚠️ | ⚠️ | backend change required (see above) |
| Scripted experiments (`TaskHandler`) | ✅ | ⚠️ | ❌ | inherits the per-instrument limits above |
| MAT browser (offline) | ✅ | ✅ | ✅ | pure Python, no hardware |
| Fitting (`fitters.py`) | ✅ | ✅ | ✅ | pure Python, no hardware |
| Image processing (`akl_image_processing.py`) | ✅ | ✅ | ✅ | pure Python, no hardware |

**Legend:** ✅ works · ⚠️ works with a code/config tweak · ❌ not supported by vendor / OS

### Bottom line
- **Windows** — full experiment + offline analysis
- **Linux** — galvos + APD only (no piezo, no Z-scans); good for headless acquisition rigs
- **macOS** — offline data analysis only (browse `.mat` files, fit curves, process images)

---

## Quick Pre-flight Check

After installing drivers, validate from the project root:

```bash
# DAQ device discoverable?
uv run python -c "import nidaqmx.system; print(nidaqmx.system.System.local().devices.device_names)"
# expected: ['Dev1']

# Piezo SDK loadable? (Windows only)
uv run python -c "import clr; clr.AddReference(r'C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll'); print('Kinesis OK')"
```
