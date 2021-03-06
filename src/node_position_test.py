#!/usr/bin/env python3

import rospy
#import cv2
#import numpy as np
import math
import xbee as xlib
import time
import struct
import re
import argparse
import math
import csv
import os

from mavros_msgs.msg import OverrideRCIn, BatteryStatus
from mavros_msgs.srv import SetMode, CommandBool, CommandTOL
#from msg import NodeStatus
from digi.xbee.models.address import XBee64BitAddress
from digi.xbee.devices import ZigBeeDevice
from digi.xbee.packets.base import DictKeys
from digi.xbee.exception import XBeeException, ConnectionException, ATCommandException, InvalidOperatingModeException
from digi.xbee.util import utils
from digi.xbee.io import IOLine, IOMode

system_nodes = {}
rssi_table = []

xbee = ZigBeeDevice("/dev/ttyUSB0", 9600)
power_level = 2
api_mode = 2
hierarchy = 0
node_id = ''
address = ''
received_packet = None
battery = 1
nodes = []
node_rely = None
node_send = None
rssi_rely = 0
current_rssi = 0
data = []
rssi_avg = 0
rssi_hist = []
turning_hist = []
data_hist = []
avg_count = 5
time_find = []
packets_sent = 0
start_net = 0


rc_pub = rospy.Publisher('/mavros/rc/override', OverrideRCIn, queue_size=10)

def instantiate_zigbee_network():
    try:
        print("Opening xbee port")
        xbee.open()
        print("Setting Power Level")
        xbee.set_parameter('PL', utils.int_to_bytes(power_level, num_bytes=1))
        print("Setting API Mode")
        xbee.set_parameter('AP', utils.int_to_bytes(api_mode, num_bytes=1))
        xbee.set_io_configuration(IOLine.DIO4_AD4, IOMode.DISABLED)
        print("Getting self id")
        global node_id
        node_id = xbee.get_node_id()
        global address
        address = str(xbee.get_64bit_addr())
        print("This Node ID: ", node_id)
        print("Is Remote: ", xbee.is_remote())
        print("Power Level: ", xbee.get_power_level())

        print("Entering discovery mode...\n")

        xnet = xbee.get_network()
        xnet.set_discovery_timeout(15)
        xnet.clear()

        xnet.add_device_discovered_callback(xlib.discoverCallback)
        xnet.add_discovery_process_finished_callback(xlib.discoverCompleteCallback)
        xnet.start_discovery_process()

        while xnet.is_discovery_running():
            time.sleep(0.5)
        global nodes
        nodes = xnet.get_devices()
        data = 'Zigbee node %s sending data' % (xbee.get_node_id())

        return 1

    except ConnectionException:
        print('Error Connection')
        xbee.close()
        return 0
    except ATCommandException:
        print('Response of the command is not valid : ATCommandException')
        xbee.close()
        return 0
    except InvalidOperatingModeException:
        print('Not in API Mode')
        xbee.close()
        return 0

def determine_architecture():
    if(node_id == 'COORDINATOR'):
        for node in nodes:
            count = 0
            sending_node = None
            rssi_det = []
            while count < avg_count:
                xbee.send_data(node,"DATREQ")
                data = None
                time_pass = 0
                start_time = time.time()
                while data == None:
                    packet = xbee.read_data()
                    data = packet
                    time_pass = check_time(start_time, 6)
                    if(time_pass):
                        rospy.logerr('Could not retreive data from node: ')
                        rospy.logerr(node)
                        return 0
                count = count + 1
                sending_node = data.remote_device
                rssi_det.append(int(data.data.decode()))
            rssi = float(sum(rssi_det)/len(rssi_det))
            init_rssi_table(sending_node, rssi)
        self_node = {}
        self_node["node"] = str(address)
        self_node["rssi"] = 0
        rssi_table.append(self_node)
        sort_table_by_rssi()
    else:
        count = 0
        while count < avg_count:
            data = None
            while data == None:
                packet = xbee.read_data()
                data = packet
            val = data.data.decode()
            sending_node = data.remote_device
            if val == 'DATREQ':
                rssi = get_RSSI()
                string = str(rssi).encode()
                xbee.send_data(sending_node, string)
            count = count + 1
    return 1

def define_node(node):
    node = re.findall(r'[\w\d]+', str(node))
    return node[0]

