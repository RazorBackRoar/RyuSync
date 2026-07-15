"""Regression tests for NSP single-file naming.

Tests both the base name extraction and renaming rule logic to ensure that
legitimate game titles containing keywords like Update, Patch, Version, Ver,
or dump tags like F33/F1 (or containing them as substrings like Everhood,
Silverhood, Clover, Patchwork) are not truncated or mangled, while keeping
the Title IDs and [GME], [UPD], [DLC] tags correct.
"""

from __future__ import annotations

import os
import queue

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ryusync import DragDropWindow, FolderProcessingWorker, _get_base_name


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


@pytest.mark.parametrize(
    "filename,expected_base",
    [
        # Primary regression case reported by the user.
        ("Everhood [0100E20014028000][GME].nsp", "Everhood"),
        # Same title with update and DLC tags.
        ("Everhood [0100E20014028800][UPD].nsp", "Everhood"),
        ("Everhood [0100E20014028000][DLC].nsp", "Everhood"),
        # Other titles containing "ver" as a substring.
        ("Silverhood [0100000000000000][GME].nsp", "Silverhood"),
        ("Overboard [0100000000000000][GME].nsp", "Overboard"),
        ("Reverie [0100000000000000][GME].nsp", "Reverie"),
        # Titles where "Update/Ver/Version" really IS a version keyword.
        ("Some Game Update 1.0.6 [0100000000000000][UPD].nsp", "Some Game"),
        ("Some Game Ver 2.0 [0100000000000000][UPD].nsp", "Some Game"),
        ("Some Game Version 1.0 [0100000000000000][GME].nsp", "Some Game"),
        # Plain title without tricky substrings.
        ("Zelda [0100000000000000][GME].nsp", "Zelda"),
        # Tricky titles containing other version or dump keywords as prefixes/substrings
        ("Clover2 [0100000000000000][GME].nsp", "Clover2"),
        ("Clover 2 [0100000000000000][GME].nsp", "Clover 2"),
        ("Patchwork [0100000000000000][GME].nsp", "Patchwork"),
        ("F1 2023 [0100E20014028000][GME].nsp", "F1 2023"),
    ],
)
def test_get_base_name_preserves_full_title(filename: str, expected_base: str) -> None:
    """The parsed base name must preserve the full game title."""
    assert _get_base_name(filename) == expected_base


@pytest.mark.parametrize(
    "filename,expected_base",
    [
        # Snake-case DLC descriptors should be stripped so the same game's DLCs
        # share a single base name.
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_deluxe_edition_bonuses_dlc.nsp",
            "V Final fantasy tactics the ivalice chronicles",
        ),
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_pre_order_bonuses_dlc.nsp",
            "V Final fantasy tactics the ivalice chronicles",
        ),
    ],
)
def test_get_base_name_strips_dlc_descriptors_for_dlc_files(
    filename: str, expected_base: str
) -> None:
    """DLC base-name extraction must strip trailing descriptors for grouping."""
    assert _get_base_name(filename, is_dlc=True) == expected_base


@pytest.mark.parametrize(
    "filename,expected_worker,expected_window",
    [
        (
            "Everhood [0100E20014028000][GME].nsp",
            "Everhood [0100E20014028000] [GME].nsp",
            "Everhood [0100E20014028000] [GME].nsp",
        ),
        (
            "Silverhood [0100000000000000][UPD].nsp",
            "Silverhood [0100000000000000] [UPD].nsp",
            "Silverhood [0100000000000000] [UPD].nsp",
        ),
        (
            "Overboard [0100000000000000][DLC].nsp",
            "Overboard - DLC [0100000000000000] [DLC].nsp",
            "Overboard [0100000000000000] [DLC].nsp",
        ),
        (
            "Clover2 [0100000000000000][GME].nsp",
            "Clover2 [0100000000000000] [GME].nsp",
            "Clover2 [0100000000000000] [GME].nsp",
        ),
        (
            "Clover 2 [0100000000000000][GME].nsp",
            "Clover 2 [0100000000000000] [GME].nsp",
            "Clover 2 [0100000000000000] [GME].nsp",
        ),
        (
            "Patchwork [0100000000000000][GME].nsp",
            "Patchwork [0100000000000000] [GME].nsp",
            "Patchwork [0100000000000000] [GME].nsp",
        ),
        (
            "F1 2023 [0100E20014028000][GME].nsp",
            "F1 2023 [0100E20014028000] [GME].nsp",
            "F1 2023 [0100E20014028000] [GME].nsp",
        ),
        (
            "Game v1 [0100E20014028000][GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
        ),
        (
            "Game Version 2.0 [0100E20014028000][GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
        ),
        (
            "Game Ver 3 [0100E20014028000][GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
        ),
        (
            "GameV1.0.3 [0100E20014028000][GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
            "Game [0100E20014028000] [GME].nsp",
        ),
        # Snake-case DLC filenames should be split into a base game title and a
        # descriptor, both tagged as [DLC].
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_deluxe_edition_bonuses_dlc.nsp",
            "V Final Fantasy Tactics the Ivalice Chronicles - Deluxe Edition Bonuses DLC [DLC].nsp",
            "V Final Fantasy Tactics The Ivalice Chronicles Deluxe Edition Bonuses DLC [DLC].nsp",
        ),
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_pre_order_bonuses_dlc.nsp",
            "V Final Fantasy Tactics the Ivalice Chronicles - Pre Order Bonuses DLC [DLC].nsp",
            "V Final Fantasy Tactics The Ivalice Chronicles Pre Order Bonuses DLC [DLC].nsp",
        ),
    ],
)
def test_apply_renaming_rules_on_worker_and_window(
    qapp: QApplication, filename: str, expected_worker: str, expected_window: str
) -> None:
    """Verifies that both worker and window renaming functions output the correct standardized names."""
    # 1. Test worker thread renaming
    q: queue.Queue = queue.Queue()
    worker = FolderProcessingWorker(q)
    worker_res = worker._apply_renaming_rules(filename)
    assert worker_res == expected_worker

    # 2. Test main window renaming
    window = DragDropWindow()
    try:
        window_res = window.apply_renaming_rules(filename)
        assert window_res == expected_window
    finally:
        window.close()
