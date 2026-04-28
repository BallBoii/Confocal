# Thorlabs Kinesis Imports
import clr, time
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericPiezoCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\ThorLabs.MotionControl.Benchtop.PrecisionPiezoCLI.dll")

from Thorlabs.MotionControl.DeviceManagerCLI import *
from Thorlabs.MotionControl.GenericPiezoCLI import *
from Thorlabs.MotionControl.GenericPiezoCLI import Piezo
from Thorlabs.MotionControl.Benchtop.PrecisionPiezoCLI import *
from System import Decimal  # necessary for real world units

DeviceManagerCLI.BuildDeviceList() # todo: check whether this works


class PFM450E:
    def __init__(self, serialNumber):
        self.current_pos = 0.0
        serialNumber = str(serialNumber)
        try:
            self.device = BenchtopPrecisionPiezo.CreateBenchtopPiezo(serialNumber)
            self.device.Connect(serialNumber)
            # Because this is a benchtop controller we need a channel object
            self.channel = self.device.GetChannel(1)

            # Ensure that the device settings have been initialized
            if not self.channel.IsSettingsInitialized():
                self.channel.WaitForSettingsInitialized(10000)  # 10 second timeout
                assert self.channel.IsSettingsInitialized() is True

            # Start polling and enable
            self.channel.StartPolling(250)  # 250ms polling rate
            time.sleep(0.25)
            self.channel.EnableDevice()
            time.sleep(0.25)  # Wait for device to enable
        except:
            print('Cannot connect to Piezo Controller: ', serialNumber)
            self.channel = None

    def SetPosition(self, position):
        if self.channel:
            self.channel.SetPosition(Decimal(position))
        self.current_pos = position

    def GetPosition(self):
        return self.current_pos

    def Disconnect(self):
        if self.channel:
            self.channel.StopPolling()
            self.channel.Disconnect()





