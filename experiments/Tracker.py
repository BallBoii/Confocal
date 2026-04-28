from PyQt6.QtCore import pyqtSignal
import numpy as np
import time, nidaqmx
from nidaqmx.constants import AcquisitionType
from scipy.signal import savgol_filter
from scipy.interpolate import UnivariateSpline

from . import ExpThread

PIEZO_DELAY = 0.01 # PFM450E settling time rated for 25ms

class Tracker(ExpThread.ExpThread):

    # Define signals for communicating with mainexp
    signal_tracker_updateplot = pyqtSignal()

    def __init__(self, mainexp):  # Tracker does not need wait_condition
        super().__init__(mainexp)

        self.ctrapd = self.mainexp.inst_params['ctrapd']
        self.ctrclk = self.mainexp.inst_params['ctrclk']

        self.signal_tracker_updateplot.connect(self.mainexp.tracker_updateplot)

    def get_pl(self, acqtime):
        # todo: verify nidaqmx function
        with nidaqmx.Task('counter') as ci_task, nidaqmx.Task('clock') as clk_task:
            # 1. Configure the counter to be gated/clocked by t
            ci_task.ci_channels.add_ci_count_edges_chan(self.ctrapd['dev'])
            if not self.ctrapd['addr_src'].endswith('Source'):
                ci_task.ci_channels[0].ci_count_edges_term = self.ctrapd['addr_src']

            # Use the clock output as the sample clock for the counter
            ci_task.timing.cfg_samp_clk_timing(
                rate=1 / acqtime,
                source=self.ctrclk['addr_out'],
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=2  # Read start and end of the interval
            )

            # 2. Configure the clock task (Pulse Generation)
            clk_task.co_channels.add_co_pulse_chan_freq(
                self.ctrclk['dev'],
                freq=1 / acqtime,
                idle_state=nidaqmx.constants.Level.LOW
            )
            # cfg_implicit_timing is used for pulse generation tasks
            clk_task.timing.cfg_implicit_timing(
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=2
            )

            # 3. Execution
            ci_task.start()
            clk_task.start()

            # Read exactly 2 samples: one at T=0 and one at T=acqtime
            # This will block until the hardware finishes the acquisition
            counts = ci_task.read(number_of_samples_per_channel=2)

            return (counts[1] - counts[0]) / acqtime

    def find_focus_max(self, zpos, scores):
        """Smooths the focus curve and identifies the peak position."""

        # 1. Apply Savitzky-Golay smoothing
        # window_length should be odd; adjust based on your typical peak width
        window = 7 if len(scores) > 7 else (len(scores) // 2 * 2 - 1)
        if window < 3:
            smoothed_scores = scores
        else:
            smoothed_scores = savgol_filter(scores, window_length=window, polyorder=2)

        # 2. Find the index of the maximum in the smoothed data
        max_idx = np.argmax(smoothed_scores)

        # 3. Refine the peak using a Spline (Sub-pixel precision)
        # This creates a continuous function from the discrete points
        try:
            spline = UnivariateSpline(zpos, smoothed_scores - np.max(smoothed_scores) + 1e-9, s=0)
            # We look for roots of the derivative to find the local maximum
            roots = spline.derivative().roots()

            # Find the root closest to our argmax
            if len(roots) > 0:
                z_best = roots[np.argmin(np.abs(roots - zpos[max_idx]))]
            else:
                z_best = zpos[max_idx]
        except:
            # Fallback to simple argmax if spline fails
            z_best = zpos[max_idx]

        return z_best, smoothed_scores

    def run(self):
        self.mainexp.set_gui_btn_enable('all', False)

        z0 = self.mainexp.dbl_tracker_zpos.value()
        dz = self.mainexp.dbl_tracker_zrng.value()
        n = self.mainexp.int_tracker_numdivs.value() + 1

        zs = np.linspace(z0 - dz, z0+dz, n)

        self.mainexp.focus_zpos = zs
        self.mainexp.focus_score = np.empty(n)
        self.mainexp.focus_score[:] = np.nan
        self.mainexp.focus_zpos_fit = []
        self.mainexp.focus_score_fit = []

        for i, z in enumerate(zs):
            self.mainexp.piezo.SetPosition(z)
            time.sleep(PIEZO_DELAY)
            pl = self.get_pl(self.mainexp.dbl_tracker_acqtime.value())

            self.mainexp.focus_score[i] = pl
            self.signal_tracker_updateplot.emit()

        # todo: check autofocus logic to set final z
        z_best, focus_score_fit = self.find_focus_max(self.mainexp.focus_zpos, self.mainexp.focus_score)

        self.mainexp.dbl_tracker_zpos.setValue(z_best)
        time.sleep(PIEZO_DELAY)
        self.mainexp.focus_zpos_fit = zs
        self.mainexp.focus_score_fit = focus_score_fit

        self.mainexp.set_gui_btn_enable('all', True)

