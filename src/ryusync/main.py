#!/usr/bin/env python3

import filecmp
import importlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
from enum import Enum, auto
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from razorcore.appinfo import AboutDialog, print_startup_info
from razorcore.config import get_version
from razorcore.logging import get_log_directory, setup_logging
from razorcore.threading import BaseWorker
from razorcore.updates import check_for_updates

from ryusync.app_resources import get_resource_dir, get_resource_path

APP_NAME = "RyuSync"
PACKAGE_NAME = "ryusync"
APP_VERSION = get_version(default="1.0.0", package_name=PACKAGE_NAME)
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "RyuSync"
HISTORY_DIR = APP_SUPPORT_DIR / "history"
SETTINGS_PATH = APP_SUPPORT_DIR / "settings.json"
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# Spec path: ~/Library/Application Support/RyuSync/logs/
LOG_DIR = get_log_directory(APP_NAME)
LOG_PATH = LOG_DIR / "ryusync.log"

# Initialize logging before optional imports that may log warnings.
setup_logging(
    app_name=APP_NAME,
    level=logging.INFO,
    log_to_file=True,
    log_to_console=True,
    colored_console=True,
    log_filename="ryusync.log",
    logger_name=APP_NAME,
    configure_root=True,
)

# Try to import rapidfuzz with a fallback for installations without it
fuzz: Any = None
process: Any = None
try:
    fuzz = importlib.import_module("rapidfuzz.fuzz")
    process = importlib.import_module("rapidfuzz.process")

    HAS_FUZZY = True
except ImportError as exc:
    logging.warning("rapidfuzz module not found, fuzzy matching disabled: %s", exc)
    HAS_FUZZY = False

# Global type hints for clarity
FileList = list[str]

DEFAULT_SETTINGS = {
    "dry_run_enabled": False,
    "fuzzy_threshold": 70,
}
GAME_FILE_SUFFIXES = (".nsp", ".xci")
ARCHIVE_SUFFIXES = (".rar", ".zip", ".7z")
MAX_FILENAME_COMPONENT_LENGTH = 180
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
INVALID_MAC_FILENAME_CHARS = '<>:"/\\|?*'


class FileOperationError(OSError):
    """Raised when a guarded file operation would be unsafe or invalid."""


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as settings_file:
            loaded_settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not load settings, using defaults: %s", exc)
        return DEFAULT_SETTINGS.copy()
    return {**DEFAULT_SETTINGS, **loaded_settings}


def save_settings(settings: dict[str, Any]) -> None:
    try:
        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        with SETTINGS_PATH.open("w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file, indent=2, sort_keys=True)
    except OSError as exc:
        logging.error("Could not save settings: %s", exc)


def write_history_record(prefix: str, payload: dict[str, Any]) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    history_path = HISTORY_DIR / f"{prefix}-{timestamp}.json"
    with history_path.open("w", encoding="utf-8") as history_file:
        json.dump(payload, history_file, indent=2, sort_keys=True)
    return history_path


# Targeted patterns for cleanup (keep DLC indicators)
COMMON_PATTERNS = [
    r"(?i)\([a-z0-9][\w\-]*\.[a-z]{2,4}\)",
    r"(?i)\[v\d+(?:\.\d+)*\]",
    r"(?i)\(v\d+(?:\.\d+)*\)",
    r"(?i)\[(us|usa|eu|eur|jp|jpn|asia|as|chn|kor|tw|hk|roc)\]",
    r"(?i)\((us|usa|eu|eur|jp|jpn|asia|as|chn|kor|tw|hk|roc)\)",
    r"(?i)\(Update.*?\)",
    r"(?i)\(eShop\)",
    r"(?i)\(NSP\)",
    r"(?i)\[NSP\]",
    r"(?i)\[XCI\]",
    r"(?i)\[Base\+DLC\]",
    r"(?i)\[Update\]",
    r"(?i)\[DLC\]",
    r"(?i)\bswitch\b",
    r"(?i)\bnintendo\b",
    r"(?i)\bgame\b",
    r"(?i)\brom\b",
    r"(?i)\bbase\b",
    r"®",  # Trademark symbol removal
]

# DLC indicators (for categorization, *not* removal)
DLC_INDICATORS = [
    r"(?i)dlc",
    r"(?i)dlcs",
    r"(?i)dlc's",  # Primary DLC indicators
    r"(?i)outfit",
    r"(?i)costume",
    r"(?i)character",
    r"(?i)pack",
    r"(?i)ticket",
    r"(?i)pass",
    r"(?i)content",
    r"(?i)items",
    r"(?i)set",
    r"(?i)raiment",
    r"(?i)garb",
    r"(?i)hairstyle",
    r"(?i)season",
    r"(?i)bonus",
    r"(?i)expansion",
    r"(?i)kit",
    r"(?i)starter",
    r"(?i)additional",
    r"(?i)cosmetic",
    r"(?i)skin",
    r"(?i)bundle",
    r"(?i)add-on",
    r"(?i)addon",
]

# Configuration constants for possessive handling
POSSESSIVE_THRESHOLD = 85  # Threshold for possessive-aware matching

# Configuration constants for pattern detection
COMMON_DLC_HEX_PATTERNS = [
    r"0100A77018EA",  # Generic pattern for series 1
    r"01006C300E9F",  # Generic pattern for series 2
    r"010049B01777",  # DRAGON QUEST TREASURES - add this pattern
    # Add more common hex patterns here as needed
]

# Known specific update hex patterns
KNOWN_UPDATE_HEX_PATTERNS = [
    r"\[01006C300E9F0800\]",  # Specific update pattern 1
    r"\[01009E301F620800\]",  # Momodora Moonlit Farewell Update
    # Add more update patterns as needed
]

# Add more common DLC description patterns
DLC_CONTENT_PATTERNS = [
    "Pack",
    "Ticket",
    "Character",
    "Costume",
    "Season",
    "Bundle",
    "Outfit",
    "Kit",
    "Content",
    "Items",
    "Set",
    "Charcuterie",
    "Accessory",
    "Pass",
    "Expansion",
]


def get_log_file_path(filename: str) -> Path:
    """Return a writable log path, preferring Desktop when it exists."""
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop / filename
    return Path.home() / filename


def append_text_to_log(log_path: Path, message: str) -> None:
    """Append a log message, falling back to stderr if the file cannot be written."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(message)
    except OSError:
        logging.exception("Failed to write log file: %s", log_path)


def log_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
    """Persist uncaught exceptions so GUI startup/runtime failures are diagnosable."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logging.critical(
        "Unhandled exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    formatted_exception = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback)
    )
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    append_text_to_log(
        get_log_file_path("RyuSync Crash Log.txt"),
        f"[{timestamp}] {formatted_exception}\n",
    )


sys.excepthook = log_unhandled_exception


def sanitize_possessive(text: str) -> str:
    """
    Standardizes possessive forms in text strings.
    Handles cases like "Mario's" and "Marios" by normalizing them.
    """
    if not text:
        return text

    # Handle "'s" possessive form
    if text.endswith("'s") or text.endswith("'s"):
        return text[:-2]

    # Handle plural possessives ending with just "s'"
    if text.endswith("s'"):
        return text[:-1]

    return text


def get_possessive_aware_match(
    target: str, choices: list[str], threshold=90
) -> str | None:
    """Fuzzy match that understands possessive forms"""
    if not HAS_FUZZY:
        return None

    normalized_target = sanitize_possessive(target.lower())

    best_match = (None, 0)
    for choice in choices:
        normalized_choice = sanitize_possessive(choice.lower())

        # Use token set ratio for better phrase matching
        score = fuzz.token_set_ratio(normalized_target, normalized_choice)

        if score > best_match[1] and score >= threshold:
            best_match = (choice, score)

    return best_match[0] if best_match[1] >= threshold else target


class FileType(Enum):
    """Enum for file types with auto-generated values"""

    GAME = auto()
    UPDATE = auto()
    DLC = auto()


# Global counters for tracking files
nsp_games: FileList = []
nsp_upds: FileList = []
nsp_dlcs: FileList = []
xci_games: FileList = []
xci_upds: FileList = []
xci_dlcs: FileList = []


# Modify extract_game_id to return the full 16-char hex ID string
def extract_game_id(filename):
    """Extract the full 16-character game ID from filename."""
    # Match common Switch title ID lengths, preferring the standard 16-character form.
    id_match = re.search(r"\[(01[0-9A-Fa-f]{14,16})\]", filename, re.IGNORECASE)
    if id_match:
        return id_match.group(1).upper()
    return None


# Add a new function to get the base ID part
def get_base_id(full_game_id):
    """Get the base game identifier (first 12 hex chars after 0100) from the full ID."""
    if full_game_id and len(full_game_id) >= 12 and full_game_id.startswith("01"):
        # Example: 0100A77018EA0000 -> 0100A77018EA
        return full_game_id[:12].upper()
    return None


# Modify is_same_game to use the base ID
def is_same_game(name1, name2):
    """Check if two games are the same based ONLY on base ID."""
    id1_full = extract_game_id(name1)
    id2_full = extract_game_id(name2)
    base_id1 = get_base_id(id1_full)
    base_id2 = get_base_id(id2_full)

    # Must have matching base IDs to be the same game
    if base_id1 and base_id2:
        return base_id1 == base_id2

    # If IDs are missing or don't match, they are considered different games
    # Avoid fuzzy name matching here as it's unreliable for distinct titles
    return False


# Modify find_dlc_parent_folder to use base ID
def find_dlc_parent_folder(dlc_file, game_folders_dict):
    """Find correct folder for a DLC file using base ID matching.
    game_folders_dict maps base_id to folder Path object.
    """
    dlc_id_full = extract_game_id(dlc_file)
    dlc_base_id = get_base_id(dlc_id_full)

    if dlc_base_id and dlc_base_id in game_folders_dict:
        return game_folders_dict[dlc_base_id]  # Return the Path object

    # Fallback: Try name matching ONLY if ID fails (less reliable)
    dlc_name = dlc_file.split("[")[0].strip()
    if not HAS_FUZZY:
        return None

    best_match_folder = None
    best_score = 0
    # Iterate through the Path objects in the values of the dict
    for folder_path in game_folders_dict.values():
        folder_name = folder_path.name  # Get name from Path object
        score = fuzz.ratio(dlc_name.lower(), folder_name.lower())
        # Use a very high threshold for name matching fallback
        if score > 95 and score > best_score:
            best_match_folder = folder_path
            best_score = score

    return best_match_folder


def reset_counters() -> None:
    """Reset all counters to empty lists"""
    try:
        global nsp_games, nsp_upds, nsp_dlcs, xci_games, xci_upds, xci_dlcs
        nsp_games.clear()
        nsp_upds.clear()
        nsp_dlcs.clear()
        xci_games.clear()
        xci_upds.clear()
        xci_dlcs.clear()
        logging.info("Successfully reset all counters")
    except Exception as e:
        logging.error(f"Failed to reset counters: {e}")
        # Initialize empty lists if clearing fails
        nsp_games = []
        nsp_upds = []
        nsp_dlcs = []
        xci_games = []
        xci_upds = []
        xci_dlcs = []


def add_file(filename: str, file_type: str, category: str) -> None:
    """Add a file to the appropriate counter"""
    if not filename or not isinstance(filename, str):
        logging.error(f"Invalid filename: {filename}")
        return

    if file_type not in ["nsp", "xci"]:
        logging.error(f"Invalid file type: {file_type}")
        return

    if category not in ["game", "update", "dlc"]:
        logging.error(f"Invalid category: {category}")
        return

    try:
        global nsp_games, nsp_upds, nsp_dlcs, xci_games, xci_upds, xci_dlcs

        if file_type == "nsp":
            if category == "game":
                if filename not in nsp_games:
                    nsp_games.append(filename)
                    logging.debug(f"Added NSP game: {filename}")
            elif category == "update":
                if filename not in nsp_upds:
                    nsp_upds.append(filename)
                    logging.debug(f"Added NSP update: {filename}")
            elif category == "dlc":
                if filename not in nsp_dlcs:
                    nsp_dlcs.append(filename)
                    logging.debug(f"Added NSP DLC: {filename}")
        elif file_type == "xci":
            if category == "game":
                if filename not in xci_games:
                    xci_games.append(filename)
                    logging.debug(f"Added XCI game: {filename}")
            elif category == "update":
                if filename not in xci_upds:
                    xci_upds.append(filename)
                    logging.debug(f"Added XCI update: {filename}")
            elif category == "dlc":
                if filename not in xci_dlcs:
                    xci_dlcs.append(filename)
                    logging.debug(f"Added XCI DLC: {filename}")
    except Exception as e:
        logging.error(f"Failed to add file {filename}: {e}")


def categorize_file(filename: str, folder_path: str | None = None) -> FileType:
    """
    Determine the file type (GAME, UPDATE, or DLC) based on filename patterns.

    Args:
        filename: Name of the file to categorize
        folder_path: Path to the folder containing the file (optional)

    Returns:
        FileType: Categorized file type (GAME, UPDATE, or DLC)
    """
    # Convert to uppercase for case-insensitive matching
    upper_filename = filename.upper()
    upper_folder = folder_path.upper() if folder_path else ""

    # --- Priority 1: Explicit DLC Tags & Hex Patterns ---
    if "[DLC]" in upper_filename or "(DLC)" in upper_filename:
        return FileType.DLC

    # Check for known DLC hex patterns (e.g., ...1xxx)
    if re.search(r"\[01[0-9A-Fa-f]{12}1[0-9A-Fa-f]{3}\]", filename, re.IGNORECASE):
        return FileType.DLC

    for base_pattern in COMMON_DLC_HEX_PATTERNS:
        if re.search(rf"\[{base_pattern}1[0-9A-Fa-f]{{3}}\]", filename, re.IGNORECASE):
            return FileType.DLC

    # --- Priority 2: DLC Keywords & Content Descriptors ---
    # Use global DLC_INDICATORS with word boundary checks for more precise matching
    for pattern in DLC_INDICATORS:
        # Remove the regex prefix/suffix and use word boundary instead
        clean_pattern = pattern.replace(r"(?i)", "")
        if re.search(
            rf"\[.*?\b{clean_pattern}\b.*?\]|\b{clean_pattern}\b",
            filename,
            re.IGNORECASE,
        ):
            return FileType.DLC

    # Content descriptors with more precise patterns
    dlc_content_regex = "|".join([re.escape(p) for p in DLC_CONTENT_PATTERNS])
    if re.search(rf"\[.*?({dlc_content_regex}).*?\]", filename, re.IGNORECASE):
        return FileType.DLC

    # Folder context
    if folder_path and "DLC" in upper_folder:
        return FileType.DLC

    # --- Priority 3: Explicit Update Tags & Hex Patterns ---
    if "[UPD]" in upper_filename:
        return FileType.UPDATE

    # Check for known update patterns
    if any(
        re.search(pattern, filename, re.IGNORECASE)
        for pattern in KNOWN_UPDATE_HEX_PATTERNS
    ):
        return FileType.UPDATE

    # Update title IDs end in 800. A standard 16-hex-char ID is "01" + 11 hex
    # + the 3-nibble type suffix, so this must be {11}, not {12} (a {12} count
    # requires a 17-char ID and never matched real title IDs).
    if re.search(r"\[01[0-9A-Fa-f]{11}800\]", filename, re.IGNORECASE):
        return FileType.UPDATE

    # --- Priority 4: Update Keywords ---
    if re.search(r"\b(UPDATE|PATCH|REVISION)\b", upper_filename):
        return FileType.UPDATE

    if folder_path and any(
        kw in upper_folder for kw in ["UPDATE", "PATCH", "REVISION"]
    ):
        return FileType.UPDATE

    # --- Priority 5: Check for Base Game Indicators First ---
    # Check for v0 specifically -> GAME (MUST BE CHECKED BEFORE v[1-9])
    if re.search(r"\b[vV]0\b|\[v0\]|\(v0\)|\[0\]", filename, re.IGNORECASE):
        # Ensure it's not explicitly marked as Update/Patch elsewhere
        if not re.search(r"\b(UPDATE|PATCH|REVISION)\b", upper_filename):
            return FileType.GAME

    # Base game title IDs end in 000. A standard 16-hex-char ID is "01" + 11 hex
    # + the 3-nibble type suffix, so this must be {11}, not {12}. This base-game
    # check is intentionally ordered before the version-number heuristic below,
    # so a base game with a non-zero version tag (e.g. [v65536]) is still GAME.
    if re.search(r"\[01[0-9A-Fa-f]{11}000\]", filename, re.IGNORECASE):
        # Ensure it's not explicitly marked as Update/Patch elsewhere
        if not re.search(r"\b(UPDATE|PATCH|REVISION)\b", upper_filename):
            return FileType.GAME

    # --- Priority 6: Version Number Patterns (Only if NOT DLC) ---
    # More comprehensive version detection for formats like: v1, v1.1.2, Version 1.0, Ver 2
    version_match_v = re.search(
        r"\b[vV](?:er(?:sion)?)?\.?\s*([1-9]\d*)[\w\.\-]*", filename
    )
    version_match_f = re.search(r"\b[fF](\d+)\b", filename)  # f<digits> format

    if version_match_v or version_match_f:
        return FileType.UPDATE

    # Version in brackets or parentheses
    if re.search(
        r"\[v[1-9]\d*[\w\.\-]*\]|\(v[1-9]\d*[\w\.\-]*\)", filename, re.IGNORECASE
    ):
        return FileType.UPDATE

    # --- Priority 7: Base+DLC Tag ---
    if "[BASE+DLC]" in upper_filename:
        return FileType.GAME

    # --- Priority 8: Default ---
    # If we get here and see APP tag, likely a base game
    if "[APP]" in upper_filename:
        return FileType.GAME

    return FileType.GAME


def restore_roman_numerals(text: str) -> str:
    """
    Restores roman numerals (I, II, III, IV, etc.) to uppercase if they appear as standalone words.
    """
    roman_numerals = [
        "I",
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "XIII",
        "XIV",
        "XV",
        "XVI",
        "XVII",
        "XVIII",
        "XIX",
        "XX",
    ]

    def repl(match):
        word = match.group(0)
        if word.upper() in roman_numerals:
            return word.upper()
        return word

    # Only replace whole words
    return re.sub(
        r"\b(i{1,3}|iv|v?i{0,3}|ix|x|xi{0,3}|xiv|xv|xvi{0,3}|xix|xx)\b",
        repl,
        text,
        flags=re.IGNORECASE,
    )


def smart_title_case(text: str) -> str:
    """
    Applies title case, preserving specific all-caps words (like 'HD', 'USA')
    and handling small words like 'of', 'the', 'a'.
    """
    # A specific list of acronyms to preserve. This is more robust than guessing.
    acronyms = {"HD", "2D", "3D", "EX", "USA", "NSP", "DLC", "UPD", "GME"}
    small_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "vs",
    }

    words = re.split(r"([ -])", text)  # Split on spaces and hyphens
    new_words = []

    for i, word in enumerate(words):
        if not word.strip():
            new_words.append(word)
            continue

        # Handle all-caps words that aren't acronyms
        if word.isupper() and word not in acronyms:
            word = word.capitalize()

        if word.upper() in acronyms:
            new_words.append(word.upper())
        elif word.lower() in small_words and i > 0:
            new_words.append(word.lower())
        else:
            new_words.append(word.capitalize())

    result = "".join(new_words)
    return restore_roman_numerals(result)


def _get_base_name(filename: str) -> str:
    """
    Extract clean base name from filename by removing common tags and patterns.

    Args:
        filename: The filename to clean

    Returns:
        str: Clean base name without tags and patterns
    """
    try:
        # Remove file extension
        name = os.path.splitext(filename)[0]
        leading_bracket_match = re.match(r"^\[([^\]]+)\]", name)
        leading_game_name = ""
        if leading_bracket_match:
            leading_value = leading_bracket_match.group(1).strip()
            if not re.fullmatch(
                r"01[0-9A-Fa-f]{14,16}", leading_value
            ) and not re.search(
                r"(?i)^(v\d+|up\b|update\b|patch\b|revision\b|us|usa|eu|eur|jp|jpn|asia|[a-z0-9][\w\-]*\.[a-z]{2,4}|[0-9.]+|[0-9]+[gmu+]+)$",
                leading_value,
            ):
                leading_game_name = leading_value

        # Remove trademark symbol
        name = re.sub(r"®", "", name)

        # Remove hex ID pattern
        name = re.sub(r"\[[0-9A-Fa-f]{16}\]", "", name)

        # Remove common tags
        patterns = [
            r"\([a-z0-9][\w\-]*\.[a-z]{2,4}\)",
            r"\s*\((US|USA|EUR|JP)\)",  # Expanded region tags
            r"\[(US|USA|EUR|JP)\]",
            r"\[APP\]",
            r"\[Base\+DLC\]",
            r"\[Update\]",
            r"\[DLC\]",
            r"\(Update.*?\)",
            r"\(eShop\)",
            r"\(NSP\)",
            r"\[NSP\]",
            r"\[XCI\]",
            r"\s*\[v\d+[\w\.\-]*\]|\s*\(v\d+[\w\.\-]*\)",  # Remove bracketed version tags [v1.2], (v1.0.3) etc.
            r"\s+[vV][\d\.]+(?:[a-zA-Z]*\d*)",  # Space + V1.6.3s pattern with possible suffix
            r"\s+[vV]\d+",  # Simple space + V1 pattern
            r"(?<=\w)[vV][\d\.]+(?:[a-zA-Z]*\d*)",  # Version attached to word with no space (GameV1.0.3)
            # Extremely specific patterns for problem cases
            r"\sV1\.6\.3s",  # Exactly matches " V1.6.3s"
            r"\sV1\.0\.3",  # Exactly matches " V1.0.3"
            r"\s+\b[fF]\d+\b",  # f33, F33 (requires preceding space to avoid stripping names like F1)
            r"\s*\b(?:Update|Patch|Revision|Version|Ver)(?![a-zA-Z])\s*[\w\d\.\-]*",  # Update 1.0.6, Version 1.0
            r"\s*\((?:Update|Patch|Revision|Version|Ver)(?![a-zA-Z])\s*[\w\d\.\-]*\s*\)",  # (Update/Patch/Version)
            r"\s*\[(?:Update|Patch|Revision|Version|Ver)(?![a-zA-Z])\s*[\w\d\.\-]*\s*\]",  # [Update/Patch/Version]
            r"\s*\[\d+[GMUgm+]+\]",
            # Enhanced version patterns for bracketed version numbers
            r"\s*\[(?!\s*[0-9A-Fa-f]{16}\s*\])[0-9\.\-]+\]",  # [1.0.6], [262144], [524288]
            r"\s*\[(?!\s*[0-9A-Fa-f]{16}\s*\])[\w\d\.\-+]+\]",  # Catch any remaining bracketed version-like strings
        ]

        for pattern in patterns:
            name = re.sub(pattern, "", name, flags=re.IGNORECASE)

        # Clean up extra spaces and brackets
        name = re.sub(r"\s+", " ", name)  # Replace multiple spaces with single space
        name = re.sub(r"[\[\(\)|\]]", "", name)  # Remove stray brackets
        name = name.strip()
        if not name and leading_game_name:
            name = leading_game_name

        name = restore_roman_numerals(name)
        return name if name else "unknown"

    except Exception as e:
        logging.error(f"Error getting base name for '{filename}': {e}")
        return "unknown"


def get_clean_base_name(filename: str) -> str:
    """Gets a clean base name by removing all bracketed/parenthesized content and junk."""
    name = os.path.splitext(filename)[0]
    leading_bracket_match = re.match(r"^\[([^\]]+)\]", name)
    if leading_bracket_match:
        leading_value = leading_bracket_match.group(1).strip()
        if not re.fullmatch(r"01[0-9A-Fa-f]{14,16}", leading_value) and not re.search(
            r"(?i)^(v\d+|up\b|update\b|patch\b|revision\b|us|usa|eu|eur|jp|jpn|asia|[a-z0-9][\w\-]*\.[a-z]{2,4}|[0-9.]+|[0-9]+[gmu+]+)$",
            leading_value,
        ):
            return smart_title_case(restore_roman_numerals(leading_value))
    name = re.sub(r"\[[^\]]*\]", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"(?i)\b(nintendo|switch|rom|base|game|upd|dlc|gme)\b", "", name)
    name = name.replace("®", "").replace("™", "").strip()
    name = re.sub(r"\s{2,}", " ", name)
    return name if name else "Unknown Game"


