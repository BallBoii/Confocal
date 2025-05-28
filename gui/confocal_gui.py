from PyQt6 import QtCore, QtWidgets, uic
from PyQt6.QtCore import QThread, pyqtSignal as Signal
import cv2
from cv2_enumerate_cameras import enumerate_cameras

# system imports
import sys, os, scipy.io, warnings, functools
import pyqtgraph as pg
import numpy as np

# user-defined imports
import file_utils
import experiments as exp
import instruments

# import UI files
import mainexp_widgets


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


        self.setFixedSize(self.size())

        # this will ensure that the application quits at the right time,
        # and that Qt has a chance to automatically delete all the children of the top-level window
        # before the python garbage-collector gets to work.
        # http://stackoverflow.com/questions/27131294/error-qobjectstarttimer-qtimer-can-only-be-used-with-threads-started-with-qt
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        self.mutex = QtCore.QMutex()
        self.wait_confocal = QtCore.QWaitCondition()
        self.wait_liveapd = QtCore.QWaitCondition()

        '''INSTRUMENT INITIALIZATION'''
        instpath = os.path.expanduser(os.path.join('~', 'Documents', 'exp_config', 'confocal_params.yaml'))
        if os.path.isfile(instpath):
            self.inst_params = file_utils.yaml2dict(instpath)
        else:
            print('inst_params.yaml not found!')

        '''WORKER THREADS INITIALIZATION'''
        self.thread_confocal = exp.Confocal.Confocal(self, self.wait_confocal)
        self.thread_liveapd = exp.APD.LiveAPD(self, self.wait_liveapd)

        '''CONFOCAL'''
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
        self.btn_confocal_save.clicked.connect(functools.partial(self.thread_confocal.save, ext=True))
        self.btn_confocal_pxsync.clicked.connect(self.confocal_pxsync)
        self.chkbx_confocal_autolevel.setChecked(True)
        self.chkbx_confocal_autolevel.stateChanged.connect(self.confocal_set_autolevel)
        self.btn_confocal_acqtime_inc.clicked.connect(self.confocal_acqtime_inc)
        self.btn_confocal_acqtime_dec.clicked.connect(self.confocal_acqtime_dec)

        self.confocal_mode = 0
        self.confocal_rngx = []
        self.confocal_rngy = []
        self.confocal_rngz = []
        self.confocal_pl = np.array([])

        self.confocal_settings = []  # for storing previous scans [(xmin, max, ymin, ymax)]

        # Create PyQtGraph plots and histogram for confocal scans
        for name in ['confocal', 'map']:
            setattr(self, 'vb_%s' % name, mainexp_widgets.ViewBoxWithROI())
            setattr(self, 'plt_%s' % name, pg.PlotItem(viewBox=getattr(self, 'vb_%s' % name)))
            setattr(self, 'qtimg_%s' % name, pg.ImageItem())
            getattr(self, 'vb_%s' % name).addItem(getattr(self, 'qtimg_%s' % name))
            getattr(self, 'plt_%s' % name).setLabels(bottom='xpos (&mu;m)', left='ypos (&mu;m)')

        '''CONFOCAL PLOTS'''
        self.glw_confocal = pg.GraphicsLayoutWidget()
        self.glw_confocal.addItem(self.plt_confocal, 0, 0)
        self.hlw_confocal = mainexp_widgets.CustomLUTWidget(image=self.qtimg_confocal)
        self.hlw_confocal.gradient.setColorMap(cm)

        self.grid_confocal.addWidget(self.glw_confocal, 0, 0)
        self.grid_confocal.addWidget(self.hlw_confocal, 0, 1)

        self.int_confocal_z_numdivs.valueChanged.connect(self.confocal_zstack_enable)
        self.confocal_zstack_enable(self.int_confocal_z_numdivs.value())

        '''MAP PLOTS'''
        self.glw_map = pg.GraphicsLayoutWidget()
        self.glw_map.addItem(self.plt_map, 0, 0)
        self.hlw_map = mainexp_widgets.CustomLUTWidget(image=self.qtimg_map)
        self.hlw_map.gradient.setColorMap(cm)

        self.grid_map.addWidget(self.glw_map, 0, 0)
        self.grid_map.addWidget(self.hlw_map, 0, 1)

        self.map_vLine = pg.InfiniteLine(angle=90, movable=False, pen=(0, 150, 100))
        self.map_hLine = pg.InfiniteLine(angle=0, movable=False, pen=(0, 150, 100))
        self.map_vLine.hide()
        self.map_hLine.hide()

        self.map_cursor = pg.ScatterPlotItem(pen=pg.mkPen('w', width=2), brush=None, symbol='o', size=7)
        self.map_cursor.setData([0], [0])
        self.map_nvlist = pg.ScatterPlotItem(pen=pg.mkPen((0, 150, 100), width=2), brush=None, symbol='o', size=7)
        self.map_nvlist.setData([], [])
        self.map_nvlabels = [] # for storing NV numbers

        self.plt_map.addItem(self.map_nvlist)
        self.plt_map.addItem(self.map_cursor)
        self.plt_map.addItem(self.map_vLine, ignoreBounds=True)
        self.plt_map.addItem(self.map_hLine, ignoreBounds=True)

        self.btn_map_load.clicked.connect(self.map_load)
        self.btn_map_copy.clicked.connect(self.map_copy)
        self.btn_map_select.clicked.connect(self.map_select)
        self.btn_map_start_roi.clicked.connect(self.map_start_roi)
        self.btn_map_home.clicked.connect(self.tracker_home)
        self.chkbx_map_autolevel.setChecked(True)
        self.chkbx_map_autolevel.stateChanged.connect(self.map_set_autolevel)
        self.btn_map_camera.clicked.connect(self.map_camera_start)

        self.map_data = np.array([])
        self.map_ax_xmin = 0
        self.map_ax_xmax = 0
        self.map_ax_ymin = 0
        self.map_ax_ymax = 0

        '''PIEZO CONTROL'''
        serial_no = "44506394"
        self.piezo = instruments.ThorlabsPiezo.PFM450E(serial_no)
        self.piezo.SetPosition(0.0)

        self.cbox_zpos_stepsize.addItems(['Z: 10 μm', 'Z:  1 μm', 'Z: 0.1 μm'])
        self.cbox_zpos_stepsize.currentTextChanged.connect(self.set_zpos_stepsize)
        self.dbl_tracker_zpos.valueChanged.connect(self.piezo.SetPosition)

        '''Camera Thread'''
        self.cbox_camera_list.addItems([str(cam).rsplit(' ', 1)[0] for cam in enumerate_cameras(cv2.CAP_MSMF)])
        self.thread_camera = CameraThread(self)
        self.thread_camera.frame_signal.connect(self.map_set_camera_image)

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
        if self.btn_map_select.isChecked():
            self.btn_map_select.setChecked(False)

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

    def map_start_roi(self):
        roi = self.vb_map.roi
        if roi and roi.isVisible():
            self.confocal_set_roi(roi)
            self.confocal_start()
        else:
            raise Exception('No ROI Selected')

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
            warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
            start_x = self.confocal_rngx[0]
            stop_x = self.confocal_rngx[-1]
            start_y = self.confocal_rngy[0]
            stop_y = self.confocal_rngy[-1]

            self.qtimg_confocal.setImage(self.confocal_pl[:, :, 0])

            for name in ['confocal']:
                qtimg = getattr(self, 'qtimg_%s' % name)
                qtimg.resetTransform()  # need to call this. otherwise pos and scale are relative to previous
                qtimg.setRect(start_x, start_y, (stop_x - start_x), (stop_y - start_y))

            if self.confocal_mode == 0:
                self.plt_confocal.setLabels(bottom='xpos (&mu;m)', left='ypos (&mu;m)')
            if self.confocal_mode == 1:
                self.plt_confocal.setLabels(bottom='xpos (&mu;m)', left='zpos (&mu;m)')
            if self.confocal_mode == 2:
                self.plt_confocal.setLabels(bottom='ypos (&mu;m)', left='zpos (&mu;m)')

            processEvents()

    def confocal_updateplot(self, zindex=0):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')

            self.qtimg_confocal.setImage(self.confocal_pl[:, :, zindex])
            # self.hlw_confocal.setImageItem(self.qtimg_confocal)

            if self.confocal_mode == 0:
                filename = 'IMG_%04d' % self.wavenum

                self.plt_confocal.setTitle('%s: Z = %.2f' % (filename, self.confocal_rngz[zindex]))
                self.label_filename.setText(filename)

    def confocal_set_autolevel(self, b):
        self.hlw_confocal.item.autoLevel = bool(b)

    def confocal_grab_screenshots(self):
        self.pixmap_confocal_graph = self.frame_main.grab()
        self.pixmap_confocal_fig = self.frame_main.grab()
        processEvents()

    def map_load(self):
        documents_path = os.path.expanduser(os.path.join('~', 'Documents', 'data_mat'))
        fd = QtWidgets.QFileDialog(directory=documents_path)
        targetfile = fd.getOpenFileName(filter='mat files (*.mat)')

        targetfile = targetfile[0]
        if targetfile != '':
            matfile = scipy.io.loadmat(targetfile)

            xvals = matfile['xvals'][0]
            yvals = matfile['yvals'][0]
            pl = np.squeeze(matfile['pl'])

            self.map_ax_xmin = xvals[0]
            self.map_ax_xmax = xvals[-1]
            self.map_ax_ymin = yvals[0]
            self.map_ax_ymax = yvals[-1]

            self.map_data = pl

            self.linein_map.setText(targetfile.split('/')[-1].split('.mat')[0])
            self.map_updateplot()

    def map_copy(self):
        if self.vb_map.roi:
            self.vb_map.roi.hide()

        self.map_ax_xmin = self.dbl_confocal_x_start.value()
        self.map_ax_xmax = self.dbl_confocal_x_stop.value()
        self.map_ax_ymin = self.dbl_confocal_y_start.value()
        self.map_ax_ymax = self.dbl_confocal_y_stop.value()

        self.map_data = self.confocal_pl[:, :, 0]

        self.linein_map.setText(self.label_filename.text())
        self.chkbx_map_autolevel.setChecked(True)
        self.map_updateplot()

    def map_select(self, checked):
        if checked:  # enable cursor
            self.map_connect(self.map_clicked_drive)
        else:  # disable cursor here
            self.map_disconnect()

    def map_updateplot(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)  # for ignoring warnings when plotting NaNs

            if len(self.map_data) > 0:
                self.qtimg_map.setImage(self.map_data)
                self.qtimg_map.resetTransform()
                self.qtimg_map.setRect(self.map_ax_xmin, self.map_ax_ymin, (self.map_ax_xmax - self.map_ax_xmin), (self.map_ax_ymax - self.map_ax_ymin))

                # self.hlw_map.setImageItem(self.qtimg_map)

                processEvents()

    def map_mouseMoved(self, pos):
        if self.plt_map.sceneBoundingRect().contains(pos):
            mousePoint = self.vb_map.mapSceneToView(pos)
            self.map_vLine.setPos(mousePoint.x())
            self.map_hLine.setPos(mousePoint.y())

    def map_updatecursor(self):
        self.map_cursor.setData([self.dbl_tracker_xpos.value()], [self.dbl_tracker_ypos.value()])
        processEvents()

    def map_clicked_drive(self, event):
        if self.map_clicked_pos(event):
            self.tracker_drive()

    def map_clicked_pos(self, event):
        pos = event.scenePos()
        if self.plt_map.sceneBoundingRect().contains(pos) and event.button() == QtCore.Qt.MouseButton.LeftButton:
            mousePoint = self.vb_map.mapSceneToView(pos)
            self.dbl_tracker_xpos.setValue(mousePoint.x())
            self.dbl_tracker_ypos.setValue(mousePoint.y())
            self.dbl_tracker_zpos.setValue(self.dbl_tracker_zpos.value())
            return True
        else:
            return False

    def map_connect(self, clicked_func):
        self.map_vLine.show()
        self.map_hLine.show()
        self.plt_map.scene().sigMouseMoved.connect(self.map_mouseMoved)
        self.plt_map.scene().sigMouseClicked.connect(clicked_func)

    def map_disconnect(self):
        self.map_vLine.hide()
        self.map_hLine.hide()
        try:
            self.plt_map.scene().sigMouseMoved.disconnect()
        except TypeError:
            pass
        try:
            self.plt_map.scene().sigMouseClicked.disconnect()
        except TypeError:
            pass

    def map_set_autolevel(self, b):
        self.hlw_map.item.autoLevel = bool(b)

    def map_camera_start(self):
        self.chkbx_map_autolevel.setChecked(True)
        self.thread_camera.start()
    def map_set_camera_image(self, image):
        xscale = 1000/128/50
        yscale = 1000/128/75
        self.qtimg_map.setImage(image)
        self.qtimg_map.setRect(-320*xscale, -240*yscale, 640*xscale, 480*yscale)

    def tracker_drive(self):
        xpos = self.dbl_tracker_xpos.value()
        ypos = self.dbl_tracker_ypos.value()
        zpos = self.dbl_tracker_zpos.value()

        self.thread_confocal.set_pos(xpos, ypos)

        self.map_updatecursor()

    def tracker_home(self):
        self.dbl_tracker_xpos.setValue(0)
        self.dbl_tracker_ypos.setValue(0)
        self.dbl_tracker_zpos.setValue(5)
        self.tracker_drive()

    def set_zpos_stepsize(self, value):
        stepsize = float(value.split()[1])
        self.dbl_tracker_zpos.setSingleStep(stepsize)

    def liveapd_start(self):
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
            if 'map_ax' in attr:
                if attr in data['map_ax'].keys():
                    setattr(self, attr, data['map_ax'][attr])

        try:
            self.map_data = data['map']
            self.map_updateplot()
        except KeyError:
            print('no confocal map loaded')
            pass

    def export_gui_settings(self):
        outdata = {'linein': {}, 'dbl': {}, 'int': {}, 'img': [], 'map': [], 'map_ax': {}}
        if len(self.confocal_pl) > 0:
            outdata['img'] = self.confocal_pl[:, :, 0]
        if len(self.map_data) > 0:
            outdata['map'] = self.map_data
        for attr in self.__dict__.keys():
            if 'linein' in attr:
                outdata['linein'][attr] = getattr(self, attr).text()
            elif 'dbl_' in attr:
                outdata['dbl'][attr] = getattr(self, attr).value()
            elif 'int_' in attr:
                outdata['int'][attr] = getattr(self, attr).value()
            elif 'map_ax_' in attr:
                outdata['map_ax'][attr] = getattr(self, attr)

        file_utils.save_config(outdata, path=os.path.expanduser(os.path.join('~', 'Documents', 'exp_config')))

    def set_gui_defaults(self):
        self.set_gui_btn_enable('all', True)
        self.set_gui_input_enable('all', True)
        self.btn_confocal_live.setChecked(False)
        # Disable the stop buttons
        self.btn_confocal_stop.setEnabled(self.thread_confocal.isRunning())
        self.btn_liveapd_stop.setEnabled(self.thread_liveapd.isRunning())

    def set_gui_btn_enable(self, section, bool_set):
        if section in ['confocal', 'all']:
            self.btn_confocal_start.setEnabled(bool_set)
            self.btn_confocal_start_preset1.setEnabled(bool_set)
            self.btn_confocal_start_preset2.setEnabled(bool_set)
            self.btn_confocal_live.setEnabled(bool_set)
            self.btn_confocal_roi_undo.setEnabled(bool_set)
            self.btn_confocal_start_roi.setEnabled(bool_set)
            self.btn_map_start_roi.setEnabled(bool_set)
        if section in ['liveapd', 'all']:
            self.btn_liveapd_start.setEnabled(bool_set)
            self.btn_liveapd_clear.setEnabled(bool_set)

    def set_gui_input_enable(self, section, bool_set):
        if section in ['confocal', 'all']:
            self.dbl_confocal_x_start.setEnabled(bool_set)
            self.dbl_confocal_x_stop.setEnabled(bool_set)
            self.int_confocal_x_numdivs.setEnabled(bool_set)
            self.dbl_confocal_y_start.setEnabled(bool_set)
            self.dbl_confocal_y_stop.setEnabled(bool_set)
            self.btn_confocal_pxsync.setEnabled(bool_set)
            self.int_confocal_y_numdivs.setEnabled(bool_set and not self.btn_confocal_pxsync.isChecked())
            self.dbl_confocal_z_start.setEnabled(bool_set)
            self.dbl_confocal_z_stop.setEnabled(bool_set)
            self.int_confocal_z_numdivs.setEnabled(bool_set)
            self.dbl_confocal_acqtime.setEnabled(bool_set)
            self.confocal_zstack_enable(bool(self.int_confocal_z_numdivs.value()) and bool_set)

    def closeEvent(self, *args, **kwargs):
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
        self.cap = cv2.VideoCapture(int(self.mainexp.cbox_camera_list.currentText().split(':', 1)[0]))
        while self.cap.isOpened() and self.mainexp.btn_map_camera.isChecked():
            try:
                _, frame = self.cap.read()
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self.frame_signal.emit(cv2.transpose(cv2.flip(frame, 0))) # transpose and flip to plot on normal coordinates
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

