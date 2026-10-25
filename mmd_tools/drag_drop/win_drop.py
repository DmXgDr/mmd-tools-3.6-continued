"""Windows-only, process-local OLE forwarding adapter for Blender 3.6.

No binary patch, DLL injection, global hooks, or registry changes. Uses the
undocumented OleDropTargetInterface window property; fails closed if absent.
Native callbacks only exchange plain Python values, never run Blender operators.
"""
import ctypes as C
from ctypes import wintypes as W
import os
import uuid
import traceback

P = C.c_void_p
HRESULT = C.c_long
DWORD = W.DWORD
ULONG = W.ULONG
CALL = C.WINFUNCTYPE
user = C.WinDLL('user32', use_last_error=True)
ole = C.OleDLL('ole32')
shell = C.WinDLL('shell32')


def api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


getprop = api(user, 'GetPropW', P, W.HWND, W.LPCWSTR)
iswindow = api(user, 'IsWindow', W.BOOL, W.HWND)
getpid = api(user, 'GetWindowThreadProcessId', DWORD, W.HWND, C.POINTER(DWORD))
getrect = api(user, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
screenclient = api(user, 'ScreenToClient', W.BOOL, W.HWND, C.POINTER(W.POINT))
getclass = api(user, 'GetClassNameW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
register = api(ole, 'RegisterDragDrop', HRESULT, W.HWND, P)
revoke = api(ole, 'RevokeDragDrop', HRESULT, W.HWND)
queryfile = api(shell, 'DragQueryFileW', W.UINT, P, W.UINT, W.LPWSTR, W.UINT)


class FORMATETC(C.Structure):
    _fields_ = [('format', W.WORD), ('target', P), ('aspect', DWORD),
                ('index', C.c_long), ('medium', DWORD)]


class STGMEDIUM(C.Structure):
    _fields_ = [('medium', DWORD), ('handle', P), ('release', P)]


release_medium = api(ole, 'ReleaseStgMedium', None, C.POINTER(STGMEDIUM))
QI = CALL(HRESULT, P, P, C.POINTER(P))
REF = CALL(ULONG, P)
ENTER = CALL(HRESULT, P, P, DWORD, C.c_int64, C.POINTER(DWORD))
OVER = CALL(HRESULT, P, DWORD, C.c_int64, C.POINTER(DWORD))
LEAVE = CALL(HRESULT, P)
GETDATA = CALL(HRESULT, P, C.POINTER(FORMATETC), C.POINTER(STGMEDIUM))
IIDS = {uuid.UUID(s).bytes_le for s in
        ['00000000-0000-0000-C000-000000000046', '00000122-0000-0000-C000-000000000046']}


def method(ptr, index, prototype):
    table = C.cast(ptr, C.POINTER(C.POINTER(P))).contents
    return prototype(table[index])


def paths_from_data(data):
    fmt = FORMATETC(15, None, 1, -1, 1)  # CF_HDROP / CONTENT / HGLOBAL
    medium = STGMEDIUM()
    hr = method(data, 3, GETDATA)(data, C.byref(fmt), C.byref(medium))
    if hr < 0:
        return []
    try:
        count = queryfile(medium.handle, 0xFFFFFFFF, None, 0)
        if count > 4096:
            return []
        result = []
        for i in range(count):
            size = queryfile(medium.handle, i, None, 0)
            buf = C.create_unicode_buffer(size + 1)
            queryfile(medium.handle, i, buf, size + 1)
            result.append(buf.value)
        return result
    finally:
        release_medium(C.byref(medium))


def windows():
    result = []
    enumtype = CALL(W.BOOL, W.HWND, W.LPARAM)
    @enumtype
    def visit(hwnd, unused):
        pid = DWORD()
        getpid(hwnd, C.byref(pid))
        if pid.value == os.getpid():
            name = C.create_unicode_buffer(256)
            getclass(hwnd, name, 256)
            if name.value == 'GHOST_WindowClass':
                result.append(hwnd)
        return True
    api(user, 'EnumWindows', W.BOOL, enumtype, W.LPARAM)(visit, 0)
    return result


class Target:
    def __init__(self, hwnd, accept, receive):
        self.hwnd, self.accept, self.receive = hwnd, accept, receive
        self.original = getprop(hwnd, 'OleDropTargetInterface')
        if not self.original:
            raise RuntimeError('Windows 未提供原拖放接口，未接管窗口')
        self.refs, self.paths, self.custom = 1, [], False
        self.forwarded = False
        self.enabled = True
        self.callbacks = [QI(self.query), REF(self.addref), REF(self.release),
                          ENTER(self.enter), OVER(self.over), LEAVE(self.leave), ENTER(self.drop)]
        self.table = (P * 7)(*[C.cast(c, P).value for c in self.callbacks])
        self.instance = C.pointer(C.cast(self.table, P))
        self.ptr = C.cast(self.instance, P)
        method(self.original, 1, REF)(self.original)
        try:
            if revoke(hwnd) < 0:
                raise RuntimeError('无法暂时替换窗口拖放接口')
            if register(hwnd, self.ptr) < 0:
                register(hwnd, self.original)
                raise RuntimeError('无法注册拖放接口')
        except BaseException:
            method(self.original, 2, REF)(self.original)
            raise

    def query(self, this, iid, out):
        out[0] = None
        if C.string_at(iid, 16) in IIDS:
            out[0] = self.ptr.value
            self.refs += 1
            return 0
        return -2147467262  # E_NOINTERFACE

    def addref(self, this):
        self.refs += 1
        return self.refs

    def release(self, this):
        self.refs = max(0, self.refs - 1)
        return self.refs

    def point(self, packed):
        p = W.POINT(C.c_int32(packed & 0xFFFFFFFF).value,
                    C.c_int32((packed >> 32) & 0xFFFFFFFF).value)
        screenclient(self.hwnd, C.byref(p))
        return p.x, p.y

    def enter(self, this, data, keys, point, effect):
        try:
            self.paths = paths_from_data(data) if self.enabled else []
            self.custom = bool(self.paths and self.accept(self.hwnd, self.paths, *self.point(point)))
            self.forwarded = True
            allowed = effect[0]
            result = method(self.original, 3, ENTER)(self.original, data, keys, point, effect)
            if self.custom:
                effect[0] = allowed & 1
                return 0
            return result
        except BaseException:
            traceback.print_exc()
            effect[0] = 0
            return 0

    def over(self, this, keys, point, effect):
        try:
            self.custom = bool(self.enabled and self.paths and self.accept(self.hwnd, self.paths, *self.point(point)))
            if self.custom:
                effect[0] &= 1
                return 0
            return method(self.original, 4, OVER)(self.original, keys, point, effect)
        except BaseException:
            effect[0] = 0
            return 0

    def leave(self, this):
        try:
            if self.forwarded:
                method(self.original, 5, LEAVE)(self.original)
        finally:
            self.paths, self.custom, self.forwarded = [], False, False
        return 0

    def drop(self, this, data, keys, point, effect):
        try:
            self.custom = bool(self.enabled and self.paths and self.accept(self.hwnd, self.paths, *self.point(point)))
            if self.custom:
                xy = self.point(point)
                if self.enabled and self.accept(self.hwnd, self.paths, *xy):
                    self.receive(self.hwnd, list(self.paths), *xy)
                    effect[0] &= 1
                else:
                    effect[0] = 0
                if self.forwarded:
                    method(self.original, 5, LEAVE)(self.original)
                return 0
            return method(self.original, 6, ENTER)(self.original, data, keys, point, effect)
        except BaseException:
            traceback.print_exc()
            effect[0] = 0
            return 0
        finally:
            self.paths, self.custom, self.forwarded = [], False, False

    def close(self):
        self.enabled = False
        if iswindow(self.hwnd):
            # Do not tear down a handler another extension installed after ours.
            if getprop(self.hwnd, 'OleDropTargetInterface') != self.ptr.value:
                raise RuntimeError('窗口拖放接口已被其他插件替换；保留回调到退出')
            if revoke(self.hwnd) < 0:
                raise RuntimeError('无法解除拖放接口；保留回调到退出')
            if register(self.hwnd, self.original) < 0:
                raise RuntimeError('无法恢复原拖放接口')
        method(self.original, 2, REF)(self.original)


# Keep ctypes callback objects alive after unregister until process shutdown.
# A native drag may still be unwinding when Blender disables the add-on.
_retired = []


class Bridge:
    def __init__(self, accept, receive):
        self.targets = {}
        self.accept, self.receive = accept, receive
        self.errors = []

    def sync(self):
        handles = set(windows())
        for hwnd in list(self.targets):
            if hwnd not in handles:
                self.targets.pop(hwnd).close()
        for hwnd in handles - self.targets.keys():
            try:
                self.targets[hwnd] = Target(hwnd, self.accept, self.receive)
            except Exception as exc:
                message = str(exc)
                if message not in self.errors:
                    self.errors.append(message)
                    print('Universal Drop:', message)

    def close(self):
        for target in self.targets.values():
            try:
                target.close()
            except Exception as exc:
                print('Universal Drop:', exc)
            _retired.append(target)
        self.targets.clear()
