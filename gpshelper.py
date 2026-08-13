from kivy.utils import platform
from kivymd.uix.dialog import MDDialog
import asyncio
from math import asin, cos, pi, sqrt
import time
import csv
from kalmanfilter import KalmanWrapper


class GpsHelper:
    gps_time: float = 500
    gps_min_distance: float = 3.0
    velocity: int = 0
    csv_debug: list = []
    i: int = 0

    def run(self, speed_queue: asyncio.Queue) -> None:
        self.speed_queue = speed_queue
        self.gps_info = []
        print('Run gps')

        # configure GPS
        if platform == 'android' or platform == 'ios':
            from plyer import gps
            gps.configure(on_location=self.on_location,
                          on_status=self.on_auth_status)
            self.kf = KalmanWrapper()
            gps.start(minTime=self.gps_time, minDistance=self.gps_min_distance)
            self.start = time.perf_counter()

    def on_location(self, *args, **kwargs):
        """callback used to gather relevant information"""
        # print(f"on_location ->>> {kwargs}")
        print(f'on_loc -> {time.time()}')
        self.kf.predict()
        self.kf.update(kwargs['speed'])
        # velo after kalman = self.kf.x
        if kwargs['accuracy'] < 25:
            self.velocity = (kwargs['speed'] * 3.6)
            self.i += 1
            kwargs.update({'time': time.time()})
            self.csv_debug.append(time.time())
            self.gps_info.append(kwargs)
            print(f'on_location -> {self.gps_info}, {self.i}')
            # FILTERING INFORMATION NEEDED:
            self.speed_queue.put_nowait(self.velocity)
        else:
            print(f'on_location_ERROR: Accuracy too low!')
        if self.i > 15:
            print('writing_csv')
            self.i = 0
            print(f'gps_csv: {self.csv_debug}')
        if len(self.gps_info) > 1:
            true_speed = self.calculate_speed
            self.gps_info.pop(0)
            print(f'true_speed_haver: {true_speed}')
            print(f'gps_speed: {self.velocity}')
            # self.speed_queue.put_nowait(true_speed)
            # self.speed_queue.put_nowait(self.velocity)
        # print(f'time_passed {time.perf_counter()-self.start}')

    def calculate_distance(self) -> float:
        r = 6371  # radius of Earth
        p = pi / 180  # multiply this to convert Degrees to Radians
        lat1 = self.gps_info[0]["lat"]
        lon1 = self.gps_info[0]["lon"]
        lat2 = self.gps_info[1]["lat"]
        lon2 = self.gps_info[1]["lon"]
        # CHECK HAVERSINE FORMULA
        return 2 * r * asin(
            sqrt(0.5 - cos((lat2 - lat1) * p) / 2 + cos(lat1 * p) * cos(lat2 * p) * (1 - cos((lon2 - lon1) * p)) / 2))

    def calculate_speed(self) -> float:
        # For current speed a & b will be first & second values on list
        # For average speed it will be first & last values
        distance = self.calculate_distance
        return float(
            distance * 3600 / ((self.gps_info[1]["time"] - self.gps_info[0]["time"])))  # Multiplying by 3600 for Km/Hrs

    def on_auth_status(self, general_status: str, status_message: str) -> None:
        if general_status == 'provider-enabled':
            pass
        else:
            self.open_gps_access_popup()

    def open_gps_access_popup(self) -> None:
        dialog = MDDialog(title='GPS Error',
                          text="You need to enable GPS access for the app to function properly")
        dialog.size_hint = [0.8, 0.8]
        dialog.pos_hint = {'center_x': 0.5, 'center_y': 0.5}
        dialog.open()