def sanitize_path_component(
    name: str,
    *,
    default: str = "Unknown Name",
    preserve_extension: bool = True,
    max_length: int = MAX_FILENAME_COMPONENT_LENGTH,
) -> str:
    """Return a single safe macOS path component, never a path.

    RyuSync generates names from user-provided file names. This helper strips
    path traversal, control characters, hidden-file prefixes, invalid Finder
    characters, and excess whitespace while preserving useful extensions.
    """
    raw_name = Path(str(name)).name
    normalized = unicodedata.normalize("NFC", raw_name)
    normalized = CONTROL_CHAR_RE.sub(" ", normalized)
    for char in INVALID_MAC_FILENAME_CHARS:
        normalized = normalized.replace(char, "_")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if preserve_extension:
        stem, extension = os.path.splitext(normalized)
    else:
        stem, extension = normalized, ""

    stem = re.sub(r"\s+", " ", stem).strip(" .")
    extension = CONTROL_CHAR_RE.sub("", extension).strip()
    if extension and not re.fullmatch(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,15}", extension):
        stem = f"{stem} {extension.lstrip('.')}".strip()
        extension = ""

    if not stem or stem in {".", ".."}:
        stem = default
    stem = stem.lstrip(".").strip() or default

    room_for_stem = max(1, max_length - len(extension))
    if len(stem) > room_for_stem:
        stem = stem[:room_for_stem].rstrip(" .") or default[:room_for_stem]

    result = f"{stem}{extension}".strip()
    if result.startswith("."):
        result = f"{default}{extension}"
    return result or default


def unique_destination_path(destination: Path, source: Path | None = None) -> Path:
    """Return a non-overwriting destination by appending _1, _2, ... if needed."""
    if not destination.exists():
        return destination
    if source is not None:
        try:
            if source.exists() and source.samefile(destination):
                return destination
        except OSError:
            pass

    stem = destination.stem or "Untitled"
    suffix = destination.suffix
    counter = 1
    candidate = destination.with_name(f"{stem}_{counter}{suffix}")
    while candidate.exists():
        counter += 1
        candidate = destination.with_name(f"{stem}_{counter}{suffix}")
    return candidate


def user_facing_error(error: BaseException) -> str:
    """Translate low-level file exceptions into short messages suitable for UI."""
    if isinstance(error, PermissionError):
        return "Permission denied. Choose a folder RyuSync can read and write."
    if isinstance(error, FileNotFoundError):
        return "The selected file or folder is missing. Drop it again from Finder."
    if isinstance(error, FileExistsError):
        return "A destination file already exists. RyuSync did not overwrite it."
    if isinstance(error, FileOperationError):
        return str(error) or "RyuSync blocked an unsafe file operation."
    if isinstance(error, OSError):
        detail = error.strerror or str(error)
        return f"macOS file operation failed: {detail}"
    return "RyuSync could not complete the requested file operation."


def resolve_safe_drop_path(path: Path) -> Path:
    """Resolve a dropped filesystem item without following unsafe scope surprises."""
    if path.is_symlink():
        raise FileOperationError(
            f"RyuSync does not process symlinks or aliases. Drop the original item: {path}"
        )
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Dropped item no longer exists: {path}") from exc
    except OSError as exc:
        raise FileOperationError(f"Could not read dropped item: {path}") from exc
    if resolved.name in {"", ".", ".."}:
        raise FileOperationError(f"Invalid dropped path: {path}")
    return resolved


def is_supported_game_or_archive_file(path: Path) -> bool:
    """Return True for file types RyuSync intentionally handles."""
    suffix = path.suffix.lower()
    return suffix in GAME_FILE_SUFFIXES or suffix in ARCHIVE_SUFFIXES


def sanitize_filename(filename: str, folder_path: str | None = None) -> str:
    """
    Sanitize a string for use as a filename or folder name.
    Removes invalid characters and common junk, but *does not* preserve hex IDs
    as this function is primarily for generating clean folder names.
    """
    try:
        # Get original extension (only relevant if it's a file)
        original_ext = os.path.splitext(filename)[1].lower()
        name_without_ext = os.path.splitext(filename)[0]

        # Sanitize by removing invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name_without_ext = name_without_ext.replace(char, "_")

        # Remove hex IDs and common tags for the *base name* part
        # This is crucial for folder names, as we don't want hex IDs in folder names.
        clean_base_name = re.sub(
            r"\[01[0-9A-Fa-f]{16}\]", "", name_without_ext, flags=re.IGNORECASE
        )
        clean_base_name = re.sub(
            r"\[(DLC|UPD|GME|Base\+DLC)\]", "", clean_base_name, flags=re.IGNORECASE
        )

        # Special handling for version tags - crucial for folder names without version numbers
        # Remove all version tag formats like V1.6.3s and V1.0.3 from folder names
        clean_base_name = re.sub(
            r"\s*\[v\d+[\w\.\-]*\]|\s*\(v\d+[\w\.\-]*\)",
            "",
            clean_base_name,
            flags=re.IGNORECASE,
        )  # Bracketed versions
        clean_base_name = re.sub(
            r"\s+[vV][\d\.]+(?:[a-zA-Z]*\d*)", "", clean_base_name, flags=re.IGNORECASE
        )  # Space + version
        clean_base_name = re.sub(
            r"\s+[vV]\d+", "", clean_base_name, flags=re.IGNORECASE
        )  # Simple versions
        clean_base_name = re.sub(
            r"(?<=\w)[vV][\d\.]+(?:[a-zA-Z]*\d*)",
            "",
            clean_base_name,
            flags=re.IGNORECASE,
        )  # Attached versions
        clean_base_name = re.sub(
            r"\sV1\.6\.3s", "", clean_base_name, flags=re.IGNORECASE
        )  # Specific problematic patterns
        clean_base_name = re.sub(
            r"\sV1\.0\.3", "", clean_base_name, flags=re.IGNORECASE
        )  # Specific problematic patterns

        clean_base_name = re.sub(
            r"\([^)]*\)", "", clean_base_name
        )  # Remove other parentheses
        clean_base_name = re.sub(
            r"\[[^\]]*\]", "", clean_base_name
        )  # Remove other brackets
        clean_base_name = re.sub(
            r"(?i)\b(nintendo|switch|rom|base|game|upd|dlc|gme)\b", "", clean_base_name
        )
        clean_base_name = clean_base_name.replace("®", "").replace("™", "").strip()
        clean_base_name = re.sub(r"\s{2,}", " ", clean_base_name)

        # If the cleaned name is too short, use the folder name as a fallback
        if len(clean_base_name) < 3 and folder_path:
            folder_name_fallback = os.path.basename(os.path.normpath(folder_path))
            # Clean the fallback folder name too
            folder_name_fallback = re.sub(r"\[.*?\]|\(.*?\)", "", folder_name_fallback)
            folder_name_fallback = re.sub(
                r"(?i)\b(nintendo|switch|rom|base|game|upd|dlc|gme)\b",
                "",
                folder_name_fallback,
            )
            folder_name_fallback = (
                folder_name_fallback.replace("®", "").replace("™", "").strip()
            )
            folder_name_fallback = re.sub(r"\s{2,}", " ", folder_name_fallback)
            clean_base_name = (
                folder_name_fallback if folder_name_fallback else "Unknown Name"
            )

        clean_base_name = sanitize_possessive(clean_base_name)
        clean_base_name = smart_title_case(clean_base_name)  # Apply smart title casing

        # Re-add extension if it was a file, then force a safe single path component.
        candidate = (
            f"{clean_base_name}{original_ext}" if original_ext else clean_base_name
        )
        return sanitize_path_component(
            candidate,
            default="Unknown Name",
            preserve_extension=bool(original_ext),
        )

    except Exception as e:
        logging.error(f"Error sanitizing filename/folder name '{filename}': {e}")
        return sanitize_path_component(
            f"unknown{os.path.splitext(filename)[1].lower() or ''}",
            default="unknown",
            preserve_extension=True,
        )


def remove_versions_from_path(path: Path) -> Path:
    """Remove version tags while preserving update identifiers"""
    try:
        if not path.exists():
            logging.warning(f"Path does not exist: {path}")
            return path

        new_name = re.sub(
            r"\s*\[v\d+\]|\s*\(v\d+\)",
            "",
            path.name,  # Only remove version tags
        )

        # Clean up double spaces and trim
        new_name = re.sub(r"\s+", " ", new_name).strip()

        # Only rename if there's actually a change
        if new_name != path.name:
            new_path = path.parent / new_name
            return path.rename(new_path)

        return path
    except Exception as e:
        logging.error(f"Error removing version tags from {path}: {e}")
        return path


def scan_directory(directory: str) -> None:
    """Scan directory recursively and track all NSP/XCI files"""
    if not directory or not os.path.isdir(directory):
        logging.error(f"Invalid directory: {directory}")
        return

    try:
        reset_counters()  # Reset counters before scanning

        total_files = 0
        processed_files = 0

        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith((".nsp", ".xci")):
                    total_files += 1

                    try:
                        file_type = categorize_file(file, root)
                        if file_type:
                            add_file(
                                file,
                                "nsp" if file.lower().endswith(".nsp") else "xci",
                                file_type.name.lower(),
                            )  # Use enum name as category
                            processed_files += 1

                    except Exception as e:
                        logging.error(f"Error processing file {file}: {e}")
                        continue

        logging.info(f"Processed {processed_files}/{total_files} files in {directory}")

    except Exception as e:
        logging.error(f"Error scanning directory {directory}: {e}")


def is_protected_directory(path: Path) -> bool:
    """Return True if *path* is a high-level directory that must never be bulk-processed.

    These are user directories where the destructive organize pipeline (which
    flattens, deletes non-game files and rmtrees subfolders) would be
    catastrophic — e.g. the Home folder, the Desktop, or a filesystem root.
    Used as a drag-and-drop guardrail so a dropped *folder* can never cause the
    whole Desktop/Home to be processed. Single dropped files are unaffected:
    they are wrapped in an isolated folder and only the file itself is touched.
    """

    def _norm(p: Path) -> Path:
        try:
            return p.expanduser().resolve()
        except OSError:
            return p

    target = _norm(path)
    home = _norm(Path.home())
    protected = {
        home,
        _norm(home / "Desktop"),
        _norm(home / "Documents"),
        _norm(home / "Downloads"),
        _norm(home / "Movies"),
        _norm(home / "Workspace"),
        _norm(home / "Workspace" / "Apps"),
        _norm(Path("/Users/home")),
        _norm(Path("/Users")),
        _norm(Path("/Applications")),
        _norm(Path("/Library")),
        _norm(Path("/System")),
        _norm(Path("/Volumes")),
        _norm(Path("/")),
    }
    return target in protected


def should_clean_file(file_path: Path) -> bool:
    """Return True if the file is a .url/.URL file or an OS/system metadata junk file."""
    name_lower = file_path.name.lower()
    suffix_lower = file_path.suffix.lower()
    if suffix_lower == ".url":
        return True
    unwanted_filenames = {
        "desktop.ini",
        "thumbs.db",
        ".ds_store",
        "icon\r",
        "icon\015",
    }
    if name_lower in unwanted_filenames or name_lower.startswith("icon"):
        return True
    return False


def is_path_safe(path: Path, allowed_roots: list[Path]) -> bool:
    """Check if the given path is within one of the allowed root paths (resolved)."""
    try:
        resolved_path = path.resolve()
        for root in allowed_roots:
            resolved_root = root.resolve()
            if resolved_path == resolved_root or resolved_root in resolved_path.parents:
                return True
    except OSError:
        pass
    return False


def is_path_safe_for_deletion(path: Path, directory: Path) -> bool:
    """Check if the path is safe to delete (must be inside directory)."""
    try:
        resolved_path = path.resolve()
        resolved_dir = directory.resolve()
        return resolved_path == resolved_dir or resolved_dir in resolved_path.parents
    except OSError:
        return False


def safe_move(src: Path, dst: Path, allowed_roots: list[Path]) -> None:
    """Move source to destination only if both are inside allowed roots."""
    if not is_path_safe(src, allowed_roots):
        raise FileOperationError(
            f"Blocked move because the source is outside the selected scope: {src}"
        )
    if not is_path_safe(dst, allowed_roots):
        raise FileOperationError(
            f"Blocked move because the destination is outside the selected scope: {dst}"
        )
    if not src.exists():
        raise FileNotFoundError(f"Source file or folder is missing: {src}")
    if dst.exists():
        try:
            if src.samefile(dst):
                return
        except OSError:
            pass
        raise FileExistsError(f"Destination already exists: {dst}")
    shutil.move(str(src), str(dst))


def safe_unlink(path: Path, allowed_roots: list[Path], directory: Path) -> None:
    """Unlink file only if it is within allowed roots and is safe for deletion."""
    if not is_path_safe(path, allowed_roots):
        raise FileOperationError(f"Blocked deletion outside the selected scope: {path}")
    if not is_path_safe_for_deletion(path, directory):
        raise FileOperationError(
            f"Blocked deletion outside the processed folder: {path}"
        )
    path.unlink()


def safe_rename(src: Path, dst: Path, allowed_roots: list[Path]) -> Path:
    """Rename path to target only if both are inside allowed roots."""
    if not is_path_safe(src, allowed_roots):
        raise FileOperationError(
            f"Blocked rename because the source is outside the selected scope: {src}"
        )
    if not is_path_safe(dst, allowed_roots):
        raise FileOperationError(
            f"Blocked rename because the destination is outside the selected scope: {dst}"
        )
    if dst.exists():
        try:
            if src.samefile(dst):
                return src
        except OSError:
            pass
        raise FileExistsError(f"Destination already exists: {dst}")
    return src.rename(dst)


def safe_mkdir(path: Path, allowed_roots: list[Path], exist_ok: bool = True) -> None:
    """Create directory inside allowed roots."""
    if not is_path_safe(path, allowed_roots):
        raise FileOperationError(
            f"Blocked folder creation outside the selected scope: {path}"
        )
    path.mkdir(parents=True, exist_ok=exist_ok)


def safe_rmdir(path: Path, allowed_roots: list[Path], directory: Path) -> None:
    """Remove empty directory only if safe for deletion."""
    if not is_path_safe(path, allowed_roots):
        raise FileOperationError(
            f"Blocked folder removal outside the selected scope: {path}"
        )
    if not is_path_safe_for_deletion(path, directory):
        raise FileOperationError(
            f"Blocked folder removal outside the processed folder: {path}"
        )
    path.rmdir()


def remove_empty_directories(
    path: Path, allowed_roots: list[Path], directory: Path
) -> None:
    """Recursively remove empty directories under path, checking safety."""
    if not path.is_dir():
        return
    if not is_path_safe(path, allowed_roots):
        return
    try:
        for child in list(path.iterdir()):
            if child.is_dir():
                remove_empty_directories(child, allowed_roots, directory)
    except OSError:
        pass
    try:
        if not any(path.iterdir()):
            safe_rmdir(path, allowed_roots, directory)
    except OSError:
        pass


# Archive formats RyuSync can auto-extract before organizing. Matched by suffix,
# so "Game [id].nsp.rar" is an archive (suffix .rar), not a game file.


def is_archive_file(path: Path) -> bool:
    """Return True if *path* is a supported archive (.rar/.zip/.7z)."""
    try:
        return path.is_file() and path.suffix.lower() in ARCHIVE_SUFFIXES
    except OSError:
        return False