def send_rssi_table():
    if(node_id == 'COORDINATOR'):
        for node in nodes:
            receive_ack = None
            time_pass = 0
            table = convert_list_to_bytearr()
            xbee.send_data(node, table)
    else:
        data = None
        while data == None:
            packet = xbee.read_data()
            data = packet
        val = data.data
        convert_bytearr_to_list(val)
    return 1

def init_rssi_table(node_sent, rssi):
    sending_node = define_node(node_sent)
    node = {}
    node["node"] = str(sending_node)
    node["rssi"] = rssi
    rssi_table.append(node)

def convert_list_to_bytearr():
    encoded_val = []
    for node in rssi_table:
        id = node["node"]
        rssi = str(node["rssi"])
        val = (id, rssi)
        val = '_'.join(val)
        encoded_val.append(val)
    encoded_val = ':'.join(encoded_val)
    encoded_val = encoded_val.encode()
    return encoded_val

def convert_bytearr_to_list(bytearr):
    data_list = bytearr.decode()
    data_list = data_list.split(':')
    for data in data_list:
        data = data.split('_')
        node = {}
        node["node"] = data[0]
        node["rssi"] = data[1]
        rssi_table.append(node)

def sort_table_by_rssi():
    rssi_table.sort(key=lambda val: val["rssi"])

def determine_rssi_value():
    if node_send:
        xbee.send_data(node_send,"RSSI_DET")
    if not node_id == 'COORDINATOR':

        data = None
        time_pass = 0
        start_time = time.time()
        rssi_found = 0
        while data == None:
            packet = xbee.read_data()
            data = packet
            time_pass = check_time(start_time, 6)
            if(time_pass):
                rospy.logerr('Could not retreive data from node: ')
                rospy.logerr(node)
                return 0
        rssi = get_RSSI()
        rospy.loginfo("Starting RSSI")
        rospy.loginfo(rssi)
        rospy.loginfo('Data Retrieved')
        sending_node = data.remote_device
        data = data.data.decode()
        if (sending_node == node_rely) and (data == 'RSSI_DET'):
            global rssi_rely
            rssi_rely = rssi

    #Update margin-of-error and thresholding values
    global rssi_margin_right
    rssi_margin_right = rssi_rely+rssi_margin
    global rssi_margin_left
    rssi_margin_left = rssi_rely-rssi_margin
    global rssi_thresh_right
    rssi_thresh_right = rssi_rely+rssi_thresh
    global rssi_thresh_left
    rssi_thresh_left = rssi_rely-rssi_thresh
    
    #Update RSSI history table with current value RSSI
    count = 0
    while count < avg_count:
        rssi_hist.append(rssi_rely)
        count = count + 1
    return 1

def determine_neighbors():
    index = 0
    for node in rssi_table:
        if node["node"] == address:
            index = rssi_table.index(node)
    global node_rely
    if index == 0:
        node_rely = None
    else:
        node_val = rssi_table[index-1]
        for node in nodes:
            if node_val["node"] == str(node.get_64bit_addr()):
                node_rely = node
    global node_send
    if index == (len(rssi_table)-1):
        node_send = None
    else:
        node_val = rssi_table[index+1]
        for node in nodes:
            if node_val["node"] == str(node.get_64bit_addr()):
                node_send = node
    return 1

def determine_RSSI(received):
    if node_rely:
        sending_node = received.remote_device
        throttle = received.data.decode()
        sending_node = define_node(sending_node)
        if sending_node is not None and (sending_node == str(node_rely.get_64bit_addr())):
            rssi = get_RSSI()
            current_time = time.time()
            rssi_hist.append(rssi)
        return throttle

def get_RSSI():
    rssi = xbee.get_parameter("DB")
    rssi = struct.unpack("=B", rssi)
    return rssi[0]

def check_time(start_time, wanted_time):
    current_time = time.time()
    if((current_time - start_time) > wanted_time):
        return 1
    return 0

def on_end():
    if xbee is not None and xbee.is_open():
        xbee.close()
        print('Xbee Closed')
    # print(rssi_hist)
    # print(data_hist)

def main():
    net_instantiated = instantiate_zigbee_network()
    arch_instantiated = determine_architecture()
    for x in rssi_table:
        print(x["node"], " : ", x["rssi"])
    
    on_end()
    
if __name__ == '__main__':
    main()