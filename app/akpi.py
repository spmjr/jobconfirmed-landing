from csv import reader
from datetime import datetime
from tkinter import *
from tkinter.filedialog import askopenfilename
from tkinter.filedialog import askdirectory
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor as Garfield
import requests
import socket
import json
import signal
import shutil
import math
from time import *
import os
import threading
import sys
from queue import Queue
import re
import base64
import logging
import traceback
import pdb

# Semaphore used to limit concurrent downloader processes
runPerm = threading.BoundedSemaphore(8)
lasaga_semaphore = threading.Semaphore(24)
spaghetti_semaphore = threading.Semaphore(24)

class KiProUltra:
    def __init__(self, ip):
        #threading.Thread.__init__(self)
        self.ip = ip
        self.storage_all_loaded = False
        self.check = False
        self.http_status_code = 0
        self.connection_url = "http://" + self.ip + "/index.html"
        self.clipN = None
        # Comma separated list of format url,downloadpath
        self.downloadStrings = []
        self.downloadClips = []
        # Set/read by formatDvrs; None means "never formatted this session"
        self.format_failed = None
        self.format_result = None

        # Making intializing requests to populate data structure of DVRs with current settings.
        self.dvrName = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_SysName'}))['value']
        self.clipName = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CustomClipName'}))['value']
        self.mode = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_MediaState'}))['value_name']
        self.takeNum = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CustomTake'}))['value_name']
        self.encoding = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_EncodeType_Low_FR'}))['value_name']
        self.state = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_TransportState'}))['value_name']
        self.actMediaSlot = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_SelectedSlot'}))['value_name']
        self.mediaLoading = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_MediaLoading'}))['value_name']
        self.storageAlarm = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_Storage_Removed_Alarm'}))['value']
        self.changeSlot = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_ChangeSlot'}))['value']
        self.storagePath = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_StoragePath'}))['value']
        self.fsState = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CurrentFileSystem'}))['value_name']
        self.mediaPercentage = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CurrentMediaAvailable'}))['value']
        self.procId = 0
        # Complicated and ugly way to get nested-dict-list-dict from response, sorry.
        self.storedVideos = (getReq(self.ip, "clips", {'action': 'get_playlists'}))['playlists'][0]['playlist']['cliplist']
        # This was done to check the storage alarm's decoded length. Length of 1 equates atleast 1 storage slot present. Sorry :]
        self.storageAlarmInteger = len(re.sub( r'[\w\n \x1a\x0f\x12\x08\x10\x00"]', '', base64.b64decode(self.storageAlarm).decode() ) )

    def reset(self):
        # Making intializing requests to populate data structure of DVRs with current settings.
        self.dvrName = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_SysName'}))['value']
        self.clipName = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CustomClipName'}))['value']
        self.mode = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_MediaState'}))['value_name']
        self.takeNum = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CustomTake'}))['value_name']
        self.encoding = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_EncodeType_Low_FR'}))['value_name']
        self.state = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_TransportState'}))['value_name']
        self.actMediaSlot = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_SelectedSlot'}))['value_name']
        self.mediaLoading = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_MediaLoading'}))['value_name']
        self.storageAlarm = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_Storage_Removed_Alarm'}))['value']
        self.changeSlot = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_ChangeSlot'}))['value']
        self.storagePath = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_StoragePath'}))['value']
        self.fsState = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CurrentFileSystem'}))['value_name']
        self.mediaPercentage = (getReq(self.ip, "config", {'action': 'get', 'paramid': 'eParamID_CurrentMediaAvailable'}))['value']
        # Complicated and ugly way to get nested-dict-list-dict from response, sorry.
        self.storedVideos = (getReq(self.ip, "clips", {'action': 'get_playlists'}))['playlists'][0]['playlist']['cliplist']
        # validate storageAlarm input and test for base64 and re modules   
        # This was done to check the storage alarm's decoded length. Length of 1 equates atleast 1 storage slot present. Sorry :]
        self.storageAlarmInteger = len(re.sub( r'[\w\n \x1a\x0f\x12\x08\x10\x00"]', '', base64.b64decode(self.storageAlarm).decode() ) )
        
         
    def printStat(self):
        print("| ", self.dvrName, " | ", self.ip, " | ", self.clipName, " | ", self.state, " | ", self.encoding, " | ", self.mode, " |", self.mediaPercentage,"% Available on Slot:",self.actMediaSlot, " | Custom Take Num:", self.takeNum,"|")
        print("----------------------------------------------------------------------------------------------------------------------------------------")

    def printClips(self):
        print("| ", self.dvrName, " | ", self.storedVideos)

class Col:
    green = '\033[92m'
    yellow = '\033[93m'
    red = '\033[91m'
    end = '\033[0m'
    bold = '\033[1m'

def logger():
   
    try:
        # DO NOT PUT logging.etc() within function as logging has not been initialized. Result gives logging failure
        # Clean old logs
        currentTime = time()
        secondsPerDay = 86400
        logAgeOutDays = 30
        logExpiration = currentTime - (logAgeOutDays * secondsPerDay)
        try:
            # Log directories 
            # TODO What if os.sys.argv[0] was tampered with?
            workDir = os.path.dirname(os.path.realpath(os.sys.argv[0]))
            logDir = os.path.join(workDir,"logs")
            if (os.path.isdir(logDir)):
                pass
            else:
                os.mkdir(logDir)
        except NameError as nm:
            print(Col.red + "[ERROR]" + Col.end + " {nm}. Exiting . . .\n")
            traceback.format_exc()
            exit(-1)
    
    
        # Iterate over logging directory and delete expired logs
        for log in os.listdir(logDir):
          
            path = os.path.join(logDir, log)
            fileCreationDate = os.stat(path).st_mtime

            if fileCreationDate < logExpiration:
                try:
                    os.remove(path)
                except PermissionError:
                    # Permission error Throw
                    #logging.error(f"Permissions error. Cannot delete old log files, permission denied.")
                    #logging.warning(f"Old log files take up storage and should be deleted.")
                    print(Col.red + "[ERROR] " + Col.end + f"Permission error in deleting {path}!")
                    traceback.format_ext() 
                    break
                except IsADirectoryError:
                    print(Col.red + "[ERROR] " + Col.end + f"Cannot delete {path}! Its a directory!")
                    traceback.format_ext()
                    break
            #logging.info(f"Log cleanup removed: {path}")
    
        # Create Logs
        try:
            currentDate = datetime.now()
            logStartTime = currentDate.strftime("%m%d%y_%H-%M-%S")
        except NameError as nm:
            print(Col.red + "[ERROR]" + Col.end + f"{nm}. Exiting . . . ")
            traceback.format_ext()
            exit(-1)
        logPath = os.path.join(logDir, logStartTime + "_akpi_log.log")

        try: 
            logging.basicConfig(filename = logPath, filemode = "w", format = "%(levelname)s : %(asctime)s - %(message)s", level = logging.DEBUG, force=True)
        except NameError as lg:
            print(Col.red + "[ERROR]" + Col.end + f" {lg}. Exiting . . .")
            logging.error(f"{lg}") 
            traceback.format_ext()
            logging.debug(traceback.format_ext())
            exit(-1)
        except PermissionError:
            logging.error(f"Permissions error. Cannot create log files, permission denied.")
            logging.warning(f"Exiting in 5")
            sleep(5)
            exit(-1)

        logging.info(f"Logging started at {currentDate}")
    except Exception as e:
        logging.error(f"Failed to load logger() with uncaught error. {e}")
        logging.debug(traceback.format_ext())
        traceback.format_ext()
        exit(-1)
   
class ProgramState:
    def __init__(self):
        self.dvrCounter = 0
        self.dvrStorageCounter = 0
        self.barrier = None
        self.error = 0
    def print_memory_addresses(self):
        logging.info(f"dvrCounter: {hex(id(self.dvrCounter))}")
        logging.info(f"dvrStorageCounter: {hex(id(self.dvrStorageCounter))}")
        logging.info(f"barrier: {hex(id(self.barrier))}")
        logging.info(f"error: {hex(id(self.error))}")

# Initializing the ProgramState class object as Global
program_state = ProgramState()
setattr(program_state,"dvrCounter",0)
setattr(program_state,"dvrStorageCounter",0)
setattr(program_state,"barrier",None)
setattr(program_state,"error",0)


def now():
    return strftime("%H:%M:%S")

def menu(dvrList):
    print(Col.green + """
  ######################################
  #                                    #
  #  UUU   UUU   LLL           AAAA    #
  #  UUU   UUU   LLL          AA   AA  #
  #  UUU   UUU   LLL         AAAAAAAAA #
  #  UUU   UUU   LLL         AAA   AAA #
  #  UUUUUUUUU   LLLLLLLLL   AAA   AAA #
  #                                    #
  ###################################### 

    ==== AJA Ki Pro Ultra ====
    === Interface  Utility ===
    1. Show Recorder Settings
    2. Change Recorder Settings
    3. Format Drives
    4. Make Stringouts
    5. Download Stringouts
    6. Archive Clips
    7. DVR Storage Check
    8. Quit/Exit   
    """ + Col.end)

    choice = "8"
    choice = input("Choice: ")

    if (choice == "1"):
        # Status sub-menu
        logging.info(f"User selected showing recorder settings")
        showStatusSubMenu(dvrList)

    elif (choice == "2"):
        # Change config sub-menu
        logging.info(f"User selected changing recorder settings")
        changeConfigSubMenu(dvrList)
    elif (choice == "3"):
        # Format drives function
        logging.info(f"User selected format drives")
        print(Col.yellow + "[WARNING]" + Col.end + " Data loss warning "+ Col.red + "!!! WARNING WARNING WARNING !!!" + Col.end + " \n\nIf you select \"Yes\" all DVRS and each slot will be formatted\n Proceed?")
        try:
            arg = input("Select Yes/No to proceed with formatting drives: ")
            if ( arg == "Yes"):
                logging.info("User opted to format drives")

                setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=None))

                with Garfield (max_workers=len(dvrList)) as executor:
                    futures_format_results = [ executor.submit(formatDvrsStager, dvr) for dvr in dvrList]
                    concurrent.futures.wait(futures_format_results)
                for dvr in futures_format_results:
                    logging.info(F"Garfield has completed format on [{dvr.result().ip}][{dvr.result().dvrName}]")
                    print(Col.green + "[SUCCESS] ["  + dvr.result().ip + "] [" + dvr.result().dvrName + "] [" + dvr.result().clipName + "] has completed formatting current slot drive" + Col.end, flush=True)
                menu(dvrList) 
            else:
                logging.info("User aborted formatting drives")
                print(Col.yellow + "[INFO] " + Col.end + "Aborting formatting drives . . . Returning to menu")
                menu(dvrList)
        except:
            logging.warning("Exception on formatDVRS")
            exit(-1) 
    elif (choice == "4"):
        # Stringout function
        logging.info(f"User selected making stringouts")
        stringout(dvrList)
        menu(dvrList)

    elif (choice == "5"):
        # Download stringouts
        logging.info(f"User selected downloading stringouts")
        # Setting queued DVRs to Data-LAN Settings
        print(Col.yellow + "[COMMAND]" +  Col.end + " All DVRs to Data-LAN Mode")

        dataLanSetting = {'name': 'eParamID_MediaState', 'value': '1'}
        reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
        setting = "config"
        totalErrors = 0

        for dvr in dvrList:
            totalErrors += setReq(dvr.ip, setting, reqParams, dataLanSetting)

        if (totalErrors == 0):
            logging.info(f"All DVRS in data-lan mode")
            print(Col.green + "[SUCCESS]" + Col.end + " All DVRs in Data-LAN Mode")

            # Init threads and pool
            start = time()
            now = datetime.now()
            timeNow = now.strftime("%m-%d-%Y %H:%M:%S")

            print("\n" + Col.yellow + "[INFO]" + Col.end + " Start time: ", timeNow)

            #queue  = []
            print("=== Scheduler Status ===")
            logging.info(f"Starting thread scheduler")
            
            # Timeout for barrier set to None to let downloads take as long as they need to complete
            setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=None))

            # instead of thread queue, lets use a threadpoolexecutor concurrency :]
            with Garfield (max_workers=len(dvrList)) as executor:
                futures_thread_results = [executor.submit(dlWorkerds , dvr) for dvr in dvrList]
                concurrent.futures.wait(futures_thread_results)

            print("=== Completion Status ===")
            for results in futures_thread_results:
                if (results.result()):
                    print(Col.green + "[SUCCESS]" + str(results) + Col.end,flush=True)
          
            duration = round((time() - start) / 60, 1)

            print("=== Threads Complete ===")
            print("Duration (minutes):", duration)
            logging.info(f"Duraction (minutes): {duration}")
    
        else:
            logging.error(f"Not all DVRs responded to commands during download")
            print(Col.red + "[ERROR]" + Col.end + " Not all DVRs responded to commands, aborting")
            menu(dvrList)

    elif (choice == "6"):
        # Download manager function
        logging.info(f"User selected downloading files")
        dlManager(dvrList)
        menu(dvrList)
    elif (choice == "7"):
        logging.info(f"User selected storage check for dvrs")
        # Scan for missing storage on detected DVRs 
        with Garfield (max_workers=len(dvrList)) as executor:
            futures_dvr_results = [ executor.submit(inventory_storage_slot_scan, dvr) for dvr in dvrList ]
            concurrent.futures.wait(futures_dvr_results)

        for dvr in futures_dvr_results:
            logging.info(f"Garfield has completed partial storage removal checks on: [{dvr.result()}] with storage all loaded on: [{dvr.result().storage_all_loaded}] ")
            print(Col.green + "[SUCCESS] [" + dvr.result().ip + "] [" + dvr.result().dvrName + "] [" + dvr.result().clipName + "]" + Col.end + " Complete Storage Removal Check Completed", flush=True)
    
        menu(dvrList)
    elif (choice == "8"):
        logging.info(f"User selected exit")
        exit()

    else:
        print ("Please enter a valid selection, try again")
        logging.info(f"User selected invalid selection")
        menu(dvrList)

