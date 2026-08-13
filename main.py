from kivymd.app import MDApp
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.utils import platform
import os
import asyncio
import json

from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.dropdown import DropDown
from CircularProgressBar import CircularProgressBar

from BLE import Connection, communication_manager
from gpshelper import GpsHelper
from AccHelper import AccHelper
from GyroHelper import GyroHelper
from kivy.factory import Factory

# ADDRESS, UUID = "78:21:84:9D:37:10", "0000181a-0000-1000-8000-00805f9b34fb"
ADDRESS, UUID = None, None
GPS_ON = True
disconnect_flag = {'disconnect': False}


class MainWindow(Screen): pass


class SecondaryWindow(Screen): pass


class WindowManager(ScreenManager): pass


class SpinnerDropdown(DropDown): pass


class Main(MDApp):
    screen_flag = True

    datajson_queue = asyncio.Queue()
    speed_queue = asyncio.Queue()
    device_queue = asyncio.Queue()
    battery_queue = asyncio.Queue()
    manipulation_queue = asyncio.Queue()

    def build(self):
        """setting design for application widget development specifications on design.kv"""
        self.theme_cls.theme_style = 'Dark'
        self.theme_cls.primary_palette = 'Gray'
        # GPS setup and start
        if GPS_ON:
            self.get_permissions()
        return Builder.load_file(filename='design.kv')

    async def launch_app(self):
        """Asyncronous function for kivy app start"""
        await self.async_run(async_lib='asyncio')

    async def start(self):
        """Asyncronous app making sure start is awaited as coroutine"""
        task = asyncio.create_task(self.launch_app())
        (_, pending) = await asyncio.wait({task}, return_when='FIRST_COMPLETED')

    def on_start(self):
        """On start method for building desired variables for later use"""
        Logger.info("Called start")

        # Ventana Conexión BLE
        # Menu desplegante oculto mientras no se busquen y encuentren dispositivos
        self.root.get_screen('main_window').ids.device_dropdown.text = ''
        self.root.get_screen('main_window').ids.device_dropdown.size = (0, 0)
        self.root.get_screen('main_window').ids.device_dropdown.opacity = 0

        # Ventana Status
        self.speedmeter_indicator = self.root.get_screen('secondary_window').ids.speed_indicator
        self.sp_indicator = self.root.get_screen('secondary_window').ids.sp_indicator
        self.manip_indicator = self.root.get_screen('secondary_window').ids.manip_indicator
        self.baterry_indicator = self.root.get_screen('secondary_window').ids.baterry_indicator

        # Ventana Settings
        self.angle_indicator = self.root.get_screen('secondary_window').ids.angle_indicator
        self.read_slider_text = self.root.get_screen('secondary_window').ids.read_slider_text
        self.per_button_pressed = True
        self.km_button_pressed = False
        self.slider_label = 'slider'
        self.slider_value = 0
        self.slider_flag = False

    def get_permissions(self):
        # Request permissions on Android
        if platform == 'android':
            from android.permissions import Permission, request_permissions
            def callback(permission, results):
                if all([res for res in results]):
                    print('Got all permissions')
                else:
                    print('Did not get all permissions')

            try:
                request_permissions(
                    [Permission.ACCESS_COARSE_LOCATION, Permission.ACCESS_FINE_LOCATION, Permission.BLUETOOTH,
                     Permission.BLUETOOTH_ADMIN, Permission.WAKE_LOCK, Permission.BLUETOOTH_CONNECT,
                     Permission.ACCESS_BACKGROUND_LOCATION,
                     callback])
            except Exception as e:
                print(e)

    def connect_ble(self, touch: bool) -> None:
        """Function handling BLE connection between App and ESP32"""
        if touch:
            self.root.get_screen('main_window').ids.spinner.active = True
            try:
                self.ble_task = asyncio.create_task(run_BLE(self, self.datajson_queue, self.battery_queue, self.device_queue, self.manipulation_queue))
                self.gps_task = GpsHelper().run(self.speed_queue)
                self.update_battery_task = asyncio.ensure_future(self.update_battery_value())
                self.update_speed_task = asyncio.ensure_future(self.update_speed_value())
                self.update_manipulation_task = asyncio.ensure_future(self.update_manipulation_value())
            except Exception as e:
                print(e)

    def device_clicked(self, _, value: str) -> None:
        """Handles events in main window dropdown"""
        if value == "ESP32":
            self.device_clicked_task = asyncio.ensure_future(self.device_event_selected(value))

    async def device_event_selected(self, value: str) -> None:
        await self.device_queue.put(value)
        self.root.get_screen('main_window').ids.spinner.active = True

    def switch_motion_detect(self, _, value: bool) -> None:
        """Switch state for adaptive mode switch. When in adaptive mode, the slider is disabled"""
        if value:
            self.root.get_screen('secondary_window').ids.adapt_slider.disabled = False
            self.datajson_queue.put_nowait(json.dumps({'adapt': 0}))
            self.root.get_screen('secondary_window').ids.adapt_slider.value = self.root.get_screen('secondary_window').ids.adapt_slider.min
        else:
            self.root.get_screen('secondary_window').ids.adapt_slider.disabled = False
            self.datajson_queue.put_nowait(json.dumps({'adapt': 1}))
            self.root.get_screen('secondary_window').ids.adapt_slider.value = self.root.get_screen('secondary_window').ids.adapt_slider.min

    def slider_unit_km(self, touch: bool) -> None:
        if touch:
            self.km_button_pressed = True
            self.per_button_pressed = False
            self.read_slider_text.text = f'0 km/h'
            self.root.get_screen('secondary_window').ids.adapt_slider.value = 0
            self.root.get_screen('secondary_window').ids.adapt_slider.max = 40
            self.root.get_screen('secondary_window').ids.adapt_slider.step = 1
            self.root.get_screen('secondary_window').ids.adapt_slider.color = "#A7D0D2"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_inactive = "#A7D0D2"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_active = "#A7D0D2"

    def slider_unit_per(self, touch: bool) -> None:
        if touch:
            self.km_button_pressed = False
            self.per_button_pressed = True
            self.read_slider_text.text = f'0 %'
            self.root.get_screen('secondary_window').ids.adapt_slider.value = 0
            self.root.get_screen('secondary_window').ids.adapt_slider.max = 100
            self.root.get_screen('secondary_window').ids.adapt_slider.step = 5
            self.root.get_screen('secondary_window').ids.adapt_slider.color = "#99998F"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_inactive = "#99998F"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_active = "#99998F"

    def slider_on_value(self, _, value: int) -> None:
        """Sends percentage of assistance wanted to ESP32
        In automatic mode: Use BATTERY and ACCELEROMETER values to determine percentage of use"""
        
        if self.per_button_pressed:
            self.read_slider_text.text = f'{value} %'
            value = int(180 * value / 100)
            label = 'slider_per'
        if self.km_button_pressed:
            self.sp_indicator.text = f'SP: {value}'
            self.read_slider_text.text = f'{value} km/h'
            value = value
            label = 'slider_km'

        self.slider_label = label
        self.slider_value = value

        if value == self.root.get_screen('secondary_window').ids.adapt_slider.max:
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = "red"
            if not self.slider_flag:
                self.datajson_queue.put_nowait(json.dumps({self.slider_label: self.slider_value}))
                self.slider_flag = True
        elif value == self.root.get_screen('secondary_window').ids.adapt_slider.min and not self.slider_flag:
            self.datajson_queue.put_nowait(json.dumps({self.slider_label: self.slider_value}))
            self.slider_flag = True
        else:
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = "white"
            self.slider_flag = False

    def slider_touch_up(self, *args) -> None:
        try:
            self.datajson_queue.put_nowait(json.dumps({self.slider_label: self.slider_value}))
        except asyncio.QueueFull:
            pass

    async def update_manipulation_value(self) -> None:
        """FOR DEBUGGING PURPOSES"""
        while True:
            try:
                manip = await self.manipulation_queue.get()
                manip = int(manip)
                self.manip_indicator.text = f'M: {manip}'  # TODO: convert to percentage
            except Exception as e:
                print(f'EXCEPTION IN MANIP: {e}')
                await asyncio.sleep(1.0)

    async def update_speed_value(self) -> None:
        """Monitors current speed of bike"""
        speed = 0
        while True:
            try:
                speed = await self.speed_queue.get()
                speed = float(speed)
                print(f"speed-> {speed}")
                await self.datajson_queue.put(json.dumps({'speed': speed}))
            except Exception as e:
                print(f'Exception in speed:: {e}')
                await asyncio.sleep(1.0)
            if float(speed) > 2 * self.speedmeter_indicator.end_value / 3.6:
                pass
            else:
                self.speedmeter_indicator.set_value = speed - 25
                self.speedmeter_indicator.text = f'{int(speed)} km/h'

    async def update_battery_value(self) -> None:
        """Monitors Battery life from bike"""
        max_battery_voltage = 23.7  # V //Voltage gotten when fully charged
        min_battery_voltage = 20.0  # V //Lowest voltage before battery starts getting damaged
        battery_life = 0
        while True: 
            try:
                current_battery_life = await self.battery_queue.get()
                battery_life = float(current_battery_life)
                battery_life = int(battery_life)
                print(f"battery-> {battery_life}")
                if battery_life > 100:
                    battery_life = int(100)
                elif battery_life < 0:
                    battery_life = 0
                else:
                    self.baterry_indicator.set_value = battery_life
                    self.baterry_indicator.text = f'{battery_life}%'
            except Exception as e:
                print(f'Exception in battery:: {e}')
                await asyncio.sleep(2.0)

    # Popup Disconnect
    def screen_flag_1(self, touch: bool) -> None:
        self.screen_flag = True
        
    def screen_flag_2(self, touch: bool) -> None:
        self.screen_flag = False
       
    def cancel_disconnect(self, touch: bool) -> None:
        if touch:
            if (self.screen_flag == True):
                self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 1')
            elif (self.screen_flag == False):
                self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 2')

    def accept_disconnect(self, touch: bool) -> None:
        if touch:
            self.root.current = 'main_window'
            self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 1')
            self.reset_ui_and_variables()
            disconnect_flag['disconnect'] = True

    def reset_ui_and_variables(self):
        # Ventana Conexión BLE
        # Menu desplegante oculto mientras no se busquen y encuentren dispositivos
        self.root.get_screen('main_window').ids.device_dropdown.text = ''
        self.root.get_screen('main_window').ids.device_dropdown.size = (0, 0)
        self.root.get_screen('main_window').ids.device_dropdown.opacity = 0
        self.root.get_screen('main_window').ids.device_dropdown.disabled = True

        # Ventana Status
        self.speedmeter_indicator = self.root.get_screen('secondary_window').ids.speed_indicator
        self.sp_indicator = self.root.get_screen('secondary_window').ids.sp_indicator
        self.manip_indicator = self.root.get_screen('secondary_window').ids.manip_indicator
        self.baterry_indicator = self.root.get_screen('secondary_window').ids.baterry_indicator
        self.baterry_indicator.text = f'0%'
        self.sp_indicator.text = f'SP: 0'
        self.manip_indicator.text = f'M: 0'

        # Ventana Settings
        self.angle_indicator = self.root.get_screen('secondary_window').ids.angle_indicator
        self.read_slider_text = self.root.get_screen('secondary_window').ids.read_slider_text
        self.per_button_pressed = True
        self.km_button_pressed = False
        self.slider_label = 'slider'
        self.slider_value = 0
        self.slider_flag = False 
        self.read_slider_text.text = f'0 %'
        self.root.get_screen('main_window').ids.spinner.active = False
        self.root.get_screen('secondary_window').ids.adapt_slider.value = self.root.get_screen('secondary_window').ids.adapt_slider.min
        self.root.get_screen('secondary_window').ids.adapt_switch.active = False

    # Popup Exit
    def cancel_exit(self, touch: bool) -> None:
        if touch:  
            self.root.current = "main_window"

    def accept_exit(self, touch: bool) -> None:
        if touch:
            print("Exit app...")
            self.stop()
            os._exit(0)

