# -*- coding: utf-8 -*-
"""Android utilities (pyjnius). On desktop they do nothing or use a local folder."""
import os
from typing import Optional

from kivy.logger import Logger
from kivy.utils import platform


def keep_screen_on(enable: bool) -> None:
    """Turns FLAG_KEEP_SCREEN_ON on/off so the screen does not switch off."""
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
    """True if the phone's Bluetooth is on (or if this is not Android)."""
    if platform != 'android':
        return True
    try:
        from jnius import autoclass

        BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
        adapter = BluetoothAdapter.getDefaultAdapter()
        return bool(adapter and adapter.isEnabled())
    except Exception as e:
        # If it cannot be queried, assume it is on so the retry is not blocked.
        Logger.warning(f'[BLE] No se pudo consultar el estado del Bluetooth: {e!r}')
        return True


def sdk_int() -> int:
    """Android API level (0 outside Android)."""
    if platform != 'android':
        return 0
    try:
        from jnius import autoclass
        return int(autoclass('android.os.Build$VERSION').SDK_INT)
    except Exception:
        return 0


# --- Files in Downloads ----------------------------------------------------
# Since Android 11 other apps cannot open Android/data/<package>; that is why
# the Excel files are saved to Downloads/<subfolder>.

def save_to_downloads(filename: str, data: bytes, mime: str, subfolder: str,
                      fallback_dir: str) -> Optional[dict]:
    """Saves the file and returns {'name', 'location', 'uri', 'path'} or None."""
    if platform == 'android':
        try:
            if sdk_int() >= 29:
                return _save_mediastore(filename, data, mime, subfolder)
            return _save_public_downloads(filename, data, mime, subfolder)
        except Exception as e:
            Logger.error(f'[XLS] No se pudo guardar en Descargas: {e!r}')
    return _save_local(filename, data, fallback_dir)


def _save_mediastore(filename: str, data: bytes, mime: str, subfolder: str) -> dict:
    """Android 10+: MediaStore, needs no permissions."""
    from jnius import autoclass

    activity = autoclass('org.kivy.android.PythonActivity').mActivity
    ContentValues = autoclass('android.content.ContentValues')
    Downloads = autoclass('android.provider.MediaStore$Downloads')
    MediaColumns = autoclass('android.provider.MediaStore$MediaColumns')
    Environment = autoclass('android.os.Environment')

    values = ContentValues()
    values.put(MediaColumns.DISPLAY_NAME, filename)
    values.put(MediaColumns.MIME_TYPE, mime)
    values.put(MediaColumns.RELATIVE_PATH, f'{Environment.DIRECTORY_DOWNLOADS}/{subfolder}')
    resolver = activity.getContentResolver()
    uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
    if uri is None:
        raise IOError('MediaStore no devolvio un Uri')
    stream = resolver.openOutputStream(uri)
    try:
        stream.write(data)
        stream.flush()
    finally:
        stream.close()
    Logger.info(f'[XLS] Guardado en Descargas/{subfolder}/{filename}')
    return {'name': filename, 'location': f'Descargas/{subfolder}/{filename}',
            'uri': uri, 'path': None}


def _save_public_downloads(filename: str, data: bytes, mime: str, subfolder: str) -> dict:
    """Android 9 or lower: file written straight to Downloads (needs WRITE_EXTERNAL_STORAGE)."""
    from jnius import autoclass

    Environment = autoclass('android.os.Environment')
    base = Environment.getExternalStoragePublicDirectory(
        Environment.DIRECTORY_DOWNLOADS).getAbsolutePath()
    folder = os.path.join(base, subfolder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, 'wb') as handle:
        handle.write(data)
    try:
        MediaScanner = autoclass('android.media.MediaScannerConnection')
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        MediaScanner.scanFile(activity, [path], [mime], None)
    except Exception as e:
        Logger.warning(f'[XLS] No se pudo avisar al indice de archivos: {e!r}')
    Logger.info(f'[XLS] Guardado en {path}')
    return {'name': filename, 'location': f'Descargas/{subfolder}/{filename}',
            'uri': None, 'path': path}


def _save_local(filename: str, data: bytes, folder: str) -> Optional[dict]:
    """Last resort (and desktop): the app's private folder."""
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, 'wb') as handle:
            handle.write(data)
        Logger.info(f'[XLS] Guardado en {path}')
        return {'name': filename, 'location': path, 'uri': None, 'path': path}
    except Exception as e:
        Logger.error(f'[XLS] No se pudo guardar el archivo: {e!r}')
        return None


def _content_uri(saved: dict):
    """Uri other apps can read: the MediaStore one, or file:// on Android 9 or lower."""
    from jnius import autoclass

    if saved.get('uri') is not None:
        return saved['uri']
    if not saved.get('path'):
        return None
    StrictMode = autoclass('android.os.StrictMode')
    VmPolicyBuilder = autoclass('android.os.StrictMode$VmPolicy$Builder')
    # Without this, Android 7+ throws FileUriExposedException for a file:// Uri
    StrictMode.setVmPolicy(VmPolicyBuilder().build())
    return autoclass('android.net.Uri').fromFile(autoclass('java.io.File')(saved['path']))


def _start_chooser(intent, title: str) -> None:
    from jnius import autoclass, cast

    Intent = autoclass('android.content.Intent')
    String = autoclass('java.lang.String')
    chooser = Intent.createChooser(intent, cast('java.lang.CharSequence', String(title)))
    autoclass('org.kivy.android.PythonActivity').mActivity.startActivity(chooser)


def open_saved_file(saved: Optional[dict], mime: str) -> bool:
    """Opens the file with the app the user picks (Excel, Sheets...)."""
    if platform != 'android' or not saved:
        return False
    try:
        from jnius import autoclass

        uri = _content_uri(saved)
        if uri is None:
            return False
        Intent = autoclass('android.content.Intent')
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, mime)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        _start_chooser(intent, 'Abrir con')
        return True
    except Exception as e:
        Logger.warning(f'[XLS] No se pudo abrir el archivo: {e!r}')
        return False


def share_saved_file(saved: Optional[dict], mime: str) -> bool:
    """Android share menu (WhatsApp, Drive, email...)."""
    if platform != 'android' or not saved:
        return False
    try:
        from jnius import autoclass, cast

        uri = _content_uri(saved)
        if uri is None:
            return False
        Intent = autoclass('android.content.Intent')
        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime)
        intent.putExtra(Intent.EXTRA_STREAM, cast('android.os.Parcelable', uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        _start_chooser(intent, 'Compartir vuelta')
        return True
    except Exception as e:
        Logger.warning(f'[XLS] No se pudo compartir el archivo: {e!r}')
        return False