def showStatusSubMenu(dvrList):
    print(Col.green + """
    ==== Show Settings Menu ====
    1. Mode Settings
    2. Encoding Settings
    3. Storage Settings
    4. Clip Name Settings
    *. Print all settings in tabular format
    5. Go Back to main menu    
    """ + Col.end)

    choice = "5"
    choice = input("Choice: ")

    if (choice == "1"):
        # Print Mode Settings
        logging.info(f"User selected print mode settings")
        #clear_screen()
        for dvr in dvrList:
            print(dvr.dvrName, ",", dvr.mode)
        menu(dvrList)

    elif (choice == "2"):
        # Print encoding
        logging.info(f"User selected print encoding settings")
        #clear_screen()
        for dvr in dvrList:
            print(dvr.dvrName, ",", dvr.encoding)
        menu(dvrList)

    elif (choice == "3"):
        # Print dvr
        logging.info(f"User selected print storage settings")
        #clear_screen()
        for dvr in dvrList:
            print(dvr.dvrName, ",", dvr.actMediaSlot)
        menu(dvrList)            

    elif (choice == "4"):
        # Print clip names
        logging.info(f"User selected print clip name settings")
        #clear_screen()
        for dvr in dvrList:
            print(dvr.dvrName, ",", dvr.clipName)
        menu(dvrList)

    elif (choice == "*"):
        # Print all settings in table
        logging.info(f"User selected print all settings")
        #clear_screen() 
        print("== Settings Table ==")
        print("----------------------------------------------------------------------------------------------------------------------------------------")
        for dvr in dvrList:
            dvr.reset()
            dvr.printStat()
        menu(dvrList)

    elif (choice == "5"):
        logging.info(f"User selected main menu")
        menu(dvrList)

    else:
        logging.info(f"User selected invalid print selection")
        print ("Please enter a valid selection, try again")
        showStatusSubMenu(dvrList)

def changeConfigSubMenu(dvrList):
    print(Col.green + """
    ==== Change Settings Menu ====
    1. Mode Settings
    2. Encoding Settings
    3. Swap Storage Slots
    4. Clip Name Change
    5. Clip Take Number Reset
    6. Go Back to main menu    
    """ + Col.end)

    choice = "5"
    choice = input("Choice: ")

    if (choice == "1"):
        # Change Mode Settings
        print(Col.green + """
        ==== Mode Options ====
        1. Data-LAN Mode
        2. Record-Play Mode
        3. Exit, go back    
        """ + Col.end)
        logging.info(f"User selected change mode settings")
        choice = "5"
        choice = input("Choice: ")

        if (choice == "1"):
            logging.info(f"User selected change data-lan mode settings")
            # Data-LAN Settings
            dataLanSetting = {'name': 'eParamID_MediaState', 'value': '1'}
            reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
            setting = "config"

            for dvr in dvrList:
                setReq(dvr.ip, setting, reqParams, dataLanSetting)

            choice = "5"
            
            print("== New Settings ==")

            for dvr in dvrList:
                dvr.reset()
                print(dvr.dvrName, ",", dvr.mode)
            menu(dvrList)
        elif (choice == "2"):
            logging.info(f"User selected change record-play mode settings")
            # Record-Play Settings
            recPlaySetting = {'name': 'eParamID_MediaState', 'value': '0'}
            reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '0'}
            setting = "config"

            for dvr in dvrList:
                setReq(dvr.ip, setting, reqParams, recPlaySetting)

            choice = "5"

            print("== New Settings ==")

            for dvr in dvrList:
                dvr.reset()
                print(dvr.dvrName, ",", dvr.mode)
            menu(dvrList)
        elif (choice == "3"):
            logging.info(f"User selected to go back to menu")
            # back to menu
            menu(dvrList)

        else:
            logging.info(f"User selected invalid selection")
            print ("Please enter a valid selection, try again")
            menu(dvrList)

    elif (choice == "2"):
        # Change Encoding Settings
        print(Col.green + """
        ==== Encoding Options ====
        1. High-Quality; ProRes 422 HQ
        2. Low-Quality; ProRes 422 Proxy
        3. Exit, go back    
        """ + Col.end)
        logging.info(f"User selected to change encoding settings")
        choice = "5"
        choice = input("Choice: ")

        if (choice == "1"):
            logging.info(f"User selected to change HQ settings")
            # High-Quality Settings
            hqSettings = {'name': 'eParamID_EncodeType_Low_FR', 'value': '1'}
            reqParams = {'action': 'set', 'paramid': 'eParamID_EncodeType_Low_FR', 'value': '1'}
            setting = "config"

            for dvr in dvrList:
                setReq(dvr.ip, setting, reqParams, hqSettings)

            choice = "3"
            
            print("== New Settings ==")

            for dvr in dvrList:
                dvr.reset()
                print(dvr.dvrName, ",", dvr.encoding)
            menu(dvrList)
        elif (choice == "2"):
            logging.info(f"User selected to change LQ settings")
            # Low-Quality Settings
            lowSettings = {'name': 'eParamID_EncodeType_Low_FR', 'value': '3'}
            reqParams = {'action': 'set', 'paramid': 'eParamID_EncodeType_Low_FR', 'value': '3'}
            setting = "config"

            for dvr in dvrList:
                setReq(dvr.ip, setting, reqParams, lowSettings)

            choice = "3"

            print("== New Settings ==")

            for dvr in dvrList:
                dvr.reset()
                print(dvr.dvrName, ",", dvr.encoding)
            menu(dvrList)
        elif (choice == "3"):
            logging.info(f"User selected to go back to main menu")
            # back to menu
            menu(dvrList)

        else:
            logging.info(f"User entered invalid selection")
            print ("Please enter a valid selection, try again")
            menu(dvrList)

    elif (choice == "3"):
        logging.info(f"User selected to swap storage slots")
        # TODO: Storage Settings works now but should be optimized or run jobs in parallel
        # TODO: EI-7 Storage Settings works now but should be optimized or run jobs in parallel
        setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=None))

        with Garfield (max_workers=len(dvrList)) as executor:
            futures_swap_results =  [ executor.submit( changeStorageSettings, dvr) for dvr in dvrList ]
            concurrent.futures.wait(futures_swap_results)

        for dvr in futures_swap_results:
            logging.info(f"Garfield has completed storage swap on: [{dvr.result()}]")
            print(Col.green + "[SUCCESS] [" + dvr.result().ip + "] [" + dvr.result().dvrName + "] [" + dvr.result().clipName + "]" + Col.end + " Storage Swap Completed" + " [" + dvr.result().storagePath + "]", flush=True)

        menu(dvrList)

    elif (choice == "4"):
        logging.info(f"User selected to change clip name settings")
        # TODO: Clip Name Settings
        clipConfig = newConfigCreation(dvrList)
        if (len(clipConfig.items()) == 0):
            print(Col.yellow + "[INFO] No clip name misconfigurations found with the inventory list provided. Returning to menu." + Col.end)
            menu(dvrList)
        else:

            setattr(program_state, "barrier", threading.Barrier(len(clipConfig.items()), timeout=None))

            with Garfield (max_workers=len(clipConfig.items())) as executor:
                futures_clip_results = [ executor.submit( changeClipNamesd, *myTuple) for myTuple in clipConfig.items() ]
                concurrent.futures.wait(futures_clip_results)
            
            for dvr in futures_clip_results:
                logging.info(f"Garfield has completed changing clip name on [{dvr.result()}]")
                print(Col.green + "[SUCCESS] [" + str(dvr.result()[0][0]) + "] [" + str(dvr.result()[1]) + "]" + Col.end + " Clip Update Completed", flush=True)
            
            logging.info(f"Finished applying new configuration")
            print("== New Settings ==")
            for dvr in dvrList:
                dvr.reset()
                print(dvr.ip, ", ", dvr.clipName)
            menu(dvrList)
    elif (choice == "5"):
        logging.info("User selected to reset clip take numbers")

        setting = "config"
        # Apply settings
        setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=None))

        logging.info(f"Resetting clip takes")
        
        resetClipTakeChange = {'name': 'eParamID_CustomTake', 'value': '1'}
        resetClipTakeParams = {'action': 'set', 'paramid': 'eParamID_CustomTake', 'value': '1'}

         # Apply settings
        event = threading.Event()
        lock = threading.Lock()
        error = 0
        try:
            with Garfield (max_workers=len(dvrList)) as executor:
                futures_error = [executor.submit(setReq2, dvr.ip, setting, resetClipTakeParams, resetClipTakeChange, event, lock) for dvr in dvrList ]
                concurrent.futures.wait(futures_error)
            for e in futures_error:
                error += e.result()
            if ( error != 0 ):
                logging.error(f"Error changing clip take of: [{error}]") 
        except Exception as e:
            logging.error(f"{e}")
        for dvr in dvrList:
            dvr.reset()
            print(Col.green + "[SUCCESS] [" + str(dvr.dvrName) + "] [" + str(dvr.clipName) + "] clip number take reset to " + str(dvr.takeNum) + Col.end, flush=True)
        menu(dvrList)
    elif (choice == "6"):
        logging.info(f"User selected to go back to main menu")
        menu(dvrList)

    else:
        logging.info(f"User selected invalid selection")
        print ("Please enter a valid selection, try again")
        showStatusSubMenu(dvrList)

