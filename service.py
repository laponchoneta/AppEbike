# -*- coding: utf-8 -*-
"""Punto de entrada del servicio en primer plano (Etapa 4).

OJO: corre en un proceso APARTE del de la app, donde
`PythonActivity.mActivity` es null. Por eso aqui NO se puede usar bleak:
su modulo bleak/backends/p4android/defs.py resuelve el Context desde la
Activity al importarse y truena. El BLE se queda en el proceso principal;
este servicio solo existe para que Android no mate ni congele a la app:
mantiene la notificacion persistente y un PARTIAL_WAKE_LOCK.
"""
import time


def _acquire_wake_lock():
    """Mantiene el CPU despierto con la pantalla apagada."""
    try:
        from jnius import autoclass, cast

        PythonService = autoclass('org.kivy.android.PythonService')
        Context = autoclass('android.content.Context')
        PowerManager = autoclass('android.os.PowerManager')

        service = PythonService.mService
        service.setAutoRestartService(True)
        manager = cast('android.os.PowerManager',
                       service.getSystemService(Context.POWER_SERVICE))
        lock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'ebike:servicio')
        lock.acquire()
        print('[ebike-service] wake lock tomado')
        return lock
    except Exception as e:
        print(f'[ebike-service] no se pudo tomar el wake lock: {e!r}')
        return None


def main():
    lock = _acquire_wake_lock()
    print('[ebike-service] en marcha')
    try:
        while True:
            time.sleep(30)
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
        print('[ebike-service] terminado')


if __name__ == '__main__':
    main()
