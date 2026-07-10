"""Regression tests for FolderProcessingWorker cancel via razorcore BaseWorker."""

from __future__ import annotations

import os
import queue
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ryusync import FolderProcessingWorker


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def test_folder_processing_worker_stop_aliases_request_cancel(
    qapp: QApplication,
) -> None:
    worker = FolderProcessingWorker(queue.Queue())

    assert not worker.is_cancelled
    worker.stop()
    assert worker.is_cancelled


def test_run_queue_exits_without_processing_when_already_cancelled(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Cancel before draining the queue must leave queued work untouched."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "My Game [0100A77018EA0000].nsp").write_text("")

    work_queue: queue.Queue = queue.Queue()
    work_queue.put((source, tmp_path))

    worker = FolderProcessingWorker(work_queue)
    worker.request_cancel()
    worker.run_queue()

    assert work_queue.qsize() == 1
    assert work_queue.unfinished_tasks == 1


def test_run_queue_stops_after_cancel_without_finishing_queued_work(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Mid-batch cancel must not keep organizing folders already in the queue."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "Game A [0100A77018EA0000].nsp").write_text("")
    (second / "Game B [0100B77018EA0000].nsp").write_text("")

    work_queue: queue.Queue = queue.Queue()
    work_queue.put((first, tmp_path))
    work_queue.put((second, tmp_path))

    finished: list[str] = []
    worker = FolderProcessingWorker(work_queue)
    worker.finished_folder.connect(finished.append)

    worker.request_cancel()
    worker.run_queue()

    assert finished == []
    assert work_queue.qsize() == 2
    assert work_queue.unfinished_tasks == 2
    assert not (tmp_path / "Game A").exists()
    assert not (tmp_path / "Game B").exists()
