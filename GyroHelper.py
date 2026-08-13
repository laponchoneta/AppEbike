from kivy.utils import platform
import asyncio
import json


class GyroHelper:
    sensorEnabled: bool = False
    x = 0
    y = 0
    z = 0
    def run(self, acc_q: asyncio.Queue) -> None:
        print(f'in_gyro')
        self.acc_q = acc_q
        if platform == 'android':
            from plyer import gyroscope
            self.gyroscope = gyroscope
            try:
                if not self.sensorEnabled:
                    self.gyroscope.enable()
                    print('gyro_enable')
                    asyncio.ensure_future(self.get_angle(1.0))
                    self.sensorEnabled = True
                else:
                    gyroscope.disable()
                    self.sensorEnabled = False
            except NotImplementedError:
                import traceback
                traceback.print_exc()

    async def get_angle(self, dt):
        while self.sensorEnabled:
        	val = self.gyroscope.rotation[:3]
        	if not val == (None,None,None):
        		self.x = val[0]
        		self.y = val[1]
        		self.z = val[2]
        		print(f'gyro_values: {val}')
        		try:
        			# await self.acc_q.put(json.dumps({'acc_x': val[0]}))
        			self.acc_q.put_nowait(json.dumps({'acc_y': self.y}))
        		except Exception as e:
        			print(f'EXCEPTION ACC :: {e}')
        	await asyncio.sleep(dt)
