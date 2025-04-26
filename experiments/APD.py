from PyQt6.QtCore import pyqtSignal
import time
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType, READ_ALL_AVAILABLE

from . import ExpThread

# todo: this should be called count rate monitor
class LiveAPD(ExpThread.ExpThread):

    signal_liveapd_updateplots = pyqtSignal(float, float)
    signal_liveapd_grab_screenshots = pyqtSignal()

    def __init__(self, mainexp, wait_condition):
        super().__init__(mainexp, wait_condition)

        self.ctrapd = self.mainexp.inst_params['instruments']['ctrapd']
        self.ctrclk = self.mainexp.inst_params['instruments']['ctrclk']

        self.signal_liveapd_updateplots.connect(mainexp.liveapd_updateplots)
        self.signal_liveapd_grab_screenshots.connect(mainexp.liveapd_grab_screenshots)

    def update(self):
        raise Exception('Single Count Rate Acquisition not implemented.')

    def run(self):
        self.mainexp.set_gui_btn_enable('all', False)
        self.mainexp.btn_liveapd_stop.setEnabled(True)
        self.mainexp.btn_liveapd_clear.setEnabled(True)

        self.cancel = False

        with nidaqmx.Task('counter') as ci_task, nidaqmx.Task('clock') as clk_task:
            ci_task.ci_channels.add_ci_count_edges_chan(self.ctrapd['dev'])
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

    def save(self):
        if self.isRunning():
            self.signal_liveapd_grab_screenshots.emit()
            self.wait_for_mainexp()
        else:
            self.mainexp.liveapd_grab_screenshots()

        time.sleep(0.1)

        graph = self.mainexp.pixmap_liveapd_graph
        filename = 'PLtime_%d' % self.mainexp.wavenum
        data_dict = {'pl': self.mainexp.liveapd_pl, 'xvals': self.mainexp.liveapd_t}

        self.save_data(filename, data_dict, graph=graph, fig=graph)

# todo: this should be called fast count rate monitor
class SeqAPD(ExpThread.ExpThread):

    signal_seqapd_updateplots = pyqtSignal()
    signal_seqapd_grab_screenshots = pyqtSignal()

    def __init__(self, mainexp, wait_condition):
        super().__init__(mainexp, wait_condition)
        self.mainexp = mainexp

        ctrapd = self.mainexp.inst_params['instruments']['ctrapd']
        self.ci_chan = ctrapd['addr']['dev']
        self.ci_src = ctrapd['addr_src']

        ctrclk = self.mainexp.inst_params['instruments']['ctrclk']
        self.clk_chan = ctrclk['addr']['dev']
        self.clk_out = ctrclk['addr_out']

        self.signal_seqapd_updateplots.connect(mainexp.seqapd_updateplots)
        self.signal_seqapd_grab_screenshots.connect(mainexp.seqapd_grab_screenshots)

    def run(self):
        self.cancel = False
        self.mainexp.set_gui_btn_enable('all', False)
        self.mainexp.btn_seqapd_start.setEnabled(False)
        self.mainexp.btn_seqapd_stop.setEnabled(True)

        self.mainexp.label_seqapd_filename.setText('SeqPLtime_%d' % self.mainexp.wavenum)

        acqtime = self.mainexp.dbl_seqapd_acqtime.value() * 0.001
        numpnts = int(self.mainexp.dbl_seqapd_int_time.value() / acqtime)

        with nidaqmx.Task('counter') as ci_task, nidaqmx.Task('clock') as clk_task:
            # todo: does it matter which task starts first?
            ci_task.ci_channels.add_ci_count_edges_chan(self.ci_chan)
            ci_task.ci_channels[0].ci_count_edges_term = self.ci_src
            ci_task.timing.cfg_samp_clk_timing(1, source=self.clk_out, samps_per_chan=(numpnts + 1))

            clk_task.co_channels.add_co_pulse_chan_freq(self.clk_chan)
            clk_task.co_channels[0].co_pulse_freq = 1 / acqtime
            clk_task.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
            clk_task.start()
            ci_task.start()

            lastcount = 0
            n_read = 0
            t_update = 0.1

            self.mainexp.seqapd_pl = np.array([])
            while not self.cancel and n_read < numpnts+1:
                ctr_raw = ci_task.read(int(t_update / acqtime))
                if n_read != 0:
                    ctr_diff = np.diff(np.append([lastcount], ctr_raw))
                else:
                    ctr_diff = np.diff(ctr_raw)

                if ctr_raw:
                    n_read += len(ctr_raw)
                    lastcount = ctr_raw[-1]
                    self.mainexp.seqapd_pl = np.append(self.mainexp.seqapd_pl, ctr_diff)
                    self.mainexp.seqapd_t = np.arange(len(self.mainexp.seqapd_pl)) * acqtime
                    self.signal_seqapd_updateplots.emit()

        if self.mainexp.thread_terminal.isRunning() or self.mainexp.thread_batch.isRunning():
            self.save()

    def save(self):
        if self.isRunning():
            self.signal_seqapd_grab_screenshots.emit()
            self.wait_for_mainexp()
        else:
            self.mainexp.seqapd_grab_screenshots()

        time.sleep(0.1)

        graph = self.mainexp.pixmap_seqapd_graph
        filename = self.mainexp.label_seqapd_filename.text()
        data_dict = {'pl': self.mainexp.seqapd_pl, 'xvals': self.mainexp.seqapd_t}

        self.save_data(filename, data_dict, graph=graph, fig=graph)
