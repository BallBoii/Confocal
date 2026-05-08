from PyQt6 import QtCore, QtWidgets, uic
from PyQt6.QtCore import QThread, pyqtSignal as Signal
import cv2
from click import progressbar
from cv2_enumerate_cameras import enumerate_cameras

# system imports
import sys, os, scipy.io, warnings, functools, time
import pyqtgraph as pg
import numpy as np

# user-defined imports
import file_utils
import experiments as exp
from instruments import ThorlabsPiezo
import nidaqmx

# import UI files
import mainexp_widgets
import akl_image_processing


def my_excepthook(type, value, tback):
    sys.__excepthook__(type, value, tback)

sys.excepthook = my_excepthook

# Define yellow-hot colormap
colors = np.array([[0, 0, 0], [255, 0, 0], [255, 255, 0]])
cm = pg.ColorMap([0, 0.5, 1], colors)


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        uic.loadUi("gui/confocal.ui", self)

        # # configure PyQTgraph to use white background
        # pg.setConfigOption('background', 'w')
        # pg.setConfigOption('foreground', 'k')


        # self.setFixedSize(self.size())
        self.showMaximized()

        # this will ensure that the application quits at the right time,
        # and that Qt has a chance to automatically delete all the children of the top-level window
        # before the python garbage-collector gets to work.
        # http://stackoverflow.com/questions/27131294/error-qobjectstarttimer-qtimer-can-only-be-used-with-threads-started-with-qt
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        self.mutex = QtCore.QMutex()
        self.wait_confocal = QtCore.QWaitCondition()
        self.wait_liveapd = QtCore.QWaitCondition()
        self.wait_tracker = QtCore.QWaitCondition()

        '''INSTRUMENT INITIALIZATION'''
        instpath = os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'confocal_params.yaml'))
        if os.path.isfile(instpath):
            self.inst_params = file_utils.yaml2dict(instpath)
        else:
            print('inst_params.yaml not found!')

        '''WORKER THREADS INITIALIZATION'''
        self.thread_confocal = exp.Confocal.Confocal(self, self.wait_confocal)
        self.thread_liveapd = exp.APD.CountMonitor(self, self.wait_liveapd)
        self.thread_tracker = exp.Tracker.Tracker(self, self.wait_tracker)

        '''CONFOCAL'''
        self.piezo = ThorlabsPiezo.PFM450E(self.inst_params['piezo']['sn'])
        self.piezo.SetPosition(0.0)
        self.cbox_zpos_stepsize.addItems(['Z: 10 μm', 'Z:  1 μm', 'Z: 0.1 μm'])
        self.cbox_zpos_stepsize.currentTextChanged.connect(self.set_zpos_stepsize)
        self.dbl_tracker_zpos.valueChanged.connect(self.piezo.SetPosition)

        self.btn_confocal_mode_xy.setChecked(True)
        self.btn_confocal_mode = QtWidgets.QButtonGroup()
        self.btn_confocal_mode.addButton(self.btn_confocal_mode_xy, 0)
        self.btn_confocal_mode.addButton(self.btn_confocal_mode_yx, 1)
        self.btn_confocal_start.clicked.connect(self.confocal_start)
        self.btn_confocal_start_roi.clicked.connect(self.confocal_start_roi)
        self.btn_confocal_start_preset1.clicked.connect(self.confocal_start_preset1)
        self.btn_confocal_start_preset2.clicked.connect(self.confocal_start_preset2)
        self.btn_confocal_roi_undo.clicked.connect(self.confocal_roi_undo)
        self.btn_confocal_live.toggled.connect(self.confocal_live)
        self.int_confocal_live_avg.valueChanged.connect(self.confocal_live_set_avg)
        self.btn_confocal_stop.clicked.connect(self.confocal_stop)
        self.btn_confocal_save.clicked.connect(lambda: self.thread_confocal.save(ext=True)) # todo: cleanup functools
        self.btn_confocal_pxsync.clicked.connect(self.confocal_pxsync)
        self.chkbx_confocal_autolevel.toggled.connect(self.confocal_set_autolevel)
        self.chkbx_confocal_loglevel.toggled.connect(self.confocal_loglevel)
        self.btn_confocal_acqtime_inc.clicked.connect(self.confocal_acqtime_inc)
        self.btn_confocal_acqtime_dec.clicked.connect(self.confocal_acqtime_dec)
        self.btn_confocal_load.clicked.connect(self.confocal_load)
        self.int_confocal_cmin.valueChanged.connect(self.confocal_update_level)
        self.int_confocal_cmax.valueChanged.connect(self.confocal_update_level)

        self.btn_tracker_mode_pixel.setChecked(True)
        self.btn_tracker_mode = QtWidgets.QButtonGroup()
        self.btn_tracker_mode.addButton(self.btn_tracker_mode_pixel, 0)
        self.btn_tracker_mode.addButton(self.btn_tracker_mode_image, 1)
        self.btn_tracker_home.clicked.connect(self.tracker_home)
        self.btn_tracker_autofocus.clicked.connect(self.tracker_start)

        self.confocal_mode = 0
        self.confocal_rngx = []
        self.confocal_rngy = []
        self.confocal_rngz = []
        self.confocal_pl = np.array([])

        self.confocal_settings = []  # for storing previous scans [(xmin, max, ymin, ymax)]

        # Create PyQtGraph plots and histogram for confocal scans
        for name in ['confocal', 'camera']:
            setattr(self, 'vb_%s' % name, mainexp_widgets.ViewBoxWithROI())
            setattr(self, 'plt_%s' % name, pg.PlotItem(viewBox=getattr(self, 'vb_%s' % name)))
            setattr(self, 'qtimg_%s' % name, pg.ImageItem())
            getattr(self, 'vb_%s' % name).addItem(getattr(self, 'qtimg_%s' % name))
            getattr(self, 'plt_%s' % name).setLabels(bottom='xpos (&mu;m)', left='ypos (&mu;m)')

        for i in range(5):
            # Create a fully transparent lookup table (All zeros for RGBA)
            transparent_lut = np.zeros((256, 4), dtype=np.ubyte)
            setattr(self, 'mask_%i' % i, np.array([]))
            setattr(self, 'qtimg_mask_%i' % i, pg.ImageItem())
            getattr(self, 'qtimg_mask_%i' % i).setLookupTable(transparent_lut)
            getattr(self, 'vb_confocal').addItem(getattr(self, 'qtimg_mask_%s' % i))

        # Create contour lines for exact match, inner land, and outside areas
        self.iso_mask_0 = pg.IsocurveItem(data=None, level=0.5, pen=pg.mkPen('w', width=1))
        self.iso_mask_0.setParentItem(self.qtimg_mask_0)
        self.iso_mask_1 = pg.IsocurveItem(data=None, level=0.5, pen=pg.mkPen('w', width=1))
        self.iso_mask_1.setParentItem(self.qtimg_mask_1)
        self.iso_mask_4 = pg.IsocurveItem(data=None, level=0.5, pen=pg.mkPen('w', width=1))
        self.iso_mask_4.setParentItem(self.qtimg_mask_4)

        '''CONFOCAL PLOTS'''
        self.glw_confocal = pg.GraphicsLayoutWidget()
        self.glw_confocal.addItem(self.plt_confocal, 0, 0)
        self.hlw_confocal = mainexp_widgets.CustomLUTWidget(image=self.qtimg_confocal)
        self.hlw_confocal.gradient.setColorMap(cm)

        self.grid_confocal.addWidget(self.glw_confocal, 0, 0)
        self.grid_confocal.addWidget(self.hlw_confocal, 0, 1)

        self.confocal_vLine = pg.InfiniteLine(angle=90, movable=False, pen=(0, 150, 100))
        self.confocal_hLine = pg.InfiniteLine(angle=0, movable=False, pen=(0, 150, 100))
        self.confocal_vLine.hide()
        self.confocal_hLine.hide()
        self.confocal_scanLine = None # handle for hLine or vLine during live scans

        self.confocal_cursor = pg.ScatterPlotItem(pen=pg.mkPen('w', width=2), brush=None, symbol='o', size=7)
        self.confocal_cursor.setData([0], [0])

        self.plt_confocal.addItem(self.confocal_cursor)
        self.plt_confocal.addItem(self.confocal_vLine, ignoreBounds=True)
        self.plt_confocal.addItem(self.confocal_hLine, ignoreBounds=True)

        self.int_confocal_z_numdivs.valueChanged.connect(self.confocal_zstack_enable)
        self.confocal_zstack_enable(self.int_confocal_z_numdivs.value())

        self.btn_confocal_select.clicked.connect(self.confocal_select)

        '''CAMERA CONTROL'''
        self.glw_camera = pg.GraphicsLayoutWidget()
        self.glw_camera.addItem(self.plt_camera, 0, 0)
        self.hlw_camera = mainexp_widgets.CustomLUTWidget(image=self.qtimg_camera)
        self.hlw_camera.gradient.setColorMap(cm)

        self.grid_camera.addWidget(self.glw_camera, 0, 0)
        self.grid_camera.addWidget(self.hlw_camera, 0, 1)

        self.camera_vLine = pg.InfiniteLine(angle=90, movable=False, pen=(0, 150, 100))
        self.camera_hLine = pg.InfiniteLine(angle=0, movable=False, pen=(0, 150, 100))
        self.camera_vLine.hide()
        self.camera_hLine.hide()

        self.camera_cursor = pg.ScatterPlotItem(pen=pg.mkPen('r', width=0), brush='r', symbol='+', size=10)
        self.camera_cursor.setData([0], [0])

        self.plt_camera.addItem(self.camera_cursor)
        self.plt_camera.addItem(self.camera_vLine, ignoreBounds=True)
        self.plt_camera.addItem(self.camera_hLine, ignoreBounds=True)

        self.btn_camera_select.clicked.connect(self.camera_select)
        self.btn_camera_start.toggled.connect(self.camera_start)
        self.btn_camera_start_roi.clicked.connect(self.camera_start_roi)
        self.chkbx_camera_autolevel.toggled.connect(self.camera_set_autolevel)
        self.chkbx_camera_loglevel.toggled.connect(self.camera_loglevel)

        self.camera_data = np.array([])

        self.cbox_camera_list.addItems([str(cam).rsplit(' ', 1)[0] for cam in enumerate_cameras(cv2.CAP_MSMF)])
        self.thread_camera = CameraThread(self)
        self.thread_camera.frame_signal.connect(self.camera_updateplot)

        '''LIVE APD'''
        self.btn_liveapd_clear.clicked.connect(self.liveapd_clear)
        self.btn_liveapd_start.clicked.connect(self.liveapd_start)
        self.btn_liveapd_stop.clicked.connect(self.liveapd_stop)
        self.btn_liveapd_stop.setEnabled(False)

        self.plt_liveapd = self.glw_liveapd.addPlot()
        self.plt_liveapd.setLabels(left='PL (Hz)', bottom='Time (s)')
        self.curve_liveapd = self.plt_liveapd.plot(pen='r')
        self.liveapd_pl = np.array([])
        self.liveapd_t = np.array([])

        self.plt_focus_score = self.glw_liveapd.addPlot()
        self.plt_focus_score.setLabels(left='focus score', bottom='zpos (um)')
        self.curve_focus_score = self.plt_focus_score.plot(pen='r')
        self.curve_focus_score_fit = self.plt_focus_score.plot(pen='w')
        self.focus_score = np.array([])
        self.focus_zpos = np.array([])
        self.focus_score_fit = np.array([])
        self.focus_zpos_fit = np.array([])
        self.plt_focus_score.setVisible(False)

        '''ILLUMINATION CONTROL'''
        self.btn_illum_laser.toggled.connect(lambda b: self.illum_set('laser', b))
        self.btn_illum_led.toggled.connect(lambda b: self.illum_set('led', b))
        self.illum_set('laser', False)
        self.illum_set('led', False)

        '''NAVIGATION PROGRESS BAR'''
        self.task_progressbar = mainexp_widgets.CustomProgressBar()
        self.task_progressbar.labels = ['Load Plate', 'Check Laser Position', 'Autofocus', 'Image Check', 'Confocal Scan', 'Image Analysis']

        self.btn_task_analyze.clicked.connect(self.task_analyze)
        self.btn_task_forward.clicked.connect(self.task_forward)
        self.btn_task_backward.clicked.connect(self.task_backward)
        self.btn_task_reset.clicked.connect(self.task_reset)
        self.btn_task_automate.clicked.connect(self.task_set_automate)
        self.set_gui_btn_enable('automation', False)

        self.layout_navigator.addWidget(self.task_progressbar)

        '''SET UP ALL GUI'''
        # Set up ranges and step limits in the gui fields
        if os.path.isfile(os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'guifield_settings_confocal.yaml'))):
            gfspath = os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'guifield_settings_confocal.yaml'))
        else:
            gfspath = 'guifield_settings_confocal.yaml'

        if os.path.isfile(gfspath):
            gfs = file_utils.yaml2dict(gfspath)
            self.esrguisettings = gfs.pop('esr', None) # return the esr key or None
            for field in gfs.keys():
                try:
                    target = getattr(self, field)
                    for setting in gfs[field].keys():
                        if type(gfs[field][setting]) is list:
                            getattr(target, setting)(*gfs[field][setting])
                        else:
                            getattr(target, setting)(gfs[field][setting])
                except AttributeError:
                    print('Field %s does not exist.' % field)
            print('loading guifield_settings_confocal.yaml')

        processEvents()

        # Load previous GUI settings
        if os.path.isfile(os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'guisettings.config'))):
            print('loading guisettings.config')
            gui_settings = file_utils.load_config(os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'guisettings.config')))
            self.import_gui_settings(gui_settings)
        elif os.path.isfile(os.path.join(os.getcwd(), 'guisettings.config')):
            warnings.warn('loading guisettings.config from exp_code. this may be bogus')
            gui_settings = file_utils.load_config(os.path.join(os.getcwd(), 'guisettings.config'))
            self.import_gui_settings(gui_settings)

        '''MISCELLANEOUS'''
        self.wavenum = file_utils.getwavenum() + 1

        self.pixmap_confocal_graph = None
        self.pixmap_confocal_fig = None

        try:
            self.tracker_home()
        except nidaqmx.errors.DaqError:
            pass

        self.piezo.SetPosition(225.0)
        self.dbl_tracker_zpos.blockSignals(True)
        self.dbl_tracker_zpos.setValue(225.0)
        self.dbl_tracker_zpos.blockSignals(False)

        self.chkbx_confocal_autolevel.setChecked(False)
        self.chkbx_camera_autolevel.setChecked(False)

    def illum_set(self, source, state):
        """Sets the digital output for the laser or LED."""
        line = self.inst_params['illum'].get(source)
        if line:
            try:
                with nidaqmx.Task() as do_task:
                    do_task.do_channels.add_do_chan(line)
                    do_task.write(bool(state))
            except Exception as e:
                print(f"Error setting {source} illumination: {e}")

    def confocal_pxsync(self, b):
        if b:
            self.int_confocal_y_numdivs.setEnabled(False)
            self.int_confocal_y_numdivs.setValue(self.int_confocal_x_numdivs.value())
            self.int_confocal_x_numdivs.valueChanged.connect(self.int_confocal_y_numdivs.setValue)
        else:
            self.int_confocal_x_numdivs.valueChanged.disconnect()
            self.int_confocal_y_numdivs.setEnabled(True)

    def confocal_zstack_enable(self, b):
        self.dbl_confocal_z_start.setEnabled(bool(b))
        self.dbl_confocal_z_stop.setEnabled(bool(b))

    def confocal_start(self):
        if self.btn_confocal_select.isChecked():
            self.btn_confocal_select.setChecked(False)
        if self.btn_camera_select.isChecked():
            self.btn_camera_select.setChecked(False)
        self.confocal_cursor.hide()

        self.confocal_settings_store()
        self.thread_confocal.start()

    def confocal_get_settings(self):
        xmin = self.dbl_confocal_x_start.value()
        xmax = self.dbl_confocal_x_stop.value()
        ymin = self.dbl_confocal_y_start.value()
        ymax = self.dbl_confocal_y_stop.value()

        return (xmin, xmax, ymin, ymax)

    def confocal_settings_store(self):
        settings = self.confocal_get_settings()

        if not self.confocal_settings or self.confocal_settings[-1] != settings:
            self.confocal_settings.append(settings)

    def confocal_set_roi(self, roi):
        self.confocal_settings_store()

        # get and set new settings from roi (roi is inverted in y-axis)
        self.dbl_confocal_x_start.setValue(roi.pos().x())
        self.dbl_confocal_x_stop.setValue(roi.pos().x() + roi.size().x())
        self.dbl_confocal_y_start.setValue(roi.pos().y() + roi.size().y())
        self.dbl_confocal_y_stop.setValue(roi.pos().y())

    def confocal_roi_undo(self):
        if self.confocal_settings:
            settings_old = self.confocal_settings.pop()

            # Try to pop another one if this is the previously saved roi
            if settings_old == self.confocal_get_settings() and self.confocal_settings:
                settings_old = self.confocal_settings.pop()

            self.dbl_confocal_x_start.setValue(settings_old[0])
            self.dbl_confocal_x_stop.setValue(settings_old[1])
            self.dbl_confocal_y_start.setValue(settings_old[2])
            self.dbl_confocal_y_stop.setValue(settings_old[3])

    def confocal_start_roi(self):
        roi = self.vb_confocal.roi
        if roi and roi.isVisible():
            self.confocal_set_roi(roi)
            self.confocal_start()
        else:
            raise Exception('No ROI Selected')

    def confocal_start_preset1(self):
        self.confocal_settings_store()

        self.dbl_confocal_x_start.setValue(-self.dbl_confocal_preset1.value()/2)
        self.dbl_confocal_x_stop.setValue(+self.dbl_confocal_preset1.value()/2)
        self.dbl_confocal_y_start.setValue(-self.dbl_confocal_preset1.value()/2)
        self.dbl_confocal_y_stop.setValue(+self.dbl_confocal_preset1.value()/2)
        self.confocal_start()

    def confocal_start_preset2(self):
        self.confocal_settings_store()

        self.dbl_confocal_x_start.setValue(-self.dbl_confocal_preset2.value()/2)
        self.dbl_confocal_x_stop.setValue(+self.dbl_confocal_preset2.value()/2)
        self.dbl_confocal_y_start.setValue(-self.dbl_confocal_preset2.value()/2)
        self.dbl_confocal_y_stop.setValue(+self.dbl_confocal_preset2.value()/2)
        self.confocal_start()

    def confocal_acqtime_inc(self):
        acqtimes = [0, 0.00001, 0.00002, 0.00005,
                    0.00010, 0.00020, 0.00050,
                    0.00100, 0.00200, 0.00500,
                    0.01000, 0.02000, 0.05000,
                    0.10000, 0.20000, 0.50000,
                    1.00000, 2.00000, 5.00000]

        self.dbl_confocal_acqtime.setValue(acqtimes[np.searchsorted(acqtimes, self.dbl_confocal_acqtime.value(), side='right')])

    def confocal_acqtime_dec(self):
        acqtimes = [0, 0.00001, 0.00002, 0.00005,
                    0.00010, 0.00020, 0.00050,
                    0.00100, 0.00200, 0.00500,
                    0.01000, 0.02000, 0.05000,
                    0.10000, 0.20000, 0.50000,
                    1.00000, 2.00000, 5.00000]

        self.dbl_confocal_acqtime.setValue(acqtimes[np.searchsorted(acqtimes, self.dbl_confocal_acqtime.value(), side='left') - 1])

    def confocal_live(self, b):
        if b:
            self.thread_confocal.isLive = True
            self.thread_confocal.start()

    def confocal_live_set_avg(self, v):
        self.thread_confocal.confocal_live_avg = v

    def confocal_stop(self):
        self.thread_confocal.cancel = True

    def confocal_initplot(self):
        with warnings.catch_warnings():
            if self.confocal_pl.size:
                warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
                start_x = self.confocal_rngx[0]
                stop_x = self.confocal_rngx[-1]
                start_y = self.confocal_rngy[0]
                stop_y = self.confocal_rngy[-1]

                px = (stop_x - start_x) / (len(self.confocal_rngx)-1)
                py = (stop_y - start_y) / (len(self.confocal_rngy)-1)

                for i in [0, 1, 4]:
                    iso = getattr(self, f'iso_mask_{i}')
                    iso.setData(None)

                if not self.chkbx_confocal_loglevel.isChecked():
                    self.qtimg_confocal.setImage(self.confocal_pl[:, :, 0])
                else:
                    self.qtimg_confocal.setImage(np.log10(self.confocal_pl[:, :, 0]+ 1))

                for name in ['confocal']:
                    qtimg = getattr(self, 'qtimg_%s' % name)
                    qtimg.resetTransform()  # need to call this. otherwise pos and scale are relative to previous
                    qtimg.setRect(start_x-px/2, start_y-py/2, (stop_x - start_x)+px, (stop_y - start_y)+py)

                if self.confocal_mode == 0:
                    self.plt_confocal.setLabels(bottom='xpos (&mu;m)', left='ypos (&mu;m)')
                if self.confocal_mode == 1:
                    self.plt_confocal.setLabels(bottom='xpos (&mu;m)', left='zpos (&mu;m)')
                if self.confocal_mode == 2:
                    self.plt_confocal.setLabels(bottom='ypos (&mu;m)', left='zpos (&mu;m)')

                processEvents()

    def confocal_updateplot(self, zindex=0, sindex=0):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
            if self.confocal_pl.size:
                if not self.chkbx_confocal_loglevel.isChecked():
                    self.qtimg_confocal.setImage(self.confocal_pl[:, :, zindex])
                else:
                    self.qtimg_confocal.setImage(np.log10(self.confocal_pl[:, :, zindex] + 1))
                # self.hlw_confocal.setImageItem(self.qtimg_confocal)
                self.confocal_scanLine.setPos(self.confocal_rngy[sindex])
                filename = 'IMG_%04d' % self.wavenum

                self.plt_confocal.setTitle('%s: Z = %.2f' % (filename, self.confocal_rngz[zindex]))
                self.label_filename.setText(filename)

    def confocal_set_autolevel(self, b):
        self.hlw_confocal.item.autoLevel = bool(b)

    def confocal_loglevel(self, checked):
        self.confocal_initplot()
        if not self.chkbx_confocal_autolevel.isChecked():
            self.confocal_update_level()

    def confocal_update_level(self):
        self.chkbx_confocal_autolevel.setChecked(False)
        self.hlw_confocal.item.autoLevel = False
        if not self.chkbx_confocal_loglevel.isChecked():
            self.hlw_confocal.item.setLevels(self.int_confocal_cmin.value(),
                                             self.int_confocal_cmax.value())
        else:
            self.hlw_confocal.item.setLevels(np.log10(self.int_confocal_cmin.value()),
                                             np.log10(self.int_confocal_cmax.value()))

    def confocal_grab_screenshots(self):
        self.pixmap_confocal_graph = self.grab()
        self.pixmap_confocal_fig = self.frame_main_confocal.grab()
        processEvents()

    def confocal_select(self, checked):
        if checked:  # enable cursor
            self.confocal_vLine.show()
            self.confocal_hLine.show()
            self.plt_confocal.scene().sigMouseMoved.connect(self.confocal_mouseMoved)
            self.plt_confocal.scene().sigMouseClicked.connect(self.confocal_clicked_drive)
        else:  # disable cursor here
            self.confocal_vLine.hide()
            self.confocal_hLine.hide()
            try:
                self.plt_confocal.scene().sigMouseMoved.disconnect()
            except TypeError:
                pass
            try:
                self.plt_confocal.scene().sigMouseClicked.disconnect()
            except TypeError:
                pass

    def confocal_clicked_drive(self, event):
        pos = event.scenePos()
        if self.plt_confocal.sceneBoundingRect().contains(pos) and event.button() == QtCore.Qt.MouseButton.LeftButton:
            mousePoint = self.vb_confocal.mapSceneToView(pos)
            self.dbl_tracker_xpos.setValue(mousePoint.x())
            self.dbl_tracker_ypos.setValue(mousePoint.y())
            self.tracker_drive()
        else:
            pass

    def confocal_mouseMoved(self, pos):
        if self.plt_confocal.sceneBoundingRect().contains(pos):
            mousePoint = self.vb_confocal.mapSceneToView(pos)
            self.confocal_vLine.setPos(mousePoint.x())
            self.confocal_hLine.setPos(mousePoint.y())

    def confocal_load(self):
        documents_path = os.path.expanduser(os.path.join('~', 'Documents', 'data_mat'))
        fd = QtWidgets.QFileDialog(directory=documents_path)
        targetfile = fd.getOpenFileName(filter='mat files (*.mat)')
        targetfile = targetfile[0]

        if targetfile != '':
            matfile = scipy.io.loadmat(targetfile)

            self.confocal_pl = matfile['pl']
            self.confocal_rngx = np.squeeze(matfile['xvals'])
            self.confocal_rngy = np.squeeze(matfile['yvals'])

            self.plt_confocal.setTitle(targetfile)
            self.confocal_initplot()

    def camera_updateplot(self, image):
        xpos = self.dbl_tracker_xpos.value()
        ypos = self.dbl_tracker_ypos.value()

        if not self.chkbx_camera_loglevel.isChecked():
            self.qtimg_camera.setImage(image)
        else:
            self.qtimg_camera.setImage(np.log10(image + 1.1))

        fov = tuple(self.inst_params['camera']['fov'])
        self.qtimg_camera.setRect(xpos-fov[0]/2, ypos-fov[1]/2, fov[0], fov[1])

    def camera_loglevel(self, checked):
        if not checked: self.hlw_camera.item.setLevels(0, 255)
        else: self.hlw_camera.item.setLevels(np.log10(255), 0)

    def camera_select(self, checked):
        if checked:  # enable cursor
            self.camera_vLine.show()
            self.camera_hLine.show()
            self.plt_camera.scene().sigMouseMoved.connect(self.camera_mouseMoved)
            self.plt_camera.scene().sigMouseClicked.connect(self.camera_clicked_drive)
        else:  # disable cursor here
            self.camera_vLine.hide()
            self.camera_hLine.hide()
            try:
                self.plt_camera.scene().sigMouseMoved.disconnect()
            except TypeError:
                pass
            try:
                self.plt_camera.scene().sigMouseClicked.disconnect()
            except TypeError:
                pass

    def camera_mouseMoved(self, pos):
        if self.plt_camera.sceneBoundingRect().contains(pos):
            mousePoint = self.vb_camera.mapSceneToView(pos)
            self.camera_vLine.setPos(mousePoint.x())
            self.camera_hLine.setPos(mousePoint.y())

    def camera_clicked_drive(self, event):
        pos = event.scenePos()
        if self.plt_camera.sceneBoundingRect().contains(pos) and event.button() == QtCore.Qt.MouseButton.LeftButton:
            mousePoint = self.vb_camera.mapSceneToView(pos)
            self.dbl_tracker_xpos.setValue(mousePoint.x())
            self.dbl_tracker_ypos.setValue(mousePoint.y())
            self.tracker_drive()
        else:
            pass

    def camera_set_autolevel(self, b):
        self.hlw_camera.item.autoLevel = bool(b)

    def camera_start(self, checked):
        if checked:
            self.chkbx_camera_autolevel.setChecked(False)
            self.hlw_camera.item.setLevels(0, 255)
            self.thread_camera.start()

    def camera_start_roi(self):
        print('TO IMPLEMENT: relative ROI')

        roi = self.vb_camera.roi
        if roi and roi.isVisible():
            self.confocal_set_roi(roi)
            self.confocal_start()
        else:
            raise Exception('No ROI Selected')
        
    def tracker_drive(self):
        xpos = self.dbl_tracker_xpos.value()
        ypos = self.dbl_tracker_ypos.value()

        self.thread_confocal.set_pos(xpos, ypos)

        self.confocal_cursor.show()
        self.confocal_cursor.setData([xpos], [ypos])

        self.camera_cursor.setData([xpos], [ypos])
        # fov = tuple(self.inst_params['camera']['fov'])
        # self.qtimg_camera.resetTransform()
        # self.qtimg_camera.setRect(xpos-fov[0]/2, ypos-fov[1]/2, fov[0], fov[1])
        processEvents()

    def tracker_home(self):
        self.dbl_tracker_xpos.setValue(0)
        self.dbl_tracker_ypos.setValue(0)
        self.tracker_drive()

    def tracker_start(self):
        self.plt_liveapd.setVisible(False)
        self.plt_focus_score.setVisible(True)
        self.thread_tracker.start()

    def tracker_updateplot(self):
        self.curve_focus_score.setData(self.focus_zpos, self.focus_score)
        self.curve_focus_score_fit.setData(self.focus_zpos_fit, self.focus_score_fit)

    def set_zpos_stepsize(self, value):
        stepsize = float(value.split()[1])
        self.dbl_tracker_zpos.setSingleStep(stepsize)

    def liveapd_start(self):
        self.plt_focus_score.hide()
        self.plt_liveapd.show()
        self.thread_liveapd.start()

    def liveapd_stop(self):
        self.thread_liveapd.cancel = True

    def liveapd_updateplots(self, acqtime, pl):
        self.liveapd_pl = np.append(self.liveapd_pl, pl)

        if not len(self.liveapd_t):
            self.liveapd_t = np.append(self.liveapd_t, 0)
        else:
            self.liveapd_t = np.append(self.liveapd_t, self.liveapd_t[-1] + acqtime)

        self.curve_liveapd.setData(self.liveapd_t, self.liveapd_pl)
        processEvents()

    def liveapd_clear(self):
        self.liveapd_pl = np.array([])
        self.liveapd_t = np.array([])
        self.curve_liveapd.setData([], [])

    def liveapd_grab_screenshots(self):
        self.pixmap_liveapd_graph = self.widget_liveapd.grab()
        processEvents()

    def focus_score_updateplot(self, zindex):
        if len(self.focus_zpos) > 1:
            self.plt_liveapd.setVisible(False)
            self.plt_focus_score.setVisible(True)
            self.curve_focus_score.setData(self.focus_zpos, self.focus_score)
            self.curve_focus_score_fit.setData(self.focus_zpos_fit, self.focus_score_fit)
        else:
            self.plt_focus_score.setVisible(False)
            self.plt_liveapd.setVisible(True)

    def task_analyze(self):
        data = {'pl': np.squeeze(self.confocal_pl),
                'xvals': np.squeeze(self.confocal_rngx),
                'yvals': np.squeeze(self.confocal_rngy)}

        best_angle = akl_image_processing.find_stripe_angle(data)
        params = akl_image_processing.search_stripe_pattern(data, best_angle)

        masks = akl_image_processing.generate_stripe_mask(data, best_angle, *params)

        print(best_angle, *params)

        start_x = data['xvals'][0]
        stop_x = data['xvals'][-1]
        start_y = data['yvals'][0]
        stop_y = data['yvals'][-1]

        px = (stop_x - start_x) / (len(data['xvals']) - 1)
        py = (stop_y - start_y) / (len(data['yvals']) - 1)

        for i in range(5):
            mask = masks[i]
            qtimg = getattr(self, f'qtimg_mask_{i}')
            setattr(self, f'mask_{i}', mask)
            qtimg.setImage(mask)
            qtimg.resetTransform()
            qtimg.setRect(start_x-px/2, start_y-py/2, (stop_x - start_x)+px, (stop_y - start_y)+py)

            if i in [0, 1, 4]:
                iso = getattr(self, f'iso_mask_{i}')
                iso.setData(mask)

        metrics = akl_image_processing.analyze_image(data, masks)

    def task_set_automate(self, checked):
        self.set_gui_btn_enable('all', not checked)
        self.set_gui_btn_enable('automation', checked)
        if checked:
            self.task_reset()
        else:
            self.statusBar().clearMessage()

    def task_reset(self):
        self.task_progressbar.value = 0
        self.int_sample_loc.setValue(0)
        self.linein_sample_comment.setText('')
        self.task_action()
        self.statusBar().showMessage('Load plate and adjust Wyko stage to the desired position.')
        self.linein_sample_name.setFocus()
        self.linein_sample_name.selectAll()

    def task_action(self):
        value = self.task_progressbar.value
        if value == 0:
            self.int_sample_loc.setValue(self.int_sample_loc.value() + 1)
            # Load Plate
            self.btn_camera_start.setChecked(True)
            self.illum_set('laser', False)
            self.illum_set('led', True)
            self.piezo.SetPosition(225.0)
            self.statusBar().showMessage('Adjust Wyko stage to the desired position.')
        elif value == 1:
            # Check Laser Position
            self.illum_set('laser', True)
            self.illum_set('led', True)
            self.piezo.SetPosition(225.0)
            self.statusBar().showMessage('Adjust laser position to land center.')
        elif value == 2:
            # Autofocus
            self.tracker_start()
            self.statusBar().showMessage('Check Autofocus.')
        elif value == 3:
            # Image Check
            self.illum_set('laser', False)
            self.illum_set('led', True)
            self.statusBar().showMessage('Check Autofocus.')
        elif value == 4:
            # Confocal Scan
            self.illum_set('laser', True)
            self.illum_set('led', False)
            self.confocal_start()
            self.statusBar().showMessage('Performing Confocal Scan.')
        elif value == 5:
            # Check Land Areas and Perform Analysis
            self.task_analyze()
            self.statusBar().showMessage('Check land areas for analysis.')
        elif value == 6:
            self.statusBar().showMessage('Analysis completed. Press CONTINUE to scan another area. Press PLATE RESET to scan a new plate.')

    def task_forward(self):
        self.task_progressbar.value = (self.task_progressbar.value + 1) % (len(self.task_progressbar.labels) + 1)
        self.task_action()

    def task_backward(self):
        value = self.task_progressbar.value
        if value in [2, 3, 4]:
            self.task_progressbar.value = 1
        else:
            self.task_progressbar.value = 0
        self.task_action()

    def import_gui_settings(self, data):
        # refill the linein, double, and integer fields in the gui
        for attr in self.__dict__.keys():
            if 'linein' in attr:
                if attr in data['linein'].keys():
                    getattr(self, attr).setText(data['linein'][attr])
            if 'dbl_' in attr:
                if attr in data['dbl'].keys():
                    getattr(self, attr).setValue(data['dbl'][attr])
            if 'int_' in attr:
                if attr in data['int'].keys():
                    getattr(self, attr).setValue(data['int'][attr])

    def export_gui_settings(self):
        outdata = {'linein': {}, 'dbl': {}, 'int': {}, 'img': []}
        if len(self.confocal_pl) > 0:
            outdata['img'] = self.confocal_pl[:, :, 0]
        for attr in self.__dict__.keys():
            if 'linein' in attr:
                outdata['linein'][attr] = getattr(self, attr).text()
            elif 'dbl_' in attr:
                outdata['dbl'][attr] = getattr(self, attr).value()
            elif 'int_' in attr:
                outdata['int'][attr] = getattr(self, attr).value()

        file_utils.save_config(outdata, path=os.path.expanduser(os.path.join('~', 'Documents', 'exp_config')))

    def set_gui_defaults(self):
        self.set_gui_btn_enable('all', True)
        self.set_gui_btn_enable('automation', False)
        self.set_gui_input_enable('all', True)
        self.btn_confocal_live.setChecked(False)
        # Disable the stop buttons
        self.btn_confocal_stop.setEnabled(self.thread_confocal.isRunning())
        self.btn_liveapd_stop.setEnabled(self.thread_liveapd.isRunning())

    def set_gui_btn_enable(self, section, bool_set):
        groups = {
            'confocal': [self.btn_confocal_start, self.btn_confocal_live,
                         self.btn_confocal_start_preset1, self.btn_confocal_start_preset2,
                         self.btn_confocal_roi_undo, self.btn_confocal_start_roi,
                         self.btn_confocal_acqtime_inc, self.btn_confocal_acqtime_dec,
                         self.btn_confocal_select, self.btn_camera_select,
                         self.btn_camera_start_roi],
            'liveapd': [self.btn_liveapd_start, self.btn_liveapd_clear],
            'tracker': [self.btn_tracker_home, self.btn_tracker_autofocus],
            'analysis': [self.btn_task_analyze],
            'automation': [self.btn_task_forward, self.btn_task_backward, self.btn_task_reset]
        }

        # Iterate through the requested sections
        for key, buttons in groups.items():
            if section == 'all' or section == key:
                for btn in buttons:
                    btn.setEnabled(bool_set)

        if self.btn_task_automate.isChecked():
            for btn in groups['automation']:
                btn.setEnabled(True)


    def set_gui_input_enable(self, section, bool_set):
        # 1. Define the standard groups
        groups = {
            'confocal': [
                self.dbl_confocal_x_start, self.dbl_confocal_x_stop,
                self.int_confocal_x_numdivs, self.dbl_confocal_y_start,
                self.dbl_confocal_y_stop, self.btn_confocal_pxsync,
                self.dbl_confocal_z_start, self.dbl_confocal_z_stop,
                self.int_confocal_z_numdivs, self.dbl_confocal_acqtime
            ],
            # Add other sections (liveapd, tracker, etc.) here as needed
        }

        # 2. Batch process the standard widgets
        for key, widgets in groups.items():
            if section == 'all' or section == key:
                for widget in widgets:
                    widget.setEnabled(bool_set)

        # 3. Handle the "Special" Conditional Logic
        if section in ['confocal', 'all']:
            # Logic for Y-divisions depends on the Sync button
            y_sync_active = self.btn_confocal_pxsync.isChecked()
            self.int_confocal_y_numdivs.setEnabled(bool_set and not y_sync_active)

            # Logic for Z-stack depends on whether there are slices defined
            z_has_slices = bool(self.int_confocal_z_numdivs.value())
            self.confocal_zstack_enable(bool_set and z_has_slices)

    def closeEvent(self, *args, **kwargs):
        if self.thread_camera.isRunning():
            self.btn_camera_start.setChecked(False)
            time.sleep(0.5)
        self.piezo.Disconnect()
        self.export_gui_settings()

    def log(self, text):
        print(text) # todo

    def log_clear(self):
        pass


class CameraThread(QThread):
    frame_signal = Signal(np.ndarray)

    def __init__(self, mainexp):
        super().__init__()
        self.mainexp = mainexp

    def run(self):
        fps = 10
        self.cap = cv2.VideoCapture(int(self.mainexp.cbox_camera_list.currentText().split(':', 1)[0]))
        while self.cap.isOpened() and self.mainexp.btn_camera_start.isChecked():
            try:
                ret, frame = self.cap.read()
                if ret:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    self.frame_signal.emit(cv2.transpose(cv2.flip(frame, 0))) # transpose and flip to plot on normal coordinates
                time.sleep(1/fps)
            except cv2.error:
                print('Camera Error!')
                break

def processEvents():
    QtWidgets.QApplication.processEvents()


def main():
    """Packaged main function that launches GUI"""

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == '__main__':
    main()