def getReq(ip, setting, params):
    # Creating json parsed response, reference-able DS
    try:
    
        url = "http://" + ip + "/" + setting

        resp = json.loads((requests.get(url, params, timeout = 5)).text)
        logging.info(f"{now()} getReq with resp: {resp}") 
    except requests.exceptions.InvalidJSONError as err:
        logging.error(f"{err}")
        print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
        sleep(3)
        exit(-1)
    except requests.exceptions.Timeout as t:
        logging.error(f"{t}")
        print(Col.yellow + "[WARNING]" + Col.end + " {t}")
    except requests.exceptions.RequestException as e:
        logging.error(f"{e}")
        print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
        sleep(3)
        exit(-1)
    return resp

def getReq2(ip, setting, params, oldResp, lock, event):
    # for this get request, we need to verify we only return when data has actually changed. Pass old get Request return value and compare to current response in a while loop
    # Continue making requests until data is changed.
    with lock:
        try:
            
            url = "http://" + ip + "/" + setting

            resp = json.loads((requests.get(url, params, timeout = 5)).text)
            logging.info(f"{now()} getReq2 with resp: {resp['value_name']} vs oldResp: {oldResp}")
            # break while loop when value has been changed
            while (oldResp == resp['value_name']):
                sleep(7) 
                logging.info(f"{now()} getReq2 with resp: {resp['value_name']} vs oldResp: {oldResp} EVENT: {event} LOCK: {lock}")
                resp = json.loads((requests.get(url, params, timeout = 5)).text)
        except requests.exceptions.InvalidJSONError as err:
            logging.error(f"{err}")
            print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)
        except requests.exceptions.Timeout as t:
            logging.error(f"{t}")
            print(Col.yellow + "[WARNING]" + Col.end + " {t}")
        except requests.exceptions.RequestException as e:
            logging.error(f"{e}")
            print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)
        event.set()
        return resp

def setReq(ip, setting, reqParams, changedParams):
    # Tracking the status of all changed params for successful application
    # Creating json parsed response, reference-able DS
    try:
        error = 0
        badSettings = ""

        url = "http://" + ip + "/" + setting

        resp = json.loads((requests.get(url, reqParams, timeout = 5)).text)
        logging.info(f"{now()} setReq with resp: {resp}")    
    except requests.exceptions.InvalidJSONError as err:
        logging.error(f"{err}")
        print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
        sleep(3)
        exit(-1)
    except requests.exceptions.Timeout as t:
        logging.error(f"{t}")
        print(Col.yellow + "[WARNING]" + Col.end + " {t}")
    except requests.exceptions.RequestException as e:
        logging.error(f"{e}")
        print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
        sleep(3)
        exit(-1)

    # This section checks if the http request is a valid one for the api to respond to. This has nothing to do with updating the endpoint or updating object attributes
    if changedParams != True:
        for key in changedParams.keys():
            if (changedParams[key] != resp[key]):
                error = 1
                badSettings += key + ", "
    
        if (error == 1):
            print(Col.red + "[ERROR]" + Col.end + " Settings: ", badSettings, " failed to apply on ", ip)
    
        return error

def setReq2(ip, setting, reqParams, changedParams, event, lock):
    with lock:
        # Tracking the status of all changed params for successful application
        # Creating json parsed response, reference-able DS
        try:
            error = 0
            badSettings = ""

            url = "http://" + ip + "/" + setting

            resp = json.loads((requests.get(url, reqParams, timeout = 5)).text)
            logging.info(f"{now()} setReq with resp: {resp}")    
        except requests.exceptions.InvalidJSONError as err:
            logging.error(f"{err}")
            print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)
        except requests.exceptions.Timeout as t:
            logging.error(f"{t}")
            print(Col.yellow + "[WARNING]" + Col.end + " {t}")
        except requests.exceptions.RequestException as e:
            logging.error(f"{e}")
            print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)

        # This section checks if the http request is a valid one for the api to respond to. This has nothing to do with updating the endpoint or updating object attributes
        if changedParams != True:
            for key in changedParams.keys():
                if (changedParams[key] != resp[key]):
                    error = 1
                    badSettings += key + ", "
        
            if (error == 1):
                print(Col.red + "[ERROR]" + Col.end + " Settings: ", badSettings, " failed to apply on ", ip)
        
            return error
        event.set()
        return error

def dlWorker(downloadStrings, downloadClips):
    # Chunked downloader for large files, capable of multi-threading
    # Accepts 2D array of url,path as arguments

    # Grab lock on the semaphore (if it isn't maxed out)
    runPerm.acquire()
    logging.info(f"Running download dlWorker job")
    # Run the download job
    try:
        for item in downloadStrings, downloadClips:
            for urlFileTuple in item:
                url, path = urlFileTuple.split(',')

                # Get the name of the DVR directory
                trimVideo = path.split('\\')[:-1]
                vidName =  path.split('\\')[-1]
                dvrDir = '\\'.join(trimVideo)

                # Check for the DVR directory and if it is not present create it
                if(not os.path.isdir(dvrDir)):
                    os.mkdir(dvrDir)
            
                try:
                    # Get the file size information
                    fileHeader = requests.head(url).headers
                    fileSize = int(fileHeader['content-length'])
                    print(f"{vidName} Remote File Size: ", fileSize)
                except:
                    print(Col.red + "[ERROR]" + Col.end , vidName,"file not found, skipping")
                    logging.info(f"Video clip, {vidName}, not found")
                    continue
            
                with requests.get(url, stream = True) as req:
                    with open(path, 'wb') as file:
                        shutil.copyfileobj(req.raw, file)

                file.close()
            
                # Verifying full file download
                locFileSize = os.path.getsize(path)
                print(f"{vidName} Local File Size: ", locFileSize)
                while (locFileSize < fileSize):

                    print(Col.red + "[ERROR]" + Col.end, vidName, "local file smaller than remote file:", locFileSize, "<", fileSize)
                    logging.error(f"{vidName} smaller on disk than on remote host, restarting download.") 

                    # Create the resume header, we start at the byte after the last good one
                    diff = fileSize - locFileSize
                    resumeHeader = {'Range': 'bytes=%d-' % diff}
                
                    print(Col.yellow + "[INFO]" + Col.end + "Resume Header:", resumeHeader)
                    logging.error(f"Resume header: {resumeHeader} created for restarting download of {vidName}")

                    with requests.get(url, stream = True, headers = resumeHeader) as resumeReq:
                        # Start the process again for the resume function.
                        with open(path, 'ab') as file:
                            shutil.copyfileobj(resumeReq.raw, file)
                
                    file.close()

                    # Verifying full file download
                    locFileSize = os.path.getsize(path)
    
    finally:
        # Get the recorder name
        recName = vidName.split('_')[0]
        #recName = vidName.split('.')[0]
        print(Col.yellow + "[INFO]" + Col.end, recName, "Download complete")
        logging.info(f"Downloads complete for: {recName}, releasing semaphore")
        runPerm.release()

def dlWorkerds(dvr: object) -> object:
    with Garfield (max_workers=1) as executor:
        worker = [executor.submit(dlWorker_stringouts, dvr)]
        concurrent.futures.wait(worker)

    program_state.barrier.wait()

    return dvr
    
def dlWorkerd(dvr: object) -> object:
    with Garfield (max_workers=1) as executor:
        worker = [executor.submit(dlWorker_archive, dvr)]
        concurrent.futures.wait(worker)

    program_state.barrier.wait()

    return dvr

def dlWorker_stringouts(dvr: object) -> object:
    with spaghetti_semaphore:
        downloadStrings = getattr(dvr,"downloadStrings")
        logging.info(f"downloadStrings: {downloadStrings}")
        if (downloadStrings != None):
            print(Col.yellow +"[INFO] dlWorker thread for " + dvr.clipName + " download scheduled" + Col.end ,flush=True)
            
            locFileSize = 0
      
            try:
                for item in downloadStrings:
                    url, path = item.split(',')
                    logging.info(f"url download {url} and path {path}")
                    # Get the name of the DVR directory
                    trimVideo = path.split('\\')[:-1]
                    vidName =  path.split('\\')[-1]
                    dvrDir = '\\'.join(trimVideo)

                    try:
                        # Get the file size information
                        fileHeader = requests.head(url).headers
                        fileSize = int(fileHeader['content-length'])
                        print(f"{vidName} Remote File Size: ", fileSize, flush=True)
                        sleep(1)
                        percentage = (locFileSize/fileSize)*100
                        print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True) 
                    except:
                        print(Col.yellow + "[INFO]" + Col.end , vidName,"file not found, skipping", flush=True)
                        logging.info(f"Video clip, {vidName}, not found")
                        continue
                
                    with requests.get(url, stream = True, timeout=None) as req:
                        with open(path, 'wb') as file:
                            shutil.copyfileobj(req.raw, file)

                    file.close()
                
                    # Verifying full file download
                    locFileSize = os.path.getsize(path)
                    percentage = (locFileSize/fileSize)*100
                    print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True) 
                    print(f"{vidName} Local File Size: ", locFileSize, flush=True)
                    while (locFileSize < fileSize):

                        print(Col.red + "[ERROR]" + Col.end, vidName, "local file smaller than remote file:", locFileSize, "<", fileSize, flush=True)
                        logging.error(f"{vidName} smaller on disk than on remote host, restarting download.") 

                        # Create the resume header, we start at the byte after the last good one
                        diff = fileSize - locFileSize
                        resumeHeader = {'Range': 'bytes=%d-' % diff}
                    
                        print(Col.yellow + "[INFO]" + Col.end + "Resume Header:", resumeHeader, flush=True)
                        logging.error(f"Resume header: {resumeHeader} created for restarting download of {vidName}")

                        with requests.get(url, stream = True, headers = resumeHeader,timeout=None) as resumeReq:
                            # Start the process again for the resume function.
                            with open(path, 'ab') as file:
                                shutil.copyfileobj(resumeReq.raw, file)
                    
                        file.close()

                        # Verifying full file download
                        locFileSize = os.path.getsize(path)
                        sleep(1)
                        percentage = (locFileSize/fileSize)*100
                        print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True)
            except Exception as e:
                logging.error(f"{e}")
                exit(-1)
            finally:
                # Get the recorder name
                recName = vidName.split('_')[0]
              
                print(Col.green + "[INFO]" + Col.end, recName, "Download complete", flush=True)
                logging.info(f"Downloads complete for: {recName}, releasing semaphore")
              
                return dvr

        else:
            print(Col.red +"[ERROR] dlWorker thread for " + dvr.clipName + " did not start" + Col.end ,flush=True)
            logging.info(f"{dvr.clipName} thread download did not start!!!")
            exit(-1)
    return dvr

