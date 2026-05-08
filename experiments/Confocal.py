from PyQt6.QtCore import pyqtSignal
import time
import numpy as np
from scipy import signal
from scipy.optimize import curve_fit

from . import ExpThread

import nidaqmx
from nidaqmx.constants import AcquisitionType, READ_ALL_AVAILABLE

class Confocal(ExpThread.ExpThread):
    # Define signals for communicating with mainexp
    signal_confocal_grab_screenshots = pyqtSignal()

    signal_confocal_initplot = pyqtSignal()
    signal_confocal_updateplot = pyqtSignal(int, int)
    signal_focus_score_updateplot = pyqtSignal(int)
    signal_tracker_home = pyqtSignal()

    def __init__(self, mainexp, wait_condition):
        super().__init__(mainexp, wait_condition)

        self.acqtime = 0.001

        self.mode = 0  # scanning mode (0: XY, 1: XZ, 2: YZ)

        self.var1 = []
        self.var2 = []
        self.var3 = []

        self.sweep_rev = False

        self.isLive = False
        self.confocal_live_stacks = []
        self.confocal_live_avg = 1
        # Connect signals
        self.signal_confocal_grab_screenshots.connect(self.mainexp.confocal_grab_screenshots)

        self.signal_confocal_initplot.connect(mainexp.confocal_initplot)
        self.signal_confocal_updateplot.connect(mainexp.confocal_updateplot)
        self.signal_focus_score_updateplot.connect(mainexp.focus_score_updateplot)
        self.signal_tracker_home.connect(self.mainexp.tracker_home)

        self.plane_coef = [0, 0, 1, 5]  # coefficients for Ax + By + Cz = D for autoZ

        self.galvos = self.mainexp.inst_params['galvos']
        self.galvos['scaleX'] = eval(self.galvos['scaleX'])
        self.galvos['scaleY'] = eval(self.galvos['scaleY'])
        self.ctrapd = self.mainexp.inst_params['ctrapd']

    def prep_mainexp(self):
        if self.mainexp.thread_liveapd.isRunning():
            self.mainexp.thread_liveapd.cancel = True
            self.mainexp.thread_liveapd.wait()
            self.wait_for_mainexp()

        self.mainexp.datasaved = False

        # enable and disable the right buttons here
        self.mainexp.set_gui_btn_enable('all', False)
        self.mainexp.set_gui_input_enable('confocal', False)
        self.mainexp.btn_confocal_stop.setEnabled(True)
        self.mainexp.chkbx_confocal_autolevel.setChecked(True)

        if self.mainexp.vb_confocal.roi:
            self.mainexp.vb_confocal.roi.hide()

        if self.isLive:
            self.mainexp.btn_confocal_live.setEnabled(True)

    def update(self):
        self.mode = self.mainexp.btn_confocal_mode.checkedId()
        self.confocal_live_avg = self.mainexp.int_confocal_live_avg.value()

    def prepsweep(self):
        mainexp = self.mainexp

        self.var1 = np.linspace(mainexp.dbl_confocal_x_start.value(), mainexp.dbl_confocal_x_stop.value(),
                                mainexp.int_confocal_x_numdivs.value() + 1)
        self.var2 = np.linspace(mainexp.dbl_confocal_y_start.value(), mainexp.dbl_confocal_y_stop.value(),
                                mainexp.int_confocal_y_numdivs.value() + 1)

        if mainexp.int_confocal_z_numdivs.value():
            self.var3 = np.linspace(mainexp.dbl_confocal_z_start.value(), mainexp.dbl_confocal_z_stop.value(),
                                    mainexp.int_confocal_z_numdivs.value() + 1)
        else:
            # ignore the z-range setting
            self.var3 = np.linspace(mainexp.dbl_tracker_zpos.value(), mainexp.dbl_tracker_zpos.value(), 1)

        mainexp.confocal_mode = self.mode
        mainexp.confocal_rngx = self.var1
        mainexp.confocal_rngy = self.var2
        mainexp.confocal_rngz = self.var3

        mainexp.focus_zpos = self.var3

        self.acqtime = mainexp.dbl_confocal_acqtime.value()

    def initplots(self):
        self.mainexp.confocal_pl = np.empty((self.var1.size, self.var2.size, self.var3.size))
        self.mainexp.confocal_pl[:][:][:] = np.nan
        self.confocal_live_stacks = np.array([])

        if len(self.var3) > 1: # z-stack, assume autofocus todo: add condition
            self.mainexp.focus_score = np.empty(len(self.var3))
            self.mainexp.focus_score[:] = np.nan
            self.mainexp.focus_zpos_fit = []
            self.mainexp.focus_score_fit = []

        if self.mode == 0: # Horizontal Scans
            self.mainexp.confocal_scanLine = self.mainexp.confocal_hLine
        elif self.mode == 1: # Vertical Scans
            self.mainexp.confocal_scanLine = self.mainexp.confocal_vLine
        else:
            print(f'Mode {self.mode} not implemented')

        self.signal_confocal_initplot.emit()
        self.signal_confocal_updateplot.emit(0, 0)
        self.signal_focus_score_updateplot.emit(0)

        if self.isLive:
            self.mainexp.confocal_scanLine.show()

        if self.isRunning():
            self.wait_for_mainexp()

    def set_pos(self, x, y):
        vx = x * self.galvos['scaleX'] + self.galvos['offsetX']
        vy = y * self.galvos['scaleY'] + self.galvos['offsetY']

        with nidaqmx.Task('Galvos') as ao_task:
            ao_task.ao_channels.add_ao_voltage_chan(f"{self.galvos['aoX']},{self.galvos['aoY']}")

            ao_task.write([vx, vy])

    def sweep(self):
        self.sweep_rev = False

        # todo: LIVE and z-stack are not compatible (duh!). Need to prevent running by accident.
        # Regular Scan. No Z-Stack.
        if len(self.var3) == 1:
            self.sweep_multiple_frames()
        # Z-Stack
        else:
            for zindex, z in enumerate(self.var3):
                self.mainexp.piezo.SetPosition(z)
                time.sleep(0.1) # PFM450E settling time rated for 25ms
                self.sweep_multiple_frames(zindex)
                if True: # todo: add condition
                    self.mainexp.focus_score[zindex] = self.calc_focus_score(zindex)
                    if zindex == len(self.var3) - 1:
                        zmax = self.fit_focus()
                    self.signal_focus_score_updateplot.emit(zindex)


    def sweep_multiple_frames(self, zindex=0):
        exit_loop = False

        while not exit_loop:
            if len(self.confocal_live_stacks):
                self.confocal_live_stacks = np.append(self.confocal_live_stacks,
                                                      np.empty((self.var1.size, self.var2.size, 1)), axis=2)
            else:
                self.confocal_live_stacks = np.empty((self.var1.size, self.var2.size, 1))
            self.confocal_live_stacks[:][:][-1] = np.nan
            if self.confocal_live_stacks.shape[2] > self.confocal_live_avg:
                self.confocal_live_stacks = self.confocal_live_stacks[:, :, -self.confocal_live_avg:]

            self.sweep_single_frame(zindex)
            self.sweep_rev = not self.sweep_rev

            if self.cancel:
                exit_loop = True
            else:
                if not self.isLive:
                    exit_loop = np.atleast_3d(self.confocal_live_stacks).shape[2] == self.confocal_live_avg
                else:
                    exit_loop = not self.mainexp.btn_confocal_live.isChecked() and \
                                np.atleast_3d(self.confocal_live_stacks).shape[2] == self.confocal_live_avg

    def sweep_single_frame(self, zindex=0):
        rate = 1/self.acqtime

        if not self.cancel:
            with (nidaqmx.Task('Galvo') as ao_task, nidaqmx.Task('Counter') as ci_task):

                ao_task.ao_channels.add_ao_voltage_chan(f"{self.galvos['aoX']},{self.galvos['aoY']}")
                ao_task.timing.cfg_samp_clk_timing(rate, source='', samps_per_chan=len(self.var1)*len(self.var2) + 1)

                ci_task.ci_channels.add_ci_count_edges_chan(f"{self.ctrapd['dev']}")
                ci_task.timing.cfg_samp_clk_timing(rate, source=f"{self.galvos['ao']}/SampleClock", samps_per_chan=len(self.var1)*len(self.var2) + 1)
                ci_task.triggers.arm_start_trigger.dig_edge_src = f"{self.galvos['ao']}/StartTrigger"
                if not self.ctrapd['addr_src'].endswith('Source'):
                    ci_task.ci_channels[0].ci_count_edges_term = self.ctrapd['addr_src']

                # Build a sweep list
                xlist = np.array([])
                ylist = np.array([])

                if self.mode == 0:
                    for i, y in enumerate(self.var2):
                        if not i % 2:
                            xlist = np.append(xlist, self.var1)
                        else:
                            xlist = np.append(xlist, np.flip(self.var1, axis=0))

                        ylist = np.append(ylist, np.ones(len(self.var1)) * y)

                    if self.sweep_rev:
                        xlist = np.flip(xlist, axis=0)
                        ylist = np.flip(ylist, axis=0)

                    xlist = np.append(xlist, xlist[-1]) * self.galvos['scaleX'] + self.galvos['offsetX']
                    ylist = np.append(ylist, ylist[-1]) * self.galvos['scaleY'] + self.galvos['offsetY']

                    # zlist = np.append(zlist, zlist[-1])

                    ci_task.start()
                    ao_task.write(np.vstack((xlist,ylist)), auto_start=True)

                    self.mainexp.seqapd_pl = np.array([])

                    numpnts1 = len(self.var1)
                    numpnts2 = len(self.var2)

                    last_counter = ci_task.read(1)

                    for index_y in range(numpnts2):
                        if not self.cancel:
                            # This will wait until the entire row is read

                            ctr_raw = ci_task.read(numpnts1)
                            ctr_diff = np.diff(np.append([last_counter], ctr_raw)) / self.acqtime

                            last_counter = ctr_raw[-1]

                            # Forward meander scan (increasing yvals)
                            if not self.sweep_rev:
                                if not index_y % 2:
                                    self.confocal_live_stacks[:, index_y, -1] = ctr_diff
                                else:
                                    self.confocal_live_stacks[:, index_y, -1] = np.flipud(ctr_diff)
                                self.mainexp.confocal_pl[:, index_y, zindex] = np.nanmean(
                                    self.confocal_live_stacks[:, index_y, :], axis=1)
                                sindex = index_y # todo: condensed this big if-else to one and substitute sindex (no time to test at time of writing)
                            # Reverse meander scan (decreasing yvals)
                            else:
                                if not index_y % 2:
                                    self.confocal_live_stacks[:, -(index_y + 1), -1] = np.flipud(ctr_diff)
                                else:
                                    self.confocal_live_stacks[:, -(index_y + 1), -1] = ctr_diff
                                self.mainexp.confocal_pl[:, -(index_y + 1), zindex] = np.nanmean(
                                    self.confocal_live_stacks[:, -(index_y + 1), :], axis=1)
                                sindex = numpnts2 - (index_y + 1)

                            self.signal_confocal_updateplot.emit(zindex, sindex)

                elif self.mode == 1:
                    for i, x in enumerate(self.var1):
                        if not i % 2:
                            ylist = np.append(ylist, self.var2)
                        else:
                            ylist = np.append(ylist, np.flip(self.var2, axis=0))

                        xlist = np.append(xlist, np.ones(len(self.var2)) * x)

                    if self.sweep_rev:
                        xlist = np.flip(xlist, axis=0)
                        ylist = np.flip(ylist, axis=0)

                    xlist = np.append(xlist, xlist[-1]) * self.galvos['scaleX'] + self.galvos['offsetX']
                    ylist = np.append(ylist, ylist[-1]) * self.galvos['scaleY'] + self.galvos['offsetY']

                    ci_task.start()
                    ao_task.write(np.vstack((xlist, ylist)), auto_start=True)

                    self.mainexp.seqapd_pl = np.array([])

                    numpnts1 = len(self.var1)
                    numpnts2 = len(self.var2)

                    last_counter = ci_task.read(1)

                    for index_x in range(numpnts1):
                        if not self.cancel:
                            # This will wait until the entire row is read

                            ctr_raw = ci_task.read(numpnts2)
                            ctr_diff = np.diff(np.append([last_counter], ctr_raw)) / self.acqtime

                            last_counter = ctr_raw[-1]

                            # Forward meander scan (increasing yvals)
                            if not self.sweep_rev:
                                if not index_x % 2:
                                    self.confocal_live_stacks[index_x, :, -1] = ctr_diff
                                else:
                                    self.confocal_live_stacks[index_x, :, -1] = np.flipud(ctr_diff)
                                self.mainexp.confocal_pl[index_x, :, zindex] = np.nanmean(
                                    self.confocal_live_stacks[index_x, :, :], axis=1)
                                sindex = index_x
                            # Reverse meander scan (decreasing yvals)
                            else:
                                if not index_x % 2:
                                    self.confocal_live_stacks[-(index_x + 1), :, -1] = np.flipud(ctr_diff)
                                else:
                                    self.confocal_live_stacks[-(index_x + 1), :, -1] = ctr_diff
                                self.mainexp.confocal_pl[-(index_x + 1), :, zindex] = np.nanmean(
                                    self.confocal_live_stacks[-(index_x + 1), :, :], axis=1)
                                sindex = -(index_x + 1)

                            self.signal_confocal_updateplot.emit(zindex, sindex) # todo: vline not implemented
                else:
                    print(f'Mode {self.mode} not implemented')
                if self.isRunning():
                    self.wait_for_mainexp()

    def calc_focus_score(self, zindex):
        image = self.mainexp.confocal_pl[:, :, zindex]
        mean = np.mean(image)
        std = np.std(image)
        image = np.squeeze(image)
        image[image < mean / 2] = 0
        image[image > mean + std] = mean + std

        # compress dynamic range
        image = np.log(image + 1)

        # median filter
        if self.mode == 0: # todo: double check axis
            image = signal.medfilt2d(image, kernel_size=(3, 1))
        elif self.mode == 1:
            image = signal.medfilt2d(image, kernel_size=(1, 3))
        else:
            print(f'Mode {self.mode} not implemented')

        # low-pass filter
        # the value can be linked to parameter on program
        fs = 50 / 300  # px/micron
        cutoff = 1 / 20  # 1/micron
        Wn = cutoff * 2 / fs
        order = 2
        b, a = signal.butter(order, Wn, btype='low')

        filtered = np.squeeze(signal.filtfilt(b, a, image, axis=self.mode))
        filtered -= np.median(filtered)
        filtered[filtered < 0] = 0

        score = np.sum(filtered)
        return score

    def lorentzian_model(self, x, A, x0, gamma, m, c):
        # Half-width at half-maximum (HWHM)
        half_gamma = gamma / 2.0

        # Lorentzian component (normalized to integrate to A)
        lorentzian = (A / np.pi) * (half_gamma / ((x - x0) ** 2 + half_gamma ** 2))

        # Linear background component
        linear_background = m * x + c

        return lorentzian + linear_background

    def lorentzian_fit(self, xs, ys):
        A_guess = np.max(ys) - np.min(ys)
        x0_guess = xs[np.argmax(ys)]
        gamma_guess = (np.max(xs) - np.min(xs)) / 5.0
        m_guess = (ys[-1] - ys[0]) / (xs[-1] - xs[0])
        c_guess = ys[0] - m_guess * xs[0]

        initial_guesses = [A_guess, x0_guess, gamma_guess, m_guess, c_guess]

        try:
            popt, pcov = curve_fit(f=self.lorentzian_model,xdata=xs,ydata=ys,
                                   p0=initial_guesses,
                                   bounds=([0.0, np.min(xs), 0.0, -np.inf, -np.inf],
                                           [np.inf, np.max(xs), np.ptp(xs), np.inf, np.inf])
                                   )

            fit_ys = self.lorentzian_model(xs, *popt)

        except RuntimeError as e:
            print("\nERROR: Curve fitting failed.")
            print(f"Details: {e}")
            popt = None
            fit_ys = None

        return popt, fit_ys

    def fit_focus(self):
        popt, fit_ys = self.lorentzian_fit(self.mainexp.focus_zpos, self.mainexp.focus_score)
        if popt is None:
            return
        zmax = popt[1]
        print('zmax = ', zmax)
        zs = self.mainexp.focus_zpos
        zs_fit = np.linspace(np.min(zs), np.max(zs), 100)
        scores_fit = self.lorentzian_model(zs_fit, *popt)
        self.mainexp.focus_zpos_fit = zs_fit
        self.mainexp.focus_score_fit = scores_fit
        return zmax

    def cleanup(self):
        if self.mainexp.chkbx_autosave.isChecked() and not self.mainexp.datasaved:
            self.save()
            self.mainexp.datasaved = True

        self.signal_tracker_home.emit()

        if self.mainexp.btn_task_automate.isChecked():
            self.mainexp.illum_set('laser', False)
            self.mainexp.illum_set('led', True)

        self.mainexp.confocal_scanLine.hide()
        self.mainexp.set_gui_btn_enable('all', True)
        self.mainexp.set_gui_input_enable('confocal', True)
        self.mainexp.btn_confocal_stop.setEnabled(False)

    def save(self, ext=False):
        if self.isRunning() and not ext:
            self.signal_confocal_grab_screenshots.emit()
            self.wait_for_mainexp()
            time.sleep(0.1)
        else:  # For use when save_esr is called from a button
            self.mainexp.confocal_grab_screenshots()

        graph = self.mainexp.pixmap_confocal_graph
        fig = self.mainexp.pixmap_confocal_fig
        filename = self.mainexp.label_filename.text()
        data_dict = {'pl': self.mainexp.confocal_pl,
                     'xvals': self.mainexp.confocal_rngx,
                     'yvals': self.mainexp.confocal_rngy,
                     'zvals': self.mainexp.confocal_rngz,
                     'cam_image': self.mainexp.qtimg_camera.image,
                     'cam_fov': tuple(self.mainexp.inst_params['camera']['fov']),
                     'mask_0': self.mainexp.mask_0,
                     'mask_1': self.mainexp.mask_1,
                     'mask_2': self.mainexp.mask_2,
                     'mask_3': self.mainexp.mask_3,
                     'mask_4': self.mainexp.mask_4,
                     }

        self.mainexp.save_data(filename, data_dict, graph=graph, fig=fig)

    def run(self):
        self.cancel = False

        self.prep_mainexp()
        self.update()

        self.prepsweep()
        self.initplots()

        self.sweep()

        self.cleanup()
        self.isLive = False

    def __del__(self):
        self.wait()
