# Workaround for OpenShell builds before NVIDIA/OpenShell#4150 on Linux kernels older than 5.19
# (RHCOS 9): getpeername() returns EOPNOTSUPP in the sandbox, which breaks Python's ssl module.
# The socket is connected; ssl only needs the call not to fail. Delete this file once the
# gateway runs a build with the fix.
import errno
import socket

_orig = socket.socket.getpeername


def _getpeername(self):
    try:
        return _orig(self)
    except OSError as e:
        if e.errno != errno.EOPNOTSUPP:
            raise
        return ("0.0.0.0", 0) if self.family == socket.AF_INET else ("::", 0, 0, 0)


socket.socket.getpeername = _getpeername