def dlWorker_archive(dvr: object) -> object:
    with spaghetti_semaphore:
        downloadClips = getattr(dvr,"downloadClips")
        logging.info(f"downloadClips: {downloadClips}")
        if (downloadClips != None):
            print(Col.yellow +"[INFO] dlWorker thread for " + dvr.clipName + " download scheduled" + Col.end ,flush=True)
       
            locFileSize = 0
         
            try:
                for item in downloadClips:
                    url, path = item.split(',')
                    
                       
                    logging.info(f"url download {url} and path {path}")
                    # Get the name of the DVR directory
                    trimVideo = path.split('\\')[:-1]
                    vidName =  path.split('\\')[-1]
                    dvrDir = '\\'.join(trimVideo)

                    # Check for the DVR directory and if it is not present create it
                    if(not os.path.isdir(dvrDir)):
                        os.mkdir(dvrDir)
                
                    try:
                        # Get the file size information
                        fileHeader = requests.head(url).headers
                        fileSize = int(fileHeader['content-length'])
                        print(f"{vidName} Remote File Size: ", fileSize, flush=True)
                        sleep(1)
                        percentage = (locFileSize/fileSize)*100
                        print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True) 
                    except:
                        print(Col.yellow + "[INFO]" + Col.end , vidName,"file not found, skipping", flush=True)
                        logging.info(f"Video clip, {vidName}, not found")
                        continue
                
                    with requests.get(url, stream = True, timeout=None) as req:
                        with open(path, 'wb') as file:
                            shutil.copyfileobj(req.raw, file)

                    file.close()
                
                    # Verifying full file download
                    locFileSize = os.path.getsize(path)
                    percentage = (locFileSize/fileSize)*100
                    print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True) 
                    print(f"{vidName} Local File Size: ", locFileSize, flush=True)
                    while (locFileSize < fileSize):

                        print(Col.red + "[ERROR]" + Col.end, vidName, "local file smaller than remote file:", locFileSize, "<", fileSize, flush=True)
                        logging.error(f"{vidName} smaller on disk than on remote host, restarting download.") 

                        # Create the resume header, we start at the byte after the last good one
                        diff = fileSize - locFileSize
                        resumeHeader = {'Range': 'bytes=%d-' % diff}
                    
                        print(Col.yellow + "[INFO]" + Col.end + "Resume Header:", resumeHeader, flush=True)
                        logging.error(f"Resume header: {resumeHeader} created for restarting download of {vidName}")

                        with requests.get(url, stream = True, headers = resumeHeader,timeout=None) as resumeReq:
                            # Start the process again for the resume function.
                            with open(path, 'ab') as file:
                                shutil.copyfileobj(resumeReq.raw, file)
                    
                        file.close()

                        # Verifying full file download
                        locFileSize = os.path.getsize(path)
                        sleep(5)
                        percentage = (locFileSize/fileSize)*100
                        print(Col.yellow + "[INFO] dlWorker downloads for " + str(dvr.clipName) + " is at " + str(percentage) + "(%) completion" + Col.end, flush=True)
            except Exception as e:
                logging.error(f"{e}")
                exit(-1)
            finally:
                # Get the recorder name
                recName = vidName.split('_')[0]
              
                print(Col.green + "[INFO]" + Col.end, recName, "Download complete", flush=True)
                logging.info(f"Downloads complete for: {recName}, releasing semaphore")
             
                return dvr

        else:
            print(Col.red +"[ERROR] dlWorker thread for " + dvr.clipName + " did not start" + Col.end ,flush=True)
            logging.info(f"{dvr.clipName} thread download did not start!!!")
            exit(-1)
    return dvr

def dlManager(dvrList):
    # Tracks running threads
    #queue = []
    
    # TODO: EI-4
    print(Col.green + """
    ==== Downloader Settings ====
    AJA Recorders use a take-based system
    Video names: clip_take+session
    > Where take is an integer, iterated on
    restart.
    > Where session is an integer, iterated
      on a time of recording basis

    === Instructions ===

    To use the downloader enter the range
    or series of both integers above that
    should be downloaded.
    * Series can be used to skip numbers
    * Do not use series AND ranges in the
      same variable e.g. 1-3,4
    * Instead use:
      1-3 or 1,3 to skip 2
    ** Modes can be range or list based. (Range mode is disabled until testing is complete)  
    """ + Col.end)

    mode = "none"
    mode = input("Mode setting (\"r\" for range or \"l\" for list): ")
    mode = mode.strip().lower()
    logging.info(f"Download Manager invoked in {mode} mode")
    logging.info(f"Checking valid mode selection")
    # Checking for valid mode selection
    if (mode != "l" and mode != "r"):
        logging.info(f"Incorrect mode setting")
        print(mode)
        print(Col.red + "[ERROR]" + Col.end + " Incorrect mode setting")
        dlManager(dvrList)
    
    if (mode == "r"):
        logging.info(f"User selected range mode")
        print(Col.yellow + "[INFO] " + Col.end + "Range mode is disabled this release until further testing is completed. Redirecting to menu . . .")
        dlManager(dvrList)
        '''Disabling range mode until implemented and tested fully!
        takeNum = "1-1"
        takeNum = input("Take number(s): ")

        sesNum = "1-1"
        sesNum = input("Session number(s): ")
        
        # Create lists of takes and sessions
        takes = []
        sessions = []
        urls = []

        # Remove spaces
        takeNum.replace(" ", "")
        sesNum.replace(" ", "")

        # Parsing the input and creating request URLs
        if ('-' in takeNum and ',' in takeNum):
            print(Col.red + "[ERROR]" + Col.end + " Incorrect format of take number ranges and series notation")
            dlManager(dvrList)

        elif ('-' in takeNum):
            takeStrt, takeEnd = takeNum.split('-')

            # Checking for misordered range, if so flip it
            if (int(takeStrt) > int(takeEnd)):
                temp = takeEnd
                takeEnd = takeStrt
                takeStrt = temp

            for i in range(int(takeStrt), int(takeEnd) + 1):
                takes.append(i)

        elif (',' in takeNum):
            takes = takeNum.split(',')

        else:
            takes = takeNum


        # Error handling for uses of ranges AND series notation
        if ('-' in sesNum and ',' in sesNum):
            print(Col.red + "[ERROR]" + Col.end + " Incorrect format of session number ranges and series notation")
            dlManager(dvrList)

        elif ('-' in sesNum):
            sesStrt, sesEnd = sesNum.split('-')

            # Checking for misordered range, if so flip it
            if (int(sesStrt) > int(sesEnd)):
                temp = sesEnd
                sesEnd = sesStrt
                sesStrt = temp

            for i in range(int(sesStrt), int(sesEnd) + 1):
                sessions.append(i)

        elif (',' in sesNum):
            sessions = sesNum.split(',')
        
        else:
            sessions = sesNum
        '''
    if (mode == "l"):
        logging.info(f"User selected list mode")
        dlList = "0"
        print(Col.yellow + "[INFO] " + Col.end + "If you are downloading files and not archiving, just hit ENTER.")
        dlList = input("Sessions/Takes comma-seperated:")    

        dlList = dlList.replace(" ", "")
        dlList = dlList.replace("+", "%2b")
        dlList = dlList.split(",")
    # Select download location
    #root = tkinter.Tk()
    #rootDir = tkFileDialog.askdirectory(parent=root, initialdir="/", title="Select download directory")
    #rootDir = "D:\\USSF-8\Archive"
    # Select download location
    #rootDir = filedialog.askdirectory()
    root = Tk()
    root.withdraw()
    try:
        workDir = os.path.dirname(os.path.realpath(os.sys.argv[0]))
        workDir += "/Archive"
        if (not os.path.isdir(workDir)):
            os.mkdir(workDir)
            logging.info(f"Created directory: {workdir}")
    except PermissionError:
        print(Col.red + "[ERROR]" + Col.end + " Permission error during archiving file creation")
        logging.error(f"Permission error detected in archiving file creation! Exiting . . .")
        exit(-1)
    except Exception as e:
        logging.warning(f"Work directory not able to be set. Using root directory {e}")
        workDir = "/"
    rootDir = askdirectory(parent=root, initialdir=workDir, title="Select root download directory for archiving stringouts and clips")
    # Creating a per-DVR list of download URLs and paths.
    if (os.path.exists(rootDir)):
        # Allow each DVR to create it's own list of URLs and paths for download
        if (mode == "r"):
            '''
            for dvr in dvrList:
                for take in takes:
                    # This snippet is to catch the clips that match the take number, but have no "+"
                    # for instance _2 whereas the next loop handles _2+1...
                    fullVideoName = dvr.clipName + "_" + str(take) + ".mov"
                    dvr.downloadStrings.append("http://" + dvr.ip + "/media/" + fullVideoName + "," + rootDir + "\\" + dvr.clipName + "\\" + fullVideoName)
                
                    for session in sessions:
                        # Catching sessions
                        # The first version has the + sign encoded to url-speak
                        fullVideoNameHTTP = dvr.clipName + "_" + str(take) + "%2b" + str(session) + ".mov"    
                        fullVideoNameFile = fullVideoNameHTTP.replace("%2b", "+")
                        dvr.downloadStrings.append("http://" + dvr.ip + "/media/" + fullVideoNameHTTP + "," + rootDir + "\\" + dvr.clipName + "\\" + fullVideoNameFile)
            '''
        if (mode == "l"):
            for dvr in dvrList:                
                for vid in dlList:
                    # Catching sessions
                    fullVideoNameHTTP = dvr.clipName + vid + ".mov"
                    fullVideoNameFile = fullVideoNameHTTP.replace("%2b", "+")    
                    dvr.downloadClips.append("http://" + dvr.ip + "/media/" + fullVideoNameHTTP + "," + rootDir + "\\" + dvr.clipName + "\\" + fullVideoNameFile)

        # Setting queued DVRs to Data-LAN Settings
        print(Col.yellow + "[COMMAND]" +  Col.end + " All DVRs to Data-LAN Mode")

        dataLanSetting = {'name': 'eParamID_MediaState', 'value': '1'}
        reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
        setting = "config"
        totalErrors = 0

        for dvr in dvrList:
            totalErrors += setReq(dvr.ip, setting, reqParams, dataLanSetting)

        if (totalErrors == 0):
            logging.info(f"All DVRS in data-lan mode")
            print(Col.green + "[SUCCESS]" + Col.end + " All DVRs in Data-LAN Mode")

            # Init threads and pool
            start = time()
            now = datetime.now()
            timeNow = now.strftime("%m-%d-%Y %H:%M:%S")

            print("\n" + Col.yellow + "[INFO]" + Col.end + " Start time: ", timeNow)

            #queue  = []
            print("=== Scheduler Status ===")
            logging.info(f"Starting thread scheduler")
            
            # Timeout for barrier set to None to let downloads take as long as they need to complete
            setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=None))

            # instead of thread queue, lets use a threadpoolexecutor concurrency :]
            with Garfield (max_workers=len(dvrList)) as executor:
                futures_thread_results = [executor.submit(dlWorkerd , dvr) for dvr in dvrList]
                concurrent.futures.wait(futures_thread_results)

            print("=== Completion Status ===")
            for results in futures_thread_results:
                if (results.result()):
                    print(Col.green + "[SUCCESS]" + str(results) + Col.end,flush=True)
          
            duration = round((time() - start) / 60, 1)

            print("=== Threads Complete ===")
            print("Duration (minutes):", duration)
            logging.info(f"Duraction (minutes): {duration}")
    
        else:
            logging.error(f"Not all DVRs responded to commands during download")
            print(Col.red + "[ERROR]" + Col.end + " Not all DVRs responded to commands, aborting")
            menu(dvrList)

def changeStorageSettings(dvr: object) -> object:
    # Return string in function
    with Garfield (max_workers=1) as executor:
        worker = [executor.submit(storage_path_after_swap, dvr)]
        concurrent.futures.wait(worker)
    program_state.barrier.wait()
    
    for item in worker:
        logging.info(f"The storage setting path is now { item.result()}")
        print(Col.green + "[SUCCESS] The Storage setting path changed to " + str(item.result()) + Col.end, flush=True)
    return dvr
            
