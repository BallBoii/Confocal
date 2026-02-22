from PyQt6 import QtGui, QtCore, QtWidgets

# system imports
import weakref, datetime, pytz
import pyqtgraph as pg
import numpy as np
import functools


class ViewBoxWithROI(pg.ViewBox):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drawing = False
        self.roi = None
        self.pos = None

        self.roiMenu()

    def mouseMoveEvent(self, event):
        if self.drawing:
            delta = self.mapSceneToView(self.pos) - self.mapSceneToView(event.scenePos())
            self.roi.setSize([self._adjustValue(- delta.x()), self._adjustValue(- delta.y())])
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing:
            self.pos = None
            self.drawing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.drawing:
            self.pos = event.scenePos()
            if not self.roi:
                roi = pg.RectROI(self.mapSceneToView(self.pos), (1, 1), removable=False, invertible=True, rotatable=False)
                self.addItem(roi)
                self.roi = roi
            else:
                self.roi.show()
                self.roi.setPos(self.mapSceneToView(self.pos))
                self.roi.setSize((1, 1))
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def roiMenu(self):
        rect = QtGui.QAction(u'Draw ROI', self)
        rect.setCheckable(True)
        rect.toggled.connect(self.drawRect)
        self.menu.addAction(rect)

    def drawRect(self, b):
        if b:
            self.drawing = True
        else:
            self.roi.hide()

    @staticmethod
    def _adjustValue(x):
        if -1 < x < 1:
            return -1 if x < 0 else 1
        return x


class CustomLUTWidget(pg.GraphicsView):

    def __init__(self, parent=None, *args, **kargs):
        background = kargs.get('background', 'default')
        pg.GraphicsView.__init__(self, parent, useOpenGL=False, background=background)
        self.item = CustomLUTItem(*args, **kargs)
        self.setCentralItem(self.item)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(80)

        self.gradient.rectSize = 7  # width of color bar
        self.gradient.tickSize = 5

    def sizeHint(self):
        return QtCore.QSize(50, 200)

    def setLabel(self, text=None, units=None, unitPrefix=None, **args):
        self.item.axis.setLabel(text=text, units=units, unitPrefix=unitPrefix, **args)
        self.setMinimumWidth(60)
        self.axis.setWidth(15)

    def __getattr__(self, attr):
        return getattr(self.item, attr)


class CustomLUTItem(pg.HistogramLUTItem):
    def __init__(self, image=None, fillHistogram=True):
        self.autoLevel = True
        super().__init__(image=image, fillHistogram=fillHistogram)
        self.vb.setMinimumWidth(10)  # width of the actual histogram
        self.vb.setContentsMargins(0.01, 0.01, 0.01, 0.01)

    def setImageItem(self, img_list):
        # Clear old signals:
        if type(self.imageItem) is not list:
            if callable(self.imageItem):
                if  self.imageItem() is not None:
                    self.imageItem().sigImageChanged.disconnect()
            elif self.imageItem is not None:
                self.imageItem.sigImageChanged.disconnect()
        else:
            for img in self.imageItem:
                img().sigImageChanged.disconnect()

        # allow setting array of images
        if type(img_list) is not list:
            self.imageItem = weakref.ref(img_list)
            img_list.sigImageChanged.connect(functools.partial(self.imageChanged, autoLevel=True))
            img_list.setLookupTable(self.getLookupTable)  ## send function pointer, not the result
            # self.gradientChanged()
            self.regionChanged()
        else:
            self.imageItem = []
            for img in img_list:
                self.imageItem.append(weakref.ref(img))
                img.sigImageChanged.connect(functools.partial(self.imageChanged, autoLevel=True))
                img.setLookupTable(self.getLookupTable)

            self.regionChanged()
        self.imageChanged(autoLevel=self.autoLevel)

    def gradientChanged(self):
        if self.imageItem is not None:
            if self.gradient.isLookupTrivial():
                lut = None  # lambda x: x.astype(np.uint8))
            else:
                lut = self.getLookupTable  ## send function pointer, not the result

            if type(self.imageItem) is not list:
                if self.imageItem() is not None:
                    self.imageItem().setLookupTable(lut)
            else:
                for img in self.imageItem:
                    img().setLookupTable(lut)

        self.lut = None
        # if self.imageItem is not None:
        # self.imageItem.setLookupTable(self.gradient.getLookupTable(512))
        self.sigLookupTableChanged.emit(self)

    def updateImageRegion(self):
        if self.imageItem is not None:
            if type(self.imageItem) is not list:
                if self.imageItem() is not None:
                    self.imageItem().setLevels(self.region.getRegion())
            else:
                for img in self.imageItem:
                    img().setLevels(self.region.getRegion())

    def regionChanged(self):
        self.updateImageRegion()
        self.sigLevelChangeFinished.emit(self)

    def regionChanging(self):
        self.updateImageRegion()
        self.sigLevelsChanged.emit(self)
        self.update()

    def imageChanged(self, autoLevel=False, autoRange=False):
        targetHistogramSize = 100
        if type(self.imageItem) is not list:
            h = self.imageItem().getHistogram(targetHistogramSize=targetHistogramSize)
        else:
            mns = []
            mxs = []

            for img in self.imageItem:
                if img().image is not None:
                    mns.append(np.nanmin(img().image))
                    mxs.append(np.nanmax(img().image))

            if mns and mxs:
                mn = np.nanmin(mns)
                mx = np.nanmax(mxs)
            else:
                mn = 0.0
                mx = 1.0

            if all(img().image is not None for img in self.imageItem):
                bins = np.linspace(mn, mx, targetHistogramSize)
                hist_total = np.linspace(0, 0, targetHistogramSize)

                for img in self.imageItem:
                    image_array = img().image
                    hist = np.histogram(image_array[~np.isnan(image_array)], bins=np.linspace(mn, mx, targetHistogramSize+1))
                    hist_total += hist[0]

                h = [bins, hist_total]
            else:
                h = [None, None]

        if h[0] is None:
            return
        self.plot.setData(*h)

        if autoLevel and self.autoLevel:
            mn = h[0][0]
            mx = h[0][-1]
            self.region.setRegion([mn, mx])
        self.updateImageRegion()