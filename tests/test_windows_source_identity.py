"""Portable tests for the Windows native token and conservative hash fallback."""
import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_cull_assistant import image_decode as decode


class Basic(ctypes.Structure):
    _fields_ = [('ChangeTime', ctypes.c_int64)]


class NativeStub:
    def __init__(self, *, fail=None, filesystem='NTFS', drive=3, token=123):
        self.fail, self.filesystem, self.drive, self.token = fail, filesystem, drive, token
        self.closed = []
        self.open_args = None

    def CreateFileW(self, *args):
        self.open_args = args
        return ctypes.c_void_p(-1).value if self.fail == 'open' else 42

    def GetVolumeInformationByHandleW(self, handle, label, length, serial, maximum, flags, fs, count):
        if self.fail == 'volume':
            return 0
        fs.value = self.filesystem
        return 1

    def GetFinalPathNameByHandleW(self, handle, target, count, flags):
        assert flags == 9
        if self.fail == 'path':
            return 0
        target.value = r'\\?\Volume{test}\照片\image.raw'
        return len(target.value)

    def GetDriveTypeW(self, root):
        assert root == '\\\\?\\Volume{test}\\'
        return self.drive

    def GetFileInformationByHandleEx(self, handle, kind, target, size):
        if self.fail == 'query':
            return 0
        if self.fail == 'exception':
            raise OSError('native query failed')
        target._obj.ChangeTime = self.token
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_native_fixed_ntfs_token_and_read_only_flags(monkeypatch):
    api = NativeStub()
    monkeypatch.setattr(decode, '_windows_identity_api', lambda: (api, Basic))
    assert decode._windows_change_time(r'C:\照片\image.raw') == 123
    assert api.open_args == ('\\\\?\\C:\\照片\\image.raw', 0x80, 7, None, 3, 0, None)
    assert api.closed == [42]


@pytest.mark.parametrize('options', [
    {'fail': 'volume'}, {'fail': 'path'}, {'fail': 'query'}, {'fail': 'exception'},
    {'filesystem': 'exFAT'}, {'filesystem': 'ReFS'}, {'drive': 4}, {'drive': 2},
    {'drive': 0}, {'token': 0},
])
def test_unknown_or_failed_native_query_closes_handle(monkeypatch, options):
    api = NativeStub(**options)
    monkeypatch.setattr(decode, '_windows_identity_api', lambda: (api, Basic))
    assert decode._windows_change_time(r'C:\image.raw') is None
    assert api.closed == [42]


def test_failed_native_open_does_not_close_invalid_handle(monkeypatch):
    api = NativeStub(fail='open')
    monkeypatch.setattr(decode, '_windows_identity_api', lambda: (api, Basic))
    assert decode._windows_change_time(r'C:\image.raw') is None
    assert api.closed == []


@pytest.mark.parametrize(('source', 'expected'), [
    (r'C:\照片\image.raw', r'\\?\C:\照片\image.raw'),
    (r'\\server\share\照片.raw', r'\\?\UNC\server\share\照片.raw'),
    (r'\\?\C:\照片.raw', r'\\?\C:\照片.raw'),
    ('C:\\' + 'x' * 300 + '.raw', '\\\\?\\C:\\' + 'x' * 300 + '.raw'),
])
def test_extended_unicode_paths(source, expected):
    assert decode._windows_extended_path(source) == expected


def frozen_windows(monkeypatch):
    monkeypatch.setattr(decode, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(decode, '_stat_identity', lambda stat: (4, 10, 10, 1, 2))


def test_native_token_invalidates_even_when_all_python_metadata_is_frozen(tmp_path, monkeypatch):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'old!')
    frozen_windows(monkeypatch)
    tokens = iter([111, 222])
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: next(tokens))
    monkeypatch.setattr(decode, '_content_identity', lambda *a: pytest.fail('Native fast path read RAW'))
    original_open = Path.open
    monkeypatch.setattr(Path, 'open', lambda *a, **kw: pytest.fail('Native identity opened RAW content'))
    before = decode.source_signature(path)
    monkeypatch.setattr(Path, 'open', original_open)
    path.write_bytes(b'new!')
    after = decode.source_signature(path)
    assert before[:6] == after[:6]
    assert before != after
    assert after[-1] == ('win-change-time', 222)