def newConfigCreation(dvrList: object) -> dict:
    # Grab the new configuration file
    # Creating a dict for the new config keyed on IP address and DVR name, belt + suspenders
    newCfg = {}
    fName = askopenfilename(filetypes=[("CSV Files", "*.csv")])
    logging.info(f"Grabbing new configuration file.")
    try:
        with open(fName, 'r') as readPtr:
            csvReader = reader(readPtr)

            # Skip header entry
            header = next(csvReader)

            if (header != None):
                for row in csvReader:
                    
                    # Creates a dictionary tuple with (IP : CLIPNAME) that has a match
                    for dvr in dvrList:
                        # TODO: catch bad input on config such as duplicate IPs, or clip names, etc
                        if(dvr.ip == row[0] and dvr.clipName != row[1]):
                            logging.info(f"Found a mismatch between configuration and dvr clipname . . . storing to new dictionary {dvr.ip} == {row[0]} but {dvr.clipName} != {row[1]}")
                            print(Col.yellow + "[INFO] Mismatch between clipnames of configuration file and current dvr ---> [" +str(dvr.clipName) + "] =//= [" +str(row[1])+"]" + Col.end, flush=True)
                            setattr(dvr, "clipN",row[1])
                            newCfg.update([ ((row[0], row[1]), dvr.clipN) ])
                        elif( dvr.ip == row[0] and dvr.clipName == row[1]):
                            logging.info(f"match found between both ends .  . . doing nothing! {dvr.ip} == {row[0]} but {dvr.clipName} == {row[1]} ")
                            print(Col.green + "[INFO] Match between clipnames of configuration file and current dvr ---> [" +str(dvr.clipName) + "] ==== [" +str(row[1])+"]" + Col.end, flush=True)
                            setattr(dvr, "clipN",row[1])
                        else:
                            continue
        return newCfg

    except FileNotFoundError:
        logging.critical("Could not find inventory file, exiting")
        exit(-1)
    except PermissionError:
        logging.critical("Could not open inventory file, permission error, exiting")
        exit(-1)

def changeClipNamesd(ip: tuple, value: str) -> object:
    with Garfield (max_workers=1) as executor:
        worker = [executor.submit(changeClipNames, ip, value)]
        concurrent.futures.wait(worker)
    program_state.barrier.wait()

    return (ip,value)

def changeClipNames(ip:tuple, value: str) -> object:
    with spaghetti_semaphore:
        
        error = 0
    
        # With a good config, set up the request to apply setting to DVR
        # ip, setting, reqParams, changedParams
        setting = "config"
        logging.info(f"Changing clip names with {ip} and {value}")
        try:
            reqParams = json.loads('{\"action\": \"set\", \"paramid\": \"eParamID_CustomClipName\", \"value\": \"' + value + '\"}')
            changedParams = json.loads('{\"name\": \"eParamID_CustomClipName\", \"value\": \"' + value + '\"}')
        except requests.exceptions.InvalidJSONError as err:
            logging.error(f"{err}")
            print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)
        except requests.exceptions.Timeout as t:
            logging.error(f"{t}")
            print(Col.yellow + "[WARNING]" + Col.end + " {t}")
        except requests.exceptions.RequestException as e:
            logging.error(f"{e}")
            print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
            sleep(3)
            exit(-1)

        # Apply settings
        event = threading.Event()
        lock = threading.Lock()
        try:
            with Garfield (max_workers=1) as executor:
                futures_error = [executor.submit(setReq2,ip[0], setting, reqParams, changedParams, event, lock)]
                concurrent.futures.wait(futures_error)
            for e in futures_error:
                error += e.result()
            if ( error != 0 ):
                logging.error(f"Error changing clip name of: [{ip[0]}][{value}]") 
        except Exception as e:
            logging.error(f"{e}")

    return (ip,value)
       
        
def formatDvrsStager(dvr: object) -> object:
    with Garfield (max_workers=1) as executor:
        futures_format = [executor.submit(formatDvrs, dvr)]
        concurrent.futures.wait(futures_format)
    program_state.barrier.wait()
    return dvr

# How long (seconds) to wait for a DVR's filesystem to come back after a
# format command before giving up on that DVR and reporting a failure,
# instead of polling forever. Tune this to your slowest observed real format
# time plus margin. This is what stops one unresponsive DVR from hanging the
# entire fleet-wide format operation (every other DVR's thread would
# otherwise sit blocked forever on program_state.barrier.wait()).
FORMAT_TIMEOUT_SECONDS = 180

# How long (seconds) to wait for a slot swap (toggle) to actually register
# before giving up on it.
SLOT_SWAP_TIMEOUT_SECONDS = 30


# How long (seconds) to wait, after a format completes, for the device to
# finish settling (media loading/indexing the freshly-formatted filesystem)
# before we attempt a slot swap. fsState reporting a real filesystem does
# not necessarily mean the device is ready to act on a slot-change command
# yet — in testing, a swap sent immediately after format consistently
# failed to register (request succeeded, but the reported slot never
# changed), which points at the device silently ignoring the toggle while
# still busy rather than rejecting it outright.
MEDIA_SETTLE_TIMEOUT_SECONDS = 60


def _wait_for_media_settle(dvr: object) -> None:
    """After a format, poll eParamID_MediaLoading (dvr.mediaLoading) until
    two consecutive reads come back identical (i.e. it's stopped changing),
    capped at MEDIA_SETTLE_TIMEOUT_SECONDS. We don't hardcode an expected
    'done loading' string here since the exact value AJA uses hasn't been
    confirmed — waiting for it to stabilize is a safer general-purpose
    signal than guessing a literal value to match against."""

    logging.info(f"{dvr.dvrName}: waiting for media to settle before next slot operation (currently: {dvr.mediaLoading})")
    print(Col.yellow + "[INFO] [" + str(dvr.dvrName) + "] letting media settle before swapping . . ." + Col.end, flush=True)

    waited = 0
    prev = dvr.mediaLoading
    while waited < MEDIA_SETTLE_TIMEOUT_SECONDS:
        sleep(3)
        waited += 3
        dvr.reset()
        print(Col.yellow + "[DEBUG] [" + str(dvr.dvrName) + f"] settle t+{waited}s  "
              f"mediaLoading={dvr.mediaLoading}  state={dvr.state}  "
              f"actMediaSlot={dvr.actMediaSlot}  fsState={dvr.fsState}" + Col.end, flush=True)
        logging.info(f"{dvr.dvrName} settle t+{waited}s mediaLoading={dvr.mediaLoading} "
                     f"state={dvr.state} actMediaSlot={dvr.actMediaSlot} fsState={dvr.fsState}")
        if dvr.mediaLoading == prev:
            logging.info(f"{dvr.dvrName}: media settled at '{dvr.mediaLoading}' after {waited}s")
            return
        prev = dvr.mediaLoading

    logging.warning(f"{dvr.dvrName}: media loading state still changing after {MEDIA_SETTLE_TIMEOUT_SECONDS}s "
                     f"(currently: {dvr.mediaLoading}) — proceeding anyway")
    print(Col.yellow + "[WARNING] [" + str(dvr.dvrName) + "] media still appears to be settling after " +
          str(MEDIA_SETTLE_TIMEOUT_SECONDS) + "s — proceeding with swap anyway" + Col.end, flush=True)


def _format_current_slot(dvr: object) -> tuple:
    """Sends the format command for whatever slot is CURRENTLY active on
    dvr, and polls (capped at FORMAT_TIMEOUT_SECONDS) until the filesystem
    comes back. The AJA format command only ever affects the active slot —
    there is no 'format all slots' primitive on the device — so formatting
    both slots means calling this twice with a swap in between.
    Returns (success: bool, message: str)."""

    reqParams = {'action': 'set', 'paramid': 'eParamID_StorageCommand', 'value': '4'}
    setting = "config"

    logging.info(f"Sending format command to {dvr.dvrName} (active slot: {dvr.actMediaSlot})")
    print(Col.yellow + "[INFO] Sending format command to [" + str(dvr.dvrName) + "] (active slot: " + str(dvr.actMediaSlot) + ")" + Col.end, flush=True)
    url = "http://" + dvr.ip + "/" + setting
    try:
        resp = json.loads((requests.get(url, reqParams, timeout=5)).text)
    except requests.exceptions.InvalidJSONError as err:
        logging.error(f"{err}")
        print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] " + Col.end + f"({err})")
        return False, f"invalid response: {err}"
    except requests.exceptions.Timeout as t:
        logging.error(f"{t}")
        print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] " + Col.end + f"format command timed out: {t}")
        return False, f"timeout sending format command: {t}"
    except requests.exceptions.RequestException as e:
        logging.error(f"{e}")
        print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] " + Col.end + f"({e})")
        return False, f"request failed: {e}"

    logging.info(f"Awaiting DVR HFS file system to be present again for {dvr.dvrName}")
    print(Col.yellow + "[INFO] Awaiting [" + str(dvr.dvrName) + "] HFS file system to be present again . . ." + Col.end, flush=True)

    # Capped poll: previously this was `while (True): ...`, which had no
    # exit if a DVR's filesystem never came back — that hung this thread
    # forever, and every other DVR's already-finished format sat blocked
    # on the shared barrier waiting for a thread that would never arrive.
    waited = 0
    while waited < FORMAT_TIMEOUT_SECONDS:
        sleep(3)
        waited += 3
        dvr.reset()
        if (dvr.fsState.strip() != "N/A"):
            logging.info("Formatting completed successfully.")
            print(Col.green + "[SUCCESS] [" + str(dvr.dvrName) + "] HFS file system detected  - - - - - > State: [" + str(dvr.fsState) + "]" + Col.end, flush=True)
            # fsState coming back does not mean the device is done indexing
            # the freshly-formatted filesystem — give it time to settle
            # before returning control to any code that might immediately
            # try to swap slots (see MEDIA_SETTLE_TIMEOUT_SECONDS above).
            _wait_for_media_settle(dvr)
            return True, f"success (slot: {dvr.actMediaSlot})"

    logging.error(f"Format timed out on {dvr.dvrName} after {FORMAT_TIMEOUT_SECONDS}s")
    print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] did not come back online within " + str(FORMAT_TIMEOUT_SECONDS) + "s — marking failed, continuing with other DVRs" + Col.end, flush=True)
    return False, f"timed out after {FORMAT_TIMEOUT_SECONDS}s waiting for filesystem (slot: {dvr.actMediaSlot})"


def _send_slot_toggle(dvr: object) -> bool:
    """Sends a single eParamID_ChangeSlot='6' toggle command. Returns True
    if the request itself succeeded (says nothing about where the slot
    ended up — that's checked separately, since the device appears to
    cycle through an intermediate 'No Media' relay position even when both
    bays have media inserted, so one toggle does not reliably mean 'now on
    the other real slot')."""
    reqParams = {'action': 'set', 'paramid': 'eParamID_ChangeSlot', 'value': '6'}
    changedParams = {'name': 'eParamID_ChangeSlot', 'value': '6'}
    try:
        setReq(dvr.ip, "config", reqParams, changedParams)
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"{e}")
        print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] slot toggle request failed: " + str(e) + Col.end, flush=True)
        return False