def find_unar() -> str | None:
    """Locate the ``unar`` extractor binary.

    Checks PATH plus common Homebrew/MacPorts locations, because GUI apps
    launched from Finder do not inherit the shell PATH.
    """
    candidates = [
        shutil.which("unar"),
        "/opt/homebrew/bin/unar",
        "/usr/local/bin/unar",
        "/opt/local/bin/unar",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def extract_archive(archive: Path, dest_dir: Path) -> int:
    """Extract *archive* into *dest_dir* using ``unar``.

    ``unar`` only reads the archive; the caller (the worker thread) deletes the
    original after the extracted contents have been organized. Returns the
    number of .nsp/.xci files found in *dest_dir* afterwards. Raises RuntimeError
    if ``unar`` is unavailable or extraction fails.
    """
    unar = find_unar()
    if not unar:
        raise RuntimeError(
            "The 'unar' extractor was not found. Install it with: brew install unar"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    logging.info("Extracting archive %s -> %s", archive, dest_dir)
    result = subprocess.run(
        [unar, "-f", "-o", str(dest_dir), str(archive)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"unar failed for {archive.name}: {message}")
    return sum(
        1
        for f in dest_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in (".nsp", ".xci")
    )


def process_single_file(file_path: Path, target_dir: Path) -> None:
    """
    Process a single game file with comprehensive error handling.

    Args:
        file_path: Path to the file to process
        target_dir: Target directory for the processed file
    """
    try:
        if not file_path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        if file_path.suffix.lower() not in {".nsp", ".xci"}:
            raise ValueError(f"Invalid file type: {file_path.suffix}")

        original_filename = file_path.name
        folder_path = str(file_path.parent)

        # Get original extension
        original_ext = file_path.suffix.lower()

        # Use original filename for categorization
        file_type = categorize_file(original_filename, folder_path)

        # Clean up the filename while preserving DLC content names
        clean_name = sanitize_filename(original_filename, folder_path)

        # Apply renaming rules based on file type
        final_name = f"{clean_name}[{file_type.name}]{original_ext}"

        # Create target path
        target_path = target_dir / final_name

        # Move file with error handling
        try:
            shutil.move(str(file_path), str(target_path))
            logging.info(f"Successfully moved {file_path.name} to {target_path}")
        except OSError as e:
            logging.error(f"Failed to move file {file_path}: {e}")
            raise

    except Exception as e:
        logging.error(f"Error processing file '{file_path}': {e}")
        raise


def merge_folders_by_base_id(parent_dir: Path) -> None:
    """Merge game folders sharing the same base Title ID into one folder."""
    try:
        folder_map: dict[str, list[Path]] = {}
        for sub in parent_dir.iterdir():
            if not sub.is_dir() or sub.name.startswith("_"):
                continue

            candidate_files = list(sub.glob("*.nsp")) + list(sub.glob("*.xci"))
            dlc_dir = sub / "DLC"
            if dlc_dir.is_dir():
                candidate_files += list(dlc_dir.glob("*.nsp")) + list(
                    dlc_dir.glob("*.xci")
                )

            base_id = None
            for file_path in candidate_files:
                full_id = extract_game_id(file_path.name)
                base_id = get_base_id(full_id)
                if base_id:
                    break

            if not base_id:
                continue

            folder_map.setdefault(base_id, []).append(sub)

        allowed_roots = [parent_dir]

        for folders in folder_map.values():
            if len(folders) < 2:
                continue

            primary = folders[0]
            for extra in folders[1:]:
                for item in extra.iterdir():
                    target = primary / item.name
                    counter = 1
                    while target.exists():
                        name, ext = os.path.splitext(item.name)
                        target = primary / f"{name}_merged_{counter}{ext}"
                        counter += 1
                    try:
                        safe_move(item, target, allowed_roots)
                        logging.info(
                            "Merged %s -> %s",
                            item.name,
                            target.relative_to(primary.parent),
                        )
                    except Exception as e:
                        logging.error(f"Error merging {item} into {primary}: {e}")

                try:
                    if not any(extra.iterdir()):
                        safe_rmdir(extra, allowed_roots, parent_dir)
                        logging.info(f"Removed empty duplicate folder {extra}")
                except OSError as e:
                    logging.error(f"Error removing duplicate folder {extra}: {e}")
    except Exception as e:
        logging.error(f"Error during folder merge by base id: {e}")


class FolderProcessingWorker(BaseWorker):
    """Queue worker for folder organize jobs.

    Uses razorcore ``BaseWorker`` for cancel/pause plumbing. Domain progress
    stays on a custom ``progress(str, int, int)`` signal (folder, done, total)
    because it is not the generic ``(current, total, message)`` schema.
    """

    progress = Signal(str, int, int)  # folder, processed, total
    summary = Signal(str)  # summary text
    # BaseWorker already defines error = Signal(str)
    finished_folder = Signal(str)  # folder path when done
    file_counts = Signal(dict)  # dictionary of file counts for updating global counters

    def __init__(self, folder_queue, parent=None):
        super().__init__(parent)
        self.folder_queue = folder_queue
        self.game_organizer = GameOrganizer()  # Create own instance for thread safety
        self._extraction_errors: list[str] = []

    def do_work(self):
        """BaseWorker entry — drain the folder queue until cancelled."""
        self.run_queue()
        return {}

    def run_queue(self):
        while not self.is_cancelled:
            try:
                # Unpack the queue item. Items are (processing_path,
                # original_parent[, archives_to_extract]); the third element is
                # optional, for backward compatibility.
                item = self.folder_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            processing_path, original_parent = item[0], item[1]
            archives = item[2] if len(item) > 2 else []
            try:
                # Auto-extract any dropped archives into the processing folder
                # BEFORE counting/organizing (kept off the UI thread). The
                # original archives are removed after organization succeeds.
                if archives:
                    self._extraction_errors = []
                    self._extract_archives_into(archives, processing_path)

                # Count files for progress reporting
                total_files = sum(
                    1
                    for f in processing_path.rglob("*")
                    if f.is_file() and f.suffix.lower() in (".nsp", ".xci")
                )
                if total_files == 0:
                    if archives:
                        self.error.emit(
                            "No .nsp/.xci files were found inside the dropped "
                            f"archive(s): {', '.join(a.name for a in archives)}"
                        )
                    else:
                        self.error.emit(
                            f"No valid Switch game files (NSP or XCI) found in {processing_path}"
                        )
                    continue

                # Emit initial progress
                self.progress.emit(str(processing_path), 0, total_files)

                # Process folder using our own thread-safe implementation
                try:
                    # Process the folder and get the summary text
                    summary_text = self.process_folder_logic(
                        processing_path, original_parent
                    )

                    # Emit final progress
                    self.progress.emit(str(processing_path), total_files, total_files)

                    # Emit the summary text to update the UI
                    if summary_text:
                        self.summary.emit(summary_text)

                    # The contents were organized successfully: remove the original
                    # archives that were extracted. Failed extractions (corrupt /
                    # password-protected) are preserved so the user can retry them.
                    if archives:
                        self._delete_extracted_archives(
                            archives, processing_path, original_parent
                        )
                except Exception as e:
                    logging.error(
                        f"Error in process_folder_logic: {e!s}", exc_info=True
                    )
                    self.error.emit(f"Error processing {processing_path}: {e!s}")

                # Signal that this folder is complete
                self.finished_folder.emit(str(processing_path))

                # Mark the task as done in the queue
                self.folder_queue.task_done()
            except Exception as e:
                logging.error(f"Worker thread error: {e!s}", exc_info=True)
                self.error.emit(f"Worker error: {e!s}")
                # Still mark the task as done even if there was an error
                try:
                    self.folder_queue.task_done()
                except ValueError:
                    pass

    def _extract_archives_into(self, archives, dest_dir: Path) -> None:
        """Extract each archive into *dest_dir* (runs on the worker thread).

        ``unar`` only reads the archives; the originals are removed afterward by
        :meth:`_delete_extracted_archives` once organization succeeds. An
        extraction failure is surfaced via the error signal but does not abort
        the rest of the batch.
        """
        for archive in archives:
            try:
                if not archive.exists():
                    continue
                logging.info(
                    "[RyuSync drag-drop] extracting archive: %s -> %s",
                    archive,
                    dest_dir,
                )
                count = extract_archive(archive, dest_dir)
                logging.info(
                    "Extracted %s (%d game file(s) found inside)", archive.name, count
                )
            except Exception as e:
                logging.error(
                    "Archive extraction failed for %s: %s", archive, e, exc_info=True
                )
                self._extraction_errors.append(archive.name)
                self.error.emit(str(e))

    def _delete_extracted_archives(
        self, archives, processing_path: Path, original_parent: Path | None
    ) -> None:
        """Remove archives that were successfully extracted and organized.

        Runs on the worker thread after :meth:`process_folder_logic` succeeds.
        Archives whose extraction failed (recorded in
        ``self._extraction_errors``) are left untouched so the user can inspect
        or retry them. Only archives that live inside the processing folder or
        the original parent — i.e. paths the user actually dropped — are ever
        removed, so this can never delete an unrelated file elsewhere.
        """
        failed = set(getattr(self, "_extraction_errors", []))
        # Allowed scopes: the processing folder, and (for single-archive wraps)
        # the original parent the archive was dropped beside.
        scopes: list[Path] = [processing_path.resolve()]
        if original_parent is not None:
            scopes.append(original_parent.resolve())

        def _in_scope(path: Path) -> bool:
            try:
                resolved = path.resolve()
            except OSError:
                return False
            for scope in scopes:
                if resolved == scope or scope in resolved.parents:
                    return True
            return False

        for archive in archives:
            try:
                if archive.name in failed:
                    continue
                if not archive.exists() or not archive.is_file():
                    continue
                if archive.suffix.lower() not in ARCHIVE_SUFFIXES:
                    continue
                if not _in_scope(archive):
                    logging.warning(
                        "[RyuSync] skipping archive cleanup outside scope: %s",
                        archive,
                    )
                    continue
                archive.unlink()
                logging.info("[RyuSync] removed extracted archive: %s", archive)
            except OSError as e:
                logging.warning("[RyuSync] could not remove archive %s: %s", archive, e)

    def process_folder_logic(
        self, directory: Path, original_parent: Path | None = None
    ) -> str:
        """Thread-safe version of directory processing logic without UI operations"""
        logging.info(f"Worker thread processing directory: {directory}")
        processed_files = 0
        failed_files = []

        # Count total files for progress reporting
        total_files = sum(
            1
            for f in directory.rglob("*")
            if f.is_file() and f.suffix.lower() in (".nsp", ".xci")
        )
        self.progress.emit(str(directory), 0, total_files)

        # --- Optional: Keep version removal from folders ---
        for folder in directory.iterdir():
            if folder.is_dir():
                has_upd = any(
                    "[UPD]" in f.name for f in folder.glob("*") if f.is_file()
                )
                if has_upd:
                    remove_versions_from_path(folder)
                    logging.info(f"Processed version tags in folder: {folder.name}")

        # Step 2: Move all files to top level directory
        logging.info("Moving all files to top directory")
        all_files_at_root = []  # Store Path objects of files moved to root
        original_subdirs = [
            d for d in directory.iterdir() if d.is_dir()
        ]  # List dirs before moving

        allowed_roots = [directory]
        if original_parent:
            allowed_roots.append(original_parent)

        for root, _, files in os.walk(
            str(directory), topdown=False
        ):  # topdown=False helps with deleting dirs later
            root_path = Path(root)
            if root_path == directory:
                # Add files already at the root
                for file in files:
                    file_lower = file.lower()
                    root_file = directory / file
                    if file_lower.endswith((".nsp", ".xci")):
                        all_files_at_root.append(root_file)
                    elif should_clean_file(root_file):
                        # Clean junk (.url/.URL, OS metadata) sitting at the root
                        # of the processing folder too — e.g. a .URL file extracted
                        # from a dropped .nsp.rar or sitting beside it.
                        try:
                            safe_unlink(root_file, allowed_roots, directory)
                            logging.info(
                                f"Deleted URL/metadata shortcut file: {root_file}"
                            )
                        except OSError as e:
                            logging.warning(
                                f"Could not remove URL/metadata shortcut file {root_file}: {e}"
                            )
                    else:
                        logging.info(f"Skipped unrelated non-game file: {root_file}")
                continue  # Skip processing root further in this loop

            for file in files:
                file_path = root_path / file
                if not file.lower().endswith((".nsp", ".xci")):
                    # Handle non-game files (e.g., delete .url/.URL or OS metadata files)
                    if should_clean_file(file_path):
                        try:
                            safe_unlink(file_path, allowed_roots, directory)
                            logging.info(
                                f"Deleted URL/metadata shortcut file: {file_path}"
                            )
                        except OSError as e:
                            logging.warning(
                                f"Could not remove URL/metadata shortcut file {file_path}: {e}"
                            )
                    else:
                        logging.info(f"Skipped unrelated non-game file: {file_path}")
                    continue

                target_path = directory / file

                try:
                    counter = 1
                    original_target_name = target_path.name
                    while target_path.exists():
                        name, ext = os.path.splitext(original_target_name)
                        target_path = directory / f"{name}_{counter}{ext}"
                        counter += 1

                    safe_move(file_path, target_path, allowed_roots)
                    all_files_at_root.append(target_path)  # Add the Path object
                    processed_files += 1
                    # Update progress periodically (every 5 files)
                    if processed_files % 5 == 0:
                        self.progress.emit(str(directory), processed_files, total_files)
                except Exception as e:
                    logging.error(f"Error moving file {file_path}: {e}")
                    failed_files.append(str(file_path))

        # Step 3: Force remove original subdirectories after flattening
        logging.info("Force removing original subdirectories...")
        for item in original_subdirs:
            try:
                if (
                    item.exists() and item.is_dir()
                ):  # Check if it still exists before attempting removal
                    remove_empty_directories(item, allowed_roots, directory)
            except Exception as e:
                logging.error(
                    f"Error removing original directory {item.name}: {e}"
                )  # Log error but continue

        # Step 4: Process and organize files by Title ID or fallback base name
        logging.info(
            "Processing and organizing files by Title ID or fallback base name"
        )
        group_key_to_folder: dict[
            str, Path
        ] = {}  # Maps group key (base_id or clean name) to folder Path
        group_key_to_best_name: dict[
            str, str
        ] = {}  # Maps group key to preferred folder name
        file_to_group_key: dict[Path, str] = {}

        # First pass: Identify grouping keys and best names
        logging.info("First pass: Identifying grouping keys and best names...")
        for file_path in all_files_at_root:
            if not file_path.exists():
                continue
            filename = file_path.name
            # Robust hex ID extraction: 16 or 15 hex chars
            id_match = re.search(r"\[(01[0-9A-Fa-f]{14,16})\]", filename)
            full_id = id_match.group(1) if id_match else None
            base_id = get_base_id(full_id) if full_id else None

            if base_id:
                group_key = base_id
            else:
                # Fallback: use cleaned base name as group key
                group_key = _get_base_name(filename)
                if not group_key or group_key.lower() in (
                    "unknown",
                    "",
                ):  # If still unknown, truly ungroupable
                    group_key = None
            if group_key is not None:
                file_to_group_key[file_path] = group_key

            # Determine a clean base name from this file
            current_clean_name = get_clean_base_name(filename)

            # Prefer names from GME/UPD files over DLC files for the folder name
            file_type = categorize_file(filename)
            is_preferred_source = (
                file_type == FileType.GAME or file_type == FileType.UPDATE
            )

            # Update the best name for this group_key if this one is better
            if group_key and (
                group_key not in group_key_to_best_name or is_preferred_source
            ):
                folder_name_candidate = re.sub(r'[<>:"/\\|?*]', "_", current_clean_name)
                folder_name_candidate = folder_name_candidate.strip() or group_key
                group_key_to_best_name[group_key] = folder_name_candidate

        # Second pass: Create folders and move files
        logging.info("Second pass: Creating folders and moving files...")
        processed_files_count = 0
        for file_path in all_files_at_root:
            if not file_path.exists():
                continue
            filename = file_path.name
            group_key = file_to_group_key.get(file_path)

            if not group_key or group_key not in group_key_to_best_name:
                # Move to truly unknown if not groupable
                unknown_folder = directory / "_UNKNOWN_ID"
                safe_mkdir(unknown_folder, allowed_roots, exist_ok=True)
                try:
                    safe_move(file_path, unknown_folder / filename, allowed_roots)
                except Exception as e:
                    logging.error(
                        f"Could not move file with unknown group {filename}: {e}"
                    )
                continue

            canonical_folder_name = self.game_organizer.sanitize_filename(
                group_key_to_best_name[group_key]
            )
            canonical_folder_name = canonical_folder_name.rstrip(".")
            canonical_folder_name = smart_title_case(
                restore_roman_numerals(canonical_folder_name)
            )
            parent_dir_name = file_path.parent.name
            # If we are already in the canonical folder, don't create another subfolder
            if parent_dir_name == canonical_folder_name:
                game_folder = file_path.parent
                # Optionally, if the folder is not exactly canonical, rename it
                if game_folder.name != canonical_folder_name:
                    new_path = game_folder.parent / canonical_folder_name
                    if not new_path.exists():
                        safe_rename(game_folder, new_path, allowed_roots)
                        game_folder = new_path
            else:
                game_folder = directory / canonical_folder_name
                safe_mkdir(game_folder, allowed_roots, exist_ok=True)
            group_key_to_folder[group_key] = game_folder

            # Apply final renaming rules to the file (ALWAYS tags file as [GME]/[UPD]/[DLC])
            renamed_file = self._apply_renaming_rules(filename)

            # Determine category based on tags in the *renamed* file
            if "[DLC]" in renamed_file.upper():
                category = "dlc"
            elif "[UPD]" in renamed_file.upper():
                category = "upd"
            else:
                category = "gme"

            # DLCs go in a DLC subfolder
            if category == "dlc":
                dlc_folder = game_folder / "DLC"
                safe_mkdir(dlc_folder, allowed_roots, exist_ok=True)
                target_path = dlc_folder / renamed_file
            else:
                target_path = game_folder / renamed_file

            # Move file to final location, handle conflicts
            try:
                counter = 1
                original_target_name = target_path.name
                final_target_path = target_path
                is_duplicate = False
                while final_target_path.exists():
                    if file_path.resolve() == final_target_path.resolve():
                        logging.warning(
                            f"Source and target are the same file, skipping move: {file_path}"
                        )
                        is_duplicate = True
                        break
                    try:
                        if filecmp.cmp(
                            str(file_path), str(final_target_path), shallow=False
                        ):
                            logging.warning(
                                f"Identical file already exists at {final_target_path.relative_to(directory)}. Skipping move for duplicate source: {filename}"
                            )
                            is_duplicate = True
                            break
                        else:
                            logging.warning(
                                f"Different file with same name exists at {final_target_path.relative_to(directory)}. Appending _{counter}."
                            )
                            name, ext = os.path.splitext(original_target_name)
                            final_target_path = (
                                final_target_path.parent / f"{name}_{counter}{ext}"
                            )
                            counter += 1
                    except OSError as cmp_error:
                        logging.error(
                            f"Error comparing file {filename} with {final_target_path}: {cmp_error}. Attempting rename."
                        )
                        name, ext = os.path.splitext(original_target_name)
                        final_target_path = (
                            final_target_path.parent / f"{name}_{counter}{ext}"
                        )
                        counter += 1
                    except Exception as e:
                        logging.error(
                            f"Unexpected error during file comparison for {filename}: {e}. Attempting rename."
                        )
                        name, ext = os.path.splitext(original_target_name)
                        final_target_path = (
                            final_target_path.parent / f"{name}_{counter}{ext}"
                        )
                        counter += 1
                if (
                    not is_duplicate
                    and file_path.exists()
                    and file_path.resolve() != final_target_path.resolve()
                ):
                    safe_move(file_path, final_target_path, allowed_roots)
                    processed_files_count += 1
                    logging.debug(
                        f"Moved {filename} -> {final_target_path.relative_to(directory)}"
                    )
                elif is_duplicate:
                    processed_files_count += 1
            except Exception as e:
                logging.error(f"Error moving file {filename} to {target_path}: {e}")
                failed_files.append(filename)

        # Generate summary text and collect file counts
        nsp_game_count = sum(
            1 for f in directory.glob("**/*.nsp") if "[GME]" in f.name.upper()
        )
        nsp_upd_count = sum(
            1 for f in directory.glob("**/*.nsp") if "[UPD]" in f.name.upper()
        )
        nsp_dlc_count = sum(
            1 for f in directory.glob("**/*.nsp") if "[DLC]" in f.name.upper()
        )
        xci_game_count = sum(
            1 for f in directory.glob("**/*.xci") if "[GME]" in f.name.upper()
        )
        xci_upd_count = sum(
            1 for f in directory.glob("**/*.xci") if "[UPD]" in f.name.upper()
        )
        xci_dlc_count = sum(
            1 for f in directory.glob("**/*.xci") if "[DLC]" in f.name.upper()
        )

        total_games = nsp_game_count + xci_game_count
        total_updates = nsp_upd_count + xci_upd_count
        total_dlcs = nsp_dlc_count + xci_dlc_count
        total_files = total_games + total_updates + total_dlcs

        # Collect file paths for each category to emit back to main thread
        file_counts_dict = {
            "nsp_games": [
                str(f) for f in directory.glob("**/*.nsp") if "[GME]" in f.name.upper()
            ],
            "nsp_updates": [
                str(f) for f in directory.glob("**/*.nsp") if "[UPD]" in f.name.upper()
            ],
            "nsp_dlcs": [
                str(f) for f in directory.glob("**/*.nsp") if "[DLC]" in f.name.upper()
            ],
            "xci_games": [
                str(f) for f in directory.glob("**/*.xci") if "[GME]" in f.name.upper()
            ],
            "xci_updates": [
                str(f) for f in directory.glob("**/*.xci") if "[UPD]" in f.name.upper()
            ],
            "xci_dlcs": [
                str(f) for f in directory.glob("**/*.xci") if "[DLC]" in f.name.upper()
            ],
        }

        # Emit file counts to be processed in the main thread
        self.file_counts.emit(file_counts_dict)

        # Build summary text
        summary = "----- Processed Switch Games -----\n\n"

        # NSP Section
        summary += f"NSP Games:  {nsp_game_count}\n"
        summary += f"NSP UPDs:   {nsp_upd_count}\n"
        summary += f"NSP DLCs:   {nsp_dlc_count}\n\n"

        # XCI Section
        summary += f"XCI Games:  {xci_game_count}\n"
        summary += f"XCI UPDs:   {xci_upd_count}\n"
        summary += f"XCI DLCs:   {xci_dlc_count}\n\n"

        # Totals Section
        summary += "----- Totals -----\n\n"
        summary += f"Total NSP:   {nsp_game_count + nsp_upd_count + nsp_dlc_count}\n"
        summary += f"Total XCI:   {xci_game_count + xci_upd_count + xci_dlc_count}\n"
        summary += f"Total Games: {nsp_game_count + xci_game_count}\n"
        summary += f"Total UPDs:  {nsp_upd_count + xci_upd_count}\n"
        summary += f"Total DLCs:  {nsp_dlc_count + xci_dlc_count}\n"
        summary += "----------------------------\n"

        unknown_dir = directory / "_UNKNOWN_ID"
        unknown_count = (
            sum(1 for f in unknown_dir.glob("*") if f.is_file())
            if unknown_dir.exists()
            else 0
        )

        if processed_files_count > 0:
            summary += f"\nSuccessfully processed {processed_files_count} files."

        if unknown_count:
            summary += f"\nSkipped (no ID): {unknown_count} file(s)"

        if failed_files:
            summary += "\n\nFailed files:\n"
            for file in failed_files:
                summary += f"- {file}\n"

        if getattr(self, "_extraction_errors", []):
            summary += "\n\nFailed to extract:\n"
            for name in self._extraction_errors:
                summary += f"- {name}\n"
            summary += "(RAR may be corrupt, password-protected,\n or split across multiple parts)"

        summary += "\nDrop another folder to process."

        # Send final progress update to ensure UI shows 100% completion
        self.progress.emit(str(directory), total_files, total_files)

        # --- Final Step: Cleanup for multi-drop operations ---
        if (
            original_parent
            and original_parent.exists()
            and directory.name.startswith("ryusync_temp_")
        ):
            logging.info(f"Cleaning up temporary directory {directory.name}")
            try:
                # Move processed contents back to the original parent directory
                for item in directory.iterdir():
                    target_path = original_parent / item.name
                    counter = 1
                    # Handle conflicts when moving back
                    while target_path.exists():
                        name, ext = os.path.splitext(item.name)
                        target_path = original_parent / f"{name}_{counter}{ext}"
                        counter += 1
                    safe_move(item, target_path, allowed_roots)
                    logging.info(
                        f"Moved processed folder {item.name} to {original_parent}"
                    )

                # Remove the now-empty temporary directory
                safe_rmdir(directory, allowed_roots, directory)
                logging.info(
                    f"Successfully removed temporary directory: {directory.name}"
                )
            except Exception as e:
                logging.error(f"Error during temporary directory cleanup: {e}")

        return summary

    def _apply_renaming_rules(self, filename: str) -> str:
        """
        Thread-safe version of apply_renaming_rules, ensuring hex IDs are preserved.
        This function is used for the initial renaming of files before they are moved
        into their final game folders.
        """
        try:
            original_ext = os.path.splitext(filename)[1].lower()
            name_part_no_ext = os.path.splitext(filename)[0]

            # 1. Extract Hex ID (must be done first and preserved)
            hex_id_match = re.search(
                r"(\[01[0-9A-Fa-f]{14,16}\])", name_part_no_ext, re.IGNORECASE
            )
            extracted_hex_id = hex_id_match.group(1) if hex_id_match else ""

            # 2. Determine File Type (GME/UPD/DLC) based on original filename
            file_type = categorize_file(filename)
            file_type_tag_map = {
                FileType.GAME: "[GME]",
                FileType.UPDATE: "[UPD]",
                FileType.DLC: "[DLC]",
            }
            # Special case for Base+DLC tag if present in original filename
            if "[BASE+DLC]" in filename.upper():
                file_type_tag = "[Base+DLC]"
            else:
                file_type_tag = file_type_tag_map.get(file_type, "[GME]")

            # 3. Clean the base name: Use the new _get_base_name helper
            base_name = _get_base_name(filename)  # Use the new helper function here
            base_name = smart_title_case(base_name)  # Apply smart title casing

            # 4. Extract DLC description (if applicable)
            dlc_desc_part = ""
            if file_type == FileType.DLC:
                # Look for specific DLC content patterns in the original filename
                dlc_content_regex = "|".join(
                    [re.escape(p) for p in DLC_CONTENT_PATTERNS]
                )
                # This regex tries to capture content within brackets that matches DLC content patterns
                desc_match = re.search(
                    rf"\[\s*(.*?({dlc_content_regex}).*?)\s*\]", filename, re.IGNORECASE
                )
                if desc_match:
                    dlc_desc_part = desc_match.group(1).strip()
                else:
                    # Fallback: find any bracketed content that contains a DLC indicator
                    bracket_matches = re.findall(r"(\[[^\]]+?\])", filename)
                    for bracket_content in bracket_matches:
                        if any(
                            re.search(
                                rf"\b{ind.replace(r'(?i)', '')}\b",
                                bracket_content,
                                re.IGNORECASE,
                            )
                            for ind in DLC_INDICATORS
                        ):
                            dlc_desc_part = bracket_content.strip(
                                "[]"
                            ).strip()  # Remove outer brackets
                            break
                if dlc_desc_part and not dlc_desc_part.startswith("-"):
                    dlc_desc_part = f"- {dlc_desc_part}"

            # 5. Construct the final filename
            final_name_parts = [base_name]
            if dlc_desc_part and dlc_desc_part.lower() not in base_name.lower():
                final_name_parts.append(dlc_desc_part)

            # Always include hex ID and file type tag
            if extracted_hex_id:
                final_name_parts.append(extracted_hex_id)
            final_name_parts.append(file_type_tag)

            final_name = (
                " ".join(part for part in final_name_parts if part).strip()
                + original_ext
            )

            # Final cleanup: remove double spaces, empty brackets/parentheses
            final_name = re.sub(r"\s{2,}", " ", final_name)
            final_name = re.sub(r"\[\s*\]", "", final_name)
            final_name = re.sub(r"\(\s*\)", "", final_name)
            final_name = final_name.strip()

            return sanitize_path_component(
                final_name, default="Unknown Game", preserve_extension=True
            )

        except Exception as e:
            logging.error(
                f"Error applying renaming rules to {filename}: {e}", exc_info=True
            )
            return filename  # Return original if error

    def _merge_folders_by_base_id(self, parent_dir: Path) -> None:
        merge_folders_by_base_id(parent_dir)

    def stop(self):
        """Backward-compatible alias for razorcore ``request_cancel()``."""
        self.request_cancel()


class DragDropWindow(QMainWindow):
    def __init__(self):
        """Initialize the drag and drop window for file organization."""
        super().__init__()

        # Initialize counters and tracking variables
        self.file_counts = {
            "nsp_games": [],
            "nsp_updates": [],
            "nsp_dlcs": [],
            "xci_games": [],
            "xci_updates": [],
            "xci_dlcs": [],
        }
        self.is_processing = False
        self.settings = load_settings()
        self.dry_run_enabled = self.settings.get("dry_run_enabled", False)
        self.processed_files = 0
        self.original_directories = {}
        self.failure_log_path = LOG_DIR / "RyuSync Failure Log.txt"
        self.last_processed_directory = None  # Track the last directory we processed
        self.processed_directories = set()  # Track directories that have been processed

        # --- Async worker/queue additions ---
        import threading

        self.folder_queue = queue.Queue()
        self._worker_lock = threading.Lock()
        self.worker = FolderProcessingWorker(self.folder_queue, parent=self)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.summary.connect(self._on_worker_summary)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished_folder.connect(self._on_worker_finished_folder)
        self.worker.file_counts.connect(self._on_worker_file_counts)

        # Window configuration
        self.setWindowTitle("RyuSync")
        self.setMinimumSize(700, 660)
        self.resize(740, 700)
        self.setAcceptDrops(True)

        # Center the window on the screen
        self.center_on_screen()

        # Create central widget and layout
        central_widget = QWidget()
        central_widget.setObjectName("appRoot")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(22, 20, 22, 20)
        main_layout.setSpacing(14)
        central_widget.setStyleSheet(
            """
            QWidget#appRoot {
                background: #0a0a0f;
                color: #e0e6ed;
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
                             "Helvetica Neue", Arial, sans-serif;
            }
            QFrame#headerPanel {
                background: #14141c;
                border: 1px solid rgba(255, 45, 85, 0.6);
                border-radius: 18px;
            }
            QFrame#footerPanel {
                background: #14141c;
                border: 1px solid rgba(0, 208, 255, 0.6);
                border-radius: 18px;
            }
            QFrame#dropPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0f0f15, stop:0.52 #12121a, stop:1 #0f0f15);
                border: 2px dashed rgba(0, 208, 255, 0.45);
                border-top-color: rgba(255, 45, 85, 0.55);
                border-left-color: rgba(255, 45, 85, 0.55);
                border-bottom-color: rgba(0, 208, 255, 0.55);
                border-right-color: rgba(0, 208, 255, 0.55);
                border-radius: 22px;
            }
            QLabel#titleLabel {
                color: #e0e6ed;
                font-size: 28px;
                font-weight: 700;
            }
            QLabel#subtitleLabel {
                color: #8b9bb4;
                font-size: 13px;
            }
            QLabel#sectionLabel {
                color: #c0c8d8;
                font-size: 15px;
                font-weight: 650;
            }
            QLabel#mutedLabel {
                color: #6b7a8f;
                font-size: 12px;
            }
            QLabel#dropTitle {
                color: #e0e6ed;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#dropHint {
                color: #8b9bb4;
                font-size: 13px;
            }
            QLabel#modePill {
                border-radius: 11px;
                font-size: 12px;
                font-weight: 700;
                padding: 5px 10px;
            }
            QLabel#scopeLabel {
                background: #1a1a24;
                border: 1px solid #2a2a3a;
                border-left: 3px solid #ff2d55;
                border-radius: 10px;
                color: #a0a8b8;
                font-size: 12px;
                padding: 8px 10px;
            }
            QPushButton {
                background: #1a1a24;
                border: 1px solid rgba(255, 45, 85, 0.45);
                border-radius: 10px;
                color: #e0e6ed;
                font-weight: 600;
                padding: 7px 12px;
            }
            QPushButton:hover {
                background: #2a2a3a;
                border-color: #00d0ff;
            }
            QCheckBox {
                color: #c0c8d8;
                font-size: 13px;
                font-weight: 600;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid rgba(255, 45, 85, 0.6);
                border-radius: 4px;
                background: #1a1a24;
            }
            QCheckBox::indicator:checked {
                background: #00d0ff;
                border-color: #00d0ff;
            }
            QCheckBox::indicator:hover {
                border-color: #00d0ff;
            }
            QProgressBar {
                background: #1a1a24;
                border: 1px solid rgba(255, 45, 85, 0.4);
                border-radius: 7px;
                height: 10px;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00d0ff, stop:1 #ff2d55);
                border-radius: 7px;
            }
            QTextEdit#summaryPanel {
                background: #0f0f15;
                border: 1px solid rgba(0, 208, 255, 0.35);
                border-top-color: rgba(255, 45, 85, 0.45);
                border-left-color: rgba(255, 45, 85, 0.45);
                border-radius: 14px;
                color: #e0e6ed;
                font-family: Menlo, Monaco, monospace;
                font-size: 13px;
                padding: 16px;
                selection-background-color: #00d0ff;
            }
            """
        )

        header = QFrame()
        header.setObjectName("headerPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(14)

        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title_label = QLabel("RyuSync")
        title_label.setObjectName("titleLabel")
        title_label.setToolTip("Double-click for About · Right-click for updates")
        title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        title_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        title_label.customContextMenuRequested.connect(self._show_title_context_menu)
        title_label.installEventFilter(self)
        self.title_label = title_label
        title_glow = QGraphicsDropShadowEffect()
        title_glow.setBlurRadius(16)
        title_glow.setColor(QColor(255, 45, 85, 110))
        title_glow.setOffset(0, 0)
        title_label.setGraphicsEffect(title_glow)
        subtitle_label = QLabel("Switch file organizer for macOS")
        subtitle_label.setObjectName("subtitleLabel")
        title_column.addWidget(title_label)
        title_column.addWidget(subtitle_label)
        header_layout.addLayout(title_column, 1)

        self.dry_mode_checkbox = QCheckBox("Dry Mode")
        self.dry_mode_checkbox.setChecked(self.dry_run_enabled)
        self.dry_mode_checkbox.toggled.connect(self._on_dry_mode_toggled)
        header_layout.addWidget(self.dry_mode_checkbox)

        open_logs_button = QPushButton("Open Logs")
        open_logs_button.clicked.connect(self._open_log_folder)
        header_layout.addWidget(open_logs_button)
        main_layout.addWidget(header)

        # Stacked widget: page 0 = splash image, page 1 = results
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        main_layout.addWidget(self.stacked_widget, 1)
        self.setup_window_image()
        self.setup_summary_display()

        footer = QFrame()
        footer.setObjectName("footerPanel")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(14, 12, 14, 12)
        footer_layout.setSpacing(9)

        mode_row = QHBoxLayout()
        self.mode_label = QLabel()
        self.mode_label.setObjectName("modePill")
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_row.addWidget(self.mode_label, 0)
        mode_row.addStretch(1)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        mode_row.addWidget(self.status_label, 1)
        footer_layout.addLayout(mode_row)

        self.selected_paths_label = QLabel("No source selected")
        self.selected_paths_label.setObjectName("scopeLabel")
        self.selected_paths_label.setWordWrap(True)
        footer_layout.addWidget(self.selected_paths_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        footer_layout.addWidget(self.progress_bar)
        main_layout.addWidget(footer)

        self._refresh_mode_ui()

        # Initialize game organizer
        self.game_organizer = GameOrganizer()  # Instantiate GameOrganizer

    def _open_log_folder(self) -> None:
        """Open RyuSync's log folder in Finder."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["open", str(LOG_DIR)])
        except OSError as exc:
            logging.error("Could not open log folder: %s", exc)
            QMessageBox.warning(self, "Open Logs Failed", user_facing_error(exc))

    def eventFilter(self, obj, event):
        """Open About when the RyuSync title is double-clicked."""
        if (
            obj is getattr(self, "title_label", None)
            and event.type() == QEvent.Type.MouseButtonDblClick
        ):
            self._show_about()
            return True
        return super().eventFilter(obj, event)

    def _show_about(self) -> None:
        """Show the standardized razorcore About dialog."""
        dialog = AboutDialog(self, APP_NAME, package_name=PACKAGE_NAME)
        dialog.exec()

    def _check_for_updates(self) -> None:
        """Check GitHub Releases for a newer RyuSync version."""
        result = check_for_updates(APP_NAME, APP_VERSION)
        if result.is_error:
            QMessageBox.warning(
                self,
                "Update Check",
                f"Update check failed: {result.error}",
            )
            return
        if result.update_available:
            detail = f"New version available: {result.latest_version}"
            if result.download_url:
                detail = f"{detail}\n{result.download_url}"
            if result.release_notes:
                detail = f"{detail}\n\n{result.release_notes[:400]}"
            QMessageBox.information(self, "Update Available", detail)
        else:
            QMessageBox.information(
                self,
                "Up to Date",
                f"You are up to date (v{APP_VERSION}).",
            )

    def _show_title_context_menu(self, position) -> None:
        """Title context menu for About and update checking."""
        menu = QMenu(self)
        about_action = menu.addAction("About RyuSync")
        update_action = menu.addAction("Check for Updates")
        chosen = menu.exec(self.title_label.mapToGlobal(position))
        if chosen is about_action:
            self._show_about()
        elif chosen is update_action:
            self._check_for_updates()

    def _on_dry_mode_toggled(self, checked: bool) -> None:
        """Persist and display the current processing mode."""
        self.dry_run_enabled = checked
        self.settings["dry_run_enabled"] = checked
        save_settings(self.settings)
        self._refresh_mode_ui()
        self._set_status("Dry Mode ready" if checked else "Regular Mode ready")

    def _refresh_mode_ui(self) -> None:
        if self.dry_run_enabled:
            self.setWindowTitle("RyuSync - Dry Mode")
            self.mode_label.setText("DRY MODE")
            self.mode_label.setStyleSheet(
                "QLabel#modePill { background: #2a1a1a; color: #ff2d55; border: 1px solid #ff2d55; }"
            )
        else:
            self.setWindowTitle("RyuSync - Regular Mode")
            self.mode_label.setText("REGULAR MODE")
            self.mode_label.setStyleSheet(
                "QLabel#modePill { background: #1a2a3a; color: #00d0ff; border: 1px solid #00d0ff; }"
            )

    def _set_status(self, message: str) -> None:
        if hasattr(self, "status_label"):
            self.status_label.setText(message)

    def _set_selected_paths(self, paths: list[Path]) -> None:
        if not hasattr(self, "selected_paths_label"):
            return
        if not paths:
            self.selected_paths_label.setText("No source selected")
            return
        labels = [str(path) for path in paths[:3]]
        if len(paths) > 3:
            labels.append(f"...and {len(paths) - 3} more")
        self.selected_paths_label.setText("Selected source: " + "\n".join(labels))

    def log_failure(self, error_message: str) -> None:
        """Log failures to the app log folder instead of exposing tracebacks."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.failure_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.failure_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {error_message}\n")
        except Exception as e:
            logging.error(f"Failed to write to failure log: {e}")

    def closeEvent(self, event) -> None:
        """Handle window close event"""
        try:
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(1000)
        except Exception:
            pass
        event.accept()

    def _on_worker_progress(self, folder, processed, total):
        """Handle progress updates from the worker thread"""
        try:
            if hasattr(self, "progress_bar"):
                self.progress_bar.setMaximum(max(total, 1))
                self.progress_bar.setValue(min(processed, max(total, 1)))
            if hasattr(self, "status_label"):
                folder_name = Path(folder).name
                self.status_label.setText(
                    f"Processing {folder_name}: {processed}/{total}"
                )
            # Log progress updates
            if processed == 0:
                logging.info(f"Starting to process {folder} with {total} files")
                # You could show a status message or update a progress bar here
                # For example:
                # self.statusBar().showMessage(f"Processing {folder}... (0/{total})")
            elif processed == total:
                logging.info(
                    f"Finished processing {folder} ({processed}/{total} files)"
                )
                if hasattr(self, "status_label"):
                    self.status_label.setText(f"Finished {Path(folder).name}")
                # self.statusBar().showMessage(f"Finished processing {folder}")
            else:
                # Only log intermediate progress for larger batches to avoid log spam
                if total > 10 and processed % 5 == 0:
                    logging.info(f"Progress on {folder}: {processed}/{total} files")
                    # self.statusBar().showMessage(f"Processing {folder}... ({processed}/{total})")
        except Exception as e:
            logging.error(f"Error updating progress: {e}")
            # Don't show error to user for progress updates as it's not critical

    def _on_worker_summary(self, summary_text):
        self.summary_widget.setText(summary_text)
        self.stacked_widget.setCurrentWidget(self.summary_widget)
        self._set_status("Finished")

    def _on_worker_error(self, error_msg):
        self.log_failure(error_msg)
        safe_message = str(error_msg).splitlines()[0][:180]
        self._set_status(f"Error: {safe_message}")
        self.summary_widget.setText(
            "RyuSync stopped before completing the operation.\n\n"
            f"{safe_message}\n\nOpen Logs for details."
        )
        self.stacked_widget.setCurrentWidget(self.summary_widget)

    def _on_worker_finished_folder(self, folder_path):
        # Called after each folder is processed
        self.is_processing = False

    def _on_worker_file_counts(self, file_counts_dict):
        """Update the global file counts from the worker thread in a thread-safe way"""
        try:
            # Update the file counts dictionary with the new files
            for category, files in file_counts_dict.items():
                if category in self.file_counts:
                    # Convert string paths back to Path objects if needed
                    self.file_counts[category].extend(files)

            logging.info(
                f"Updated global file counts: NSP Games: {len(self.file_counts['nsp_games'])}, "
                f"NSP Updates: {len(self.file_counts['nsp_updates'])}, "
                f"NSP DLCs: {len(self.file_counts['nsp_dlcs'])}, "
                f"XCI Games: {len(self.file_counts['xci_games'])}, "
                f"XCI Updates: {len(self.file_counts['xci_updates'])}, "
                f"XCI DLCs: {len(self.file_counts['xci_dlcs'])}"
            )
        except Exception as e:
            logging.error(f"Error updating file counts: {e}")
            self.log_failure(f"Error updating file counts: {e}")

    def _report_processing_progress(
        self, directory: Path, processed: int, total: int
    ) -> None:
        """Report synchronous processing progress through the existing handler."""
        if total <= 0:
            return
        self._on_worker_progress(str(directory), processed, total)

    def setup_window_image(self) -> None:
        """Page 0 of the stacked widget — splash image shown before first drop."""
        self.image_widget = QFrame()
        self.image_widget.setObjectName("dropPanel")
        self.image_widget.setAcceptDrops(False)
        drop_layout = QVBoxLayout(self.image_widget)
        drop_layout.setContentsMargins(34, 34, 34, 34)
        drop_layout.setSpacing(12)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Drop files to organize")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Neon glow around the drop title
        title_glow = QGraphicsDropShadowEffect()
        title_glow.setBlurRadius(20)
        title_glow.setColor(QColor(0, 208, 255, 120))
        title_glow.setOffset(0, 0)
        title.setGraphicsEffect(title_glow)
        drop_layout.addWidget(title)

        hint = QLabel("NSP, XCI, ZIP, RAR, 7Z, or a specific game folder")
        hint.setObjectName("dropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        drop_layout.addWidget(hint)

        safety = QLabel("RyuSync only processes the selected source scope.")
        safety.setObjectName("mutedLabel")
        safety.setAlignment(Qt.AlignmentFlag.AlignCenter)
        safety.setWordWrap(True)
        drop_layout.addWidget(safety)

        self.stacked_widget.addWidget(self.image_widget)

    def setup_summary_display(self) -> None:
        """Page 1 of the stacked widget — results panel shown after processing."""
        self.summary_widget = QTextEdit()
        self.summary_widget.setObjectName("summaryPanel")
        self.summary_widget.setReadOnly(True)
        self.summary_widget.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.summary_widget.setText(
            "Drop Switch files or a specific folder to see the processing summary."
        )
        self.stacked_widget.addWidget(self.summary_widget)

    def _generate_dry_run_preview(self, dropped_paths: list[Path]) -> str:
        preview_items = []
        scanned_files: list[Path] = []
        archive_files: list[Path] = []
        for dropped_path in dropped_paths:
            # SAFETY: preview ONLY the exact dropped path. A dropped file is
            # previewed as that single file — we never fall back to scanning its
            # parent directory (which would expose unrelated files on, e.g., the
            # Desktop). A dropped folder is previewed by its own contents.
            # Archives are reported (would be extracted) but NEVER extracted in
            # Dry Mode — it stays strictly read-only.
            if dropped_path.is_file():
                if is_archive_file(dropped_path):
                    archive_files.append(dropped_path)
                elif dropped_path.suffix.lower() in (".nsp", ".xci"):
                    scanned_files.append(dropped_path)
            elif dropped_path.is_dir():
                scanned_files.extend(
                    file_path
                    for file_path in dropped_path.rglob("*")
                    if file_path.is_file()
                    and file_path.suffix.lower() in (".nsp", ".xci")
                )
                try:
                    archive_files.extend(
                        child
                        for child in dropped_path.iterdir()
                        if is_archive_file(child)
                    )
                except OSError:
                    pass

        for file_path in scanned_files:
            file_type = categorize_file(file_path.name, str(file_path.parent))
            renamed_file = self.apply_renaming_rules(file_path.name)
            base_name = get_clean_base_name(file_path.name)
            folder_name = smart_title_case(
                restore_roman_numerals(self.game_organizer.sanitize_filename(base_name))
            )
            destination = file_path.parent / folder_name
            if file_type == FileType.DLC or "[DLC]" in renamed_file.upper():
                destination = destination / "DLC"
            preview_items.append(
                {
                    "source": str(file_path),
                    "planned_destination": str(destination / renamed_file),
                    "type": file_type.name,
                }
            )

        history_path = write_history_record(
            "dry-run-preview",
            {
                "app_version": APP_VERSION,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": True,
                "items": preview_items,
                "archives": [str(a) for a in archive_files],
            },
        )

        lines = [
            "----- RyuSync Dry Run Preview -----",
            "",
            f"Files scanned: {len(scanned_files)}",
            f"Archives to extract: {len(archive_files)}",
            f"Planned changes: {len(preview_items) + len(archive_files)}",
            f"Preview saved: {history_path}",
            "",
        ]
        for archive in archive_files[:18]:
            lines.append(f"{archive.name}  (archive)")
            lines.append("  -> extract in place, organize, then remove the archive")
        for item in preview_items[:18]:
            lines.append(f"{Path(item['source']).name}")
            lines.append(f"  -> {item['planned_destination']}")
        if len(preview_items) > 18:
            lines.append(
                f"...and {len(preview_items) - 18} more. Open the preview JSON for full details."
            )
        lines.append("")
        lines.append("Dry Run is ON. No files were moved, renamed, merged, or deleted.")
        return "\n".join(lines)

    def _show_drop_warning(self, title: str, message: str) -> None:
        self.log_failure(f"{title}: {message}")
        self._set_status(message.splitlines()[0][:160])
        QMessageBox.warning(self, title, message)

    def _drop_path_can_process(self, path: Path) -> tuple[bool, str]:
        kind = self._classify_path(path)
        if kind == "missing":
            return False, "The item is missing or cannot be read."
        if kind == "file":
            if is_supported_game_or_archive_file(path):
                return True, ""
            return False, "Only .nsp, .xci, .zip, .rar, and .7z files are supported."
        if is_protected_directory(path):
            return (
                False,
                "Choose a specific game folder instead of a high-level folder.",
            )
        return True, ""

    def _collect_dropped_paths(self, mime) -> tuple[list[Path], list[str]]:
        """Collect local filesystem URLs from a drop and reject everything else."""
        if not mime.hasUrls():
            return [], ["Drop files or folders from Finder."]

        dropped_paths: list[Path] = []
        errors: list[str] = []
        for url in mime.urls():
            if not url.isLocalFile():
                errors.append("RyuSync only accepts local Finder files and folders.")
                continue
            local = url.toLocalFile()
            if not local:
                errors.append("Dropped item was not a local filesystem path.")
                continue
            try:
                resolved = resolve_safe_drop_path(Path(local))
            except OSError as exc:
                errors.append(user_facing_error(exc))
                continue
            can_process, reason = self._drop_path_can_process(resolved)
            if not can_process:
                errors.append(f"{Path(local).name}: {reason}")
                continue
            dropped_paths.append(resolved)
        return dropped_paths, errors

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event"""
        dropped_paths, _errors = self._collect_dropped_paths(event.mimeData())
        if dropped_paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event"""
        pass

    def dropEvent(self, event) -> None:
        """Handle dropped files/folders with strict path safety.

        Safety contract:
          * Dry Mode is strictly read-only — it only previews the EXACT dropped
            path(s) and never moves, copies, renames or deletes anything.
          * Regular Mode performs the real organize operation, but ONLY on the
            exact dropped file/folder. It never walks up to, scans, or processes
            a parent directory (e.g. the Desktop or the Home folder).
        """
        mime = event.mimeData()
        dropped_paths, drop_errors = self._collect_dropped_paths(mime)
        if drop_errors and not dropped_paths:
            self._show_drop_warning(
                "Unsupported Drop",
                "\n".join(dict.fromkeys(drop_errors[:5])),
            )
            return
        if drop_errors:
            logging.info("Skipped unsupported dropped item(s): %s", drop_errors)
        if not dropped_paths:
            return

        self._set_selected_paths(dropped_paths)
        mode = "Dry" if self.dry_run_enabled else "Regular"

        # --- Dry Mode: strictly read-only preview of the EXACT dropped paths ---
        if self.dry_run_enabled:
            for path in dropped_paths:
                self._log_drop_decision(
                    mode,
                    path,
                    self._classify_path(path),
                    "preview only — no file will be moved, copied, renamed or deleted",
                )
            try:
                preview_text = self._generate_dry_run_preview(dropped_paths)
                self.summary_widget.setText(preview_text)
                self.stacked_widget.setCurrentWidget(self.summary_widget)
            except Exception as e:
                logging.error("Error generating dry run preview: %s", e, exc_info=True)
                self.log_failure(f"Error generating dry run preview: {e}")
                QMessageBox.warning(
                    self,
                    "Dry Run Error",
                    "Failed to generate the preview. Open logs for details.",
                )
            return

        # --- Regular Mode: act ONLY on the exact dropped path(s) ---
        try:
            queue_items = self._prepare_drop(dropped_paths)
        except Exception as e:
            logging.error("Error preparing dropped paths: %s", e, exc_info=True)
            self.log_failure(f"Error preparing dropped paths: {e}")
            QMessageBox.warning(
                self,
                "Error",
                user_facing_error(e),
            )
            return

        if not queue_items:
            return

        started = False
        for queue_item in queue_items:
            try:
                if queue_item[0].exists():
                    self.folder_queue.put(queue_item)
                    started = True
                else:
                    QMessageBox.warning(
                        self, "Error", "The path to process does not exist."
                    )
            except Exception as e:
                self.log_failure(f"Error queueing {queue_item[0]}: {e!s}")
                QMessageBox.warning(
                    self,
                    "Error",
                    "Failed to queue files for processing. Open Logs for details.",
                )

        if started:
            with self._worker_lock:
                if not self.worker.isRunning():
                    self.worker.start()

    @staticmethod
    def _classify_path(path: Path) -> str:
        """Return 'file', 'folder' or 'missing' for a dropped path."""
        try:
            if path.is_file():
                return "file"
            if path.is_dir():
                return "folder"
        except OSError:
            pass
        return "missing"

    def _log_drop_decision(self, mode: str, path: Path, kind: str, action: str) -> None:
        """Log exactly what a drop will do, BEFORE the action is performed."""
        logging.info(
            "[RyuSync drag-drop] mode=%s | path=%s | kind=%s | action=%s",
            mode,
            path,
            kind,
            action,
        )

    def _make_isolated_temp_dir(self, base: Path) -> Path:
        """Create a fresh, empty ``ryusync_temp_*`` directory INSIDE *base*.

        The temp dir is always created inside *base* (never *base.parent*), so a
        wrap operation can never escalate upward into the Home folder. The
        ``ryusync_temp_`` prefix is what the worker recognises to move the
        organized result back beside the dropped item and remove the temp dir.
        """
        stamp = int(time.time())
        temp_dir = base / f"ryusync_temp_{stamp}"
        counter = 0
        while temp_dir.exists():
            counter += 1
            temp_dir = base / f"ryusync_temp_{stamp}_{counter}"
        try:
            temp_dir.mkdir()
        except OSError as exc:
            raise FileOperationError(
                f"Could not create a staging folder inside {base}"
            ) from exc
        return temp_dir

    def _prepare_drop(self, dropped_paths: list[Path]) -> list:
        """Build a list of ``(processing_path, original_parent)`` queue items.

        Only the EXACT dropped paths are ever touched — never a parent directory.
        Returns an empty list when there is nothing safe to process.
        """
        safe_paths = []
        for path in dropped_paths:
            try:
                safe_paths.append(resolve_safe_drop_path(path))
            except OSError as exc:
                self._show_drop_warning("Unsupported Drop", user_facing_error(exc))
        if len(safe_paths) == 1:
            item = self._prepare_single_drop(safe_paths[0])
            return [item] if item else []
        return self._prepare_multi_drop(safe_paths)

    def _prepare_single_drop(self, path: Path):
        """Plan a single dropped item.

        Game files are wrapped and organized in isolation. Archives
        (.rar/.zip/.7z) are auto-extracted in the SAME folder, organized, and
        then the original archive is removed. Folders run in place, extracting
        any archives they contain first.
        """
        kind = self._classify_path(path)

        if kind == "missing":
            self._log_drop_decision(
                "Regular", path, kind, "rejected — item no longer exists"
            )
            QMessageBox.warning(self, "Error", "The dropped item no longer exists.")
            return None

        if kind == "file":
            # A dropped file is handled as a single file. We NEVER fall back to
            # its parent directory.
            if is_archive_file(path):
                return self._wrap_single_archive(path)
            if path.suffix.lower() not in GAME_FILE_SUFFIXES:
                self._log_drop_decision(
                    "Regular", path, "file", "rejected — unsupported file type"
                )
                self._show_drop_warning(
                    "Unsupported File",
                    "RyuSync handles .nsp/.xci game files and "
                    ".rar/.zip/.7z archives.\n\n"
                    f"Ignored: {path.name}",
                )
                return None
            self._log_drop_decision(
                "Regular",
                path,
                "file",
                "wrap this single file in an isolated folder and organize ONLY it",
            )
            return self._wrap_single_file(path)

        # kind == "folder": only process explicit folder drops, and never a
        # protected top-level directory (Home, Desktop, drive root, ...).
        if is_protected_directory(path):
            self._log_drop_decision(
                "Regular", path, "folder", "BLOCKED — protected directory"
            )
            QMessageBox.warning(
                self,
                "Operation Blocked",
                f"For your safety, RyuSync will not process this folder:\n\n{path}\n\n"
                "Drop a specific game folder, or a single .nsp/.xci file instead.",
            )
            return None

        # Auto-extract any archives sitting directly inside the dropped folder
        # (in the same folder), then organize the folder in place.
        try:
            archives = sorted(
                child for child in path.iterdir() if is_archive_file(child)
            )
        except OSError:
            archives = []
        if archives:
            self._log_drop_decision(
                "Regular",
                path,
                "folder",
                f"extract {len(archives)} archive(s) in place, organize, then remove the archive(s)",
            )
            return (path, None, archives)

        self._log_drop_decision(
            "Regular", path, "folder", "organize the game files inside this folder"
        )
        return (path, None)

    def _wrap_single_archive(self, archive: Path):
        """Plan a single dropped archive: extract it in the SAME folder.

        An isolated temp dir is created inside the archive's own folder; the
        worker extracts the archive into it, organizes the contents, and moves
        the resulting game folder back beside the archive. The original archive
        is removed once organization succeeds.
        """
        base = archive.parent
        temp_dir = self._make_isolated_temp_dir(base)
        self._log_drop_decision(
            "Regular",
            archive,
            "archive",
            f"extract in place ({base}), organize, then remove the archive",
        )
        return (temp_dir, base, [archive])

    def _wrap_single_file(self, file_path: Path):
        """Move ONLY *file_path* into a fresh isolated temp dir beside it.

        The organize worker then processes that one-file directory and moves the
        resulting game folder back next to where the file was dropped. No other
        file in the parent directory is ever read, moved or modified.
        """
        base = file_path.parent
        temp_dir = self._make_isolated_temp_dir(base)
        safe_name = sanitize_path_component(file_path.name, default="Dropped File")
        destination = unique_destination_path(temp_dir / safe_name, source=file_path)
        safe_move(file_path, destination, [base])
        return (temp_dir, base)

    def _prepare_multi_drop(self, dropped_paths: list[Path]) -> list:
        """Isolate the EXACT dropped items into a temp dir and organize them.

        Only the items the user actually dropped are moved — never their parent
        directory — and the temp dir is created inside the shared parent so the
        operation can never escalate into the Home folder. If the only shared
        ancestor is itself a protected directory, each item is wrapped on its
        own instead of dumping organized output into the Home folder.
        """
        items: list[Path] = []
        for path in dropped_paths:
            try:
                path = resolve_safe_drop_path(path)
            except OSError as exc:
                self._log_drop_decision(
                    "Regular", path, "missing", f"SKIPPED — {user_facing_error(exc)}"
                )
                continue
            kind = self._classify_path(path)
            if kind == "missing":
                continue
            if kind == "file" and not is_supported_game_or_archive_file(path):
                self._log_drop_decision(
                    "Regular", path, "file", "SKIPPED — unsupported file type"
                )
                continue
            if kind == "folder" and is_protected_directory(path):
                self._log_drop_decision(
                    "Regular", path, "folder", "SKIPPED — protected directory"
                )
                continue
            items.append(path)

        if not items:
            return []

        parents = {p.parent for p in items}
        if len(parents) == 1:
            base = next(iter(parents))
        else:
            common = Path(os.path.commonpath([str(p) for p in items]))
            base = common if common.is_dir() else common.parent
            if is_protected_directory(base):
                # Don't dump organized output into Home/root: wrap each item
                # independently in its own parent directory instead.
                results = []
                for path in items:
                    planned = self._prepare_single_drop(path)
                    if planned:
                        results.append(planned)
                return results

        temp_dir = self._make_isolated_temp_dir(base)
        archives: list[Path] = []
        staged_any = False
        for path in items:
            if is_archive_file(path):
                # The worker extracts the archive into temp_dir and removes the
                # original after organization succeeds.
                archives.append(path)
                staged_any = True
                self._log_drop_decision(
                    "Regular",
                    path,
                    "archive",
                    f"extract into {temp_dir.name}, organize, then remove the archive",
                )
                continue
            self._log_drop_decision(
                "Regular",
                path,
                self._classify_path(path),
                f"isolate into {temp_dir.name} and organize",
            )
            try:
                safe_name = sanitize_path_component(
                    path.name,
                    default="Dropped Item",
                    preserve_extension=path.is_file(),
                )
                staged_path = unique_destination_path(temp_dir / safe_name, source=path)
                safe_move(path, staged_path, [base])
                staged_any = True
            except Exception as e:
                self.log_failure(
                    f"Error isolating dropped item {path}: {user_facing_error(e)}"
                )

        if not staged_any:
            try:
                temp_dir.rmdir()
            except OSError:
                pass
            return []
        return [(temp_dir, base, archives)]

    def process_dropped_directory(self, directory: Path) -> str:
        """Process the dropped directory using ID-based file organization."""
        try:
            directory = resolve_safe_drop_path(directory)
            if not directory.is_dir():
                message = "RyuSync can only process a folder here."
                self._show_drop_warning("Invalid Source", message)
                return message
            if is_protected_directory(directory):
                message = (
                    "RyuSync blocked this high-level folder. "
                    "Choose a specific game folder instead."
                )
                self._show_drop_warning("Operation Blocked", message)
                return message
            self._set_selected_paths([directory])
            logging.info(f"Starting to process directory: {directory}")
            if self.dry_run_enabled:
                logging.info(f"Dry run enabled. Previewing directory: {directory}")
                preview_text = self._generate_dry_run_preview([directory])
                self.summary_widget.setText(preview_text)
                self.stacked_widget.setCurrentWidget(self.summary_widget)
                return preview_text

            self.processed_files = 0
            failed_files = []
            self.last_processed_directory = directory

            # --- Optional: Keep version removal from folders ---
            # (This part seems okay, can be kept or removed based on preference)
            for folder in directory.iterdir():
                if folder.is_dir():
                    has_upd = any(
                        "[UPD]" in f.name for f in folder.glob("*") if f.is_file()
                    )
                    if has_upd:
                        remove_versions_from_path(folder)
                        logging.info(f"Processed version tags in folder: {folder.name}")
            # --- End Optional Part ---

            # Step 2: Move all files to top level directory
            logging.info("Moving all files to top directory")
            all_files_at_root = []  # Store Path objects of files moved to root
            original_subdirs = [
                d for d in directory.iterdir() if d.is_dir()
            ]  # List dirs before moving

            allowed_roots = [directory]

            for root, _, files in os.walk(
                str(directory), topdown=False
            ):  # topdown=False helps with deleting dirs later
                root_path = Path(root)
                if root_path == directory:
                    # Add files already at the root
                    for file in files:
                        file_lower = file.lower()
                        root_file = directory / file
                        if file_lower.endswith((".nsp", ".xci")):
                            all_files_at_root.append(root_file)
                        elif should_clean_file(root_file):
                            # Clean junk (.url/.URL, OS metadata) sitting at the
                            # root of the processing folder too — e.g. a .URL file
                            # extracted from a dropped .nsp.rar or sitting beside it.
                            try:
                                safe_unlink(root_file, allowed_roots, directory)
                                logging.info(
                                    f"Deleted URL/metadata shortcut file: {root_file}"
                                )
                            except OSError as e:
                                logging.warning(
                                    f"Could not remove URL/metadata shortcut file {root_file}: {e}"
                                )
                        else:
                            logging.info(
                                f"Skipped unrelated non-game file: {root_file}"
                            )
                    continue  # Skip processing root further in this loop

                for file in files:
                    file_path = root_path / file
                    if not file.lower().endswith((".nsp", ".xci")):
                        # Handle non-game files (e.g., delete .url/.URL or OS metadata files)
                        if should_clean_file(file_path):
                            try:
                                safe_unlink(file_path, allowed_roots, directory)
                                logging.info(
                                    f"Deleted URL/metadata shortcut file: {file_path}"
                                )
                            except OSError as e:
                                logging.warning(
                                    f"Could not remove URL/metadata shortcut file {file_path}: {e}"
                                )
                        else:
                            logging.info(
                                f"Skipped unrelated non-game file: {file_path}"
                            )
                        continue

                    target_path = directory / file

                    try:
                        counter = 1
                        original_target_name = target_path.name
                        while target_path.exists():
                            name, ext = os.path.splitext(original_target_name)
                            target_path = directory / f"{name}_{counter}{ext}"
                            counter += 1

                        safe_move(file_path, target_path, allowed_roots)
                        all_files_at_root.append(target_path)  # Add the Path object
                    except Exception as e:
                        logging.error(f"Error moving file {file_path}: {e}")
                        failed_files.append(str(file_path))

            # Step 3: Force remove original subdirectories after flattening
            logging.info("Force removing original subdirectories...")
            for item in original_subdirs:
                try:
                    if (
                        item.exists() and item.is_dir()
                    ):  # Check if it still exists before attempting removal
                        remove_empty_directories(item, allowed_roots, directory)
                except Exception as e:
                    logging.error(
                        f"Error removing original directory {item.name}: {e}"
                    )  # Log error but continue

            # Step 4: Process and organize files based on Title ID
            logging.info("Processing and organizing files by Title ID")
            game_id_to_folder_path: dict[
                str, Path
            ] = {}  # Maps base_id to canonical folder Path
            game_id_to_best_name: dict[
                str, str
            ] = {}  # Maps base_id to preferred folder name
            total_files = len(all_files_at_root)

            # First pass: Identify base IDs and best names
            logging.info("First pass: Identifying base IDs and best names...")
            for file_path in all_files_at_root:
                if not file_path.exists():
                    continue  # Skip if moved/deleted

                filename = file_path.name
                full_id = extract_game_id(filename)
                base_id = get_base_id(full_id)

                if not base_id:
                    logging.warning(
                        f"Could not extract base ID from {filename}. Skipping."
                    )
                    # Optionally move to an "Unknown" folder
                    unknown_folder = directory / "_UNKNOWN_ID"
                    safe_mkdir(unknown_folder, allowed_roots, exist_ok=True)
                    try:
                        safe_move(file_path, unknown_folder / filename, allowed_roots)
                    except Exception as e:
                        logging.error(
                            f"Could not move file with unknown ID {filename}: {e}"
                        )
                    continue

                # Determine a clean base name from this file
                current_clean_name = get_clean_base_name(filename)

                # Prefer names from GME/UPD files over DLC files for the folder name
                file_type = categorize_file(
                    filename
                )  # Use original name for categorization
                is_preferred_source = (
                    file_type == FileType.GAME or file_type == FileType.UPDATE
                )

                # Update the best name for this base_id if this one is better
                if base_id not in game_id_to_best_name or is_preferred_source:
                    # Basic sanitization for folder name
                    folder_name_candidate = re.sub(
                        r'[<>:"/\\|?*]', "_", current_clean_name
                    )
                    folder_name_candidate = folder_name_candidate.strip()
                    if folder_name_candidate:  # Ensure not empty
                        game_id_to_best_name[base_id] = folder_name_candidate

            # Second pass: Always create a folder for each game and move the file inside
            logging.info("Second pass: Creating folders and moving files...")
            processed_files_count = 0
            processed_files = 0
            for file_path in all_files_at_root:
                if not file_path.exists():
                    continue  # Skip if already moved or deleted

                filename = file_path.name
                full_id = extract_game_id(filename)
                base_id = get_base_id(full_id)

                if not base_id or base_id not in game_id_to_best_name:
                    # Already handled (moved to Unknown or logged) in first pass
                    continue

                # Always create a folder named after the cleaned game name
                canonical_folder_name = self.game_organizer.sanitize_filename(
                    game_id_to_best_name[base_id]
                )
                canonical_folder_name = canonical_folder_name.rstrip(".")
                canonical_folder_name = smart_title_case(
                    restore_roman_numerals(canonical_folder_name)
                )
                game_folder = directory / canonical_folder_name
                safe_mkdir(game_folder, allowed_roots, exist_ok=True)
                game_id_to_folder_path[base_id] = game_folder

                # Apply final renaming rules to the file
                renamed_file = self.apply_renaming_rules(filename)

                # Determine category based on tags in the *renamed* file
                if "[DLC]" in renamed_file.upper():
                    category = "dlc"
                elif "[UPD]" in renamed_file.upper():
                    category = "upd"
                else:  # Assume GME otherwise
                    category = "gme"

                # Always move into game_folder (DLCs in game_folder/DLC)
                if category == "dlc":
                    dlc_folder = game_folder / "DLC"
                    safe_mkdir(dlc_folder, allowed_roots, exist_ok=True)
                    target_path = dlc_folder / renamed_file
                else:
                    target_path = game_folder / renamed_file

                # Move file to final location, handle conflicts
                try:
                    counter = 1
                    original_target_name = target_path.name
                    final_target_path = target_path
                    is_duplicate = False
                    while final_target_path.exists():
                        if file_path.resolve() == final_target_path.resolve():
                            logging.warning(
                                f"Source and target are the same file, skipping move: {file_path}"
                            )
                            is_duplicate = True
                            break
                        try:
                            if filecmp.cmp(
                                str(file_path), str(final_target_path), shallow=False
                            ):
                                logging.warning(
                                    f"Identical file already exists at {final_target_path.relative_to(directory)}. Skipping move for duplicate source: {filename}"
                                )
                                is_duplicate = True
                                break
                            else:
                                logging.warning(
                                    f"Different file with same name exists at {final_target_path.relative_to(directory)}. Appending _{counter}."
                                )
                                name, ext = os.path.splitext(original_target_name)
                                final_target_path = (
                                    final_target_path.parent / f"{name}_{counter}{ext}"
                                )
                                counter += 1
                        except OSError as cmp_error:
                            logging.error(
                                f"Error comparing file {filename} with {final_target_path}: {cmp_error}. Attempting rename."
                            )
                            name, ext = os.path.splitext(original_target_name)
                            final_target_path = (
                                final_target_path.parent / f"{name}_{counter}{ext}"
                            )
                            counter += 1
                        except Exception as e:
                            logging.error(
                                f"Unexpected error during file comparison for {filename}: {e}. Attempting rename."
                            )
                            name, ext = os.path.splitext(original_target_name)
                            final_target_path = (
                                final_target_path.parent / f"{name}_{counter}{ext}"
                            )
                            counter += 1
                    if (
                        not is_duplicate
                        and file_path.exists()
                        and file_path.resolve() != final_target_path.resolve()
                    ):
                        safe_move(file_path, final_target_path, allowed_roots)
                        processed_files_count += 1
                        processed_files += 1
                        logging.debug(
                            f"Moved {filename} -> {final_target_path.relative_to(directory)}"
                        )
                        # Update progress periodically (every 3 files)
                        if processed_files % 3 == 0:
                            self._report_processing_progress(
                                directory, processed_files, total_files
                            )
                    elif is_duplicate:
                        processed_files_count += 1
                        processed_files += 1
                except Exception as e:
                    logging.error(f"Error moving file {filename} to {target_path}: {e}")
                    failed_files.append(filename)

            self.processed_files = processed_files_count  # Update count

            # Step 5: Remove or disable the problematic merge function call
            # REMOVE THIS CALL: self._merge_related_game_folders(directory) # Already removed/commented
            logging.info("Skipping problematic _merge_related_game_folders step.")

            # Step 6: Disable possessive/fuzzy folder consolidation to prevent incorrect merges
            logging.info(
                "Skipping possessive/fuzzy folder consolidation step to prioritize Title ID grouping."
            )
            # self.game_organizer.consolidate_apostrophe_folders(str(directory)) # DISABLED

            # Step 7: Disable folder structure fixing to prevent incorrect merges
            logging.info(
                "Skipping folder structure fixing step to prioritize Title ID grouping."
            )
            # fix_folder_structure(directory) # DISABLED

            # --- NEW STEP 7.25: Merge folders by base ID (consolidate duplicates) ---
            try:
                merge_folders_by_base_id(directory)
            except Exception as e:
                logging.error(f"Error during folder merge: {e}")
                self.log_failure(f"Error during folder merge step: {e!s}")

            # --- NEW STEP 7.5: Standardize filenames to match folder names ---
            try:
                standardize_filenames_to_folder(directory)
            except Exception as e:
                logging.error(f"Error during filename standardization: {e}")
                self.log_failure(
                    f"Error during filename standardization step: {e!s}"
                )
            # --- END NEW STEP ---

            # Step 8: Final cleanup of unwanted files (like .DS_Store potentially created)
            self.remove_unwanted_files(str(directory))
            self._report_processing_progress(directory, total_files, total_files)

            # Step 9: Remove empty game folders (excluding _UNKNOWN_ID) - Refined Check
            logging.info("Removing empty game folders (final check)...")
            for item in list(directory.iterdir()):  # Iterate over a copy
                if item.is_dir() and item.name != "_UNKNOWN_ID":
                    is_truly_empty = True
                    dlc_subfolder_path = item / "DLC"
                    has_dlc_subfolder = dlc_subfolder_path.is_dir()
                    # Check for files directly within the item folder, ignoring the DLC subfolder itself
                    contains_files_directly = any(f.is_file() for f in item.iterdir())

                    if contains_files_directly:
                        is_truly_empty = False
                    elif has_dlc_subfolder:
                        # Check if DLC subfolder has files
                        if any(dlc_subfolder_path.iterdir()):
                            is_truly_empty = False
                        # Check if there are other items besides the (potentially empty) DLC folder
                        elif len(list(item.iterdir())) > 1:
                            is_truly_empty = (
                                False  # Contains other things (like other folders)
                            )
                    elif any(f.is_dir() and f.name != "DLC" for f in item.iterdir()):
                        # Contains other subdirectories besides potentially DLC
                        is_truly_empty = False
                    # If it only contained an empty DLC folder, is_truly_empty remains True

                    if is_truly_empty:
                        try:
                            remove_empty_directories(item, allowed_roots, directory)
                        except Exception as e:
                            logging.error(
                                f"Error removing final empty game folder {item.name}: {e}"
                            )

            # Step 10: Update counters and generate summary (use the existing logic)
            logging.info("Updating final file counts for summary...")
            self._update_file_counts_after_merge(
                directory
            )  # Update counts based on final state

            # FINAL POLISH: Standardize filenames to match folder names
            standardize_filenames_to_folder(directory)

            success_count = self.processed_files
            summary = self.generate_file_summary(success_count, failed_files)

            self.summary_widget.setText(summary)

            return summary

        except Exception as e:
            logging.error(f"Error processing directory: {e}", exc_info=True)
            # Log failure to file
            readable_error = user_facing_error(e)
            self.log_failure(f"FATAL ERROR processing {directory}: {readable_error}")
            # Show error message to user
            QMessageBox.critical(
                self,
                "Processing Error",
                f"A critical error occurred processing {directory.name}.\n\n"
                f"{readable_error}\n\nOpen Logs for details.",
            )
            # Return error string for internal handling if needed
            return f"Error processing directory: {readable_error}"

    def apply_renaming_rules(self, filename: str) -> str:
        """Apply comprehensive renaming rules with improved UPD detection"""
        try:
            # Get original extension
            original_ext = os.path.splitext(filename)[1].lower()

            # Clean up the filename while preserving original for categorization
            name_to_clean = os.path.splitext(filename)[0]
            name_to_clean = re.sub(r"®", "", name_to_clean)

            # Determine file type based on ORIGINAL name BEFORE cleaning versions
            file_type = categorize_file(
                filename
            )  # Use original name for categorization

            # --- Extract Hex ID with high precision ---
            hex_id = ""
            hex_match = re.search(r"\[([0-9A-Fa-f]{16})\]", name_to_clean)
            if hex_match:
                hex_id = hex_match.group(0)
                name_to_clean = name_to_clean.replace(hex_id, " __HEXID__ ")

            # --- Define Cleaning Patterns ---
            # Patterns to remove regardless of file type
            patterns_to_remove_always = [
                r"\([a-z0-9][\w\-]*\.[a-z]{2,4}\)",
                r"\s*\[(us|usa|eu|eur|jp|jpn|asia|as|chn|kor|tw|hk|roc)\]",
                r"\s*\((us|usa|eu|eur|jp|jpn|asia|as|chn|kor|tw|hk|roc)\)",
                r"\(eShop\)",
                r"\(NSP\)",
                r"\[NSP\]",
                r"\[XCI\]",
                r"\[APP\]",
                # Remove explicit type tags - they will be re-added based on categorization
                r"\s*\[Update\]",
                r"\s*\[DLC\]",
                r"\s*\[UPD\]",
                r"\s*\[GME\]",
                r"\s*\[Base\+DLC\]",
                r"\s*\[UPDATE\]",
                r"\s*\[GAME\]",
            ]

            # Patterns for version strings (only applied to non-DLC)
            patterns_to_remove_versions = [
                r"\s*\b[vV](?:er(?:sion)?)?\.?\s*\d+[\w\.\-]*",  # v1, v1.1, ver1.0, version 2.0b etc. (requires word boundary)
                r"(?<=\w)[vV][\d\.]+(?:[a-zA-Z]*\d*)",  # Version attached to word with no space (GameV1.0.3)
                r"\s+\b[fF]\d+\b",  # f33, F33 (requires preceding space to avoid stripping names like F1)
                r"\s*\b(?:Update|Patch|Revision)(?![a-zA-Z])\s*[\w\d\.\-]*",  # Update 1.0.6, Patch 1.1 etc.
                r"\s*\((?:Update|Patch|Revision)(?![a-zA-Z])\s*[\w\d\.\-]*\s*\)",  # (Update/Patch)
                r"\s*\[(?:Update|Patch|Revision)(?![a-zA-Z])\s*[\w\d\.\-]*\s*\]",  # [Update/Patch]
                r"\s*\(v\d+[\w\.\-]*\)",  # (v1), (v2.1)
                r"\s*\[v\d+[\w\.\-]*\]",  # [v1], [v1.2]
                # Enhanced version patterns for bracketed version numbers
                r"\s*\[(?!\s*[0-9A-Fa-f]{16}\s*\])[0-9\.\-]+\]",  # [1.0.6], [262144], [524288]
                r"\s*\[(?!\s*[0-9A-Fa-f]{16}\s*\])[\w\d\.\-]+\]",  # Catch any remaining bracketed version-like strings
            ]

            # --- Apply Cleaning ---
            cleaned_name = name_to_clean
            # Apply universal cleaning
            for pattern in patterns_to_remove_always:
                cleaned_name = re.sub(pattern, "", cleaned_name, flags=re.IGNORECASE)

            # Conditionally apply version cleaning based on file type
            if file_type != FileType.DLC:
                for pattern in patterns_to_remove_versions:
                    cleaned_name = re.sub(
                        pattern, "", cleaned_name, flags=re.IGNORECASE
                    )

            # --- Handle DLC Descriptions (Extract after cleaning non-version tags) ---
            dlc_desc = ""
            # Use specific known DLC content patterns first
            dlc_content_regex = "|".join([re.escape(p) for p in DLC_CONTENT_PATTERNS])
            desc_match = re.search(
                rf"\[\s*(.*?({dlc_content_regex}).*?)\s*\]", cleaned_name, re.IGNORECASE
            )
            if desc_match:
                dlc_desc = desc_match.group(0)
                cleaned_name = cleaned_name.replace(dlc_desc, "")
            else:
                # Fallback check for any bracket content with DLC indicators
                bracket_matches = re.findall(r"(\[[^\]]+?\])", cleaned_name)
                for bracket_content in bracket_matches:
                    if any(
                        re.search(
                            rf"\b{ind.replace(r'(?i)', '')}\b",
                            bracket_content,
                            re.IGNORECASE,
                        )
                        for ind in DLC_INDICATORS
                    ):
                        dlc_desc = bracket_content
                        cleaned_name = cleaned_name.replace(dlc_desc, "")
                        break

            # Final cleanup
            base_name = re.sub(r"\s+", " ", cleaned_name).strip()
            if hex_id:
                base_name = base_name.replace("__HEXID__", "").strip()

            # Specifically remove [v0] for DLC files after other cleaning
            if file_type == FileType.DLC:
                base_name = re.sub(
                    r"\s*\[v0\]", "", base_name, flags=re.IGNORECASE
                ).strip()

            base_name = sanitize_possessive(base_name) or "Unknown Game"

            # Convert base_name to title case
            base_name = base_name.title()

            # --- Determine Final Tag ---
            # Map FileType enum to the correct abbreviated tags
            file_type_tag_map = {
                FileType.GAME: "[GME]",
                FileType.UPDATE: "[UPD]",
                FileType.DLC: "[DLC]",
            }

            tag = file_type_tag_map.get(file_type, "[GME]")

            # Special case for Base+DLC
            if "[Base+DLC]" in filename.upper():
                tag = "[Base+DLC]"

            # --- Construct Final Name ---
            final_name_parts = [base_name]
            if dlc_desc and dlc_desc not in base_name:
                final_name_parts.append(dlc_desc.strip())
            if hex_id:
                final_name_parts.append(hex_id)
            final_name_parts.append(tag)

            final_name = (
                " ".join(part for part in final_name_parts if part).strip()
                + original_ext
            )

            # Remove any remaining empty brackets and clean up double spaces
            final_name = re.sub(r"\[\s*\]", "", final_name)
            final_name = re.sub(r"\(\s*\)", "", final_name)
            final_name = re.sub(r"\s+", " ", final_name)

            return sanitize_path_component(
                final_name, default="Unknown Game", preserve_extension=True
            )
        except Exception as e:
            logging.error(
                f"Error applying renaming rules to '{filename}': {e}", exc_info=True
            )
            return filename

    def count_dlc_files(self, directory: str) -> int:
        """Count DLC files recursively in directory"""
        dlc_count = 0
        try:
            for file_path in Path(directory).rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() == ".nsp":
                    if "[DLC]" in file_path.name:
                        dlc_count += 1
        except Exception as e:
            logging.error(f"Error counting DLC files: {e}")
        return dlc_count

    def generate_file_summary(self, success_count: int, failed_files: list[str]) -> str:
        """Generate summary of processed files with proper formatting."""
        # Make all counts dynamic based on the actual processed categories
        nsp_count = (
            len(self.file_counts["nsp_games"])
            + len(self.file_counts["nsp_updates"])
            + len(self.file_counts["nsp_dlcs"])
        )
        xci_count = (
            len(self.file_counts["xci_games"])
            + len(self.file_counts["xci_updates"])
            + len(self.file_counts["xci_dlcs"])
        )
        gme_count = len(self.file_counts["nsp_games"]) + len(
            self.file_counts["xci_games"]
        )
        upd_count = len(self.file_counts["nsp_updates"]) + len(
            self.file_counts["xci_updates"]
        )
        dlc_count = len(self.file_counts["nsp_dlcs"]) + len(
            self.file_counts["xci_dlcs"]
        )

        # Format the summary with a more polished style
        summary = "----- Processed Switch Games -----\n\n"

        # NSP Section
        summary += f"NSP Games:  {len(self.file_counts['nsp_games'])}\n"
        summary += f"NSP UPDs:   {len(self.file_counts['nsp_updates'])}\n"
        summary += f"NSP DLCs:   {len(self.file_counts['nsp_dlcs'])}\n\n"

        # XCI Section
        summary += f"XCI Games:  {len(self.file_counts['xci_games'])}\n"
        summary += f"XCI UPDs:   {len(self.file_counts['xci_updates'])}\n"
        summary += f"XCI DLCs:   {len(self.file_counts['xci_dlcs'])}\n\n"

        # Totals Section
        summary += "----- Totals -----\n\n"
        summary += f"Total NSP:   {nsp_count}\n"
        summary += f"Total XCI:   {xci_count}\n"
        summary += f"Total Games: {gme_count}\n"
        summary += f"Total UPDs:  {upd_count}\n"
        summary += f"Total DLCs:  {dlc_count}\n"
        summary += "----------------------------\n"

        if success_count > 0:
            summary += f"\nSuccessfully processed {success_count} files."

        if failed_files:
            summary += "\n\nFailed files:\n"
            for file in failed_files:
                summary += f"- {file}\n"

        return summary

    def is_valid_game_file(self, file_path: Path) -> bool:
        """Validate if the file is a proper Switch game file."""
        try:
            # Check file extension
            if file_path.suffix.lower() not in (".nsp", ".xci"):
                return False

            return True
        except Exception as e:
            logging.error(f"Error validating file {file_path}: {e}")
            return False

    def organize_dlc_folders(self, directory: str) -> None:
        """Organize DLC into proper subfolders."""
        try:
            # Get a list of all game folders (excluding any standalone DLC folders)
            game_folders = []
            for folder_path in Path(directory).iterdir():
                if folder_path.is_dir() and folder_path.name != "DLC":
                    game_folders.append(folder_path)

            if not game_folders:
                logging.info("No game folders found for DLC organization")
                return

            # First, handle loose DLC files in the root directory
            for file_path in Path(directory).glob("*[DLC]*.nsp"):
                if file_path.is_file() and "[DLC]" in file_path.name:
                    # Find the most likely parent game folder using ID-based matching
                    parent_folder = find_dlc_parent_folder(file_path.name, game_folders)

                    if parent_folder:
                        # Create DLC folder in the parent game folder if it doesn't exist
                        dlc_folder = parent_folder / "DLC"
                        dlc_folder.mkdir(exist_ok=True)

                        # Move DLC file to the parent folder's DLC folder
                        target_path = dlc_folder / file_path.name

                        # Handle name conflicts
                        counter = 1
                        orig_name = target_path.name
                        while target_path.exists():
                            name, ext = os.path.splitext(orig_name)
                            target_path = dlc_folder / f"{name}_{counter}{ext}"
                            counter += 1

                        try:
                            shutil.move(str(file_path), str(target_path))
                            logging.info(
                                f"Moved DLC file: {file_path.name} to {target_path}"
                            )
                        except Exception as e:
                            logging.error(
                                f"Error moving DLC file {file_path.name}: {e}"
                            )
                    else:
                        # No matching game folder found, create a temporary DLC folder in the root
                        dlc_root = Path(directory) / "DLC"
                        dlc_root.mkdir(exist_ok=True)
                        target_path = dlc_root / file_path.name

                        # Handle name conflicts
                        counter = 1
                        orig_name = target_path.name
                        while target_path.exists():
                            name, ext = os.path.splitext(orig_name)
                            target_path = dlc_root / f"{name}_{counter}{ext}"
                            counter += 1

                        try:
                            shutil.move(str(file_path), str(target_path))
                            logging.info(
                                f"Moved DLC file to root DLC folder: {file_path.name}"
                            )
                        except Exception as e:
                            logging.error(
                                f"Error moving DLC file {file_path.name}: {e}"
                            )

            # Second, handle standalone DLC folders (folders that only contain DLC files)
            for game_dir in Path(directory).iterdir():
                if not game_dir.is_dir():
                    continue

                # Check if this is already a DLC folder
                if game_dir.name == "DLC":
                    # Move DLC folder contents up one level to parent directory
                    for file_path in game_dir.glob("*"):
                        if file_path.is_file() and "[DLC]" in file_path.name:
                            # Try to find parent for each DLC file
                            parent_folder = find_dlc_parent_folder(
                                file_path.name, game_folders
                            )

                            if parent_folder:
                                # Create DLC folder in the parent game folder if it doesn't exist
                                dlc_folder = parent_folder / "DLC"
                                dlc_folder.mkdir(exist_ok=True)

                                # Move DLC file to the parent folder's DLC folder
                                target_path = dlc_folder / file_path.name

                                # Handle name conflicts
                                counter = 1
                                while target_path.exists():
                                    name, ext = os.path.splitext(file_path.name)
                                    target_path = dlc_folder / f"{name}_{counter}{ext}"
                                    counter += 1

                                try:
                                    shutil.move(str(file_path), str(target_path))
                                    logging.info(
                                        f"Moved DLC file: {file_path.name} to {target_path}"
                                    )
                                except Exception as e:
                                    logging.error(
                                        f"Error moving DLC file {file_path.name}: {e}"
                                    )

                    # Remove DLC folder if empty after moving files
                    if not any(game_dir.iterdir()):
                        try:
                            shutil.rmtree(str(game_dir))
                            logging.info(f"Removed empty DLC folder: {game_dir}")
                        except Exception as e:
                            logging.error(f"Error removing DLC folder {game_dir}: {e}")
                    continue

                # Check if this folder contains only DLC files
                has_non_dlc = False
                has_dlc = False
                for file_path in game_dir.glob("*.nsp"):
                    if file_path.is_file():
                        if "[DLC]" in file_path.name:
                            has_dlc = True
                        else:
                            has_non_dlc = True
                            break

                # If the folder has only DLC files, treat it as a DLC-only folder
                if has_dlc and not has_non_dlc:
                    # Check each DLC file for its parent folder
                    for file_path in game_dir.glob("*[DLC]*.nsp"):
                        if file_path.is_file():
                            # Find best match parent folder
                            parent_folder = find_dlc_parent_folder(
                                file_path.name, game_folders
                            )

                            if parent_folder and parent_folder != game_dir:
                                # Create DLC folder in the parent game folder if it doesn't exist
                                dlc_folder = parent_folder / "DLC"
                                dlc_folder.mkdir(exist_ok=True)

                                # Move DLC file to the parent folder's DLC folder
                                target_path = dlc_folder / file_path.name

                                # Handle name conflicts
                                counter = 1
                                while target_path.exists():
                                    name, ext = os.path.splitext(file_path.name)
                                    target_path = dlc_folder / f"{name}_{counter}{ext}"
                                    counter += 1

                                try:
                                    shutil.move(str(file_path), str(target_path))
                                    logging.info(
                                        f"Moved DLC file: {file_path.name} to {parent_folder.name}/DLC/"
                                    )
                                except Exception as e:
                                    logging.error(
                                        f"Error moving DLC file {file_path.name}: {e}"
                                    )

                    # Remove DLC-only folder if it's now empty
                    if not any(p.is_file() for p in game_dir.rglob("*")):
                        try:
                            shutil.rmtree(str(game_dir))
                            logging.info(f"Removed empty folder: {game_dir.name}")
                        except Exception as e:
                            logging.error(f"Error removing folder {game_dir.name}: {e}")

                # For regular game folders, ensure all their DLC is in a DLC subfolder
                else:
                    # Check for nested DLC folder
                    dlc_dir = game_dir / "DLC"
                    if dlc_dir.exists() and dlc_dir.is_dir():
                        # Make sure there's no nested DLC/DLC folder
                        nested_dlc = dlc_dir / "DLC"
                        if nested_dlc.exists() and nested_dlc.is_dir():
                            # Move all files from nested DLC up one level
                            for file_path in nested_dlc.glob("*"):
                                if file_path.is_file():
                                    # Move to parent DLC folder
                                    target_path = dlc_dir / file_path.name

                                    # Handle name conflicts
                                    counter = 1
                                    while target_path.exists():
                                        base, ext = os.path.splitext(file_path.name)
                                        target_path = dlc_dir / f"{base}_{counter}{ext}"
                                        counter += 1

                                    try:
                                        shutil.move(str(file_path), str(target_path))
                                        logging.info(
                                            f"Fixed nested DLC: {file_path.name}"
                                        )
                                    except Exception as e:
                                        logging.error(
                                            f"Error fixing nested DLC {file_path.name}: {e}"
                                        )

                            # Remove nested DLC folder if empty
                            if not any(nested_dlc.iterdir()):
                                try:
                                    shutil.rmtree(str(nested_dlc))
                                    logging.info("Removed empty nested DLC folder")
                                except Exception as e:
                                    logging.error(
                                        f"Error removing nested DLC folder: {e}"
                                    )

                    # Find any DLC files not in the DLC folder and move them
                    dlc_files = []
                    for file_path in game_dir.glob("*[DLC]*.nsp"):
                        if file_path.is_file() and file_path.parent != dlc_dir:
                            dlc_files.append(file_path)

                    if dlc_files:
                        # Create DLC folder if it doesn't exist
                        dlc_dir.mkdir(exist_ok=True)

                        # Move DLC files
                        for file_path in dlc_files:
                            target_path = dlc_dir / file_path.name

                            # Handle name conflicts
                            counter = 1
                            orig_name = target_path.name
                            while target_path.exists():
                                name, ext = os.path.splitext(orig_name)
                                target_path = dlc_dir / f"{name}_{counter}{ext}"
                                counter += 1

                            try:
                                shutil.move(str(file_path), str(target_path))
                                logging.info(
                                    f"Moved DLC file to subfolder: {file_path.name}"
                                )
                            except Exception as e:
                                logging.error(
                                    f"Error moving DLC file {file_path.name}: {e}"
                                )

        except Exception as e:
            logging.error(f"Error organizing DLC folders: {e}")
            raise

    def remove_unwanted_files(self, directory: str) -> None:
        """Remove unwanted content files."""
        try:
            logging.info(f"Removing unwanted files in {directory}...")

            # Expanded list of unwanted extensions and specific files
            unwanted_extensions = (".url",)
            unwanted_filenames = (
                "desktop.ini",
                "thumbs.db",
                ".ds_store",
                "icon\r",
                "icon\015",
            )

            allowed_roots = [Path(directory)]
            removed_count = 0

            for file_path in Path(directory).rglob("*"):
                try:
                    if file_path.is_file():
                        # Check for unwanted extensions
                        if file_path.suffix.lower() in unwanted_extensions:
                            safe_unlink(file_path, allowed_roots, Path(directory))
                            logging.info(f"Deleted URL shortcut file: {file_path}")
                            removed_count += 1
                        # Check for specific unwanted files
                        elif file_path.name.lower() in unwanted_filenames:
                            safe_unlink(file_path, allowed_roots, Path(directory))
                            logging.info(f"Deleted system file: {file_path}")
                            removed_count += 1
                        # Special check for Icon with carriage return or other control chars
                        elif (
                            file_path.name == "Icon\r"
                            or file_path.name == "Icon\015"
                            or file_path.name.startswith("Icon")
                        ):
                            safe_unlink(file_path, allowed_roots, Path(directory))
                            logging.info(f"Deleted Icon file: {file_path}")
                            removed_count += 1
                except Exception as e:
                    logging.error(f"Error removing unwanted file {file_path.name}: {e}")

            if removed_count > 0:
                logging.info(f"Removed {removed_count} unwanted files.")

        except Exception as e:
            logging.error(f"Error removing unwanted files: {e}")
            raise

    def _merge_related_game_folders(self, directory: Path) -> None:
        """Merge related game folders using fuzzy matching with special handling for abbreviations"""
        try:
            logging.info(f"Looking for related game folders to merge in {directory}")
            folders = [f for f in directory.iterdir() if f.is_dir()]
            processed = set()

            # First handle any obvious abbreviation matches directly
            abbreviation_pairs = []
            for folder in folders:
                folder_name = folder.name.lower()

                # Check for special case: TMNT and Teenage Mutant Ninja Turtles
                if "tmnt" in folder_name and folder not in processed:
                    tmnt_match = next(
                        (
                            f
                            for f in folders
                            if f != folder
                            and "teenage mutant ninja turtle" in f.name.lower()
                        ),
                        None,
                    )
                    if tmnt_match:
                        abbreviation_pairs.append((folder, tmnt_match))
                        processed.add(folder)
                        processed.add(tmnt_match)
                        logging.info(
                            f"Found abbreviation match: {folder.name} -> {tmnt_match.name}"
                        )

            # Process any direct abbreviation matches first
            for source, target in abbreviation_pairs:
                # Always prefer the full name with apostrophes over abbreviations
                if "teenage mutant ninja turtle" in source.name.lower():
                    source, target = target, source
                logging.info(
                    f"Merging abbreviated folder: {source.name} into {target.name}"
                )

                # Special DLC handling - merge all DLC folders first
                source_dlc = source / "DLC"
                target_dlc = target / "DLC"

                if source_dlc.exists():
                    # Ensure target DLC folder exists
                    target_dlc.mkdir(exist_ok=True)

                    # Move all DLC files
                    for item in source_dlc.glob("*"):
                        if item.is_file():
                            dest_name = item.name
                            dest_path = target_dlc / dest_name

                            # Handle name conflicts
                            counter = 1
                            while dest_path.exists():
                                base, ext = os.path.splitext(dest_name)
                                dest_path = target_dlc / f"{base}_merged_{counter}{ext}"
                                counter += 1

                            try:
                                shutil.move(str(item), str(dest_path))
                                logging.info(
                                    f"Moved DLC file: {item.name} to {dest_path}"
                                )
                            except Exception as e:
                                logging.error(f"Error moving DLC file {item.name}: {e}")

                    # Remove source DLC folder if empty
                    if not any(source_dlc.iterdir()):
                        try:
                            shutil.rmtree(str(source_dlc))
                            logging.info(f"Removed empty DLC folder: {source_dlc}")
                        except Exception as e:
                            logging.error(
                                f"Error removing empty DLC folder {source_dlc}: {e}"
                            )

                # Move regular files
                for item in source.glob("*"):
                    if item.is_file():
                        dest_path = target / item.name

                        # Handle name conflicts
                        counter = 1
                        while dest_path.exists():
                            base, ext = os.path.splitext(item.name)
                            dest_path = target / f"{base}_merged_{counter}{ext}"
                            counter += 1

                        try:
                            shutil.move(str(item), str(dest_path))
                            logging.info(f"Moved file: {item.name} to {target.name}")
                        except Exception as e:
                            logging.error(f"Error moving file {item.name}: {e}")

                # Check if source is empty and can be removed
                if not any(source.iterdir()):
                    try:
                        shutil.rmtree(str(source))
                        logging.info(f"Removed empty source folder: {source.name}")
                    except Exception as e:
                        logging.error(
                            f"Error removing source folder {source.name}: {e}"
                        )

            # Handle related series folders with shared hex patterns
            hex_pattern_folders = {}

            # Group folders by hex pattern series or by matching IDs
            for folder in folders:
                if folder in processed:
                    continue

                # Skip existing DLC folders to prevent nesting
                if folder.name == "DLC":
                    continue

                # Look for hex patterns in files within the folder
                for hex_pattern in COMMON_DLC_HEX_PATTERNS:
                    found = False
                    for file_path in folder.glob("*.nsp"):
                        if re.search(
                            rf"\[{hex_pattern}[0-9A-Fa-f]{{4}}\]", file_path.name
                        ):
                            if hex_pattern not in hex_pattern_folders:
                                hex_pattern_folders[hex_pattern] = []
                            hex_pattern_folders[hex_pattern].append(folder)
                            found = True
                            break
                    if found:
                        break

            # Process each group of related folders
            for hex_pattern, related_folders in hex_pattern_folders.items():
                if len(related_folders) > 1:
                    # Find main folder (without comma or DLC in name)
                    main_folder = None
                    for folder in related_folders:
                        if "," not in folder.name and "DLC" not in folder.name.upper():
                            main_folder = folder
                            break

                    # If no main folder found, use the first one
                    if not main_folder:
                        main_folder = related_folders[0]

                    logging.info(
                        f"Selected main folder for series {hex_pattern}: {main_folder.name}"
                    )

                    # Create DLC folder in main folder
                    dlc_folder = main_folder / "DLC"
                    dlc_folder.mkdir(exist_ok=True)

                    # Process each other folder
                    for folder in related_folders:
                        if folder == main_folder:
                            continue

                        # Process all files in this folder
                        for file_path in folder.rglob("*"):
                            if not file_path.is_file():
                                continue

                            # Ensure files have proper DLC tag
                            new_name = file_path.name
                            if "[DLC]" not in new_name and new_name.endswith(".nsp"):
                                new_name = new_name[:-4] + "[DLC].nsp"

                            # Determine target path (always DLC folder)
                            target_path = dlc_folder / new_name

                            # Handle name conflicts
                            counter = 1
                            while target_path.exists():
                                name_parts = target_path.stem.split("[")
                                if len(name_parts) > 1:
                                    new_name = f"{name_parts[0]}_m{counter}[{'['.join(name_parts[1:])}"
                                else:
                                    new_name = f"{target_path.stem}_m{counter}{target_path.suffix}"
                                target_path = dlc_folder / new_name
                                counter += 1

                            # Rename/move the file
                            try:
                                shutil.move(str(file_path), str(target_path))
                                logging.info(
                                    f"Moved file: {file_path.name} -> {target_path}"
                                )
                            except Exception as e:
                                logging.error(f"Error moving file {file_path}: {e}")

                        # Remove empty folder
                        try:
                            if all(not p.is_file() for p in folder.rglob("*")):
                                shutil.rmtree(str(folder))
                                logging.info(f"Removed empty folder: {folder.name}")
                        except Exception as e:
                            logging.error(f"Error removing folder {folder}: {e}")

                    # Mark all processed
                    processed.update(related_folders)

            # Continue with ID-based matching first, then try fuzzy matching
            remaining_folders = [f for f in folders if f not in processed]

            # First, find folders with matching game IDs
            id_matched_pairs = []
            for i, folder1 in enumerate(remaining_folders):
                if folder1 in processed:
                    continue

                # Get some sample files to extract IDs from
                folder1_files = list(folder1.glob("*.nsp"))
                if not folder1_files:
                    continue

                for j in range(i + 1, len(remaining_folders)):
                    folder2 = remaining_folders[j]
                    if folder2 in processed:
                        continue

                    folder2_files = list(folder2.glob("*.nsp"))
                    if not folder2_files:
                        continue

                    # Check all file combinations for matching IDs
                    match_found = False
                    for file1 in folder1_files:
                        for file2 in folder2_files:
                            if is_same_game(file1.name, file2.name):
                                id_matched_pairs.append((folder1, folder2))
                                processed.add(folder1)
                                processed.add(folder2)
                                match_found = True
                                logging.info(
                                    f"ID-based match: {folder1.name} and {folder2.name}"
                                )
                                break
                        if match_found:
                            break

            # Process ID-matched pairs
            for folder1, folder2 in id_matched_pairs:
                # Prefer the shorter/cleaner folder name as target
                if len(folder1.name) <= len(folder2.name):
                    source, target = folder2, folder1
                else:
                    source, target = folder1, folder2

                logging.info(
                    f"Merging ID-matched folders: {source.name} into {target.name}"
                )

                # Create DLC folder in target if needed
                target_dlc = target / "DLC"
                target_dlc.mkdir(exist_ok=True)

                # Move all DLC files from source
                source_dlc = source / "DLC"
                if source_dlc.exists() and source_dlc.is_dir():
                    # Ensure target DLC folder exists
                    target_dlc.mkdir(exist_ok=True)

                    # Move all DLC files
                    for file_path in source_dlc.glob("*"):
                        if file_path.is_file():
                            dest_path = target_dlc / file_path.name
                            # Handle name conflicts
                            counter = 1
                            while dest_path.exists():
                                base, ext = os.path.splitext(file_path.name)
                                dest_path = target_dlc / f"{base}_merged_{counter}{ext}"
                                counter += 1

                            try:
                                shutil.move(str(file_path), str(dest_path))
                                logging.info(
                                    f"Moved DLC file: {file_path.name} to {dest_path}"
                                )
                            except Exception as e:
                                logging.error(
                                    f"Error moving DLC file {file_path.name}: {e}"
                                )

                # Move files from source to target
                for item in source.iterdir():
                    # Skip DLC folder as we handled it separately
                    if item.is_dir() and item.name == "DLC":
                        continue

                    source_path = source / item.name
                    target_path = target / item.name

                    # Handle file conflicts
                    if target_path.exists():
                        new_name = f"{target_path.stem}_merged{target_path.suffix}"
                        target_path = target_path.with_name(new_name)

                    shutil.move(str(source_path), str(target_path))
                    logging.info(f"Moved {source_path.name} to {target_path}")

                # Delete empty source folder
                if not any(p for p in source.glob("*") if p.name != "DLC"):
                    # Check if DLC folder is empty too
                    if source_dlc.exists():
                        if not any(source_dlc.iterdir()):
                            shutil.rmtree(str(source_dlc))
                            logging.info(
                                f"Removed empty DLC folder in source: {source_dlc}"
                            )

                    try:
                        source.rmdir()  # Safe removal of directory (only if empty)
                        logging.info(f"Removed empty folder: {source}")
                        processed.add(source)
                    except OSError as e:
                        logging.warning(
                            f"Could not remove source folder, may not be empty: {e}"
                        )
                    else:
                        processed.add(target)

                        # Update file counts after each folder merge
                        self._update_file_counts_after_merge(directory)

            # Now do the legacy fuzzy matching for any remaining folders
            remaining_folders = [f for f in folders if f not in processed]
            while remaining_folders:
                current = remaining_folders.pop(0)
                if current in processed:
                    continue

                # Find candidates for merging
                candidates = [f.name for f in remaining_folders if f not in processed]
                match_name = self.game_organizer.fuzzy_match(current.name, candidates)

                if match_name:
                    # Found a match with fuzzy matching
                    match_path = next(
                        f for f in remaining_folders if f.name == match_name
                    )
                    remaining_folders.remove(match_path)

                    logging.info(
                        f"Fuzzy matched '{current.name}' with '{match_path.name}'"
                    )

                    # Determine which is canonical (prefer apostrophe version)
                    source, target = (
                        (current, match_path)
                        if "'" not in current.name and "'" in match_path.name
                        else (match_path, current)
                    )
                    # Move contents from source to target
                    try:
                        # First handle DLC folders
                        source_dlc = source / "DLC"
                        target_dlc = target / "DLC"

                        if source_dlc.exists() and source_dlc.is_dir():
                            # Ensure target DLC folder exists
                            target_dlc.mkdir(exist_ok=True)

                            # Move all DLC files
                            for file_path in source_dlc.glob("*"):
                                if file_path.is_file():
                                    dest_path = target_dlc / file_path.name
                                    # Handle name conflicts
                                    counter = 1
                                    while dest_path.exists():
                                        base, ext = os.path.splitext(file_path.name)
                                        dest_path = (
                                            target_dlc / f"{base}_merged_{counter}{ext}"
                                        )
                                        counter += 1

                                    try:
                                        shutil.move(str(file_path), str(dest_path))
                                        logging.info(
                                            f"Moved DLC file: {file_path.name} to {dest_path}"
                                        )
                                    except Exception as e:
                                        logging.error(
                                            f"Error moving DLC file {file_path.name}: {e}"
                                        )

                        # Move files from source to target
                        for item in source.iterdir():
                            # Skip DLC folder as we handled it separately
                            if item.is_dir() and item.name == "DLC":
                                continue

                            source_path = source / item.name
                            target_path = target / item.name

                            # Handle file conflicts
                            if target_path.exists():
                                new_name = (
                                    f"{target_path.stem}_merged{target_path.suffix}"
                                )
                                target_path = target_path.with_name(new_name)

                            shutil.move(str(source_path), str(target_path))
                            logging.info(f"Moved {source_path.name} to {target_path}")

                        # Delete empty source folder
                        if not any(p for p in source.glob("*") if p.name != "DLC"):
                            # Check if DLC folder is empty too
                            if source_dlc.exists():
                                if not any(source_dlc.iterdir()):
                                    shutil.rmtree(str(source_dlc))
                                    logging.info(
                                        f"Removed empty DLC folder in source: {source_dlc}"
                                    )

                            try:
                                source.rmdir()  # Safe removal of directory (only if empty)
                                logging.info(f"Removed empty folder: {source}")
                                processed.add(source)
                            except OSError as e:
                                logging.warning(
                                    f"Could not remove source folder, may not be empty: {e}"
                                )
                        else:
                            logging.warning(
                                f"Source folder not empty after merge: {source}"
                            )

                        processed.add(target)

                        # Update file counts after each folder merge
                        self._update_file_counts_after_merge(directory)

                    except Exception as e:
                        logging.error(
                            f"Error merging folders {source} and {target}: {e}"
                        )
                else:
                    processed.add(current)

            # Perform a final update of file counts
            self._update_file_counts_after_merge(directory)

        except Exception as e:
            logging.error(f"Error in fuzzy folder consolidation: {e}")

    def _update_file_counts_after_merge(self, directory: Path | None = None):
        """Update file counts after merge operations to preserve counters"""
        try:
            # If no directory provided, use the last processed directory or try to determine from context
            if directory is None:
                if (
                    hasattr(self, "last_processed_directory")
                    and self.last_processed_directory
                ):
                    directory = self.last_processed_directory
                else:
                    # If we can't determine it, use the first directory we can find
                    for path_dict in [
                        self.file_counts["nsp_games"],
                        self.file_counts["nsp_updates"],
                        self.file_counts["nsp_dlcs"],
                    ]:
                        if path_dict and len(path_dict) > 0:
                            sample_file = Path(path_dict[0])
                            if sample_file.parent and sample_file.parent.parent:
                                directory = sample_file.parent.parent
                                break

            # If we still don't have a directory, fall back to current working directory
            if not directory:
                directory = Path(os.getcwd())

            logging.debug(f"Updating file counts for directory: {directory}")

            # Update NSP game files
            self.file_counts["nsp_games"] = [
                f.name
                for f in directory.rglob("*/*.nsp")
                if "[GME]" in f.name.upper() and f.parent.name != "DLC"
            ]

            # Update NSP update files
            self.file_counts["nsp_updates"] = [
                f.name
                for f in directory.rglob("*/*.nsp")
                if "[UPD]" in f.name.upper() and f.parent.name != "DLC"
            ]

            # Update NSP DLC files
            self.file_counts["nsp_dlcs"] = [
                f.name
                for f in directory.rglob("*/*.nsp")
                if (f.parent.name == "DLC" or "[DLC]" in f.name.upper())
            ]

            # Update XCI game files
            self.file_counts["xci_games"] = [
                f.name
                for f in directory.rglob("*/*.xci")
                if "[GME]" in f.name.upper() and f.parent.name != "DLC"
            ]

            # Update XCI update files
            self.file_counts["xci_updates"] = [
                f.name
                for f in directory.rglob("*/*.xci")
                if "[UPD]" in f.name.upper() and f.parent.name != "DLC"
            ]

            # Update XCI DLC files
            self.file_counts["xci_dlcs"] = [
                f.name
                for f in directory.rglob("*/*.xci")
                if (f.parent.name == "DLC" or "[DLC]" in f.name.upper())
            ]

            logging.debug(
                f"Updated file counts after merge: "
                f"{len(self.file_counts['nsp_games'])} NSP games, "
                f"{len(self.file_counts['nsp_updates'])} NSP updates, "
                f"{len(self.file_counts['nsp_dlcs'])} NSP DLCs"
            )
        except Exception as e:
            logging.error(f"Error updating file counts after merge: {e}")

    def safe_consolidate_dlc(self, game_folder: Path):
        """Non-destructive DLC consolidation - move all DLC files to the DLC folder"""
        try:
            # Check if there are any DLC files at the game folder level
            dlc_files_exist = False

            # Check for DLC files in the game folder
            for file in game_folder.glob("*[DLC]*.nsp"):
                if file.is_file() and "[DLC]" in file.name:
                    if file.parent == game_folder:  # Directly in game folder
                        dlc_files_exist = True
                        break

            # Create DLC folder if DLC files exist
            if dlc_files_exist:
                # Create DLC folder if it doesn't exist
                dlc_folder = game_folder / "DLC"
                dlc_folder.mkdir(exist_ok=True)

                moved_count = 0

                # Move files with [DLC] tag from root folder to DLC folder
                for file in game_folder.glob("*[DLC]*.nsp"):
                    if file.is_file() and "[DLC]" in file.name:
                        # Make sure the file is directly in the game folder
                        if file.parent == game_folder:
                            target = dlc_folder / file.name
                            if not target.exists():  # Prevent duplicate moves
                                try:
                                    shutil.move(str(file), str(target))
                                    logging.info(
                                        f"Safely moved DLC: {file.name} to {target}"
                                    )
                                    moved_count += 1
                                except Exception as e:
                                    logging.error(
                                        f"Error moving DLC file {file.name}: {e}"
                                    )

            # Always check for and remove empty DLC folder
            dlc_folder = game_folder / "DLC"
            if (
                dlc_folder.exists()
                and dlc_folder.is_dir()
                and not any(dlc_folder.iterdir())
            ):
                try:
                    shutil.rmtree(str(dlc_folder))
                    logging.info(f"Removed empty DLC folder in {game_folder.name}")
                except Exception as e:
                    logging.error(
                        f"Error removing empty DLC folder in {game_folder.name}: {e}"
                    )

        except Exception as e:
            logging.error(f"Error safely consolidating DLC in {game_folder}: {e}")
            # Log error but don't propagate to avoid breaking the overall process

    def center_on_screen(self):
        """Center the window on the screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            logging.warning(
                "No primary screen detected; leaving window at default position."
            )
            return

        screen_geometry = screen.geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)


class GameOrganizer:
    """Organize game files and folders with improved handling of abbreviations and fuzzy matching."""

    def __init__(self):
        self.resources_dir = str(get_resource_dir(base_file=__file__))
        self.similarity_threshold = 70  # Lowered threshold for better matching
        self.canonical_folder_map = {}

    def sanitize_filename(self, filename):
        """Sanitize filename by removing invalid characters and version tags."""
        invalid_chars = '<>:"/\\|?*'
        clean_name = filename

        for char in invalid_chars:
            clean_name = clean_name.replace(char, "_")

        # Remove all version tags - crucial for folder names
        # This ensures folders are always named after the game itself, not the version
        clean_name = re.sub(
            r"\s*\[v\d+[\w\.\-]*\]|\s*\(v\d+[\w\.\-]*\)",
            "",
            clean_name,
            flags=re.IGNORECASE,
        )  # Bracketed versions
        clean_name = re.sub(
            r"\s+[vV][\d\.]+(?:[a-zA-Z]*\d*)", "", clean_name, flags=re.IGNORECASE
        )  # Space + version like V1.6.3s
        clean_name = re.sub(
            r"\s+[vV]\d+", "", clean_name, flags=re.IGNORECASE
        )  # Simple versions like V1
        clean_name = re.sub(
            r"(?<=\w)[vV][\d\.]+(?:[a-zA-Z]*\d*)", "", clean_name, flags=re.IGNORECASE
        )  # Attached versions

        clean_name = sanitize_possessive(clean_name)
        clean_name = clean_name.title()
        return sanitize_path_component(
            clean_name.rstrip("."),
            default="Unknown Game",
            preserve_extension=False,
        )

    def normalize_name(self, name: str) -> str:
        normalized = sanitize_possessive(name).lower()
        normalized = re.sub(r"[^\w\s']", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        # Treat 'remix' and 'mix' as equivalent
        normalized = re.sub(r"remix", "mix", normalized)
        # Remove common articles and conjunctions
        normalized = re.sub(
            r"^\s*the\s+|\s+the\s*$", " ", normalized, flags=re.IGNORECASE
        ).strip()
        normalized = re.sub(
            r"\b(and|&|or|vs|versus)\b", "", normalized, flags=re.IGNORECASE
        )
        return normalized

    def fuzzy_match(self, target: str, candidates: list[str]) -> str | None:
        """Enhanced fuzzy matching with better abbreviation handling and lower threshold."""
        if not HAS_FUZZY or not candidates:
            return None

        # First try possessive-aware matching with a higher threshold
        possessive_match = get_possessive_aware_match(
            target, candidates, POSSESSIVE_THRESHOLD
        )
        if possessive_match and possessive_match != target:
            score = fuzz.token_sort_ratio(
                self.normalize_name(target), self.normalize_name(possessive_match)
            )
            if score >= self.similarity_threshold:
                return possessive_match

        # Normalize the target and all candidates
        normalized_target = self.normalize_name(target)

        # Create mapping of normalized names to original names
        normalized_candidates_map = {self.normalize_name(c): c for c in candidates}

        # Try multiple matching algorithms for better results
        # 1. Token sort ratio (order insensitive)
        token_sort_result = process.extractOne(
            normalized_target,
            list(normalized_candidates_map.keys()),
            scorer=fuzz.token_sort_ratio,
        )

        # 2. Token set ratio (handles substrings better)
        token_set_result = process.extractOne(
            normalized_target,
            list(normalized_candidates_map.keys()),
            scorer=fuzz.token_set_ratio,
        )

        # Get the best result from the algorithms
        best_result = max(
            [token_sort_result, token_set_result], key=lambda x: x[1] if x else 0
        )

        if best_result and best_result[1] >= self.similarity_threshold:
            matched_key = best_result[0]
            return normalized_candidates_map[matched_key]

        # Special handling for potential abbreviations
        for candidate in candidates:
            # Convert to initials and check
            candidate_norm = self.normalize_name(candidate)

            # Check if one is an acronym of the other
            candidate_words = candidate_norm.split()
            target_words = normalized_target.split()

            # Create acronym from first letters
            candidate_acronym = "".join(word[0] for word in candidate_words if word)
            target_acronym = "".join(word[0] for word in target_words if word)

            if candidate_acronym and target_acronym:
                # Check if one is an acronym of the other
                if (
                    candidate_acronym == normalized_target
                    or target_acronym == candidate_norm
                ):
                    return candidate

        return None

    def normalize_folder_name(self, folder_name):
        """Normalize folder name for consistent matching."""
        return self.normalize_name(folder_name).replace("'", "")

    def get_canonical_folder_name(self, folder_name, base_dir: Path | None = None):
        """Get or create a canonical folder name, handling possessives and variants."""
        map_key = self.normalize_folder_name(folder_name)

        if map_key in self.canonical_folder_map:
            return self.canonical_folder_map[map_key]

        # Check if folder exists as-is
        def check_exists(name):
            return base_dir and (base_dir / name).exists()

        if check_exists(folder_name):
            self.canonical_folder_map[map_key] = folder_name
            return folder_name

        # Check possessive variations
        apostrophe_version = (
            folder_name.replace("s", "'s")
            if not folder_name.endswith("'s")
            else folder_name
        )
        no_apostrophe_version = folder_name.replace("'s", "s")

        exists_apostrophe = check_exists(apostrophe_version)
        exists_no_apostrophe = check_exists(no_apostrophe_version)

        if exists_apostrophe:
            self.canonical_folder_map[map_key] = apostrophe_version
            return apostrophe_version

        if exists_no_apostrophe:
            self.canonical_folder_map[map_key] = no_apostrophe_version
            return no_apostrophe_version

        # Otherwise, sanitize and use the original
        canonical = self.sanitize_filename(folder_name)
        self.canonical_folder_map[map_key] = canonical
        return canonical

    def find_or_create_canonical_folder(
        self, root_directoryectory: Path, base_name: str
    ) -> str:
        """Find matching folder or create canonical name based on existing folders."""
        existing_folders = [
            f.name for f in root_directoryectory.iterdir() if f.is_dir()
        ]
        return self.get_canonical_folder_name_refined(base_name, existing_folders)

    def get_canonical_folder_name_refined(
        self, base_folder_name: str, existing_folders: list[str]
    ) -> str:
        """Get refined canonical folder name with improved matching."""
        normalized_base = self.normalize_name(base_folder_name)
        map_key = normalized_base.replace("'", "")

        if map_key in self.canonical_folder_map:
            return self.canonical_folder_map[map_key]

        if base_folder_name in existing_folders:
            self.canonical_folder_map[map_key] = base_folder_name
            return base_folder_name

        # Try possessive variations
        possessive_variations = {base_folder_name}

        if "'" in base_folder_name:
            possessive_variations.add(base_folder_name.replace("'s", "s"))
        elif base_folder_name.endswith("s") and not base_folder_name.endswith("'s"):
            possessive_variations.add(base_folder_name[:-1] + "'s")

        for variation in possessive_variations:
            if variation in existing_folders:
                canonical = base_folder_name if "'" in base_folder_name else variation
                self.canonical_folder_map[map_key] = canonical
                return canonical

        # Try fuzzy matching with improved algorithm
        match = self.fuzzy_match(base_folder_name, existing_folders)

        if match:
            # Preserve apostrophes when possible
            if "'" in match and "'" not in base_folder_name:
                canonical = match
            elif "'" not in match and "'" in base_folder_name:
                canonical = base_folder_name
            else:
                canonical = match

            self.canonical_folder_map[map_key] = canonical
            return canonical

        # Create new sanitized name if no match
        canonical = self.sanitize_filename(base_folder_name)
        self.canonical_folder_map[map_key] = canonical
        return canonical

    def organize_files(self, files: list[str]) -> None:
        """Organize files into appropriate folders."""
        folder_mapping = {}
        base_dir = Path(files[0]).parent if files else Path.cwd()

        for file_path_str in files:
            file_path = Path(file_path_str)

            if file_path.name.lower().endswith((".nsp", ".xci")):
                base_name = file_path.name
                sanitized_name = self.sanitize_filename(base_name)
                folder_name = os.path.splitext(sanitized_name)[0]

                normalized_folder_name = self.normalize_folder_name(folder_name)
                canonical_folder_name = self.get_canonical_folder_name(
                    folder_name, base_dir
                )

                if normalized_folder_name not in folder_mapping:
                    folder_mapping[normalized_folder_name] = canonical_folder_name

                target_folder_name = folder_mapping[normalized_folder_name]
                target_dir = base_dir / target_folder_name
                target_dir.mkdir(exist_ok=True)

                target_path = target_dir / sanitized_name

                try:
                    if file_path != target_path:
                        shutil.move(str(file_path), str(target_path))
                        logging.info(
                            f"Moved (organize_files): {file_path.name} to {target_path.relative_to(base_dir)}"
                        )
                except Exception as e:
                    logging.error(
                        f"Error moving (organize_files) {file_path.name} to {target_path}: {e!s}"
                    )

        self.consolidate_apostrophe_folders(str(base_dir))

    def consolidate_apostrophe_folders(self, directory: str) -> None:
        """Consolidate folders with apostrophe variants and abbreviation matches."""
        try:
            folders = [f for f in Path(directory).iterdir() if f.is_dir()]
            processed = set()
            folders_dict = {f.name: f for f in folders}

            while True:
                merged_in_pass = False
                current_folder_names = list(folders_dict.keys())

                for name in current_folder_names:
                    if name in processed or name not in folders_dict:
                        continue

                    current_path = folders_dict[name]
                    candidates = [
                        c_name
                        for c_name in folders_dict
                        if c_name != name and c_name not in processed
                    ]

                    # Use enhanced fuzzy matching
                    match_name = self.fuzzy_match(name, candidates)

                    if match_name:
                        match_path = folders_dict[match_name]
                        logging.info(
                            f"Consolidating folders: '{name}' with '{match_name}'"
                        )

                        # Choose which name to keep - prefer apostrophe version
                        source, target = (
                            (current_path, match_path)
                            if "'" not in name and "'" in match_name
                            else (match_path, current_path)
                        )
                        logging.info(f"Merging '{source.name}' into '{target.name}'")

                        try:
                            # Verify paths exist
                            if not source.exists():
                                logging.error(
                                    f"Source folder no longer exists: {source}"
                                )
                                processed.add(name)
                                processed.add(match_name)
                                continue

                            if not target.exists():
                                logging.error(
                                    f"Target folder no longer exists: {target}"
                                )
                                processed.add(name)
                                processed.add(match_name)
                                continue

                            # Get list of items to move before modifying
                            source_items = list(source.iterdir())

                            for item in source_items:
                                source_item = source / item.name
                                target_item = target / item.name

                                if not source_item.exists():
                                    logging.warning(
                                        f"Source item no longer exists: {source_item}"
                                    )
                                    continue

                                if target_item.exists():
                                    # Handle name conflict
                                    if source_item.is_file() and target_item.is_file():
                                        base, suffix = os.path.splitext(
                                            target_item.name
                                        )
                                        new_name = f"{base}_merged{suffix}"
                                        target_item = target / new_name
                                    elif source_item.is_dir() and target_item.is_dir():
                                        # Recursively merge subdirectories
                                        sub_items = list(source_item.iterdir())

                                        for sub_item in sub_items:
                                            sub_source = source_item / sub_item.name
                                            sub_target = target_item / sub_item.name

                                            # Handle name conflicts
                                            counter = 1
                                            while sub_target.exists():
                                                name_parts = os.path.splitext(
                                                    sub_item.name
                                                )
                                                new_name = f"{name_parts[0]}_merged_{counter}{name_parts[1]}"
                                                sub_target = target_item / new_name
                                                counter += 1

                                            try:
                                                if sub_source.exists():
                                                    shutil.move(
                                                        str(sub_source), str(sub_target)
                                                    )
                                                    logging.debug(
                                                        f"Moved nested item: {sub_source.name} to {sub_target}"
                                                    )
                                            except Exception as e:
                                                logging.error(
                                                    f"Error moving nested item: {sub_source} to {sub_target}: {e}"
                                                )
                                        continue
                                    else:
                                        # Skip if types mismatch
                                        logging.warning(
                                            f"Cannot merge items of different types: {source_item} vs {target_item}"
                                        )
                                        continue

                                try:
                                    shutil.move(str(source_item), str(target_item))
                                    logging.debug(
                                        f"Moved item: {source_item.name} to {target.name}"
                                    )
                                except Exception as e:
                                    logging.error(
                                        f"Error moving item {source_item} to {target_item}: {e}"
                                    )

                            # Check if source is now empty before removing
                            remaining = list(source.iterdir())
                            if not remaining:
                                try:
                                    source.rmdir()
                                    merged_in_pass = True
                                    logging.info(
                                        f"Successfully removed empty folder: {source.name}"
                                    )
                                    if source.name in folders_dict:
                                        del folders_dict[source.name]
                                except Exception as e:
                                    logging.error(
                                        f"Error removing source folder {source.name}: {e}"
                                    )
                            else:
                                logging.warning(
                                    f"Source folder not empty after merge: {source.name}, {len(remaining)} items remain"
                                )

                            processed.add(source.name)
                            processed.add(target.name)

                        except Exception as e:
                            logging.error(
                                f"Error consolidating {source.name} into {target.name}: {e}"
                            )
                            processed.add(source.name)
                            processed.add(target.name)
                    else:
                        processed.add(name)

                if not merged_in_pass:
                    break

        except Exception as e:
            logging.error(f"Error in consolidate_apostrophe_folders: {e}")


def count_dlc_files(directory: str) -> int:
    """Count total DLC files by scanning for [DLC] tag"""
    if not directory or not os.path.isdir(directory):
        logging.error(f"Invalid directory: {directory}")
        return 0

    try:
        total_dlc = 0
        for _root, _, files in os.walk(directory):
            for file in files:
                if "[DLC]" in file:
                    total_dlc += 1
        logging.info(f"Found {total_dlc} DLC files in {directory}")
        return total_dlc

    except Exception as e:
        logging.error(f"Error counting DLC files in {directory}: {e}")
        return 0


def generate_file_summary(directory: str) -> str:
    """Generate a detailed summary of all tracked files"""
    if not directory or not os.path.isdir(directory):
        logging.error(f"Invalid directory: {directory}")
        return "Error: Invalid directory"

    try:
        # Get accurate DLC count
        total_dlc_count = count_dlc_files(directory)

        summary = "----- Processed Switch Games -----\n\n"

        # NSP Section
        summary += f"NSP Games:  {len(nsp_games)}\n"
        summary += f"NSP UPDs:   {len(nsp_upds)}\n"
        summary += f"NSP DLCs:   {total_dlc_count}\n\n"

        # XCI Section
        summary += f"XCI Games:  {len(xci_games)}\n"
        summary += f"XCI UPDs:   {len(xci_upds)}\n"
        summary += f"XCI DLCs:   {len(xci_dlcs)}\n\n"

        # Totals Section
        summary += "----- Totals -----\n\n"
        summary += f"Total NSP:   {len(nsp_games) + len(nsp_upds) + total_dlc_count}\n"
        summary += f"Total XCI:   {len(xci_games) + len(xci_upds) + len(xci_dlcs)}\n"
        summary += f"Total Games: {len(nsp_games) + len(xci_games)}\n"
        summary += f"Total UPDs:  {len(nsp_upds) + len(xci_upds)}\n"
        summary += f"Total DLCs:  {total_dlc_count}\n"
        summary += "-------------------"

        logging.info("Successfully generated file summary")
        return summary

    except Exception as e:
        logging.error(f"Error generating summary: {e}")
        return "Error: Failed to generate summary"


def process_folder(directory: Path) -> None:
    """
    Process folder names to handle possessive forms and merge related folders.
    """
    logging.info(f"Processing folders in {directory} for possessive forms...")

    # Get all folders in the directory
    folders = [f for f in directory.iterdir() if f.is_dir()]
    processed_folders = set()
    organizer = GameOrganizer()

    for folder in folders:
        if not folder.exists() or folder.name in processed_folders:
            continue

        # Clean the current folder name
        current_name = folder.name

        # Find potential matches
        existing = [
            f.name
            for f in directory.iterdir()
            if f.is_dir() and f.name != current_name and f.name not in processed_folders
        ]

        match_name = organizer.fuzzy_match(current_name, existing)

        if match_name and match_name != current_name:
            target_path = directory / match_name
            source_path = folder

            # Log the match
            logging.info(
                f"Possessive/Fuzzy match found: '{source_path.name}' -> '{target_path.name}'"
            )

            # Prefer the name with apostrophe
            if "'" in source_path.name and "'" not in target_path.name:
                # Swap source and target to keep the apostrophe version
                source_path, target_path = target_path, source_path
                logging.info(f"Keeping name with apostrophe: '{target_path.name}'")

            try:
                # First, verify that both paths exist
                if not source_path.exists():
                    logging.error(f"Source path does not exist: {source_path}")
                    processed_folders.add(current_name)
                    continue

                if not target_path.exists():
                    logging.error(f"Target path does not exist: {target_path}")
                    processed_folders.add(current_name)
                    continue

                # Get list of items before attempting to move
                source_items = list(source_path.iterdir())

                # Move all items from source to target folder
                for item in source_items:
                    source_item = source_path / item.name
                    target_item = target_path / item.name

                    if not source_item.exists():
                        logging.warning(f"Source item does not exist: {source_item}")
                        continue

                    # Handle potential file conflicts
                    if target_item.exists():
                        if source_item.is_file() and target_item.is_file():
                            # If target already exists, add a suffix
                            base, suffix = os.path.splitext(target_item.name)
                            new_name = f"{base}_merged{suffix}"
                            target_item = target_path / new_name
                        elif source_item.is_dir() and target_item.is_dir():
                            # If merging directories, handle recursively
                            logging.info(
                                f"Merging directory contents: {source_item.name} into {target_item.name}"
                            )

                            # Get items in source directory before moving
                            sub_items = list(source_item.iterdir())

                            for sub_item in sub_items:
                                sub_source = source_item / sub_item.name
                                sub_target = target_item / sub_item.name

                                # Handle name conflicts
                                counter = 1
                                while sub_target.exists():
                                    base, suffix = os.path.splitext(sub_item.name)
                                    new_name = f"{base}_merged_{counter}{suffix}"
                                    sub_target = target_item / new_name
                                    counter += 1

                                try:
                                    shutil.move(str(sub_source), str(sub_target))
                                    logging.debug(
                                        f"Moved: {sub_source.name} to {sub_target}"
                                    )
                                except Exception as e:
                                    logging.error(
                                        f"Error moving nested item: {sub_source} -> {sub_target}: {e}"
                                    )

                            # Skip this directory in the outer loop since we processed it
                            continue
                        else:
                            # Cannot merge a file with a directory
                            logging.error(
                                f"Cannot merge: {source_item} and {target_item} (type mismatch)"
                            )
                            continue

                    # Perform the move operation
                    try:
                        shutil.move(str(source_item), str(target_item))
                        logging.debug(f"Moved: {source_item.name} to {target_path}")
                    except Exception as e:
                        logging.error(
                            f"Error moving item {source_item.name} to {target_path}: {e}"
                        )

                # Check if source folder is now empty
                remaining_items = list(source_path.iterdir())
                if not remaining_items:
                    try:
                        source_path.rmdir()
                        logging.info(
                            f"Successfully merged and removed '{source_path.name}'"
                        )
                    except Exception as e:
                        logging.error(
                            f"Error removing source folder {source_path.name}: {e}"
                        )
                else:
                    logging.warning(
                        f"Source folder {source_path.name} not empty after merge, {len(remaining_items)} items remain"
                    )

                processed_folders.add(source_path.name)
                processed_folders.add(target_path.name)

            except Exception as e:
                logging.error(
                    f"Error merging folders {source_path.name} -> {target_path.name}: {e}"
                )
                processed_folders.add(source_path.name)
                processed_folders.add(target_path.name)

    logging.info(f"Completed possessive form processing in {directory}")

    # After merging, standardize filenames in all subfolders
    for folder in directory.iterdir():
        if folder.is_dir():
            try:
                standardize_filenames_to_folder(folder)
            except Exception as e:
                logging.error(f"Error standardizing filenames in folder {folder}: {e}")


def fix_folder_structure(directory: Path) -> None:
    """Fix folder structure for a clean Switch game library organization."""
    logging.info(f"Fixing folder structure in: {directory}")

    # Get all subdirectories in the root
    game_folders = [f for f in directory.iterdir() if f.is_dir()]

    # First pass: collect all game folders and their IDs
    game_folder_mapping = {}
    for game_folder in game_folders:
        if game_folder.name == "DLC":  # Skip standalone DLC folders
            continue

        # Look for game files with IDs
        for file_path in game_folder.glob("*.nsp"):
            if "[GME]" in file_path.name or "[GAME]" in file_path.name:
                game_id = extract_game_id(file_path.name)
                if game_id:
                    game_folder_mapping[game_id] = game_folder
                    break

    # Process each game folder
    for game_folder in game_folders:
        # Clean up folder name (if needed)
        folder_name = game_folder.name

        # 1. Clean up folder name
        clean_name = folder_name
        # Remove typical brackets from folder name
        clean_name = re.sub(r"\[.*?\]|\(.*?\)", "", clean_name)
        clean_name = re.sub(r"\s+", " ", clean_name).strip()

        # If folder name changed, rename it (ensure new name is Title Case)
        if clean_name != folder_name and clean_name:
            new_folder_name = clean_name.title()  # Ensure Title Case
            new_folder = game_folder.parent / new_folder_name

            # Check if destination exists (case-insensitive check might be needed depending on OS)
            # For simplicity, we'll rely on the earlier uppercase creation and consolidation logic
            # If a folder with the same name but different case exists, merging might be complex.
            # Let's assume the consolidation handles most cases.
            if new_folder.exists() and new_folder.resolve() != game_folder.resolve():
                # Merge folders instead of renaming
                try:
                    # Move contents to existing folder
                    for item in game_folder.iterdir():
                        target_path = new_folder / item.name
                        if not target_path.exists():
                            shutil.move(str(item), str(target_path))
                            logging.info(
                                f"Moved {item.name} to existing folder {new_folder.name}"
                            )
                        else:
                            logging.info(
                                f"Skipped {item.name} (already exists in {new_folder.name})"
                            )

                    # Remove the source folder if now empty
                    if not any(game_folder.iterdir()):
                        shutil.rmtree(str(game_folder))
                        logging.info(f"Removed empty source folder: {game_folder.name}")

                    # Use the new folder for further processing
                    game_folder = new_folder
                except Exception as e:
                    logging.error(f"Error merging folders: {e}")
                    game_folder = new_folder
            else:
                # Rename the folder
                try:
                    game_folder.rename(new_folder)
                    game_folder = new_folder
                    logging.info(f"Renamed folder to: {clean_name}")
                except Exception as e:
                    logging.error(f"Error renaming folder: {e}")

        # Fix nested DLC folders
        dlc_folder = game_folder / "DLC"
        if dlc_folder.exists() and dlc_folder.is_dir():
            nested_dlc = dlc_folder / "DLC"
            if nested_dlc.exists() and nested_dlc.is_dir():
                logging.info(
                    f"Found nested DLC folder in {game_folder.name}, fixing..."
                )

                # Move all files from nested DLC folder to parent DLC folder
                for nested_file in nested_dlc.rglob("*"):
                    if not nested_file.is_file():
                        continue

                    # Determine relative path to nested_dlc
                    rel_path = nested_file.relative_to(nested_dlc)

                    # If it's in a subfolder, we just want the filename
                    if len(rel_path.parts) > 1:
                        target_path = dlc_folder / rel_path.name
                    else:
                        target_path = dlc_folder / rel_path

                    # Handle name conflicts
                    counter = 1
                    while target_path.exists():
                        name, ext = os.path.splitext(nested_file.name)
                        target_path = dlc_folder / f"{name}_{counter}{ext}"
                        counter += 1

                    try:
                        shutil.move(str(nested_file), str(target_path))
                        logging.info(
                            f"Moved file from nested DLC: {nested_file.name} → {target_path}"
                        )
                    except Exception as e:
                        logging.error(f"Error moving file from nested DLC: {e}")

                # Remove all empty directories under nested_dlc
                for subdir in list(nested_dlc.rglob("*"))[
                    ::-1
                ]:  # Process deepest dirs first
                    if subdir.is_dir():
                        try:
                            subdir.rmdir()  # Will only remove if empty
                            logging.info(f"Removed empty directory: {subdir}")
                        except Exception:
                            pass  # Not empty or other error

                # Finally remove the nested DLC folder itself
                try:
                    nested_dlc.rmdir()
                    logging.info(f"Removed empty nested DLC folder: {nested_dlc}")
                except Exception as e:
                    logging.error(f"Could not remove nested DLC folder: {e}")

            # Fix any DLC subfolders in the DLC folder (shouldn't be any)
            for subfolder in dlc_folder.iterdir():
                if subfolder.is_dir() and subfolder.name != "DLC":
                    # Move all files from subfolders directly to DLC folder
                    for file_path in subfolder.rglob("*"):
                        if not file_path.is_file():
                            continue

                        target_path = dlc_folder / file_path.name
                        counter = 1
                        orig_name = target_path.name
                        while target_path.exists():
                            name, ext = os.path.splitext(orig_name)
                            target_path = dlc_folder / f"{name}_{counter}{ext}"
                            counter += 1

                        try:
                            shutil.move(str(file_path), str(target_path))
                            logging.info(
                                f"Moved file from subfolder: {file_path.name} → {target_path}"
                            )
                        except Exception as e:
                            logging.error(f"Error moving file from subfolder: {e}")

                    # Remove empty subfolder
                    try:
                        if all(not p.is_file() for p in subfolder.rglob("*")):
                            shutil.rmtree(str(subfolder))
                            logging.info(f"Removed empty subfolder: {subfolder.name}")
                    except Exception as e:
                        logging.error(f"Error removing subfolder: {e}")

            # Check for misplaced DLC files (DLC in wrong game folder)
            for file_path in dlc_folder.glob("*.nsp"):
                if "[DLC]" in file_path.name:
                    # Extract DLC game ID
                    dlc_id = extract_game_id(file_path.name)
                    if dlc_id and dlc_id in game_folder_mapping:
                        correct_game_folder = game_folder_mapping[dlc_id]

                        # Skip if already in correct folder
                        if correct_game_folder == game_folder:
                            continue

                        # Move to correct game folder's DLC folder
                        correct_dlc_folder = correct_game_folder / "DLC"
                        correct_dlc_folder.mkdir(exist_ok=True)

                        target_path = correct_dlc_folder / file_path.name

                        # Handle name conflicts
                        counter = 1
                        orig_name = target_path.name
                        while target_path.exists():
                            name, ext = os.path.splitext(orig_name)
                            target_path = correct_dlc_folder / f"{name}_{counter}{ext}"
                            counter += 1

                        try:
                            shutil.move(str(file_path), str(target_path))
                            logging.info(
                                f"Moved DLC file to correct game folder: {file_path.name} → {correct_game_folder.name}/DLC/"
                            )
                        except Exception as e:
                            logging.error(
                                f"Error moving DLC file to correct folder: {e}"
                            )

    logging.info("Folder structure fixing complete")


def rename_single_file(file_path: Path, authoritative_base_name: str):
    """
    Rebuilds and renames a single file to match the standard format,
    using the folder's name as the primary base, and preserving all tags.
    """
    original_filename = file_path.name
    name_part, file_ext = os.path.splitext(original_filename)

    # 1. Extract the essential, non-negotiable tags (Hex ID and Type Tag)
    # Use the 16-character regex for hex ID
    id_match = re.search(r"(\[01[0-9A-Fa-f]{14,16}\])", name_part, re.IGNORECASE)
    type_match = re.search(r"(\[(?:GME|UPD|DLC|Base\+DLC)\])", name_part, re.IGNORECASE)

    # If essential tags are missing, we cannot standardize reliably.
    # Log a warning and return without renaming.
    if not id_match or not type_match:
        logging.warning(
            f"Skipping standardization for '{original_filename}': Missing essential ID or Type tag."
        )
        return

    file_id = id_match.group(1)
    file_tag = type_match.group(1)

    # 2. Extract any *other* bracketed content (likely DLC description)
    # Temporarily remove the known ID and Type tags to isolate other content.
    temp_name_for_dlc_desc = name_part.replace(file_id, "").replace(file_tag, "")

    dlc_desc_part = ""
    # Look for specific DLC content patterns within brackets
    dlc_content_regex = "|".join([re.escape(p) for p in DLC_CONTENT_PATTERNS])
    desc_match = re.search(
        rf"\[\s*(.*?({dlc_content_regex}).*?)\s*\]",
        temp_name_for_dlc_desc,
        re.IGNORECASE,
    )
    if desc_match:
        dlc_desc_part = desc_match.group(1).strip()
    else:
        # Fallback: find any remaining bracketed content that contains a DLC indicator
        bracket_matches = re.findall(r"(\[[^\]]+?\])", temp_name_for_dlc_desc)
        for bracket_content in bracket_matches:
            if any(
                re.search(
                    rf"\b{ind.replace(r'(?i)', '')}\b", bracket_content, re.IGNORECASE
                )
                for ind in DLC_INDICATORS
            ):
                dlc_desc_part = bracket_content.strip(
                    "[]"
                ).strip()  # Remove outer brackets
                break

    if dlc_desc_part and not dlc_desc_part.startswith("-"):
        dlc_desc_part = f"- {dlc_desc_part}"

    # 3. Construct the new filename using the authoritative folder name
    new_name_parts = [authoritative_base_name]
    if dlc_desc_part:
        new_name_parts.append(dlc_desc_part)

    new_name_parts.append(file_id)  # Re-insert the extracted hex ID
    new_name_parts.append(file_tag)  # Re-insert the extracted type tag

    new_filename_base = " ".join(p for p in new_name_parts if p)
    new_filename = sanitize_path_component(
        re.sub(r"\s{2,}", " ", new_filename_base).strip() + file_ext,
        default="Unknown Game",
        preserve_extension=True,
    )

    # 4. Rename the file if it has changed
    if new_filename != original_filename:
        new_path = file_path.parent / new_filename
        logging.info(f"Standardizing: '{original_filename}' -> '{new_filename}'")
        allowed_roots = [file_path.parent]
        try:
            # Check if target exists and is the same file (hard link or already moved)
            if new_path.exists() and file_path.samefile(new_path):
                logging.debug(
                    f"Source and target are the same file, skipping rename: {original_filename}"
                )
                return  # No actual rename needed

            # If target exists and is a different file, log and skip or handle conflict
            if new_path.exists() and not file_path.samefile(new_path):
                logging.warning(
                    f"Target '{new_path.name}' already exists and is a different file. Skipping rename for '{original_filename}'."
                )
                return  # Or implement a conflict resolution (e.g., append _1)

            safe_rename(file_path, new_path, allowed_roots)
        except Exception as e:
            logging.error(
                f"Error during standardization rename for '{original_filename}': {e}"
            )


def process_all_files(root_directory: Path):
    """Processes ALL files in a folder, preserving hex IDs"""
    for folder in root_directory.iterdir():
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            print(f"\n📁 Processing: {folder.name}")
            for ext in ("*.nsp", "*.xci"):
                for file in folder.rglob(ext):
                    rename_single_file(file, folder.name)


def process_files(root_directory: Path):
    """Simple, reliable processor"""
    for item in root_directory.glob("*"):
        if item.is_dir() and not item.name.startswith("_"):
            for ext in ("*.nsp", "*.xci"):
                for file in item.rglob(ext):
                    rename_single_file(file, item.name)


def standardize_filenames_to_folder(root_directoryectory: Path) -> None:
    """
    Iterates through game folders and standardizes the base name and casing
    of internal files (.nsp/.xci) to match the parent game folder's name.
    Also merges folders like 'Mix' and 'Remix' if needed.
    """
    logging.info(
        f"Standardizing filenames to match folder names in {root_directoryectory}..."
    )
    if not root_directoryectory.is_dir():
        return

    # Merge 'Mix' and 'Remix' folders if both exist (this logic is fine)
    folder_names = [f.name for f in root_directoryectory.iterdir() if f.is_dir()]
    mix_remix_map = {}
    for name in folder_names:
        norm = re.sub(r"remix", "mix", name, flags=re.IGNORECASE)
        mix_remix_map.setdefault(norm.lower(), []).append(name)

    allowed_roots = [root_directoryectory]

    for _norm, variants in mix_remix_map.items():
        if len(variants) > 1:
            # Merge all into the first variant (assuming it's the canonical one)
            target_folder_name = variants[0]
            target_path = root_directoryectory / target_folder_name

            for v in variants[1:]:
                src_path = root_directoryectory / v
                if src_path.exists() and src_path.is_dir():
                    logging.info(
                        f"Merging '{src_path.name}' into '{target_path.name}' (Mix/Remix merge)"
                    )
                    try:
                        for item in src_path.iterdir():
                            # Handle conflicts during merge
                            dest_item_path = target_path / item.name
                            if dest_item_path.exists():
                                if (
                                    item.is_file()
                                    and dest_item_path.is_file()
                                    and filecmp.cmp(
                                        str(item), str(dest_item_path), shallow=False
                                    )
                                ):
                                    logging.info(
                                        f"Skipping identical file during merge: {item.name}"
                                    )
                                    safe_unlink(
                                        item, allowed_roots, root_directoryectory
                                    )  # Delete the duplicate source
                                    continue
                                else:
                                    # Append a suffix if different file with same name
                                    base, ext = os.path.splitext(item.name)
                                    counter = 1
                                    while (
                                        target_path / f"{base}_{counter}{ext}"
                                    ).exists():
                                        counter += 1
                                    safe_move(
                                        item,
                                        target_path / f"{base}_{counter}{ext}",
                                        allowed_roots,
                                    )
                                    logging.info(
                                        f"Moved '{item.name}' to '{target_path.name}/{base}_{counter}{ext}' (conflict resolved)"
                                    )
                            else:
                                safe_move(item, target_path / item.name, allowed_roots)
                                logging.info(
                                    f"Moved '{item.name}' to existing folder '{target_path.name}'"
                                )

                        # Remove the source folder if now empty
                        if not any(src_path.iterdir()):
                            remove_empty_directories(
                                src_path, allowed_roots, root_directoryectory
                            )
                            logging.info(
                                f"Removed empty merged folder: {src_path.name}"
                            )
                    except Exception as e:
                        logging.error(
                            f"Error merging Mix/Remix folders {src_path.name} into {target_path.name}: {e}"
                        )

    # Now iterate through the (potentially merged and renamed) folders
    for item in list(
        root_directoryectory.iterdir()
    ):  # Use list() to iterate over a copy
        if item.is_dir() and not item.name.startswith(("_", ".")):
            game_folder_path = item

            # --- Step 1: Standardize the folder name itself ---
            # Use smart_title_case to handle acronyms like HD and small words correctly.
            canonical_name = smart_title_case(
                restore_roman_numerals(game_folder_path.name)
            )

            # If the folder name needs to be changed, rename it.
            if game_folder_path.name != canonical_name:
                try:
                    new_folder_path = game_folder_path.parent / canonical_name
                    # If a folder with the correct name already exists (e.g., from a previous merge),
                    # move the contents into it and delete the old folder.
                    if new_folder_path.exists() and not new_folder_path.samefile(
                        game_folder_path
                    ):
                        logging.warning(
                            f"Target folder '{canonical_name}' exists. Merging '{game_folder_path.name}' into it."
                        )
                        for src_item in game_folder_path.iterdir():
                            # Handle conflicts during merge
                            dest_item_path = new_folder_path / src_item.name
                            if dest_item_path.exists():
                                if (
                                    src_item.is_file()
                                    and dest_item_path.is_file()
                                    and filecmp.cmp(
                                        str(src_item),
                                        str(dest_item_path),
                                        shallow=False,
                                    )
                                ):
                                    logging.info(
                                        f"Skipping identical file during merge: {src_item.name}"
                                    )
                                    safe_unlink(
                                        src_item, allowed_roots, root_directoryectory
                                    )  # Delete the duplicate source
                                    continue
                                else:
                                    # Append a suffix if different file with same name
                                    base, ext = os.path.splitext(src_item.name)
                                    counter = 1
                                    while (
                                        new_folder_path / f"{base}_{counter}{ext}"
                                    ).exists():
                                        counter += 1
                                    safe_move(
                                        src_item,
                                        new_folder_path / f"{base}_{counter}{ext}",
                                        allowed_roots,
                                    )
                                    logging.info(
                                        f"Moved '{src_item.name}' to '{new_folder_path.name}/{base}_{counter}{ext}' (conflict resolved)"
                                    )
                            else:
                                safe_move(
                                    src_item,
                                    new_folder_path / src_item.name,
                                    allowed_roots,
                                )
                                logging.info(
                                    f"Moved '{src_item.name}' to existing folder '{new_folder_path.name}'"
                                )

                        # Remove the source folder if now empty
                        if not any(game_folder_path.iterdir()):
                            remove_empty_directories(
                                game_folder_path, allowed_roots, root_directoryectory
                            )
                            logging.info(
                                f"Removed empty source folder: {game_folder_path.name}"
                            )
                        game_folder_path = new_folder_path  # Update reference to the new canonical folder
                    else:
                        # Rename the folder directly if no conflict
                        game_folder_path = safe_rename(
                            game_folder_path, new_folder_path, allowed_roots
                        )
                        logging.info(f"Renamed folder to: {canonical_name}")

                except Exception as e:
                    logging.error(
                        f"Could not rename/merge folder {game_folder_path.name}: {e}"
                    )
                    continue  # Skip this folder if rename/merge fails

            # This is now the authoritative name for all files inside.
            authoritative_base_name = game_folder_path.name

            # --- Step 2: Standardize all files within the corrected folder ---
            files_to_process = list(game_folder_path.rglob("*.nsp")) + list(
                game_folder_path.rglob("*.xci")
            )

            for file_path in files_to_process:
                if not file_path.is_file():
                    continue

                # Call the dedicated rename_single_file function
                rename_single_file(file_path, authoritative_base_name)

    logging.info("Standardization complete.")


def main() -> None:
    """Main entry point for the application."""
    print_startup_info(APP_NAME, package_name=PACKAGE_NAME)
    app = QApplication(sys.argv)
    # Fusion style ensures dark QSS renders consistently on macOS (native Aqua
    # ignores parts of dark styling like hover states and some borders).
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    icon_path = get_resource_path("RyuSync.icns", base_file=__file__)
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = DragDropWindow()

    # Show the window first
    window.show()

    # Check if a directory path was provided as an argument
    if len(sys.argv) > 1:
        directory_path = sys.argv[1]
        if os.path.isdir(directory_path):
            # Add to processed directories to prevent reprocessing
            window.processed_directories.add(directory_path)

            logging.info(f"Processing directory from command line: {directory_path}")
            window.process_dropped_directory(Path(directory_path))

        else:
            logging.error(f"Invalid directory path: {directory_path}")

    # Keep the application running until the user closes it
    app.exec()


if __name__ == "__main__":
    main()