def test_fallback_hashes_every_call_with_frozen_metadata(tmp_path, monkeypatch):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'old!')
    frozen_windows(monkeypatch)
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: None)
    original = decode._content_identity
    calls = []
    def tracked(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(decode, '_content_identity', tracked)
    before = decode.source_signature(path)
    assert decode.source_signature(path) == before
    path.write_bytes(b'new!')
    after = decode.source_signature(path)
    assert before[:6] == after[:6]
    assert before != after and after[-1][0] == 'sha256'
    assert len(calls) == 3


def test_hash_fallback_streams_bounded_chunks(tmp_path, monkeypatch):
    path = tmp_path / 'large.raw'
    path.write_bytes(b'x' * (3 * 1024 * 1024 + 17))
    original = Path.open
    reads = []
    class Stream:
        def __enter__(self):
            self.file = original(path, 'rb')
            return self
        def __exit__(self, *args):
            self.file.close()
        def fileno(self):
            return self.file.fileno()
        def read(self, count):
            reads.append(count)
            return self.file.read(count)
    monkeypatch.setattr(Path, 'open', lambda *a, **kw: Stream())
    result = decode._content_identity(path, decode._stat_identity(path.stat()))
    assert result[0] == 'sha256'
    assert len(reads) == 5 and set(reads) == {1024 * 1024}


def test_hash_fallback_rejects_observed_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'old!')
    expected = decode._stat_identity(path.stat())
    states = iter([expected, (999, *expected[1:])])
    monkeypatch.setattr(decode, '_stat_identity', lambda stat: next(states))
    with pytest.raises(decode.DecodeError, match='校验'):
        decode._content_identity(path, expected)


def test_linux_retains_original_identity_without_windows_io(tmp_path, monkeypatch):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'data')
    monkeypatch.setattr(decode, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: pytest.fail('Windows-only query'))
    assert decode.source_signature(path) == (str(path.resolve()), *decode._stat_identity(path.stat()))


def test_frozen_metadata_fallback_invalidates_decoded_pixels(tmp_path, monkeypatch):
    from PIL import Image
    path = tmp_path / 'image.bmp'
    Image.new('RGB', (8, 8), 'red').save(path)
    frozen_windows(monkeypatch)
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: None)
    decode.clear_decode_cache()
    try:
        with decode.load_standard_image(path) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
        Image.new('RGB', (8, 8), 'blue').save(path)
        with decode.load_standard_image(path) as image:
            assert image.getpixel((0, 0)) == (0, 0, 255)
    finally:
        decode.clear_decode_cache()


def test_fallback_accepts_stable_divergent_path_and_handle_ctime(tmp_path, monkeypatch):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'data')
    monkeypatch.setattr(decode, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: None)
    real_fstat = decode.os.fstat
    def divergent_fstat(fd):
        actual = real_fstat(fd)
        return SimpleNamespace(st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns,
                               st_ctime_ns=actual.st_ctime_ns + 777,
                               st_dev=actual.st_dev, st_ino=actual.st_ino)
    monkeypatch.setattr(decode.os, 'fstat', divergent_fstat)
    first = decode.source_signature(path)
    assert first[-1][0] == 'sha256'
    assert decode.source_signature(path) == first


@pytest.mark.parametrize('field', ['st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_dev', 'st_ino'])
def test_fallback_rejects_changed_handle_metadata(tmp_path, monkeypatch, field):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'data')
    real_fstat = decode.os.fstat
    calls = []
    def changing_fstat(fd):
        actual = real_fstat(fd)
        values = {name: getattr(actual, name) for name in
                  ('st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_dev', 'st_ino')}
        if calls:
            values[field] += 1
        calls.append(True)
        return SimpleNamespace(**values)
    monkeypatch.setattr(decode.os, 'fstat', changing_fstat)
    with pytest.raises(decode.DecodeError, match='校验'):
        decode._content_identity(path, decode._stat_identity(path.stat()))


def test_fallback_rejects_path_replacement_after_hash(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'image.raw'
    path.write_bytes(b'old!')
    replacement = tmp_path / 'replacement.raw'
    replacement.write_bytes(b'new!')
    before = path.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(decode, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(decode, '_windows_change_time', lambda path: None)
    original = decode._content_identity
    def replace_after_hash(*args):
        value = original(*args)
        os.replace(replacement, path)
        return value
    monkeypatch.setattr(decode, '_content_identity', replace_after_hash)
    with pytest.raises(decode.DecodeError, match='校验'):
        decode.source_signature(path)


def test_fallback_rejects_replacement_before_handle_open(tmp_path):
    path = tmp_path / 'image.raw'
    path.write_bytes(b'old!')
    expected = decode._stat_identity(path.stat())
    replacement = tmp_path / 'replacement.raw'
    replacement.write_bytes(b'new!')
    replacement.replace(path)
    with pytest.raises(decode.DecodeError, match='校验'):
        decode._content_identity(path, expected)