def _swap_to_slot(dvr: object, target_slot: str) -> bool:
    """Toggles dvr's active slot until it reaches target_slot ('S1' or
    'S2'). Confirmed device behavior: the toggle command cycles in a fixed
    order — S1 -> S2 -> No Media -> S1 -> ... — even when both bays are
    physically loaded (i.e. 'No Media' here is a relay position in the
    cycle, not necessarily an empty bay). Given that fixed order, this
    checks dvr.actMediaSlot, computes exactly how many toggles are needed
    to reach target_slot from wherever it currently sits (1 or 2 — never
    more, since the cycle length is 3), and sends exactly that many —
    verifying position after each one rather than assuming the device
    behaved as expected. If the device ever lands somewhere other than
    what the known cycle predicts, that's logged as a warning and a few
    extra toggles are attempted as a fallback rather than failing outright.
    Returns True once dvr.actMediaSlot == target_slot, or False if it
    couldn't get there."""

    CYCLE = ["S1", "S2", "No Media"]

    current = dvr.actMediaSlot
    if current == target_slot:
        return True

    if current not in CYCLE:
        logging.warning(f"{dvr.dvrName}: unrecognized slot state '{current}', falling back to trial toggling")
        steps_needed = len(CYCLE) - 1  # unknown position — don't assume, just cap generously below
    else:
        steps_needed = (CYCLE.index(target_slot) - CYCLE.index(current)) % len(CYCLE)

    logging.info(f"{dvr.dvrName}: at {current}, target {target_slot} — {steps_needed} toggle(s) expected")
    print(Col.yellow + "[INFO] [" + str(dvr.dvrName) + "] at " + str(current) + ", target " + target_slot +
          " — " + str(steps_needed) + " toggle(s) expected" + Col.end, flush=True)

    max_toggles = len(CYCLE)  # safety cap in case actual behavior deviates from the known cycle
    for attempt in range(1, max_toggles + 1):
        prev = dvr.actMediaSlot

        if not _send_slot_toggle(dvr):
            return False

        waited = 0
        changed = False
        while waited < SLOT_SWAP_TIMEOUT_SECONDS:
            sleep(2)
            waited += 2
            dvr.reset()
            # DIAGNOSTIC: print every tracked field each poll so a stuck
            # swap shows exactly what is/isn't moving. Once we know which
            # field actually reflects "busy," this can be trimmed back down.
            print(Col.yellow + "[DEBUG] [" + str(dvr.dvrName) + f"] t+{waited}s  "
                  f"actMediaSlot={dvr.actMediaSlot}  mediaLoading={dvr.mediaLoading}  "
                  f"state={dvr.state}  changeSlot={dvr.changeSlot}  "
                  f"fsState={dvr.fsState}  storagePath={dvr.storagePath}" + Col.end, flush=True)
            logging.info(f"{dvr.dvrName} t+{waited}s actMediaSlot={dvr.actMediaSlot} "
                         f"mediaLoading={dvr.mediaLoading} state={dvr.state} "
                         f"changeSlot={dvr.changeSlot} fsState={dvr.fsState} storagePath={dvr.storagePath}")
            if dvr.actMediaSlot != prev:
                changed = True
                break

        if not changed:
            logging.error(f"{dvr.dvrName}: slot toggle did not register within {SLOT_SWAP_TIMEOUT_SECONDS}s")
            print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] slot toggle did not register within " +
                  str(SLOT_SWAP_TIMEOUT_SECONDS) + "s" + Col.end, flush=True)
            return False

        if dvr.actMediaSlot == target_slot:
            if attempt != steps_needed:
                logging.warning(f"{dvr.dvrName}: reached {target_slot} after {attempt} toggle(s), "
                                 f"expected {steps_needed} — cycle order may not be exactly as assumed")
            else:
                logging.info(f"{dvr.dvrName} reached target slot {target_slot} after {attempt} toggle(s), as expected")
            print(Col.green + "[SUCCESS] [" + str(dvr.dvrName) + "] slot is now " + str(dvr.actMediaSlot) +
                  Col.end, flush=True)
            return True

        # Didn't land on target yet — if we've already used the expected
        # number of toggles and still aren't there, the device is behaving
        # differently than the known cycle predicts; log it clearly but
        # keep trying up to max_toggles rather than giving up immediately.
        if attempt >= steps_needed:
            logging.warning(f"{dvr.dvrName}: toggle {attempt} landed on {dvr.actMediaSlot}, "
                             f"not {target_slot} as the known cycle predicted — continuing to retry")

    logging.error(f"{dvr.dvrName}: could not reach {target_slot} after {max_toggles} toggles (stuck on {dvr.actMediaSlot})")
    print(Col.red + "[ERROR] [" + str(dvr.dvrName) + "] could not reach " + target_slot + " after " +
          str(max_toggles) + " toggles — currently on " + str(dvr.actMediaSlot) + Col.end, flush=True)
    return False


def formatDvrs(dvr: object, slot: str = "both") -> object:
    with spaghetti_semaphore:

        # slot: "S1", "S2", "current" (whatever this DVR's active slot is
        # right now), or "both" (default — matches original behavior).
        #
        # The device only formats whatever slot is currently active — there
        # is no 'format all slots' or 'format slot N directly' command, and
        # switching slots is a toggle that can pass through an intermediate
        # 'No Media' relay state (see _swap_to_slot) rather than a clean
        # direct switch — so every slot change below goes through
        # _swap_to_slot with an explicit target rather than a single blind
        # toggle.
        original_slot = dvr.actMediaSlot
        results = []  # list of (slot_name, success, message) for reporting

        def format_now():
            ok, msg = _format_current_slot(dvr)
            results.append((dvr.actMediaSlot, ok, msg))
            return ok

        def restore_original():
            if original_slot in ("S1", "S2") and dvr.actMediaSlot != original_slot:
                if not _swap_to_slot(dvr, original_slot):
                    results.append((original_slot, False,
                                    f"failed to swap back to original slot {original_slot} — "
                                    f"DVR is left on {dvr.actMediaSlot}, verify manually"))

        if slot == "current":
            if original_slot not in ("S1", "S2"):
                results.append(("(active slot)", False,
                                 f"no media in currently active slot ({original_slot}) — nothing to format"))
            else:
                format_now()

        elif slot in ("S1", "S2"):
            if _swap_to_slot(dvr, slot):
                format_now()
                restore_original()
            else:
                results.append((slot, False, f"could not reach slot {slot} (currently {dvr.actMediaSlot}) — format not sent"))

        else:  # "both"
            if _swap_to_slot(dvr, "S1"):
                format_now()
            else:
                results.append(("S1", False, f"could not reach S1 (currently {dvr.actMediaSlot}) — skipping"))

            if _swap_to_slot(dvr, "S2"):
                format_now()
            else:
                results.append(("S2", False, f"could not reach S2 (currently {dvr.actMediaSlot}) — skipping"))

            restore_original()

        overall_ok = bool(results) and all(r[1] for r in results)
        summary = "; ".join(f"{name}: {'ok' if ok else 'FAILED'} ({msg})" for name, ok, msg in results)

        setattr(dvr, "format_failed", not overall_ok)
        setattr(dvr, "format_result", summary if summary else "no action taken")
    return dvr
 
