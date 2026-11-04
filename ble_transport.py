"""KneeROM BLE UART stream. No radio access until BleStream is constructed.

The BLE event loop owns all WinRT objects in its background thread. Complete
lines are queued; overflow drops whole old lines, never joins partial samples.
"""
import asyncio
import concurrent.futures
import os
import queue
import threading

SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
RX_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
TX_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
DEFAULT_NAME = 'KneeROM_7C0A7C'


class BleStream:
    def __init__(self, name=None, timeout=0.2, scan_timeout=12.0):
        self.name = name or os.getenv('ACL_BLE_NAME', DEFAULT_NAME)
        self.timeout = timeout
        self.scan_timeout = scan_timeout
        self.connected = False
        self.is_open = True
        self.dropped_lines = 0
        self._error = None
        self._stop = threading.Event()
        self._lines = queue.Queue(maxsize=256)
        self._incoming = bytearray()
        self._read_buffer = bytearray()
        self._discard = False
        self._loop = None
        self._client = None
        self._thread = threading.Thread(target=self._run, name='knee-ble', daemon=True)
        self._thread.start()

    def _notification(self, _characteristic, data):
        for byte in data:
            if byte == 10:
                if not self._discard:
                    line = bytes(self._incoming) + b'\n'
                    try:
                        self._lines.put_nowait(line)
                    except queue.Full:
                        try:
                            self._lines.get_nowait()
                        except queue.Empty:
                            pass
                        self.dropped_lines += 1
                        self._lines.put_nowait(line)
                self._incoming.clear()
                self._discard = False
            elif not self._discard:
                self._incoming.append(byte)
                if len(self._incoming) > 1024:
                    self._incoming.clear()
                    self._discard = True

    async def _session(self):
        from bleak import BleakClient, BleakScanner
        if os.name == 'nt':
            from bleak.backends.winrt.util import uninitialize_sta
            uninitialize_sta()

        def matches(device, advertisement):
            return (advertisement.local_name or device.name) == self.name and SERVICE_UUID in [
                uuid.lower() for uuid in advertisement.service_uuids]

        device = await BleakScanner.find_device_by_filter(
            matches, timeout=self.scan_timeout, service_uuids=[SERVICE_UUID])
        if device is None:
            raise OSError(f'Không tìm thấy BLE {self.name}. Bật Bluetooth và cấp nguồn S3 đã nạp firmware BLE.')
        async with BleakClient(device, timeout=15, winrt={'use_cached_services': False}) as client:
            self._client = client
            await client.start_notify(TX_UUID, self._notification)
            self.connected = True
            try:
                while not self._stop.is_set():
                    if not client.is_connected:
                        raise OSError('S3 đã ngắt BLE')
                    await asyncio.sleep(0.1)
            finally:
                self.connected = False

    async def _main(self):
        task = asyncio.create_task(self._session())
        try:
            while not task.done() and not self._stop.is_set():
                await asyncio.sleep(0.05)
            if self._stop.is_set():
                task.cancel()
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _run(self):
        try:
            with asyncio.Runner() as runner:
                self._loop = runner.get_loop()
                runner.run(self._main())
        except Exception as exc:
            if not self._stop.is_set():
                self._error = OSError(f'BLE {self.name}: {exc}')
        finally:
            self.connected = False
            self.is_open = False
            self._client = None

    def read(self, size=256):
        if size <= 0:
            return b''
        if self._error:
            raise self._error
        if not self._read_buffer:
            try:
                self._read_buffer.extend(self._lines.get(timeout=self.timeout))
            except queue.Empty:
                if self._error:
                    raise self._error
                if not self.is_open and not self._stop.is_set():
                    raise OSError('Kết nối BLE đã đóng')
                return b''
        data = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return data

    def readline(self, size=1024):
        # Queue entries contain exactly one complete line, including its newline.
        return self.read(size)

    def write(self, data):
        payload = bytes(data).strip().upper()
        if len(payload) != 1 or payload not in (b'C', b'F', b'L', b'R'):
            raise ValueError('BLE chỉ nhận lệnh C/F/L/R')
        if not self.connected or not self._loop or not self._client:
            raise OSError('BLE chưa sẵn sàng; chờ kết nối S3')
        operation = self._client.write_gatt_char(RX_UUID, payload, response=True)
        try:
            future = asyncio.run_coroutine_threadsafe(operation, self._loop)
        except RuntimeError as exc:
            operation.close()
            raise OSError('BLE đã đóng khi gửi lệnh') from exc
        try:
            future.result(timeout=3)
        except Exception as exc:
            future.cancel()
            raise OSError(f'Không gửi được lệnh BLE: {exc}') from exc
        return len(data)

    def close(self):
        self._stop.set()
        self.is_open = False
        self.connected = False
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