async def run_BLE(app: MDApp, datajson_queue: asyncio.Queue, battery_queue: asyncio.Queue, device_queue: asyncio.Queue,
                   manipulation_queue: asyncio.Queue) -> None:
    """Asyncronous connection protocol for BLE"""
    print('Run BLE')
    read_char = "00002A3D-0000-1000-8000-00805f9b34fb"
    flag = asyncio.Event()
    connection = Connection(loop=loop,
                            uuid=UUID,
                            address=ADDRESS,
                            read_char=read_char,
                            write_char=read_char,
                            flag=flag,
                            app=app,
                            device_queue=device_queue)
    disconnect_flag['disconnect'] = False

    try:
        asyncio.ensure_future(connection.manager())
        asyncio.ensure_future(communication_manager(connection=connection,
                                                    write_char=read_char,
                                                    read_char=read_char,
                                                    datajson_queue=datajson_queue,
                                                    battery_queue=battery_queue, 
                                                    manipulation_queue=manipulation_queue, disconnect_flag=disconnect_flag))
        print(f"Fetching connection...")
        await connection.flag.wait()
    finally:
        print(f"flag status confirmed!")
        AccHelper().run(datajson_queue)

    try:
        app.root.current = 'secondary_window'
    except Exception as e:
        print(f'EXCEPTION WHEN CHANGING WINDOW -> {e}')


if __name__ == '__main__':
    async def mainThread():
        """Creating main thread for asynchronous task definition"""
        BikeApp = Main()
        a = asyncio.create_task(BikeApp.start())
        (done, pending) = await asyncio.wait({a}, return_when='FIRST_COMPLETED')


    loop = asyncio.get_event_loop()
    asyncio.run(mainThread())