def stringout(dvrList):
    # Stringout creation tool
    successStartedJobs = 0
    compCount = 0
    jobWatchDog = 0 
    logging.info(f"Called stringout creation tool") 
    print("=== Stringout Creation Tool ===")
    print("Enter the desired clip/take number pair")
    print("For example: _2+1 or _1")
    
    clipSessionId = input("Clip + Take Number: ")
    # Clean the plus signs for the URLs
    clipSessionId.replace("+", "%2b")

    print("Enter the starting timehack for the stringout clip. Ex: If launch time T0 is " + Col.yellow + "22:03:00:00" + Col.end + ", go back 1 min and enter " + Col.green + "22:02:00:00" + Col.end)
    print("Format: HH:MM:SS:mm")
    startTh = input("Starting timehack: ")

    print("Enter the duration of the stringout clip. Should be 3 minutes! Ex: " + Col.green + "00:03:00:00" + Col.end)
    print("Format: HH:MM:SS:mm")
    duration = input("Clip duration: ")

    print("== Confirm Settings ==")
    print("Confirm settings for stringout creation:")
    print("Clip/Take:", clipSessionId)
    print("Start Time:", startTh)
    print("Duration:", duration)
    confirm = input("Are these settings correct? (Y/N): ")

    if(confirm == "y" or confirm == "Y"):
        # Setting queued DVRs to Data-LAN Settings
        print(Col.yellow + "[COMMAND]" +  Col.end + " All DVRs to Data-LAN Mode")

        dataLanSetting = {'name': 'eParamID_MediaState', 'value': '1'}
        reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
        setting = "config"
        totalErrors = 0

        for dvr in dvrList:
            totalErrors += setReq(dvr.ip, setting, reqParams, dataLanSetting)

        if (totalErrors == 0):
            print(Col.green + "[SUCCESS]" + Col.end + " All DVRs in Data-LAN Mode")
       
        print("== Status ==")

        for dvr in dvrList:
            
            # Creating path based on active media slot
            if(dvr.actMediaSlot == "S1"):
                devPath = "/mnt/S1/AJA/"
            else:
                devPath = "/mnt/S2/AJA/"

            setting = "mediaedit"
            
            arg1 = devPath + dvr.clipName + clipSessionId + ".mov"
            arg2 = devPath + dvr.clipName + ".mov"
            arg1 = arg1.replace("+", "%2b")
            arg2 = arg2.replace("+", "%2b")

            # Stringout request module, customized to parse AJA's malformed 'JSON' requests...
            url = "http://" + dvr.ip + "/" + setting + "?action=subclip&arg1=" + arg1 + "&arg2=" + arg2 + "&arg3=" + startTh + "&arg4=" + duration
            
            # Pull the text out of the response, comes out as a linebroken text.
            response = requests.get(url, timeout = 5)
            respText = response.text
            
            # Parse the string response
            cleanedResp = respText.replace("\n", "\", \"")
            cleanedResp = cleanedResp[:-4]
            cleanedResp = "{\"" + cleanedResp + "\"}"
            cleanedResp = cleanedResp.replace(": ", "\": \"")
            
            # Converting parsed response to JSON dict-type DS
            try:
                jobStatus = json.loads(cleanedResp)
            except requests.exceptions.InvalidJSONError as err:
                logging.error(f"{err}")
                print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
                sleep(3)
                exit(-1)
            except requests.exceptions.Timeout as t:
                logging.error(f"{t}")
                print(Col.yellow + "[WARNING]" + Col.end + " {t}")
            except requests.exceptions.RequestException as e:
                logging.error(f"{e}")
                print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
                sleep(3)
                exit(-1)
            print(jobStatus)
            
            if(jobStatus.get('error') != "none"):
                print(Col.red + "[ERROR]" + Col.end + " Job failed to start on", dvr.dvrName, "reason:", jobStatus.get('error'))
            
            else:
                print(Col.green + "[SUCCESS]" + Col.end +" Job started on", dvr.dvrName)

                # If the job started successfully give it's object the proc ID. If it was unsuccessful it will remain as 0
                setattr(dvr, "procID", jobStatus.get('id'))
                successStartedJobs += 1 

        print("== Job Status ==")
        print("Polling successfully started jobs for completion")
        
        setting = "mediaedit"
        logging.debug("Polling DVRS for completion of stringout jobs")
        while compCount < successStartedJobs and jobWatchDog < 10:
            
            compCount = 0
            
            for dvr in dvrList:
            
                if(dvr.procId != 0):
                    # Creating the query string to find the job status
                    url = "http://" + dvr.ip + "/" + setting + "?action=status&id=" + id
                
                    response = requests.get(url, timeout = 5)
                    respText = response.text
                            
                    # Parse the string response
                    cleanedResp = respText.replace("\n", "\", \"")
                    cleanedResp = cleanedResp[:-4]
                    cleanedResp = "{\"" + cleanedResp + "\"}"
                    cleanedResp = cleanedResp.replace(": ", "\": \"")
                    try:
                        stat = json.loads(cleanedResp)
                        logging.info(f"The job status is as follows for {dvr.procID}: {stat} with status {stat['status']}") 
                    except requests.exceptions.InvalidJSONError as err:
                        logging.error(f"{err}")
                        print(Col.red + "[ERROR] " + Col.end + "({err})\nAborting and exiting in 3 seconds . . ." )
                        sleep(3)
                        exit(-1)
                    except requests.exceptions.Timeout as t:
                        logging.error(f"{t}")
                        print(Col.yellow + "[WARNING]" + Col.end + " {t}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"{e}")
                        print(Col.red + "[ERROR] " + Col.end + "({e})\nAborting and exiting in 3 seconds . . ." )
                        sleep(3)
                        exit(-1)

                    if(stat['status'] == "Completed"):
                        compCount += 1
            sleep(3)
            jobWatchDog += 1
            logging.debug(f"Stringout job polling completed {jobWatchDog} iteration(s) {compCount} out of {successStartedJobs} complete")
       
        #logging.info(f"All requested stringout jobs complete, calling dlManager in stringout mode to download!")
        print(Col.yellow + "[INFO] " + Col.end + "Where would you like to download the stringouts to?")
        tk = Tk()
        tk.withdraw()
        workDir = os.path.dirname(os.path.realpath(os.sys.argv[0]))
        workDir += "/stringouts"
        if (not os.path.isdir(workDir)):
            os.mkdir(workDir)
            logging.info(f"Created directory: {workDir}")
        downloadDir = askdirectory(parent=tk, initialdir=workDir, title="Select stringouts download directory")  
       
        # Create download strings for fetching the stringouts
        for dvr in dvrList:
            stringoutName = dvr.clipName + ".mov"
            logging.info(f"Creating stringoutName: {stringoutName}")
            # Ex http://192.168.190.1/media/clip.mov,/home/alavaae/aja/\\clip.mov
            dvr.downloadStrings.append("http://" + dvr.ip + "/media/" + stringoutName + "," + downloadDir + "\\" + stringoutName)
            logging.info(f"Appending to download string array: {dvr.downloadStrings}")
        print(Col.yellow + "[INFO]" + Col.end + " Stringout creation complete. Stringout download ready. Proceed to archiving when ready")
        sleep(5)
        menu(dvrList)
    else:
        print("Settings not confirmed, trying again")
        stringout(dvrList)

def checkConnection(kiObj: object) -> bool:
    # Using current IP, check the get request status code 
    try: 
        req = requests.get(kiObj.connection_url,timeout=5)
        setattr(kiObj,"http_status_code", req.status_code)

        #print(Col.red +"[DEBUG 1111] " + "req: " + str(req) + Col.end)
    except requests.exceptions.ConnectionError:
        logging.error(f"Connection error on {url}")
        #print(Col.yellow + "[INFO] " + Col.end + f"See logs. Safely Exiting . . .")
        exit(-1)
    except requests.exceptions.HTTPError:
        logging.error(f"HTTP error on {url}")
        #print(Col.yellow + "[INFO] " + Col.end + f"See logs. Safely Exiting . . .")
        exit(-1)
    except requests.exceptions.Timeout:
        logging.error(f"Timeout error on {url}")
        #print(Col.yellow + "[INFO] " + Col.end + "See logs. Safely Exiting . . .")
        exit(-1)
    except requests.exceptions.TooManyRedirects:
        logging.error(f"Redirection error on {url}")
        #print(Col.yellow + "[INFO] " + Col.end + f"See logs. Safely Exiting . . .")
        exit(-1)
    if kiObj.http_status_code == 200:     
        return True
    else:
        return False

def clear_screen():
    # Do I want to make two functions where one is just running a clear command based on OS while another function just returns the os.name it detects?
    # That way there is reusability of the function for other purposes in the future
    try: 
        if ( os.name == "posix"):
            clear = lambda: os.system('clear')
            clear()
        if ( os.name == "nt"):
            clear = lambda: os.system('cls')
            clear()
    except NameError as uk:
       print(Col.red + "[ERROR]" + Col.end + f"{uk}.")
       exit(-1) 
    except OSError as op:
        print(Col.yellow + "[WARNING] " + Col.end + f"{op}. Unknown Operating System detected.")
        exit(-1)
    except Exception as e:
        print(Col.red + "[ERROR] " + Col.end + f"{e}")
        exit(-1)

def version_detection():

    major = 3
    minor = 11
    micro = 1
    
    try:
        if ( sys.version_info.major != major or sys.version_info.minor != minor or sys.version_info.micro != micro):
            print(Col.yellow + "]" + Col.end, end='\r')
            print(Col.yellow + "[WARNING] " + Col.end + "Python version may be out of date...\n" + Col.yellow + "[INFO]" + Col.end + " Current version: Python v" + Col.red + str(sys.version_info.major) + "." + str(sys.version_info.minor) + "." + str(sys.version_info.micro) + Col.end + " Expected version: Python v" +Col.green +  str(major) + "." + str(minor) + "." + str(micro) + Col.end)
            logging.warning(f"Python version mismatch, features may or may not be support. Unexpected errors may occur")
        else:  
            print(Col.yellow + "]" + Col.end, end='\r')
            print(Col.yellow + "[INFO] " + Col.end + "Current version: Python v" + Col.green + str(sys.version_info.major) + "." + str(sys.version_info.minor) + "." + str(sys.version_info.micro) + Col.end + " Expected version: Python v" +Col.green +  str(major) + "." + str(minor) + "." + str(micro) + Col.end)
            logging.info(f"Python version match")
    except NameError as nm:
        logging.error(f"{nm}")
        traceback.format_exc()
        logging.debug(traceback.format_exc())
        exit(-1)
    except Exception as e:
        logging.warning(f"Uncaught Exception: {e}\n Exiting . . .")
        logging.debug(traceback.format_exc())
        exit(-1)

def inventory_file_selection_loader(dvrList: object) -> object: 
    try:
        # Needed to close additional Tk window when calling inventoryFile 
        dummy = Tk()
        dummy.withdraw()

        print(Col.yellow + "[INFO]" + Col.end + " Enumerating remote devices, standby...", flush=True)
        print(Col.yellow + "[Loading]" + Col.end, flush=True)
        
        inventoryFile = askopenfilename(parent=dummy, initialdir=".", title = "Select inventory CSV", filetypes=(("CSV Files","*.csv"),))
        logging.info(f"Attempting to use file: {inventoryFile}")
    except PermissionError:
        logging.error(f"Permissions error detected when trying to open inventory file. Exiting")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except FileNotFoundError: 
        logging.error(f"Inventory file not found. Exiting")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except SystemExit:
        logging.debug(f"System Exit")
    except KeyboardInterrupt:
        logging.debug(f"User keyboard interrupted execution")
    except Exception as e:
        logging.warning(f"Unaccounted for exception: {e}")
    try: 
        with open(inventoryFile, 'r') as readPtr:
            csvReader = reader(readPtr)

            # Skip header entry
            header = next(csvReader)
        
            if (header != None):
                for row in csvReader:
                    # For each of the entries initialize the object
                   
                    # create obj for kiproultra to store a unique kiprojultra object to the dvrList with ip stored
                    kiObj = KiProUltra(row[0].strip())

                    #print(Col.red +"[DEBUG 1216] " + "dvrmem: "  + str(kiObj) +"\tip: "+ str(kiObj.ip) + Col.end)
                    # fix if statement and see if correct obj is used in list

                    if (checkConnection(kiObj)):
                        try:
                            # actually send the same initial object class to the global dvr list
                           
                            dvrList.append(kiObj)
                            
                            setattr(kiObj,"check", True)

                            #print(Col.red +"[DEBUG 1221] " + "dvrmem: " + str(kiObj) + "\tdvrname: " + str(kiObj.dvrName) +"\tcheck: " + str(kiObj.check) + Col.end)
                     
                        except Exception as e:
                            logging.error(f"Failed to initialize dvrList object {row[0]}")
        return dvrList
    except SystemExit:
        logging.debug(f"System Exit")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except KeyboardInterrupt:
        logging.debug(f"User keyboard interrupted execution")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except FileNotFoundError as e:
        logging.error(f"{e}")
        logging.debug(traceback.format_exc())
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except FileExistsError as ee:
        logging.error(f"{ee}")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1)
    except Exception as uk:
        logging.warning(f"Unaccounted for exception: {uk}")
        print(Col.yellow + "\r[INFO]" + Col.end + " See logs. Safely Exiting . . .")
        exit(-1) 
    print(Col.yellow + "]" + Col.end, end='\r')

def dvr_check_hardware_software_match(dvr: object) -> bool:
    # At the time of testing, the dvr listed on the web may not match the physical hardware path due to testing on one box
    # Should only impact on testing environment and not live environment, otherwise an extra safe procedure 
    try:
        if ( dvr.storagePath == "/mnt/S1/AJA"):
            logging.info(f"{now()} {dvr.storagePath} set to {dvr.actMediaSlot}") 
            
            setattr(dvr, "storagePath", dvr.storagePath)
            return True
        elif ( dvr.storagePath == "/mnt/S2/AJA"):
            logging.info(f"{now()} {dvr.storagePath} set to {dvr.actMediaSlot}") 
         
            setattr(dvr, "storagePath", dvr.storagePath)
            return True
        else:
            logging.error(f"{now()} Storage Path [{dvr.dvrName, dvr.ip}]: {dvr.storagePath} {dvr.actMediaSlot} is none! Terminating")
            return False
    except Exception as e:
            logging.error(f"{e}")
            exit(-1)

def dvr_swap(dvr,lock,event):
    # Function should utilize Lock object because both threads are going to interact with a shared object (AJA unit) class.
    # The Lock will prevent race conditions for both threads and any unintended outcomes.
    
    lock_ = threading.Lock()
    event_ = threading.Event()
    with lock:
        try: 

            logging.info(f"[DEBUG 1296] lock mem check:  {lock}")
            logging.info(f"[DEBUG 1297] event in swap mem check: {event}")
            logging.info(f"Media Slot before swap: {getattr(dvr,'actMediaSlot')}")
            slotSettings = {'name': 'eParamID_ChangeSlot', 'value': '6'}
            reqParams = {'action': 'set', 'paramid': 'eParamID_ChangeSlot', 'value': '6'}
            setting = "config"
            error = 0 
            error = setReq(dvr.ip, setting, reqParams, slotSettings)
            if ( error != 0):
                logging.error(f"setReq Fail dvr_swap! {dvr.ip}")    
            logging.info(f"{now()} event set for {event} with error {error}")
            setattr(dvr,"actMediaSlot",(getReq2(dvr.ip, "config", {'action': 'get', 'paramid': 'eParamID_SelectedSlot'},dvr.actMediaSlot,lock_ , event_))['value_name'])
            event_.wait()
            logging.info(f"Media Slot after swap without direct get request: {getattr(dvr,'actMediaSlot')}")
            event.set()
             
        except Exception as e:
            logging.error(f"{e}") 
            exit(-1)
    
