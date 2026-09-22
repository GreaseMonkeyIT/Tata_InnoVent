"""S7comm (ISO-on-TCP) server for the siemens-s7-1200 profile (FLEET.md 4.3). python-snap7 3.1.2.

python-snap7 3.x ships a pure Python server (snap7.Server). This module registers one data block,
DB1 of 400 bytes, and keeps it in step with the runtime image:

  bytes   0..7    %IX       bit b of byte a = %IXa.b
  bytes   8..15   %QX       bit b of byte 8+a = %QXa.b
  bytes  16..143  %IW0..63  INT big-endian, word n at byte 16 + 2n
  bytes 144..271  %QW0..63  INT big-endian, word n at byte 144 + 2n
  bytes 272..399  %MW0..63  INT big-endian, word n at byte 272 + 2n

A client write to bytes 272..399 queues those words for the next scan. A write to any other byte
lands in the buffer, adds 1 to denied_writes, and the next publish overwrites it.

The snap7 server has no write callback, so VplcS7Server overrides its internal write method. The
pin to python-snap7 3.1.2 keeps that stable. The subclass also removes behavior that would not be
honest for a virtual PLC:
  - the identity lists (SZL 0x001C, 0x0011) name this runtime as virtual, not a Siemens CPU
  - reads of unregistered areas answer an error instead of placeholder bytes
  - block upload, download, PLC start and stop, and set clock answer "not supported", because
    none of them reach the runtime (tasks load and run through the control port)
"""
import struct

import snap7
from snap7.datatypes import S7Area
from snap7.type import SrvArea

from image import N_WORDS

DB_NUMBER = 1
DB_SIZE = 400
IX_OFF, QX_OFF, IW_OFF, QW_OFF, MW_OFF = 0, 8, 16, 144, 272
_NOT_SUPPORTED = 0x8001

_WORDS = struct.Struct(">64h")


def _pack_bits(bits):
    out = bytearray(8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return out


def render_db1(img, pending=None):
    """Build the 400 DB1 bytes for an image. pending overlays queued %MW writes."""
    mw = list(img.mw)
    if pending:
        for i, v in pending.items():
            mw[i] = v
    buf = bytearray(DB_SIZE)
    buf[IX_OFF:IX_OFF + 8] = _pack_bits(img.ix)
    buf[QX_OFF:QX_OFF + 8] = _pack_bits(img.qx)
    buf[IW_OFF:QW_OFF] = _WORDS.pack(*img.iw)
    buf[QW_OFF:MW_OFF] = _WORDS.pack(*img.qw)
    buf[MW_OFF:DB_SIZE] = _WORDS.pack(*mw)
    return buf


class VplcS7Server(snap7.Server):
    """snap7.Server with the write hook and the honesty changes described above."""

    def __init__(self, runtime, plc_name="vplc"):
        super().__init__(log=False)
        self.rt = runtime
        self.plc_name = plc_name

    # --- the write path
    def _write_to_memory_area(self, area, db_number, start, write_data):
        key = (area, db_number)
        if area != S7Area.DB or db_number != DB_NUMBER or key not in self.memory_areas:
            return False
        end = start + len(write_data)
        if start < 0 or end > DB_SIZE or not write_data:
            return False
        with self.area_locks[key]:
            buf = self.memory_areas[key]
            buf[start:end] = write_data
            words = {}
            for n in range(N_WORDS):
                b = MW_OFF + 2 * n
                if b < end and b + 2 > start:
                    words[n] = struct.unpack_from(">h", buf, b)[0]
            if words:
                self.rt.queue_mw(words)
            if start < MW_OFF:
                self.rt.count_denied()
        return True

    def _read_from_memory_area(self, area, db_number, start, count):
        if (area, db_number) not in self.memory_areas:
            return None
        return super()._read_from_memory_area(area, db_number, start, count)

    # --- honest identity
    def _get_szl_data(self, szl_id, szl_index):
        if szl_id == 0x001C:
            data = bytearray(210)
            data[6:30] = self.plc_name.encode("ascii", "replace")[:23].ljust(24, b"\x00")
            data[40:64] = b"VISR vPLC (virtual)".ljust(24, b"\x00")
            data[108:134] = b"virtual PLC, not firmware".ljust(26, b"\x00")
            data[142:166] = b"VIRTUAL".ljust(24, b"\x00")
            data[176:208] = b"soft PLC, S7comm profile".ljust(32, b"\x00")
            return bytes(data)
        if szl_id == 0x0011:
            return b"VIRTUAL-VPLC".ljust(20, b"\x00") + b"V0.1"
        return super()._get_szl_data(szl_id, szl_index)

    # --- functions that do not reach the runtime
    def _not_supported(self, request, client_address=None):
        return self._build_error_response(request, _NOT_SUPPORTED)

    def _handle_plc_control(self, request, client_address):
        return self._not_supported(request)

    def _handle_plc_stop(self, request, client_address):
        return self._not_supported(request)

    def _handle_start_upload(self, request, client_address):
        return self._not_supported(request)

    def _handle_upload(self, request, client_address):
        return self._not_supported(request)

    def _handle_end_upload(self, request, client_address):
        return self._not_supported(request)

    def _handle_request_download(self, request, client_address):
        return self._not_supported(request)

    def _handle_download_block(self, request, client_address):
        return self._not_supported(request)

    def _handle_download_ended(self, request, client_address):
        return self._not_supported(request)

    def _handle_set_clock(self, request, userdata_params, client_address):
        return self._build_userdata_error_response(request, _NOT_SUPPORTED)


class S7Db1Server:
    """Owns the DB1 buffer and the snap7 server, and follows every runtime publish."""

    def __init__(self, runtime, host="0.0.0.0", port=102, plc_name="vplc"):
        self.rt = runtime
        self.host, self.port = host, port
        self.buffer = bytearray(DB_SIZE)
        self.server = VplcS7Server(runtime, plc_name)
        self.server.host = host
        self.server.register_area(SrvArea.DB, DB_NUMBER, self.buffer)
        self._key = (S7Area.DB, DB_NUMBER)

    def render(self, published):
        """Publish listener: copy the image into DB1, keeping writes that wait for the next scan."""
        with self.server.area_locks[self._key]:
            self.buffer[:] = render_db1(published, self.rt.pending_mw())

    def start(self):
        self.rt.add_publish_listener(self.render)
        self.server.start(tcp_port=self.port)

    def stop(self):
        self.server.stop()
