from PyQt6.QtCore import pyqtSignal
import time
import nidaqmx
from nidaqmx.constants import AcquisitionType, READ_ALL_AVAILABLE

from . import ExpThread

class CountMonitor(ExpThread.ExpThread):

    signal_liveapd_updateplots = pyqtSignal(float, float)
    signal_liveapd_grab_screenshots = pyqtSignal()

    def __init__(self, mainexp, wait_condition):
        super().__init__(mainexp, wait_condition)

        self.ctrapd = self.mainexp.inst_params['ctrapd']
        self.ctrclk = self.mainexp.inst_params['ctrclk']

        self.signal_liveapd_updateplots.connect(mainexp.liveapd_updateplots)
        self.signal_liveapd_grab_screenshots.connect(mainexp.liveapd_grab_screenshots)

    def update(self):
        raise Exception('Single Count Rate Acquisition not implemented.')

    def run(self):
        self.mainexp.set_gui_btn_enable('all', False)
        self.mainexp.btn_confocal_start.setEnabled(True) # thread_confocal.run() can override and stop CountMonitor
        self.mainexp.btn_tracker_autofocus.setEnabled(True) # thread_tracker.run() can override and stop CountMonitor
        self.mainexp.btn_liveapd_stop.setEnabled(True)
        self.mainexp.btn_liveapd_clear.setEnabled(True)

        self.cancel = False

        with nidaqmx.Task('counter') as ci_task, nidaqmx.Task('clock') as clk_task:
            ci_task.ci_channels.add_ci_count_edges_chan(self.ctrapd['dev'])
            if not self.ctrapd['addr_src'].endswith('Source'):
                ci_task.ci_channels[0].ci_count_edges_term = self.ctrapd['addr_src']
            ci_task.timing.cfg_samp_clk_timing(1, source=self.ctrclk['addr_out'])

            clk_task.co_channels.add_co_pulse_chan_freq(self.ctrclk['dev'])
            acqtime = self.mainexp.dbl_liveapd_acqtime.value()
            clk_task.co_channels[0].co_pulse_freq = 1 / acqtime
            clk_task.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
            clk_task.start()

            lastcount = 0
            ci_task.start()

            while not self.cancel:
                count = ci_task.read(1)[0]
                pl = (count - lastcount)/acqtime
                self.signal_liveapd_updateplots.emit(acqtime, pl)
                lastcount = count

                if self.mainexp.dbl_liveapd_acqtime.value() != acqtime:
                    acqtime = self.mainexp.dbl_liveapd_acqtime.value()
                    clk_task.co_channels[0].co_pulse_freq = 1 / acqtime

        self.mainexp.set_gui_btn_enable('all', True)
