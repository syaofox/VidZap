import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class DownloadTask:
    url: str
    format_id: str
    cookie_file: str | None
    write_thumbnail: bool
    write_subtitles: bool
    subtitle_langs: list[str] | None
    progress_callback: Callable[[float, str, str], None] | None
    download_id: int | None


class DownloadQueue:
    """同源串行、不同源并行的下载队列调度器。

    每个 origin (scheme://netloc) 维护一个独立队列和 worker，
    worker 串行消费队列中的任务，不同 origin 的 worker 完全并行。
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[DownloadTask | None]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _get_origin(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def enqueue(
        self,
        url: str,
        format_id: str,
        cookie_file: str | None,
        write_thumbnail: bool = False,
        write_subtitles: bool = False,
        subtitle_langs: list[str] | None = None,
        progress_callback: Callable[[float, str, str], None] | None = None,
        download_id: int | None = None,
    ) -> None:
        origin = self._get_origin(url)
        task = DownloadTask(
            url=url,
            format_id=format_id,
            cookie_file=cookie_file,
            write_thumbnail=write_thumbnail,
            write_subtitles=write_subtitles,
            subtitle_langs=subtitle_langs,
            progress_callback=progress_callback,
            download_id=download_id,
        )

        async with self._lock:
            if origin not in self._queues:
                self._queues[origin] = asyncio.Queue()
                self._workers[origin] = asyncio.ensure_future(self._worker(origin))
            await self._queues[origin].put(task)

    async def _worker(self, origin: str) -> None:
        from core.ytdlp_handler import start_download

        queue = self._queues[origin]
        while True:
            task = await queue.get()
            if task is None:
                queue.task_done()
                break

            try:
                await start_download(
                    url=task.url,
                    format_id=task.format_id,
                    cookie_file=task.cookie_file,
                    write_thumbnail=task.write_thumbnail,
                    write_subtitles=task.write_subtitles,
                    subtitle_langs=task.subtitle_langs,
                    progress_callback=task.progress_callback,
                    download_id=task.download_id,
                )
            except Exception:
                logger.exception("Download failed for %s", task.url)

            queue.task_done()

    async def shutdown(self) -> None:
        async with self._lock:
            for origin, queue in self._queues.items():
                await queue.put(None)
            for task in self._workers.values():
                await task
            self._queues.clear()
            self._workers.clear()


download_queue = DownloadQueue()
