# -*- coding: utf-8 -*-
"""Utilidades de Android (pyjnius). En escritorio todas son no-operativas."""
from typing import Optional

from kivy.logger import Logger
from kivy.utils import platform


def keep_screen_on(enable: bool) -> None:
    """Activa/desactiva FLAG_KEEP_SCREEN_ON para que la pantalla no se apague."""
    if platform != 'android':
        return
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        LayoutParams = autoclass('android.view.WindowManager$LayoutParams')

        @run_on_ui_thread
        def _apply():
            window = PythonActivity.mActivity.getWindow()
            if enable:
                window.addFlags(LayoutParams.FLAG_KEEP_SCREEN_ON)
            else:
                window.clearFlags(LayoutParams.FLAG_KEEP_SCREEN_ON)

        _apply()
        Logger.info(f'[BLE] Pantalla siempre encendida = {enable}')
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo cambiar FLAG_KEEP_SCREEN_ON: {e!r}')


def is_bluetooth_enabled() -> bool:
    """True si el Bluetooth del celular esta encendido (o si no es Android)."""
    if platform != 'android':
        return True
    try:
        from jnius import autoclass

        BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
        adapter = BluetoothAdapter.getDefaultAdapter()
        return bool(adapter and adapter.isEnabled())
    except Exception as e:
        # Si no se puede consultar, asumimos que si para no bloquear el reintento.
        Logger.warning(f'[BLE] No se pudo consultar el estado del Bluetooth: {e!r}')
        return True


def vibrate(seconds: float = 0.3) -> None:
    """Vibracion corta para las alertas."""
    if platform != 'android':
        return
    try:
        from plyer import vibrator
        vibrator.vibrate(seconds)
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo vibrar: {e!r}')


def external_files_dir() -> Optional[str]:
    """Carpeta privada de la app en memoria externa, visible por USB.

    Es /sdcard/Android/data/<paquete>/files y no requiere permisos extra.
    """
    if platform != 'android':
        return None
    try:
        from jnius import autoclass, cast

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = cast('android.content.Context', PythonActivity.mActivity)
        directory = context.getExternalFilesDir(None)
        return directory.getAbsolutePath() if directory else None
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo obtener la carpeta externa: {e!r}')
        return None


def share_file(path: str, mime: str = 'text/csv',
               title: str = 'Compartir recorrido') -> bool:
    """Abre el menu de compartir de Android con el archivo indicado."""
    if platform != 'android':
        return False
    try:
        from jnius import autoclass, cast

        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        File = autoclass('java.io.File')
        String = autoclass('java.lang.String')
        StrictMode = autoclass('android.os.StrictMode')
        VmPolicyBuilder = autoclass('android.os.StrictMode$VmPolicy$Builder')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')

        # Sin esto Android 7+ lanza FileUriExposedException con un URI file://
        StrictMode.setVmPolicy(VmPolicyBuilder().build())

        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime)
        intent.putExtra(Intent.EXTRA_STREAM,
                        cast('android.os.Parcelable', Uri.fromFile(File(path))))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        chooser = Intent.createChooser(intent,
                                       cast('java.lang.CharSequence', String(title)))
        PythonActivity.mActivity.startActivity(chooser)
        return True
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo compartir el archivo: {e!r}')
        return False


# --- Segundo plano (Etapa 4) ---------------------------------------------- #
# Debe coincidir con 'services = ebike:service.py:...' de buildozer.spec
SERVICE_CLASS = 'org.ebiamtz.elbike.ServiceEbike'

_wake_lock = None


def acquire_wake_lock() -> None:
    """PARTIAL_WAKE_LOCK: el CPU sigue corriendo con la pantalla apagada."""
    global _wake_lock
    if platform != 'android' or _wake_lock is not None:
        return
    try:
        from jnius import autoclass, cast

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        Context = autoclass('android.content.Context')
        PowerManager = autoclass('android.os.PowerManager')

        manager = cast('android.os.PowerManager',
                       PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE))
        _wake_lock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'ebike:ble')
        _wake_lock.acquire()
        Logger.info('[BLE] Wake lock tomado')
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo tomar el wake lock: {e!r}')
        _wake_lock = None


def release_wake_lock() -> None:
    global _wake_lock
    if _wake_lock is None:
        return
    try:
        _wake_lock.release()
        Logger.info('[BLE] Wake lock liberado')
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo liberar el wake lock: {e!r}')
    _wake_lock = None


def start_background_service() -> bool:
    """Arranca el servicio en primer plano. Si falla, la app sigue igual."""
    if platform != 'android':
        return False
    try:
        from jnius import autoclass

        service = autoclass(SERVICE_CLASS)
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        service.start(activity, '')
        Logger.info('[BLE] Servicio en primer plano iniciado')
        return True
    except Exception as e:
        # En Android 14 falla si el servicio no declara foregroundServiceType.
        Logger.warning(f'[BLE] No se pudo iniciar el servicio: {e!r}')
        return False


def stop_background_service() -> None:
    if platform != 'android':
        return
    try:
        from jnius import autoclass

        service = autoclass(SERVICE_CLASS)
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        service.stop(activity)
        Logger.info('[BLE] Servicio en primer plano detenido')
    except Exception as e:
        Logger.warning(f'[BLE] No se pudo detener el servicio: {e!r}')
