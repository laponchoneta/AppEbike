from kivy.utils import platform
from kivy.clock import Clock
import asyncio
import json


class AccHelper:
    sensorEnabled: bool = False
    x = 0
    y = 0
    z = 0
    def run(self, acc_q: asyncio.Queue) -> None:
        print('run_acc')
        self.acc_q = acc_q
        if platform == 'android' or platform == 'ios':
            from plyer import accelerometer
            self.accelerometer = accelerometer
            try:
                print(f'in_acc')
                if not self.sensorEnabled:
                    self.accelerometer.enable()
                    print('acc_enable')
                    asyncio.ensure_future(self.get_acceleration(1))
                    self.sensorEnabled = True
                    self.init_velo = 0
                else:
                    self.accelerometer.disable()
                    self.sensorEnabled = False
            except NotImplementedError:
                import traceback
                traceback.print_exc()

    async def get_acceleration(self, dt):
        while self.sensorEnabled:
            val = self.accelerometer.acceleration[:3]
            if not val == (None,None,None):
                self.x = val[0]
                self.y = val[1]
                self.z = val[2]
                print(f'accelerometer_values: {val}')
                try:
                    self.acc_q.put_nowait(json.dumps({'acc_y': self.y}))
                    print("Acceleration: ")
                    print(self.y)
                except Exception as e:
                    print(f'EXCEPTION ACC :: {e}')
            await asyncio.sleep(dt)
