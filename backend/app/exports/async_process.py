"""Nonblocking subprocess adapter usable with Uvicorn's Windows selector loop.

Windows asyncio SelectorEventLoop does not implement subprocess transports.
Popen creation and pipe reads therefore run in worker threads while callers
retain an awaitable interface for progress, waiting, timeouts, and cancellation.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import Any

_STDOUT_EOF = object()
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024


def _notify_loop(loop: asyncio.AbstractEventLoop, callback, *args: Any) -> None:
    try:
        loop.call_soon_threadsafe(callback, *args)
    except RuntimeError:
        # The loop is closing; process cleanup still completes on its worker.
        pass


class _AsyncLineReader:
    def __init__(self, pipe, loop: asyncio.AbstractEventLoop) -> None:
        self._pipe = pipe
        self._loop = loop
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._thread = threading.Thread(target=self._read, name="arma-te-stdout", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                line = self._pipe.readline()
                if not line:
                    break
                _notify_loop(self._loop, self._queue.put_nowait, line)
        finally:
            _notify_loop(self._loop, self._queue.put_nowait, _STDOUT_EOF)
            self._pipe.close()

    async def readline(self) -> bytes:
        item = await self._queue.get()
        return b"" if item is _STDOUT_EOF else item

    async def read_all(self) -> bytes:
        chunks: list[bytes] = []
        while True:
            item = await self._queue.get()
            if item is _STDOUT_EOF:
                return b"".join(chunks)
            chunks.append(item)


class _AsyncByteReader:
    def __init__(self, pipe, loop: asyncio.AbstractEventLoop) -> None:
        self._pipe = pipe
        self._loop = loop
        self._finished = asyncio.Event()
        self._data = bytearray()
        self._thread = threading.Thread(target=self._read, name="arma-te-stderr", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self._pipe.read(64 * 1024)
                if not chunk:
                    break
                self._data.extend(chunk)
                if len(self._data) > _MAX_CAPTURE_BYTES:
                    del self._data[:-_MAX_CAPTURE_BYTES]
        finally:
            self._pipe.close()
            _notify_loop(self._loop, self._finished.set)

    async def read(self) -> bytes:
        await self._finished.wait()
        return bytes(self._data)


class AsyncSubprocess:
    """Popen-backed subprocess with nonblocking asyncio-compatible pipe reads."""

    def __init__(self, process: subprocess.Popen, loop: asyncio.AbstractEventLoop) -> None:
        self._process = process
        self.stdout = _AsyncLineReader(process.stdout, loop) if process.stdout is not None else None
        self.stderr = _AsyncByteReader(process.stderr, loop) if process.stderr is not None else None

    @classmethod
    async def create(cls, *argv: str, stdout=subprocess.PIPE, stderr=subprocess.PIPE) -> AsyncSubprocess:
        """Spawn without blocking the event loop, including during cancellation."""
        loop = asyncio.get_running_loop()
        spawn = asyncio.create_task(asyncio.to_thread(
            subprocess.Popen,
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            bufsize=0,
        ))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # A cancelled await cannot abandon a Popen that is still being created.
            process = await spawn
            process.kill()
            await asyncio.to_thread(process.wait)
            raise
        return cls(process, loop)

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()

    def kill(self) -> None:
        if self._process.poll() is None:
            self._process.kill()

    async def wait(self) -> int:
        while (returncode := self._process.poll()) is None:
            await asyncio.sleep(.05)
        await self._join_readers()
        return returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        await self.wait()
        stdout = await self.stdout.read_all() if self.stdout is not None else b""
        stderr = await self.stderr.read() if self.stderr is not None else b""
        return stdout, stderr

    async def _join_readers(self) -> None:
        threads = [
            reader._thread for reader in (self.stdout, self.stderr)
            if reader is not None and reader._thread.is_alive()
        ]
        if threads:
            await asyncio.gather(*(asyncio.to_thread(thread.join) for thread in threads))

