from PyQt6 import QtGui, QtCore, QtWidgets, uic

# system imports
import sys, os, struct, scipy.io, warnings, functools, time, datetime
import pyqtgraph as pg
import PyDAQmx
import pdb
import numpy as np
import csv

# user-defined imports
import file_utils
import instruments as instr
import experiments as exp

# import UI files
import mainexp as mainwindow
import mainexp_widgets

import qdarkstyle

def my_excepthook(type, value, tback):
    sys.__excepthook__(type, value, tback)


sys.excepthook = my_excepthook


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

class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        uic.loadUi("gui/confocal.ui", self)

        # configure PyQTgraph to use white background
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')


        self.setFixedSize(self.size())


def main():
    """Packaged main function that launches GUI"""

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == '__main__':
    main()

