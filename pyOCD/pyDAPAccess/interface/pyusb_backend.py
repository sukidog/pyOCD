"""
 mbed CMSIS-DAP debugger
 Copyright (c) 2006-2013 ARM Limited

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

from interface import Interface
from ..dap_access_api import DAPAccessIntf
import logging, os, threading


try:
    import usb.core
    import usb.util
except:
    if os.name == "posix" and not os.uname()[0] == 'Darwin':
        logging.error("PyUSB is required on a Linux Machine")
    isAvailable = False
else:
    isAvailable = True

class PyUSB(Interface):
    """
    This class provides basic functions to access
    a USB HID device using pyusb:
        - write/read an endpoint
    """

    isAvailable = isAvailable

    def __init__(self, serial_number):
        super(PyUSB, self).__init__()
        self.ep_out = None
        self.ep_in = None
        self.dev = None
        self.closed = False
        self.rcv_data = []
        self.read_sem = threading.Semaphore(0)
        self._open = False
        self.serial_number = serial_number

    def open(self):
        assert self._open is False

        def match_self(dev):
            return dev.serial_number == self.serial_number
        dev = usb.core.find(custom_match=match_self)

        if dev is None:
            # Could not find the device
            raise DAPAccessIntf.Error()

        # get active config
        config = dev.get_active_configuration()

        # iterate on all interfaces:
        #    - if we found a HID interface -> CMSIS-DAP
        interface_number = -1
        for interface in config:
            if interface.bInterfaceClass == 0x03:
                interface_number = interface.bInterfaceNumber
                break

        if interface_number == -1:
            # Could not find interface for device
            raise DAPAccessIntf.Error()

        try:
            if dev.is_kernel_driver_active(interface_number):
                dev.detach_kernel_driver(interface_number)
        except Exception as e:
            print e

        ep_in, ep_out = None, None
        for ep in interface:
            if ep.bEndpointAddress & 0x80:
                ep_in = ep
            else:
                ep_out = ep

        """If there is no EP for OUT then we can use CTRL EP"""
        if not ep_in:
            # Endpoints not found
            raise DAPAccessIntf.Error()

        self.intf_number = interface_number

        self.ep_in = ep_in
        self.ep_out = ep_out

        self.vid = dev.idVendor
        self.pid = dev.idProduct
        self.product_name = dev.product
        self.vendor_name = dev.manufacturer

        self.thread = threading.Thread(target=self.rx_task)
        self.thread.daemon = True
        self.thread.start()
        self.dev = dev
        self._open = True

    def rx_task(self):
        while not self.closed:
            self.read_sem.acquire()
            if not self.closed:
                # Timeouts appear to corrupt data occasionally.  Because of this the
                # timeout is set to infinite.
                self.rcv_data.append(self.ep_in.read(self.ep_in.wMaxPacketSize, -1))

    @staticmethod
    def getInterface(device_id):
        return PyUSB(device_id)

    @staticmethod
    def getAllConnectedInterface():
        """
        returns all the connected devices which matches PyUSB.vid/PyUSB.pid.
        returns an array of PyUSB (Interface) objects
        """
        # find all devices matching the vid/pid specified
        all_devices = usb.core.find(find_all=True)

        if not all_devices:
            logging.debug("No device connected")

        boards = []

        # iterate on all devices found
        for board in all_devices:
            product = board.product
            if (product.find("CMSIS-DAP") < 0):
                # Not a cmsis-dap device so close it
                usb.util.dispose_resources(board)
                continue

            new_board = PyUSB(board.serial_number)
            # Close the board
            usb.util.dispose_resources(board)
            boards.append(new_board)

        return boards

    def write(self, data):
        """
        write data on the OUT endpoint associated to the HID interface
        """

        report_size = 64
        if self.ep_out:
            report_size = self.ep_out.wMaxPacketSize

        for _ in range(report_size - len(data)):
           data.append(0)

        self.read_sem.release()

        if not self.ep_out:
            bmRequestType = 0x21              #Host to device request of type Class of Recipient Interface
            bmRequest = 0x09              #Set_REPORT (HID class-specific request for transferring data over EP0)
            wValue = 0x200             #Issuing an OUT report
            wIndex = self.intf_number  #mBed Board interface number for HID
            self.dev.ctrl_transfer(bmRequestType, bmRequest, wValue, wIndex, data)
            return
            #raise ValueError('EP_OUT endpoint is NULL')

        self.ep_out.write(data)
        #logging.debug('sent: %s', data)
        return


    def read(self):
        """
        read data on the IN endpoint associated to the HID interface
        """
        while len(self.rcv_data) == 0:
            pass
        return self.rcv_data.pop(0)

    def setPacketCount(self, count):
        # No interface level restrictions on count
        self.packet_count = count

    def getUniqueId(self):
        return self.serial_number

    def close(self):
        """
        close the interface
        """
        logging.debug("closing interface")
        assert self._open is True
        self.closed = True
        self.read_sem.release()
        self.thread.join()
        usb.util.dispose_resources(self.dev)