def storage_path_after_swap(dvr: object) -> str:
    
    # The Event object should be utilized for each thread to control execution flow 
    # event = threading.Event()  
    # event.wait(timeout=None)  // we should probably set a 10s timeout for debugging - - - - > event.set()
    with lasaga_semaphore:
        lock_1 = threading.Lock()
        event_1 = threading.Event()
        logging.info(f"{now()} DVR object in memory address: {dvr}")
        th = threading.Thread(target=dvr_swap, args=(dvr,lock_1,event_1,))
        th.start() 
        logging.info(f"{now()} Thread-1 identity tag #: {th.ident}")
        logging.info(f"{now()} Event-1 object: {event_1} ")
        logging.info(f"{now()} Thread-1 is alive: {th.is_alive()} \tlock-1 object memory address: {lock_1} \tevent-1 object memory address: {event_1}")
        
        event_1.wait()
        th.join()
        logging.info(f"{now()} Thread-1 joined: {th.is_alive()} \tevent-1 is set {event_1.is_set()} lock-1 status: {lock_1}")
        # breaks loop when both events are set
        lock_2 = threading.Lock()
        event_2 = threading.Event()
        th2 = threading.Thread(target=dvr_swap, args=(dvr,lock_2,event_2,))
        logging.info(f"{now()} Thread-2 is alive: {th2.is_alive()} \tlock-2 object memory address: {lock_2} \tevent-2 object memory address: {event_2}")
        logging.info(f"{now()} The storage path is currently set to: {dvr.storagePath} vs getattr: {getattr(dvr,'storagePath')}")
        logging.info(f"{now()} The active media slot is now: {getattr(dvr,'actMediaSlot')}")
        while(not( event_1.is_set() and  event_2.is_set())) :
            sleep(1)
            logging.info(f"The active media slot is now: {getattr(dvr,'actMediaSlot')}")

            if (getattr(dvr,'actMediaSlot') == "No Media"):
                    
                th2.start()
                logging.info(f"{now()} Thread-2 identity tag #: {th2.ident}")
                logging.info(f"{now()} Event-2 object: {event_2}")

                event_2.wait()
                th2.join()
                # Safe to set storagePath to what is stored in active Media Slot, either /mnt/S1/AJA or /mnt/S2/AJA
                if ( getattr(dvr,'actMediaSlot') == "S1"):
                    setattr(dvr,'storagePath',"/mnt/S1/AJA")
                else:
                    setattr(dvr,'storagePath',"/mnt/S2/AJA")    
                logging.info(f"{now()} Thread-2 is alive: {th2.is_alive()} \tlock-2 object memory address: {lock_2} \tevent-2 object memory address: {event_2}")
                logging.info(f"{now()} The storage path is currently set to: {dvr.storagePath} vs getattr: {getattr(dvr,'storagePath')}")
                logging.info(f"{now()} The active media slot is now: {getattr(dvr,'actMediaSlot')}")
        return (dvr.storagePath)    

def inventory_check_for_partial_storage_removal(dvr: object) -> bool:
    # Function that checks the slot media is present on the AJA unit for both slots
    # Relies on the storage path. Due to the function, storage_path_after_swap, we need to ensure that each dvr has a thread lock 
    storage_path_before_1st_pass = getattr(dvr,"storagePath")
    logging.info(f"{now()} storage_path before 1st swap: {storage_path_before_1st_pass}")
    storage_path_after_1st_pass = storage_path_after_swap(dvr)
    
    logging.info(f"{now()} storage_path_after_1st_pass:   {storage_path_after_1st_pass} storage_path_before_1st_pass:  {storage_path_before_1st_pass}")
    logging.info(f"{now()} storage_path before 2nd swap: {storage_path_after_1st_pass}")
    storage_path_after_2nd_pass = storage_path_after_swap(dvr)
    logging.info(f"{now()} storage_path_after_2nd_pass:   {storage_path_after_2nd_pass} storage_path_before_2nd_pass:  {storage_path_after_1st_pass}")
    
    return (storage_path_before_1st_pass == storage_path_after_2nd_pass)

def network_conn_check(dvr: object) -> object:
    print(Col.yellow + "[Loading] ["+ dvr.ip +"] [" + dvr.dvrName + "] ["+ dvr.clipName + "]" + Col.end + " Network Connectivity Check ", flush=True)

    check = getattr(dvr, "check")

    logging.info("[DEBUG 1430] " + "check: " + str(check))

    if (check):
        setattr(program_state, "dvrCounter", program_state.dvrCounter+1)

    logging.info(f"{now()} Hit Barrier for {dvr}")
    program_state.barrier.wait()

    return dvr

def inventory_storage_slot_scan(dvr: object) -> object:
   
    print(Col.yellow + "[Loading] ["+ dvr.ip +"] [" + dvr.dvrName + "] ["+ dvr.clipName + "]" + Col.end + " Storage Scan ", flush=True)
            
    check = getattr(dvr, "check")
    changeSlot = getattr(dvr, "changeSlot")
    storageAlarm = getattr(dvr, "storageAlarm")
    complete_storage_removal = getattr(dvr, "storageAlarmInteger")
    changeSlot = getattr(dvr, "changeSlot")
            
    logging.info("[DEBUG 1336] " + "dvr mem: " + str(dvr) + "\tcheck: " + str(check) + "\tstorageAlarm: " + str(storageAlarm) + "\tchangeSlot: " + str(changeSlot) )
            
    if (check):

        setattr(program_state, "dvrCounter", program_state.dvrCounter+1)
        print(Col.yellow + "[Loading] [" + dvr.ip + "] [" + dvr.dvrName + "] ["+ dvr.clipName + "]" + Col.end + " Complete Storage Removal Check ", flush=True) 

        # Ensure that there is atleast 1 storage media inserted and that the aja is not in a state where it is asking for, "Please Reboot" 
        if ( complete_storage_removal == 1 and changeSlot != "Please Reboot"):
                    
            # Watch out for invalid storagePath during media transitions
            if (not (dvr_check_hardware_software_match(dvr))):
                
                print(Col.red + "[ERROR] [" + dvr.ip + "]  [" + dvr.dvrName + "] [" +dvr.clipName + "]" + Col.end + " Storage mount path failed to detect !!! Terminating !")
                logging.shutdown()
                exit(-1)

            with Garfield (max_workers=1) as executor:
                # swapping operations requiring threads
                partial_swap_results = [executor.submit(inventory_check_for_partial_storage_removal, dvr)]
                # Will not go past this point until all results come back
                concurrent.futures.wait(partial_swap_results)

            for partial_swap_result in partial_swap_results:
                if ( partial_swap_result.result() ):
                    logging.info(f"Garfield has completed partial storage removal checks on all threads, partial_swap_result[]: {partial_swap_results}")
                    setattr(program_state, "dvrStorageCounter", program_state.dvrStorageCounter+1)
                    setattr(dvr, "storage_all_loaded", True)
                    print(Col.green + "[SUCCESS] [" + dvr.ip + "] [" + dvr.dvrName + "] [" + dvr.clipName + "]" + Col.end + " Complete Storage Removal Check Completed")
                else:
                    print(Col.yellow + "[WARNING]" + Col.end + " Storage slot partially removed on " + Col.yellow + "[" + dvr.clipName + "][" + dvr.ip + "]" + Col.end + ". INSERT MISSING STORAGE SLOT AND REBOOT!")
                    logging.error(f"Partial storage slot detected on [{dvr.clipName}][{dvr.ip}]")                  
        else: 
            print(Col.red + "[ERROR]" + Col.end + " Storage slot removed completely on " + Col.red + "[" + dvr.clipName + "][" + dvr.ip + "]" + Col.end + ". REBOOT REQUIRED!")
            logging.critical(f"One of the storage slots for {dvr.clipName}{dvr.ip} was removed")
            logging.error(f"Storage slot removed detected! . . .")
    
    logging.info(f"{now()} Hit Barrier for {dvr}")
    program_state.barrier.wait()

    return dvr

def inventory_name_update(dvrList):
    # eParamID_SysName & eParamID_CustomClipName
    setting = "config"
    
    for dvr in dvrList:
        dvrRequest = json.loads('{\"action\": \"set\", \"paramid\": \"eParamID_SysName\", \"value\": \"' + dvr.dvrName + '\"}')
        clipRequest = json.loads('{\"action\": \"set\", \"paramid\": \"eParamID_CustomClipName\", \"value\": \"' + dvr.clipName + '\"}')
        dvrParams = json.loads('{\"name\": \"eParamID_SysName\", \"value\": \"' + dvr.dvrName + '\"}')
        clipParams = json.loads('{\"name\": \"eParamID_CustomClipName\", \"value\": \"' + dvr.clipName + '\"}')

        errorD = setReq(dvr.ip, setting, dvrRequest, dvrParams)
        errorC = setReq(dvr.ip, setting, clipRequest, clipParams)

        if (errorD != 0 and errorC != 0):
            print("[DEBUG 1388] Failure to update inventory dvrName and clipName")
            logging.info(f"inventory_name_updates requests update failure!!!")

    return dvrList

def main():

    # Clear display
    clear_screen()

    # Start logging service   
    logger()
    program_state.print_memory_addresses()
    # Detect python version in use 
    version_detection()

    # Open inventory CSV file and initialize the DVR objects
    dvrList = []
    dvrList = inventory_file_selection_loader(dvrList)
    logging.info(f"The inventory file selection loader completed\n DVR List: {dvrList}")
    logging.info(f"{now()} The ProgramState object is callable at {program_state} , instance of object {isinstance(program_state, object)}, thats resides at {hex(id(program_state))} \ndvrStorageCounter: {getattr(program_state,'dvrStorageCounter')} mem(dvrStorageCounter): {hex(id(getattr(program_state,'dvrStorageCounter')))}")

    # Create Barrier - timeout of 2 mins. 
    setattr(program_state, "barrier", threading.Barrier(len(dvrList), timeout=120))
    
    # Check network connectivity to DVRs on list
    with Garfield (max_workers=len(dvrList)) as executor:
        futures_conn_results = [executor.submit(network_conn_check, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures_conn_results)

    for dvr in futures_conn_results:
        logging.info(f"Garfield has completed connectivity checks on: [{dvr.result()}] ")
        print(Col.green + "[SUCCESS] [" + dvr.result().ip + "] [" + dvr.result().dvrName + "] [" + dvr.result().clipName + "]" + Col.end + " Connectivity Check Completed", flush=True)
     
    # Get dvr Counters
    #dvrStoreCounter = getattr(program_state, "dvrStorageCounter") 
    dvrCounter = getattr(program_state, "dvrCounter")
    
    # Barrier setup will not continue until all dvrs are accounted for. If threads are not executing as intended, we wont get past this point and will have to troubleshoot deadlocks/race conditions
    if (dvrCounter != len(dvrList)):
        print(Col.red + "[FAIL]" + Col.end + " DVR storage loading failed to initialize. Loaded: " + Col.red + str(dvrCounter) + "/" + str(len(dvrList)) + Col.end, flush=True)
        print(Col.yellow + "[INFO] " + Col.end + "Exiting ...", flush=True)
        logging.error(f"Failed to initialize all DVRS on inventory list. Exiting", flush=True)
        exit(-1)
    else:
        print(Col.green + "[SUCCESS]" + Col.end + " DVR storage loading initialized. Loaded: " + Col.green + str(dvrCounter) + "/" + str(len(dvrList)) + Col.end, flush=True)
        #logging.info(f"DVR storage loaded {dvrStoreCounter}/{dvrCounter}")
        logging.info(f"DVR storage loaded {dvrCounter}/{len(dvrList)}")

 
    # TODO: Implement multi-threading where applicable 
    # TODO: EI-3
    # TODO: EI-7
    
    menu(dvrList)

if __name__ =="__main__":
    main()
