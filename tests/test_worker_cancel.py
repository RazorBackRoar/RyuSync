"""Regression: FolderProcessingWorker must honour razorcore cancel via stop()."""

from __future__ import annotations

import os
import queue
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from ryusync import FolderProcessingWorker


@pytest.fixture(scope="module")
def qapp() -> QCoreApplication:
    app = QCoreApplication.instance()
    if isinstance(app, QCoreApplication):
        return app
    return QCoreApplication([])


def test_stop_aliases_request_cancel(qapp: QCoreApplication) -> None:
    worker = FolderProcessingWorker(queue.Queue())

    assert not worker.is_cancelled

    worker.stop()

    assert worker.is_cancelled


def test_run_queue_skips_work_when_cancelled_before_start(
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    folder = tmp_path / "games"
    folder.mkdir()
    (folder / "Game [0100A77018EA0000].nsp").write_text("x")

    work_queue: queue.Queue = queue.Queue()
    work_queue.put((folder, tmp_path))

    worker = FolderProcessingWorker(work_queue)
    processed: list[str] = []

    def _should_not_run(*_args: object, **_kwargs: object) -> str:
        processed.append("ran")
        return ""

    worker.process_folder_logic = _should_not_run  # type: ignore[method-assign]
    worker.request_cancel()
    worker.run_queue()

    assert processed == []
    assert work_queue.qsize() == 1


def test_run_queue_stops_after_cancel_mid_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QCoreApplication,
) -> None:
    folder_one = tmp_path / "first"
    folder_two = tmp_path / "second"
    for folder in (folder_one, folder_two):
        folder.mkdir()
        (folder / "Game [0100A77018EA0000].nsp").write_text("x")

    work_queue: queue.Queue = queue.Queue()
    work_queue.put((folder_one, tmp_path))
    work_queue.put((folder_two, tmp_path))

    worker = FolderProcessingWorker(work_queue)
    processed: list[str] = []

    def fake_process(processing_path: Path, _original_parent: Path) -> str:
        processed.append(processing_path.name)
        if len(processed) == 1:
            worker.request_cancel()
        return ""

    monkeypatch.setattr(worker, "process_folder_logic", fake_process)
    worker.run_queue()

    assert processed == ["first"]
    assert work_queue.qsize() == 1
