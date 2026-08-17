"""Fail-closed network guard injected into offline west subprocesses."""
from __future__ import annotations

import os
import socket
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn, cast


def _deny(operation: str) -> NoReturn:
    raise RuntimeError(f"offline network access denied: {operation}")


if os.environ.get("NXP_MONKEY_OFFLINE") == "1":
    _real_socket = socket.socket
    _real_popen = cast(Callable[..., subprocess.Popen[bytes]], subprocess.Popen)

    class _OfflineSocket(_real_socket):
        def connect(self, address: object) -> NoReturn:
            _deny(f"socket connect to {address!r}")

        def connect_ex(self, address: object) -> NoReturn:
            _deny(f"socket connect_ex to {address!r}")

    def _deny_create_connection(*args: object, **kwargs: object) -> NoReturn:
        _deny("socket.create_connection")

    def _deny_getaddrinfo(*args: object, **kwargs: object) -> NoReturn:
        _deny("DNS lookup")

    def _guarded_popen(
        args: object, *popen_args: object, **kwargs: object
    ) -> subprocess.Popen[bytes]:
        if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
            _deny("shell subprocess")
        command = [str(part) for part in args]
        executable = Path(command[0]).name.casefold()
        if executable not in {"git", "git.exe"}:
            _deny(f"subprocess {executable}")
        forbidden = {"clone", "fetch", "ls-remote", "pull", "push", "submodule"}
        if any(part.casefold() in forbidden for part in command[1:]):
            _deny(f"git network command {' '.join(command[1:])}")
        return _real_popen(args, *popen_args, **kwargs)

    socket.socket = _OfflineSocket
    socket.create_connection = _deny_create_connection
    socket.getaddrinfo = _deny_getaddrinfo
    setattr(subprocess, "Popen", _guarded_popen)  # noqa: B010
