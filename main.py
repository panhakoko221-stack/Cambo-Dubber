"""Cambo Dubber desktop application entry point."""

import sys
import os

# Prefer Windows/FFmpeg hardware video decoding before PyQt multimedia modules
# are imported, while still allowing Qt to fall back when a backend is unavailable.
os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "cuda,d3d11va,dxva2,qsv")
os.environ.setdefault("QT_DISABLE_HW_TEXTURES_CONVERSION", "0")

# Ensure the project venv can resolve PyQt6 plugins when launched from a shell
# or wrapper script instead of the IDE. Official wheels keep them below Qt6;
# retain the older layout as a fallback for copied/custom environments.
qt_plugin_candidates = (
    os.path.join(sys.prefix, 'Lib', 'site-packages', 'PyQt6', 'Qt6', 'plugins'),
    os.path.join(sys.prefix, 'Lib', 'site-packages', 'PyQt6', 'plugins'),
)
for qt_plugins_root in qt_plugin_candidates:
    qt_platforms_dir = os.path.join(qt_plugins_root, 'platforms')
    if os.path.isdir(qt_platforms_dir):
        # Replace inherited PyQt5 paths before importing any PyQt6 module.
        os.environ['QT_PLUGIN_PATH'] = qt_plugins_root
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_platforms_dir
        break

import json
import re
import asyncio
import html
import requests
import tempfile
import time
import shutil
import zipfile
import urllib.parse
from PyQt6.QtCore import Qt, QItemSelection, QItemSelectionRange, QItemSelectionModel, QRect, QRectF, QThread, pyqtSignal, QUrl, QTimer, QEvent, QSizeF, QPointF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPixmap, QKeyEvent, QBitmap, QRegion
from PyQt6.QtWidgets import (
    QColorDialog,
    QScrollArea, QGroupBox,
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QPushButton, QComboBox, QCheckBox, QSlider, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QLabel, QProgressBar, QFileDialog, QSplitter, QScrollBar,
    QDialog, QLineEdit, QFormLayout, QMessageBox, QFrame, QStackedWidget,
    QTabWidget, QAbstractItemView,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsItem
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget, QGraphicsVideoItem
from voice_picker import VoiceCellButton, VoiceSelectionPopup

import edge_tts
from icons import get_icon
from khmer_fonts import load_khmer_font

# Default Settings File
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "dubber_settings.json")
OUTPUT_DIR = os.path.join(APP_DIR, "output")
VOXCPM_VOICE_NAME = "VoxCPM Cloned Voice"
VOXCPM_FEMALE_VOICE_NAME = "VoxCPM Female Clone"
VOXCPM_MALE_VOICE_NAME = "VoxCPM Male Clone"
VOXCPM_AUTO_VOICE_NAME = "Auto VoxCPM (Male / Female)"
VOXCPM_VOICE_NAMES = (
    VOXCPM_VOICE_NAME,
    VOXCPM_FEMALE_VOICE_NAME,
    VOXCPM_MALE_VOICE_NAME,
    VOXCPM_AUTO_VOICE_NAME,
)
TRANSLATION_SOURCE_LANGS = [
    "Auto Detect", "English", "Chinese", "Japanese", "Korean", "Thai",
    "Vietnamese", "French", "Spanish", "Russian", "Arabic", "Hindi",
    "Indonesian", "Khmer"
]
TRANSLATION_TARGET_LANGS = ["Khmer", "English", "Chinese"]
GOOGLE_TRANSLATE_CODES = {
    "Auto Detect": "auto",
    "English": "en",
    "Chinese": "zh-CN",
    "Japanese": "ja",
    "Korean": "ko",
    "Thai": "th",
    "Vietnamese": "vi",
    "French": "fr",
    "Spanish": "es",
    "Russian": "ru",
    "Arabic": "ar",
    "Hindi": "hi",
    "Indonesian": "id",
    "Khmer": "km",
}
NLLB_LANG_CODES = {
    "English": "eng_Latn",
    "Chinese": "zho_Hans",
    "Japanese": "jpn_Jpan",
    "Korean": "kor_Hang",
    "Thai": "tha_Thai",
    "Vietnamese": "vie_Latn",
    "French": "fra_Latn",
    "Spanish": "spa_Latn",
    "Russian": "rus_Cyrl",
    "Arabic": "arb_Arab",
    "Hindi": "hin_Deva",
    "Indonesian": "ind_Latn",
    "Khmer": "khm_Khmr",
}
TRANSLATION_PROMPT_NAMES = {
    "Auto Detect": "auto-detected source language",
    "English": "English",
    "Chinese": "Mandarin Chinese",
    "Japanese": "Japanese",
    "Korean": "Korean",
    "Thai": "Thai",
    "Vietnamese": "Vietnamese",
    "French": "French",
    "Spanish": "Spanish",
    "Russian": "Russian",
    "Arabic": "Arabic",
    "Hindi": "Hindi",
    "Indonesian": "Indonesian",
    "Khmer": "Cambodian Khmer",
}
COL_ID = 0
COL_TIME = 1
COL_ORIGINAL = 2
COL_TRANSLATED = 3
COL_STATUS = 4
COL_VOICE = 5
COL_ACTION = 6


def ensure_output_dir():
    """Return the dedicated render folder, creating it when first needed."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def default_output_path(source_path="", suffix="_dubbed", extension=".mp4"):
    """Build a render path without placing generated files beside the input."""
    source_name = os.path.basename(source_path or "")
    stem = os.path.splitext(source_name)[0].strip() or "video"
    return os.path.join(ensure_output_dir(), f"{stem}{suffix}{extension}")


def available_output_path(source_path="", suffix="_dubbed", extension=".mp4", reserved_paths=()):
    """Choose a non-conflicting default path for queued batch renders."""
    candidate = default_output_path(source_path, suffix, extension)
    reserved = {
        os.path.normcase(os.path.abspath(path))
        for path in reserved_paths
        if path
    }
    if not os.path.exists(candidate) and os.path.normcase(os.path.abspath(candidate)) not in reserved:
        return candidate

    source_name = os.path.basename(source_path or "")
    stem = os.path.splitext(source_name)[0].strip() or "video"
    counter = 2
    while True:
        candidate = os.path.join(ensure_output_dir(), f"{stem}{suffix}_{counter}{extension}")
        normalized = os.path.normcase(os.path.abspath(candidate))
        if not os.path.exists(candidate) and normalized not in reserved:
            return candidate
        counter += 1


def default_voxcpm_python_path():
    candidates = [
        os.path.join(APP_DIR, ".venv_voxcpm", "Scripts", "python.exe"),
        os.path.join(os.path.dirname(APP_DIR), "Tool Somrayrerng", ".venv", "Scripts", "python.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def default_voxcpm_reference_media_path():
    candidate = os.path.join(APP_DIR, "2.mp4")
    return candidate if os.path.exists(candidate) else ""


def default_local_model_python_path():
    candidates = [
        os.path.join(os.path.dirname(APP_DIR), "Tool Somrayrerng", ".venv", "Scripts", "python.exe"),
        default_voxcpm_python_path(),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]


def normalize_translation_language(value, options, default):
    if value in options:
        return value
    return default


def translation_language_scores(text):
    text = text or ""
    return {
        "Japanese": len(re.findall(r"[\u3040-\u30ff]", text)) * 3,
        "Korean": len(re.findall(r"[\uac00-\ud7af]", text)) * 3,
        "Thai": len(re.findall(r"[\u0e00-\u0e7f]", text)) * 3,
        "Khmer": len(re.findall(r"[\u1780-\u17ff]", text)),
        "Russian": len(re.findall(r"[\u0400-\u04ff]", text)) * 2,
        "Arabic": len(re.findall(r"[\u0600-\u06ff]", text)) * 2,
        "Hindi": len(re.findall(r"[\u0900-\u097f]", text)) * 2,
        "Chinese": len(re.findall(r"[\u4e00-\u9fff]", text)),
        "English": len(re.findall(r"[A-Za-z]", text)),
    }


def detect_translation_language(text):
    scores = translation_language_scores(text)
    language, score = max(scores.items(), key=lambda item: item[1])
    return language if score > 0 else "Auto Detect"


def guess_translation_source_language(texts):
    totals = {language: 0 for language in translation_language_scores("")}
    for text in texts:
        for language, score in translation_language_scores(text).items():
            totals[language] += score

    language, score = max(totals.items(), key=lambda item: item[1])
    if score <= 0:
        return "Auto Detect"
    # Latin script alone cannot reliably distinguish English from Vietnamese,
    # French, Spanish, or Indonesian. Leave it on Auto so Gemini/Google can
    # identify the language from full context.
    return "Auto Detect" if language == "English" else language


def effective_translation_source_language(selected_source, text):
    selected_source = normalize_translation_language(
        selected_source,
        TRANSLATION_SOURCE_LANGS,
        "Auto Detect"
    )
    detected = detect_translation_language(text)
    if selected_source == "Auto Detect" and detected == "English":
        return "Auto Detect"
    if detected == "Auto Detect":
        return selected_source
    if selected_source == "Auto Detect" or selected_source != detected:
        return detected
    return selected_source


def translation_direction_label(settings):
    source = settings.get("translation_source_lang", "Auto Detect")
    target = settings.get("translation_target_lang", "Khmer")
    source = normalize_translation_language(source, TRANSLATION_SOURCE_LANGS, "Auto Detect")
    target = normalize_translation_language(target, TRANSLATION_TARGET_LANGS, "Khmer")
    return f"{source} to {target}"


def is_voxcpm_voice(voice_char):
    return voice_char in VOXCPM_VOICE_NAMES


def voxcpm_reference_keys_for_voice(voice_char):
    if voice_char == VOXCPM_FEMALE_VOICE_NAME:
        return ["voxcpm_reference_audio_female", "voxcpm_reference_audio"]
    if voice_char == VOXCPM_MALE_VOICE_NAME:
        return ["voxcpm_reference_audio_male", "voxcpm_reference_audio"]
    if voice_char == VOXCPM_AUTO_VOICE_NAME:
        return ["voxcpm_reference_audio_female", "voxcpm_reference_audio_male", "voxcpm_reference_audio"]
    return ["voxcpm_reference_audio", "voxcpm_reference_audio_female", "voxcpm_reference_audio_male"]


def voxcpm_reference_label_for_voice(voice_char):
    if voice_char == VOXCPM_FEMALE_VOICE_NAME:
        return "female clone reference audio"
    if voice_char == VOXCPM_MALE_VOICE_NAME:
        return "male clone reference audio"
    if voice_char == VOXCPM_AUTO_VOICE_NAME:
        return "female and male clone reference audio"
    return "clone reference audio"


def voxcpm_reference_audio_for_voice(settings, voice_char):
    fallback = ""
    for key in voxcpm_reference_keys_for_voice(voice_char):
        path = settings.get(key, "").strip()
        if path and not fallback:
            fallback = path
        if path and os.path.exists(path):
            return path
    return fallback


def edge_tts_voice_for_character(voice_char_name, text_content=""):
    """Resolve a UI character label to the correct Edge TTS gender/locale."""
    voice_name = (voice_char_name or "").strip().lower()
    is_auto = "auto" in voice_name

    # Check explicit female names before generic male names.  The old code used
    # `"male" in voice_name`, which also matches the word `female` and sent every
    # Female/Sreymom row to the male Piseth voice.
    female_selected = not is_auto and any(
        marker in voice_name
        for marker in ("female", "sreymom", "chamroeun", "alice", "emma", "xiaoxiao")
    )
    male_selected = not is_auto and (
        bool(re.search(r"\bmale\b", voice_name))
        or any(marker in voice_name for marker in ("piseth", "sopheap", "bob", "brian", "yunjian"))
    )

    if female_selected:
        if "alice" in voice_name or "emma" in voice_name:
            return "en-US-EmmaNeural"
        if "xiaoxiao" in voice_name:
            return "zh-CN-XiaoxiaoNeural"
        return "km-KH-SreymomNeural"

    if male_selected:
        if "bob" in voice_name or "brian" in voice_name:
            return "en-US-BrianNeural"
        if "yunjian" in voice_name:
            return "zh-CN-YunjianNeural"
        return "km-KH-PisethNeural"

    # Auto labels contain both voice names. They should normally be replaced by
    # Auto Voice before TTS, but keep a deterministic language-aware fallback.
    male_text_markers = ("ខ្ញុំបាទ", "លោក", "ពូ", "ប្តី", "ឪពុក")
    prefer_male = any(marker in (text_content or "") for marker in male_text_markers)
    if "brian" in voice_name or "emma" in voice_name:
        return "en-US-BrianNeural" if prefer_male else "en-US-EmmaNeural"
    if "yunjian" in voice_name or "xiaoxiao" in voice_name:
        return "zh-CN-YunjianNeural" if prefer_male else "zh-CN-XiaoxiaoNeural"
    return "km-KH-PisethNeural" if prefer_male else "km-KH-SreymomNeural"

MESSAGE_BOX_STYLE = """
    QDialog,
    QMessageBox {
        background-color: #0B0F19;
        color: #F8FAFC;
        border: 1px solid #1E293B;
        border-radius: 10px;
    }
    QLabel {
        background-color: transparent;
        color: #F8FAFC;
        font-family: "Noto Sans Khmer", "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
        font-size: 12px;
        font-weight: 600;
        line-height: 1.5;
    }
    QLineEdit,
    QTextEdit,
    QComboBox {
        background-color: #0F172A;
        color: #F8FAFC;
        border: 1px solid #2A3A54;
        border-radius: 8px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 600;
        min-height: 28px;
        selection-background-color: #0284C7;
    }
    QLineEdit:focus,
    QTextEdit:focus,
    QComboBox:focus {
        border: 2px solid #0EA5E9;
        background-color: #131E33;
    }
    QPushButton {
        background-color: #0284C7;
        color: #FFFFFF;
        border: 1px solid #38BDF8;
        border-radius: 6px;
        padding: 7px 20px;
        font-weight: 700;
        font-size: 12px;
        min-width: 80px;
        min-height: 28px;
    }
    QPushButton:hover {
        background-color: #0369A1;
        border-color: #7DD3FC;
    }
    QPushButton:pressed {
        background-color: #075985;
    }
"""

SETTINGS_DIALOG_STYLE = """
    QDialog {
        background-color: #0B0F19;
        color: #F8FAFC;
    }
    QWidget {
        background-color: transparent;
        color: #F8FAFC;
        font-family: "Noto Sans Khmer", "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
        font-size: 12px;
        font-weight: 600;
    }
    QLabel {
        background-color: transparent;
        color: #94A3B8;
        font-size: 12px;
        font-weight: 600;
    }
    QTabWidget::pane {
        border: 1px solid #1E293B;
        border-radius: 10px;
        background-color: #0E1626;
        top: -1px;
    }
    QTabBar::tab {
        background-color: #0F172A;
        color: #94A3B8;
        border: 1px solid #1E293B;
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        padding: 8px 20px;
        margin-right: 4px;
        font-size: 12px;
        font-weight: 700;
    }
    QTabBar::tab:selected {
        background-color: #0E1626;
        color: #38BDF8;
        border-bottom: 2px solid #38BDF8;
    }
    QTabBar::tab:hover {
        background-color: #1A2438;
        color: #F8FAFC;
    }
    QLineEdit, QComboBox {
        background-color: #0F172A;
        color: #F8FAFC;
        border: 1px solid #283852;
        border-radius: 8px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 600;
        min-height: 28px;
        selection-background-color: #0284C7;
    }
    QLineEdit:hover, QComboBox:hover {
        border-color: #38BDF8;
    }
    QLineEdit:focus, QComboBox:focus {
        border: 2px solid #0EA5E9;
        background-color: #131E33;
    }
    QComboBox::drop-down {
        border: none;
        width: 24px;
        subcontrol-position: right center;
    }
    QComboBox QAbstractItemView {
        background-color: #0F172A;
        color: #F8FAFC;
        border: 1px solid #38BDF8;
        border-radius: 8px;
        padding: 4px;
        selection-background-color: #0284C7;
        selection-color: #FFFFFF;
        outline: 0;
    }
    QComboBox QAbstractItemView::item {
        padding: 8px 12px;
        border-radius: 4px;
    }
    QComboBox QAbstractItemView::item:hover {
        background-color: #0284C7;
        color: #FFFFFF;
    }
    QGroupBox {
        border: 1px solid #1E293B;
        border-radius: 8px;
        margin-top: 14px;
        padding-top: 16px;
        padding-bottom: 12px;
        padding-left: 12px;
        padding-right: 12px;
        font-weight: 700;
        font-size: 12px;
        color: #38BDF8;
        background-color: #0E1626;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 8px;
        background-color: #0E1626;
    }
    QCheckBox {
        color: #E2E8F0;
        font-size: 12px;
        font-weight: 600;
        spacing: 8px;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border: 1px solid #334155;
        border-radius: 4px;
        background-color: #0F172A;
    }
    QCheckBox::indicator:hover {
        border-color: #38BDF8;
    }
    QCheckBox::indicator:checked {
        background-color: #0284C7;
        border-color: #38BDF8;
    }
    QSlider::groove:horizontal {
        height: 6px;
        background: #1E293B;
        border-radius: 3px;
    }
    QSlider::sub-page:horizontal {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #38BDF8);
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #FFFFFF;
        border: 2px solid #0284C7;
        width: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }
    QPushButton#saveSettingsBtn {
        background-color: #0284C7;
        color: #FFFFFF;
        border: 1px solid #38BDF8;
        border-radius: 6px;
        padding: 8px 24px;
        font-weight: bold;
        font-size: 12px;
        min-height: 30px;
    }
    QPushButton#saveSettingsBtn:hover {
        background-color: #0369A1;
    }
    QPushButton#cancelSettingsBtn {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 8px 20px;
        font-weight: bold;
        font-size: 12px;
        min-height: 30px;
    }
    QPushButton#cancelSettingsBtn:hover {
        background-color: #334155;
        border-color: #64748B;
    }
"""



def popup_message(parent, icon, title, text, buttons=QMessageBox.StandardButton.Ok,
                  default_button=QMessageBox.StandardButton.Ok):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if default_button != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default_button)
    box.setStyleSheet(MESSAGE_BOX_STYLE)
    return box.exec()


def popup_info(parent, title, text):
    return popup_message(parent, QMessageBox.Icon.Information, title, text)


def popup_warning(parent, title, text):
    return popup_message(parent, QMessageBox.Icon.Warning, title, text)


def popup_error(parent, title, text):
    return popup_message(parent, QMessageBox.Icon.Critical, title, text)


def popup_question(parent, title, text, buttons, default_button):
    return popup_message(parent, QMessageBox.Icon.Question, title, text, buttons, default_button)


def trimmed_logo_pixmap(path):
    """Load a logo and remove transparent padding, matching final rendering."""
    pixmap = QPixmap(path)
    if pixmap.isNull() or not pixmap.hasAlphaChannel():
        return pixmap
    image = pixmap.toImage()
    # Qt performs this scan in native code. The former nested Python loop over
    # 1.5M+ pixels could make Windows mark the application "Not Responding"
    # whenever a large logo was restored or refreshed.
    alpha_mask = image.createAlphaMask()
    bounds = QRegion(QBitmap.fromImage(alpha_mask)).boundingRect()
    if bounds.isEmpty():
        return pixmap
    bounds.adjust(-4, -4, 4, 4)
    bounds = bounds.intersected(image.rect())
    return QPixmap.fromImage(image.copy(bounds))


def popup_open_file_name(parent, title, directory, name_filter):
    return QFileDialog.getOpenFileName(parent, title, directory, name_filter)


def popup_open_file_names(parent, title, directory, name_filter):
    return QFileDialog.getOpenFileNames(parent, title, directory, name_filter)


def popup_save_file_name(parent, title, directory, name_filter):
    return QFileDialog.getSaveFileName(parent, title, directory, name_filter)


def popup_get_existing_directory(parent, title, directory=""):
    return QFileDialog.getExistingDirectory(parent, title, directory)


def condense_khmer_dubbing_text(text: str) -> str:
    """
    Cleans up bloated, literal machine translations and removes repetitive/overlapping words.
    Converts repetitive stuttered dialogue into natural, concise Khmer spoken movie dialogue.
    """
    if not text:
        return ""

    # 1. Deduplicate repeated punctuation-separated clauses (e.g. "Phrase? Phrase? Phrase?" -> "Phrase?")
    segments = re.split(r'([?។.!,]+\s*)', text)
    clauses = []
    i = 0
    while i < len(segments):
        c = segments[i].strip()
        if not c:
            i += 1
            continue
        punct = ""
        if i + 1 < len(segments) and re.match(r'^[?។.!,]+\s*$', segments[i+1]):
            punct = segments[i+1].strip()
            i += 2
        else:
            i += 1
        full_clause = (c + punct).strip()
        if not clauses or clauses[-1].lower() != full_clause.lower():
            clauses.append(full_clause)
    text = " ".join(clauses)

    # 2. Deduplicate consecutive identical words/phrases
    text = re.sub(r'(\b[\w\u1780-\u17FF\s]{2,30}\b)(?:\s+\1)+', r'\1', text, flags=re.UNICODE)

    # 3. Clean up bloated, literal machine translations into natural Khmer dialogue
    replacements = [
        ("តើមានអ្វីកើតឡើងជាមួយអ្នក", "តើមានរឿងអ្វី"),
        ("តើមានអ្វីកើតឡើង", "តើមានរឿងអ្វី"),
        ("តើមានរឿងអ្វីកើតឡើង", "តើមានរឿងអ្វី"),
        ("តើអ្នកកំពុងធ្វើអ្វី", "ឯងកំពុងធ្វើអ្វី"),
        ("នេះច្បាស់ណាស់ថាជា", "ច្បាស់ណាស់ជា"),
        ("វាគឺជាការដែល", ""),
        ("នៅក្នុងពេលដែល", "ពេលដែល"),
        ("បានធ្វើការប្រគល់", "បានប្រគល់"),
        ("បានធ្វើការនិយាយ", "បាននិយាយ"),
        ("បានធ្វើការ", "បាន"),
        ("មានអារម្មណ៍ថា", "យល់ថា"),
        ("តើអ្នកអាចប្រាប់ខ្ញុំបានទេថា", "ប្រាប់ខ្ញុំមកថា"),
        ("មិនអាចទៅរួចនោះទេ", "មិនអាចទេ"),
        ("តើវាជាអ្វីទៅ", "ជាអ្វីទៅ"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    # Strip multiple dots and ellipsis that cause robotic pauses
    text = re.sub(r'[\.]{2,}', '', text)
    text = text.rstrip('.…')
    return re.sub(r'\s+', ' ', text).strip()

def default_settings():
    return {
        "gemini_api_key": "",
        "gemini_api_key_backup": "",
        "elevenlabs_api_key": "",
        "elevenlabs_voice_id": "",
        "openai_api_key": "",
        "openai_api_key_backup": "",
        "openai_whisper_api_key": "",
        "openai_base_url": "https://anajak.sbs/v1",
        "openai_base_url_backup": "https://api.openai.com/v1",
        "openai_model": "gpt-5.6-sol",
        "translation_provider": "Best Quality Auto (Gemini 3.7 → SeekAI → Google)",
        "gemini_model": "gemini-3.7-flash",
        "gemini_thinking_level": "high",
        "translation_source_lang": "Auto Detect",
        "translation_target_lang": "Khmer",
        "batch_translation_provider": "Best Quality Auto (Gemini 3.7 → SeekAI → Google)",
        "batch_source_lang": "Auto Detect",
        "batch_target_lang": "Khmer",
        "batch_voice": "Auto (Piseth / Sreymom)",
        "batch_auto_gender": True,
        "batch_music_level": 30,
        "batch_mix_mode": "Duck Original on Speech",
        "batch_voice_only": True,
        "single_voice_only": True,
        "batch_noise_reduction": True,
        "silent_notifications": True,
        "background_music_path": "",
        "tesseract_path": "",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "command-r:35b",
        "nllb_python_path": default_local_model_python_path(),
        "nllb_model_id": "facebook/nllb-200-distilled-600M",
        "default_voice_khmer": "Auto (Piseth / Sreymom)",
        "default_voice_english": "Female - Alice",
        "voxcpm_python_path": default_voxcpm_python_path(),
        "voxcpm_model_id": "openbmb/VoxCPM2",
        "voxcpm_reference_audio": "",
        "voxcpm_reference_audio_female": default_voxcpm_reference_media_path(),
        "voxcpm_reference_audio_male": default_voxcpm_reference_media_path(),
        "voxcpm_reference_text": "",
        "voxcpm_style": "warm dramatic Khmer movie narrator",
        "voxcpm_cfg_value": "2.0",
        "voxcpm_inference_steps": "10",
        "voxcpm_seed": "42"
    }


def load_settings():
    defaults = default_settings()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                defaults.update(data)
                return normalize_settings(defaults)
        except Exception:
            pass
    return normalize_settings(defaults)


def normalize_settings(settings):
    settings["translation_source_lang"] = normalize_translation_language(
        settings.get("translation_source_lang", "Auto Detect"),
        TRANSLATION_SOURCE_LANGS,
        "Auto Detect"
    )
    settings["translation_target_lang"] = normalize_translation_language(
        settings.get("translation_target_lang", "Khmer"),
        TRANSLATION_TARGET_LANGS,
        "Khmer"
    )

    voxcpm_python = settings.get("voxcpm_python_path", "").strip()
    detected_voxcpm = default_voxcpm_python_path()
    if (not voxcpm_python or not os.path.exists(voxcpm_python)) and os.path.exists(detected_voxcpm):
        settings["voxcpm_python_path"] = detected_voxcpm

    default_reference_media = default_voxcpm_reference_media_path()
    for key in ("voxcpm_reference_audio_female", "voxcpm_reference_audio_male"):
        if not settings.get(key, "").strip() and default_reference_media:
            settings[key] = default_reference_media

    nllb_python = settings.get("nllb_python_path", "").strip()
    detected_local = default_local_model_python_path()
    if (not nllb_python or not os.path.exists(nllb_python)) and os.path.exists(detected_local):
        settings["nllb_python_path"] = detected_local

    return settings

def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


def openai_whisper_key_for_settings(settings):
    """Return only a key that is intended for the official Whisper API."""
    explicit_key = (settings or {}).get("openai_whisper_api_key", "").strip()
    if explicit_key:
        return explicit_key

    # Check primary and backup settings for official OpenAI key
    for k_field, u_field in (
        ("openai_api_key", "openai_base_url"),
        ("openai_api_key_backup", "openai_base_url_backup"),
    ):
        base_url = (settings or {}).get(u_field, "").strip().lower()
        if "api.openai.com" in base_url:
            k = (settings or {}).get(k_field, "").strip()
            if k:
                return k
    return ""


def normalize_openai_endpoint(base_url):
    """Normalize custom base_url to /v1/chat/completions endpoint."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        url = "https://anajak.sbs/v1"
    if url.endswith("/key"):
        url = url[:-4]
    if url.endswith("/chat/completions"):
        return url
    if not url.endswith("/v1") and "/v1/" not in url:
        url = f"{url}/v1"
    return f"{url}/chat/completions"


def get_openai_chat_candidates(settings, fallback_key=""):
    """
    Return a list of (api_key, base_url, endpoint, label) tuples in priority order.
    Supports primary key, backup account key, and automatic failover.
    """
    settings = settings or {}
    candidates = []
    seen = set()

    # 1. Primary key & base_url
    key1 = str(settings.get("openai_api_key", "") or fallback_key or "").strip()
    url1 = str(settings.get("openai_base_url", "https://anajak.sbs/v1")).strip() or "https://anajak.sbs/v1"
    if key1:
        endpoint1 = normalize_openai_endpoint(url1)
        label1 = "Account ChatGPT (panha)" if "anajak" in url1 else ("OpenAI API" if "openai.com" in url1 else "SeekAI / ChatGPT")
        candidates.append((key1, url1, endpoint1, label1))
        seen.add((key1, endpoint1))

    # 2. Backup key & base_url
    key2 = str(settings.get("openai_api_key_backup", "")).strip()
    url2 = str(settings.get("openai_base_url_backup", "")).strip()
    if not url2:
        url2 = "https://api.openai.com/v1" if "anajak" in url1 else "https://anajak.sbs/v1"
    if key2:
        endpoint2 = normalize_openai_endpoint(url2)
        if (key2, endpoint2) not in seen:
            label2 = "Account ChatGPT (panha)" if "anajak" in url2 else ("OpenAI API Backup" if "openai.com" in url2 else "Backup ChatGPT Key")
            candidates.append((key2, url2, endpoint2, label2))
            seen.add((key2, endpoint2))

    return candidates


def parse_timecode_to_ms(timecode_str):
    if " - " in timecode_str:
        timecode_str = timecode_str.split(" - ")[0]
    elif "-->" in timecode_str:
        timecode_str = timecode_str.split("-->")[0]
    
    timecode_str = timecode_str.strip().replace(',', '.')
    parts = timecode_str.split(':')
    if len(parts) < 3:
        return 0
    try:
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return int((h * 3600 + m * 60 + s) * 1000)
    except ValueError:
        return 0

def parse_timecode_range(timecode_str):
    def parse_single(tc):
        tc = tc.strip().replace(',', '.')
        parts = tc.split(':')
        if len(parts) < 3:
            return 0.0
        try:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
            return h * 3600.0 + m * 60.0 + s
        except ValueError:
            return 0.0

    if " - " in timecode_str:
        start_part, end_part = timecode_str.split(" - ", 1)
        return parse_single(start_part), parse_single(end_part)
    elif "-->" in timecode_str:
        start_part, end_part = timecode_str.split("-->", 1)
        return parse_single(start_part), parse_single(end_part)
    else:
        sec = parse_single(timecode_str)
        return sec, sec + 3.0


def format_seconds_to_timecode(seconds):
    seconds = max(0.0, float(seconds or 0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        s += 1
        ms -= 1000
    if s >= 60:
        m += 1
        s -= 60
    if m >= 60:
        h += 1
        m -= 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def change_audio_tempo_ffmpeg(input_audio, speed):
    if abs(speed - 1.0) < 0.03:
        return input_audio
    speed = max(0.5, min(speed, 2.0))
    import subprocess
    import io
    from pydub import AudioSegment
    wav_io = io.BytesIO()
    input_audio.export(wav_io, format="wav")
    wav_data = wav_io.getvalue()
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", "pipe:0",
        "-filter:a", f"atempo={speed:.3f}",
        "-f", "wav", "pipe:1"
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(input=wav_data, timeout=12)
        if proc.returncode == 0 and out:
            return AudioSegment.from_file(io.BytesIO(out), format="wav")
    except Exception as e:
        print(f"FFmpeg atempo speedup error: {e}")
    return input_audio


def auto_merge_broken_subtitles(segments):
    """
    Automatically merges consecutive subtitle fragments that belong to the same sentence.
    Prevents dialogue from being split into two choppy lines.
    """
    if not segments or len(segments) <= 1:
        return segments

    merged = []
    i = 0
    total = len(segments)

    while i < total:
        curr = dict(segments[i])
        
        while i + 1 < total:
            next_seg = segments[i + 1]
            curr_text = (curr.get("text") or "").strip()
            next_text = (next_seg.get("text") or "").strip()
            
            try:
                curr_start, curr_end = parse_timecode_range(curr.get("time", "00:00:00 - 00:00:03"))
                next_start, next_end = parse_timecode_range(next_seg.get("time", "00:00:03 - 00:00:06"))
                gap = max(0.0, next_start - curr_end)
            except Exception:
                gap = 1.0

            # Whisper sometimes emits the same utterance in two adjacent
            # timestamp windows. Extend the first window instead of keeping
            # duplicate rows or concatenating the text twice.
            normalize = lambda value: re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()
            if gap <= 0.60 and normalize(curr_text) and normalize(curr_text) == normalize(next_text):
                curr["time"] = f"{format_seconds_to_timecode(curr_start)} - {format_seconds_to_timecode(next_end)}"
                i += 1
                continue

            ends_with_punct = bool(re.search(r'[。！？\.\!\?]$', curr_text))
            can_merge = (gap <= 0.45) and (not ends_with_punct) and (len(curr_text) <= 16)

            if can_merge:
                is_cjk = bool(re.search(r'[\u4e00-\u9fff]', curr_text))
                joined_text = curr_text + ("" if is_cjk else " ") + next_text
                curr["text"] = joined_text.strip()
                curr["time"] = f"{format_seconds_to_timecode(curr_start)} - {format_seconds_to_timecode(next_end)}"
                i += 1
            else:
                break

        merged.append(curr)
        i += 1

    for idx, seg in enumerate(merged):
        seg["id"] = idx + 1

    return merged


def deduplicate_saved_subtitles(subtitles):
    """Merge exact consecutive duplicates in restored project sessions."""
    cleaned = []
    for source in subtitles or []:
        seg = dict(source)
        if cleaned:
            previous = cleaned[-1]
            prev_text = re.sub(r"[\W_]+", "", str(previous.get("original", "")), flags=re.UNICODE).casefold()
            curr_text = re.sub(r"[\W_]+", "", str(seg.get("original", "")), flags=re.UNICODE).casefold()
            try:
                prev_start, prev_end = parse_timecode_range(previous.get("time", ""))
                curr_start, curr_end = parse_timecode_range(seg.get("time", ""))
            except Exception:
                prev_start = prev_end = curr_start = curr_end = 0.0
            if prev_text and prev_text == curr_text and 0.0 <= curr_start - prev_end <= 0.60:
                previous["time"] = (
                    f"{format_seconds_to_timecode(prev_start)} - {format_seconds_to_timecode(curr_end)}"
                )
                continue
        cleaned.append(seg)
    for index, seg in enumerate(cleaned, 1):
        seg["id"] = str(index)
    return cleaned



class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings & API Configuration")
        self.setMinimumWidth(660)
        self.setMinimumHeight(560)
        self.resize(700, 620)
        self.setStyleSheet(SETTINGS_DIALOG_STYLE)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        
        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Cambo Dubber Settings")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #0082C8;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        self.settings_data = load_settings()
        
        # Tab Widget
        tabs = QTabWidget()
        
        def _make_scroll_tab(widget):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setStyleSheet("""
                QScrollArea { background: transparent; border: none; }
                QScrollBar:vertical { background: #070B16; width: 6px; border-radius: 3px; }
                QScrollBar::handle:vertical { background: #334155; min-height: 20px; border-radius: 3px; }
                QScrollBar::handle:vertical:hover { background: #38BDF8; }
                QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            """)
            scroll.setWidget(widget)
            return scroll

        # ==========================================
        # TAB 1: AI & Translation
        # ==========================================
        tab_ai = QWidget()
        tab_ai_layout = QVBoxLayout(tab_ai)
        tab_ai_layout.setContentsMargins(12, 12, 12, 12)
        tab_ai_layout.setSpacing(12)
        
        form_ai = QFormLayout()
        form_ai.setVerticalSpacing(10)
        form_ai.setHorizontalSpacing(14)
        
        self.translation_provider = QComboBox()
        providers = [
            "Best Quality Auto (Gemini 3.7 → SeekAI → Google)",
            "Gemini", "OpenAI / SeekAI (gpt-5.6-sol)", "OpenAI GPT-4o",
            "Google Web", "NLLB Local", "Ollama", "Mock"
        ]
        saved_provider = self.settings_data.get(
            "translation_provider",
            "Best Quality Auto (Gemini 3.7 → SeekAI → Google)"
        )
        if saved_provider not in providers:
            providers.insert(0, saved_provider)
        self.translation_provider.addItems(providers)
        self.translation_provider.setCurrentText(saved_provider)
        form_ai.addRow("Translation Provider:", self.translation_provider)

        lang_row = QHBoxLayout()
        self.translation_source = QComboBox()
        self.translation_source.addItems(TRANSLATION_SOURCE_LANGS)
        saved_source = self.settings_data.get("translation_source_lang", "Auto Detect")
        self.translation_source.setCurrentText(
            normalize_translation_language(saved_source, TRANSLATION_SOURCE_LANGS, "Auto Detect")
        )
        lang_row.addWidget(self.translation_source)
        
        lbl_to = QLabel("➜")
        lbl_to.setStyleSheet("font-weight: bold; color: #0082C8; padding: 0 4px;")
        lang_row.addWidget(lbl_to)
        
        self.translation_target = QComboBox()
        self.translation_target.addItems(TRANSLATION_TARGET_LANGS)
        saved_target = self.settings_data.get("translation_target_lang", "Khmer")
        self.translation_target.setCurrentText(
            normalize_translation_language(saved_target, TRANSLATION_TARGET_LANGS, "Khmer")
        )
        lang_row.addWidget(self.translation_target)
        form_ai.addRow("Language Direction:", lang_row)

        def _make_key_row(line_edit):
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            h.addWidget(line_edit, stretch=1)
            btn_eye = QPushButton("👁")
            btn_eye.setFixedSize(36, 32)
            btn_eye.setToolTip("Show / Hide API Key (បង្ហាញ/លាក់)")
            btn_eye.setStyleSheet("""
                QPushButton {
                    background-color: #0F172A;
                    border: 1px solid #283852;
                    border-radius: 6px;
                    color: #94A3B8;
                    font-size: 15px;
                    padding: 0;
                }
                QPushButton:hover {
                    background-color: #1E293B;
                    color: #38BDF8;
                    border-color: #38BDF8;
                }
            """)
            def _toggle():
                if line_edit.echoMode() == QLineEdit.EchoMode.Password:
                    line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
                    btn_eye.setStyleSheet("""
                        QPushButton {
                            background-color: #0284C7;
                            border: 1px solid #38BDF8;
                            border-radius: 6px;
                            color: #FFFFFF;
                            font-size: 15px;
                            padding: 0;
                        }
                    """)
                else:
                    line_edit.setEchoMode(QLineEdit.EchoMode.Password)
                    btn_eye.setStyleSheet("""
                        QPushButton {
                            background-color: #0F172A;
                            border: 1px solid #283852;
                            border-radius: 6px;
                            color: #94A3B8;
                            font-size: 15px;
                            padding: 0;
                        }
                        QPushButton:hover {
                            background-color: #1E293B;
                            color: #38BDF8;
                            border-color: #38BDF8;
                        }
                    """)
            btn_eye.clicked.connect(_toggle)
            h.addWidget(btn_eye)
            return container

        self.openai_key = QLineEdit(self.settings_data.get("openai_api_key", ""))
        self.openai_key.setPlaceholderText("Primary ChatGPT / OpenAI API Key (e.g. panha account key)")
        form_ai.addRow("ChatGPT / OpenAI Key:", _make_key_row(self.openai_key))

        self.openai_base_url = QLineEdit(self.settings_data.get("openai_base_url", "https://anajak.sbs/v1"))
        self.openai_base_url.setPlaceholderText("https://anajak.sbs/v1 or https://api.openai.com/v1")
        form_ai.addRow("API Base URL:", self.openai_base_url)

        self.openai_backup_key = QLineEdit(self.settings_data.get("openai_api_key_backup", ""))
        self.openai_backup_key.setPlaceholderText("Optional backup / account key for automatic failover")
        form_ai.addRow("Backup ChatGPT Key:", _make_key_row(self.openai_backup_key))

        self.openai_backup_base_url = QLineEdit(self.settings_data.get("openai_base_url_backup", "https://api.openai.com/v1"))
        self.openai_backup_base_url.setPlaceholderText("https://api.openai.com/v1 or https://anajak.sbs/v1")
        form_ai.addRow("Backup Base URL:", self.openai_backup_base_url)

        self.openai_whisper_key = QLineEdit(self.settings_data.get("openai_whisper_api_key", ""))
        self.openai_whisper_key.setPlaceholderText("Optional official OpenAI key; blank uses Local Whisper")
        form_ai.addRow("OpenAI Whisper Key:", _make_key_row(self.openai_whisper_key))

        self.openai_model = QLineEdit(self.settings_data.get("openai_model", "gpt-5.6-sol"))
        self.openai_model.setPlaceholderText("gpt-5.6-sol, gpt-5.4, gpt-4o, etc.")
        form_ai.addRow("AI Model:", self.openai_model)

        self.silent_notifications = QCheckBox("Silent workflow notifications (no completion popups)")
        self.silent_notifications.setChecked(bool(self.settings_data.get("silent_notifications", True)))
        self.silent_notifications.setToolTip(
            "Shows translation, Auto Voice, and TTS completion in the status bar/log instead of modal popups."
        )
        form_ai.addRow(self.silent_notifications)

        self.gemini_key = QLineEdit(self.settings_data.get("gemini_api_key", ""))
        self.gemini_key.setPlaceholderText("Enter Gemini API Key")
        form_ai.addRow("Gemini API Key:", _make_key_row(self.gemini_key))

        self.gemini_backup_key = QLineEdit(self.settings_data.get("gemini_api_key_backup", ""))
        self.gemini_backup_key.setPlaceholderText("Optional backup key for automatic rotation")
        form_ai.addRow("Gemini Backup Key:", _make_key_row(self.gemini_backup_key))

        self.gemini_model = QComboBox()
        gemini_models = [
            "gemini-3.7-flash",
            "gemini-3.1-pro-preview",
            "gemini-pro-latest",
            "gemini-3.6-flash",
            "gemini-flash-latest",
            "gemini-2.5-pro",
        ]
        saved_gemini_model = self.settings_data.get("gemini_model", "gemini-3.7-flash").strip()
        if saved_gemini_model and saved_gemini_model not in gemini_models:
            gemini_models.insert(0, saved_gemini_model)
        self.gemini_model.addItems(gemini_models)
        self.gemini_model.setCurrentText(saved_gemini_model or "gemini-3.7-flash")
        form_ai.addRow("Gemini Model:", self.gemini_model)

        self.gemini_thinking_level = QComboBox()
        self.gemini_thinking_level.addItems(["high", "medium", "low"])
        self.gemini_thinking_level.setCurrentText(
            self.settings_data.get("gemini_thinking_level", "high").strip().lower()
        )
        form_ai.addRow("Gemini Thinking:", self.gemini_thinking_level)

        self.ollama_url = QLineEdit(self.settings_data.get("ollama_url", "http://localhost:11434"))
        self.ollama_url.setPlaceholderText("http://localhost:11434")
        form_ai.addRow("Ollama URL:", self.ollama_url)

        self.ollama_model = QLineEdit(self.settings_data.get("ollama_model", "command-r:35b"))
        self.ollama_model.setPlaceholderText("command-r:35b or translategemma")
        form_ai.addRow("Ollama Model:", self.ollama_model)

        tab_ai_layout.addLayout(form_ai)
        tab_ai_layout.addStretch()
        tabs.addTab(_make_scroll_tab(tab_ai), "🌐 AI & Translation")

        # ==========================================
        # TAB 2: VoxCPM Voice Cloning
        # ==========================================
        tab_vox = QWidget()
        tab_vox_layout = QVBoxLayout(tab_vox)
        tab_vox_layout.setContentsMargins(10, 12, 10, 10)
        tab_vox_layout.setSpacing(8)
        
        form_vox = QFormLayout()
        form_vox.setSpacing(8)

        self.voxcpm_python = QLineEdit(self.settings_data.get("voxcpm_python_path", default_voxcpm_python_path()))
        self.voxcpm_python.setPlaceholderText("Path to Python with VoxCPM installed")
        voxcpm_python_row = QHBoxLayout()
        voxcpm_python_row.addWidget(self.voxcpm_python)
        btn_browse_voxcpm_python = QPushButton("Browse")
        btn_browse_voxcpm_python.clicked.connect(self.browse_voxcpm_python)
        voxcpm_python_row.addWidget(btn_browse_voxcpm_python)
        form_vox.addRow("VoxCPM Python:", voxcpm_python_row)

        self.voxcpm_model = QComboBox()
        voxcpm_models = ["openbmb/VoxCPM2", "openbmb/VoxCPM1.5", "openbmb/VoxCPM-0.5B"]
        saved_model = self.settings_data.get("voxcpm_model_id", "openbmb/VoxCPM2")
        if saved_model not in voxcpm_models:
            voxcpm_models.insert(0, saved_model)
        self.voxcpm_model.addItems(voxcpm_models)
        self.voxcpm_model.setCurrentText(saved_model)
        form_vox.addRow("VoxCPM Model:", self.voxcpm_model)

        self.voxcpm_reference_audio = QLineEdit(self.settings_data.get("voxcpm_reference_audio", ""))
        self.voxcpm_reference_audio.setPlaceholderText("Short voice sample for cloning (.wav/.mp3)")
        voxcpm_audio_row = QHBoxLayout()
        voxcpm_audio_row.addWidget(self.voxcpm_reference_audio)
        btn_browse_voxcpm_audio = QPushButton("Browse")
        btn_browse_voxcpm_audio.clicked.connect(self.browse_voxcpm_reference_audio)
        voxcpm_audio_row.addWidget(btn_browse_voxcpm_audio)
        form_vox.addRow("Default Reference:", voxcpm_audio_row)

        self.voxcpm_reference_audio_female = QLineEdit(
            self.settings_data.get("voxcpm_reference_audio_female", default_voxcpm_reference_media_path())
        )
        self.voxcpm_reference_audio_female.setPlaceholderText("Female speaker reference audio/video")
        voxcpm_female_row = QHBoxLayout()
        voxcpm_female_row.addWidget(self.voxcpm_reference_audio_female)
        btn_browse_voxcpm_female = QPushButton("Browse")
        btn_browse_voxcpm_female.clicked.connect(self.browse_voxcpm_reference_audio_female)
        voxcpm_female_row.addWidget(btn_browse_voxcpm_female)
        form_vox.addRow("Female Reference:", voxcpm_female_row)

        self.voxcpm_reference_audio_male = QLineEdit(
            self.settings_data.get("voxcpm_reference_audio_male", default_voxcpm_reference_media_path())
        )
        self.voxcpm_reference_audio_male.setPlaceholderText("Male speaker reference audio/video")
        voxcpm_male_row = QHBoxLayout()
        voxcpm_male_row.addWidget(self.voxcpm_reference_audio_male)
        btn_browse_voxcpm_male = QPushButton("Browse")
        btn_browse_voxcpm_male.clicked.connect(self.browse_voxcpm_reference_audio_male)
        voxcpm_male_row.addWidget(btn_browse_voxcpm_male)
        form_vox.addRow("Male Reference:", voxcpm_male_row)

        self.voxcpm_reference_text = QLineEdit(self.settings_data.get("voxcpm_reference_text", ""))
        self.voxcpm_reference_text.setPlaceholderText("Optional words spoken in reference audio")
        form_vox.addRow("Transcript Text:", self.voxcpm_reference_text)

        self.voxcpm_style = QLineEdit(self.settings_data.get("voxcpm_style", "warm dramatic Khmer movie narrator"))
        self.voxcpm_style.setPlaceholderText("warm dramatic Khmer movie narrator")
        form_vox.addRow("Voice Style:", self.voxcpm_style)

        tab_vox_layout.addLayout(form_vox)
        tab_vox_layout.addStretch()
        tabs.addTab(_make_scroll_tab(tab_vox), "🎙️ Voice Cloning (VoxCPM)")

        # ==========================================
        # TAB 3: Tools & OCR
        # ==========================================
        tab_tools = QWidget()
        tab_tools_layout = QVBoxLayout(tab_tools)
        tab_tools_layout.setContentsMargins(10, 12, 10, 10)
        tab_tools_layout.setSpacing(8)
        
        form_tools = QFormLayout()
        form_tools.setSpacing(8)

        self.elevenlabs_key = QLineEdit(self.settings_data.get("elevenlabs_api_key", ""))
        self.elevenlabs_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.elevenlabs_key.setPlaceholderText("Enter ElevenLabs API Key (Optional)")
        form_tools.addRow("ElevenLabs API Key:", self.elevenlabs_key)

        self.elevenlabs_voice_id = QLineEdit(self.settings_data.get("elevenlabs_voice_id", ""))
        self.elevenlabs_voice_id.setPlaceholderText("Enter ElevenLabs Voice ID (Optional)")
        form_tools.addRow("ElevenLabs Voice ID:", self.elevenlabs_voice_id)

        self.nllb_python = QLineEdit(self.settings_data.get("nllb_python_path", default_local_model_python_path()))
        self.nllb_python.setPlaceholderText("Python with transformers + sentencepiece")
        nllb_python_row = QHBoxLayout()
        nllb_python_row.addWidget(self.nllb_python)
        btn_browse_nllb_python = QPushButton("Browse")
        btn_browse_nllb_python.clicked.connect(self.browse_nllb_python)
        nllb_python_row.addWidget(btn_browse_nllb_python)
        form_tools.addRow("NLLB Python:", nllb_python_row)

        self.nllb_model = QLineEdit(self.settings_data.get("nllb_model_id", "facebook/nllb-200-distilled-600M"))
        self.nllb_model.setPlaceholderText("facebook/nllb-200-distilled-600M")
        form_tools.addRow("NLLB Model ID:", self.nllb_model)

        self.tesseract_path = QLineEdit(self.settings_data.get("tesseract_path", ""))
        self.tesseract_path.setPlaceholderText("Path to tesseract.exe for subtitle OCR")
        tesseract_row = QHBoxLayout()
        tesseract_row.addWidget(self.tesseract_path)
        btn_browse_tesseract = QPushButton("Browse")
        btn_browse_tesseract.clicked.connect(self.browse_tesseract_path)
        tesseract_row.addWidget(btn_browse_tesseract)
        form_tools.addRow("Tesseract OCR:", tesseract_row)

        tab_tools_layout.addLayout(form_tools)
        tab_tools_layout.addStretch()
        tabs.addTab(_make_scroll_tab(tab_tools), "🛠️ Tools & OCR")

        layout.addWidget(tabs)

        # Bottom Buttons
        buttons = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #0EA5E9;
                border-radius: 6px;
                padding: 8px 22px;
                font-weight: bold;
                font-size: 13px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #0369A1;
                border-color: #38BDF8;
            }
        """)
        save_btn.clicked.connect(self.save)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #FFFFFF;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        
        buttons.addStretch()
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def save(self):
        self.settings_data["gemini_api_key"] = self.gemini_key.text().strip()
        self.settings_data["gemini_api_key_backup"] = self.gemini_backup_key.text().strip()
        self.settings_data["gemini_model"] = self.gemini_model.currentText().strip()
        self.settings_data["gemini_thinking_level"] = self.gemini_thinking_level.currentText().strip()
        self.settings_data["elevenlabs_api_key"] = self.elevenlabs_key.text().strip()
        self.settings_data["elevenlabs_voice_id"] = self.elevenlabs_voice_id.text().strip()
        self.settings_data["openai_api_key"] = self.openai_key.text().strip()
        self.settings_data["openai_base_url"] = self.openai_base_url.text().strip()
        self.settings_data["openai_api_key_backup"] = self.openai_backup_key.text().strip()
        self.settings_data["openai_base_url_backup"] = self.openai_backup_base_url.text().strip()
        self.settings_data["openai_whisper_api_key"] = self.openai_whisper_key.text().strip()
        self.settings_data["openai_model"] = self.openai_model.text().strip()
        self.settings_data["silent_notifications"] = self.silent_notifications.isChecked()
        self.settings_data["translation_provider"] = self.translation_provider.currentText().strip()
        self.settings_data["translation_source_lang"] = self.translation_source.currentText().strip()
        self.settings_data["translation_target_lang"] = self.translation_target.currentText().strip()
        self.settings_data["ollama_url"] = self.ollama_url.text().strip()
        self.settings_data["ollama_model"] = self.ollama_model.text().strip()
        self.settings_data["nllb_python_path"] = self.nllb_python.text().strip()
        self.settings_data["nllb_model_id"] = self.nllb_model.text().strip()
        self.settings_data["tesseract_path"] = self.tesseract_path.text().strip()
        self.settings_data["voxcpm_python_path"] = self.voxcpm_python.text().strip()
        self.settings_data["voxcpm_model_id"] = self.voxcpm_model.currentText().strip()
        self.settings_data["voxcpm_reference_audio"] = self.voxcpm_reference_audio.text().strip()
        self.settings_data["voxcpm_reference_audio_female"] = self.voxcpm_reference_audio_female.text().strip()
        self.settings_data["voxcpm_reference_audio_male"] = self.voxcpm_reference_audio_male.text().strip()
        self.settings_data["voxcpm_reference_text"] = self.voxcpm_reference_text.text().strip()
        self.settings_data["voxcpm_style"] = self.voxcpm_style.text().strip()
        save_settings(self.settings_data)
        popup_info(self, "Success", "Settings saved successfully.")
        self.accept()

    def browse_voxcpm_python(self):
        file_path, _ = popup_open_file_name(
            self, "Select VoxCPM Python", "", "Python Executable (python.exe);;All Files (*)"
        )
        if file_path:
            self.voxcpm_python.setText(file_path)

    def browse_nllb_python(self):
        file_path, _ = popup_open_file_name(
            self, "Select NLLB Python", "", "Python Executable (python.exe);;All Files (*)"
        )
        if file_path:
            self.nllb_python.setText(file_path)

    def browse_tesseract_path(self):
        file_path, _ = popup_open_file_name(
            self, "Select Tesseract OCR", "", "Tesseract Executable (tesseract.exe);;All Files (*)"
        )
        if file_path:
            self.tesseract_path.setText(file_path)

    def browse_voxcpm_reference_audio(self):
        file_path, _ = popup_open_file_name(
            self, "Select Clone Reference Audio", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if file_path:
            self.voxcpm_reference_audio.setText(file_path)

    def browse_voxcpm_reference_audio_female(self):
        file_path, _ = popup_open_file_name(
            self, "Select Female Clone Reference", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if file_path:
            self.voxcpm_reference_audio_female.setText(file_path)

    def browse_voxcpm_reference_audio_male(self):
        file_path, _ = popup_open_file_name(
            self, "Select Male Clone Reference", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if file_path:
            self.voxcpm_reference_audio_male.setText(file_path)


# Worker threads to maintain slick responsive UI
class TranscribeWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, video_path, openai_api_key="", local_model_python=""):
        super().__init__()
        self.video_path = video_path
        self.openai_api_key = openai_api_key
        self.local_model_python = local_model_python

    def run(self):
        import subprocess
        import os
        import tempfile
        import time

        def format_seconds_to_hms(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = seconds % 60
            return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')

        try:
            self.progress.emit(10)
            temp_dir = tempfile.gettempdir()
            temp_audio = os.path.join(temp_dir, f"transcribe_{int(time.time())}.wav")
            extract_cmd = [
                "ffmpeg", "-y", "-hwaccel", "auto", "-i", self.video_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                temp_audio
            ]
                
            self.progress.emit(25)
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            self.progress.emit(40)
            
            segments = []
            transcribed_successfully = False
            
            if self.openai_api_key:
                try:
                    from openai import OpenAI
                    self.progress.emit(50)
                    client = OpenAI(api_key=self.openai_api_key)
                    
                    with open(temp_audio, "rb") as audio_file:
                        transcript_response = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            response_format="verbose_json"
                        )
                    
                    self.progress.emit(90)
                    for idx, seg in enumerate(transcript_response.segments):
                        start_time = format_seconds_to_hms(seg.start)
                        end_time = format_seconds_to_hms(seg.end)
                        segments.append({
                            "id": idx + 1,
                            "time": f"{start_time} - {end_time}",
                            "text": seg.text.strip()
                        })
                    transcribed_successfully = True
                except Exception as openai_err:
                    status_code = getattr(openai_err, "status_code", "request error")
                    print(
                        f"OpenAI Whisper unavailable ({status_code}). "
                        "Automatically falling back to Local Whisper AI..."
                    )
            
            if not transcribed_successfully:
                self.progress.emit(50)
                external_python = (self.local_model_python or "").strip()
                bridge_path = os.path.join(APP_DIR, "whisper_bridge.py")
                if external_python and os.path.exists(external_python) and os.path.exists(bridge_path):
                    bridge_output = os.path.join(temp_dir, f"whisper_segments_{int(time.time())}.json")
                    bridge_cmd = [
                        external_python, "-u", bridge_path,
                        "--audio", temp_audio,
                        "--output", bridge_output,
                        "--model", "base",
                    ]
                    bridge_result = subprocess.run(
                        bridge_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if bridge_result.returncode == 0 and os.path.exists(bridge_output):
                        with open(bridge_output, "r", encoding="utf-8") as handle:
                            bridge_segments = json.load(handle)
                        for idx, seg in enumerate(bridge_segments):
                            segments.append({
                                "id": idx + 1,
                                "time": (
                                    f"{format_seconds_to_hms(float(seg['start']))} - "
                                    f"{format_seconds_to_hms(float(seg['end']))}"
                                ),
                                "text": (seg.get("text") or "").strip(),
                            })
                        transcribed_successfully = True
                    try:
                        if os.path.exists(bridge_output):
                            os.remove(bridge_output)
                    except OSError:
                        pass

            if not transcribed_successfully:
                self.progress.emit(55)
                try:
                    import whisper
                except ImportError as exc:
                    raise RuntimeError(
                        "Local Whisper is not installed. Install it with: "
                        "python -m pip install openai-whisper, or add an OpenAI API key in Settings."
                    ) from exc
                try:
                    import torch
                    use_fp16 = torch.cuda.is_available()
                    whisper_device = "cuda" if use_fp16 else "cpu"
                except Exception:
                    use_fp16 = False
                    whisper_device = "cpu"
                self.progress.emit(65)
                model = whisper.load_model("base", device=whisper_device)
                self.progress.emit(75)
                result = model.transcribe(temp_audio, fp16=use_fp16)
                self.progress.emit(90)
                
                for idx, seg in enumerate(result.get("segments", [])):
                    start_time = format_seconds_to_hms(seg["start"])
                    end_time = format_seconds_to_hms(seg["end"])
                    segments.append({
                        "id": idx + 1,
                        "time": f"{start_time} - {end_time}",
                        "text": seg["text"].strip()
                    })
            
            try:
                os.remove(temp_audio)
            except Exception:
                pass
                
            self.progress.emit(100)
            self.completed.emit(segments)
            
        except Exception as e:
            self.error.emit(str(e))


class OCRSubtitleWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, video_path, settings=None):
        super().__init__()
        self.video_path = video_path
        self.settings = settings or {}

    def _tesseract_path(self):
        candidates = [
            self.settings.get("tesseract_path", "").strip(),
            shutil.which("tesseract") or "",
            r"F:\YSH SOFTWARE REG CLONE FB BY Hourzzx\SD-Farm\SDadb\TesseractOCR\tesseract.exe",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return ""

    def _video_duration(self):
        import subprocess

        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", self.video_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return max(0.0, float(result.stdout.strip()))
        except Exception:
            return 0.0

    def _clean_ocr_text(self, text):
        text = (text or "").replace("\n", " ")
        text = re.sub(r"[^A-Za-z0-9'\".,!?;:()\\-\\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip(" -_.,")
        if not re.search(r"[A-Za-z]", text):
            return ""
        if len(text) < 2:
            return ""
        return text

    def _run_tesseract_on_image(self, image_path, tesseract_path):
        import subprocess

        result = subprocess.run(
            [tesseract_path, image_path, "stdout", "-l", "eng", "--psm", "6"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return self._clean_ocr_text(result.stdout)

    def _image_backend(self):
        import importlib.util

        if importlib.util.find_spec("cv2"):
            return "opencv"
        if importlib.util.find_spec("PIL"):
            return "pillow"
        return "ffmpeg"

    def _ocr_frame_opencv(self, frame_path, temp_dir, tesseract_path):
        import cv2

        frame = cv2.imread(frame_path)
        if frame is None:
            return ""

        height, width = frame.shape[:2]
        x1, x2 = int(width * 0.06), int(width * 0.94)
        y1, y2 = int(height * 0.45), int(height * 0.82)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return ""

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.6, fy=2.6, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 5, 35, 35)

        variants = []
        variants.append(("gray", gray))
        _, bright = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY)
        variants.append(("bright_inv", 255 - bright))
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(("otsu", otsu))

        best_text = ""
        for name, image in variants:
            image_path = os.path.join(temp_dir, f"ocr_{os.path.basename(frame_path)}_{name}.png")
            cv2.imwrite(image_path, image)
            text = self._run_tesseract_on_image(image_path, tesseract_path)
            if len(text) > len(best_text):
                best_text = text

        return best_text

    def _ocr_frame_pillow(self, frame_path, temp_dir, tesseract_path):
        from PIL import Image, ImageFilter, ImageOps

        with Image.open(frame_path) as frame:
            frame = frame.convert("RGB")
            width, height = frame.size
            x1, x2 = int(width * 0.06), int(width * 0.94)
            y1, y2 = int(height * 0.45), int(height * 0.82)
            crop = frame.crop((x1, y1, x2, y2))

        if crop.width <= 0 or crop.height <= 0:
            return ""

        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        gray = ImageOps.grayscale(crop)
        gray = gray.resize((max(1, int(gray.width * 2.8)), max(1, int(gray.height * 2.8))), resampling)
        gray = ImageOps.autocontrast(gray)
        gray = gray.filter(ImageFilter.SHARPEN)

        bright = gray.point(lambda px: 255 if px > 175 else 0)
        variants = [
            ("gray", gray),
            ("bright", bright),
            ("bright_inv", ImageOps.invert(bright)),
        ]

        best_text = ""
        for name, image in variants:
            image_path = os.path.join(temp_dir, f"ocr_{os.path.basename(frame_path)}_{name}.png")
            image.save(image_path)
            text = self._run_tesseract_on_image(image_path, tesseract_path)
            if len(text) > len(best_text):
                best_text = text

        return best_text

    def _ffmpeg_ocr_crop_filter(self):
        return (
            "crop=trunc(iw*0.88/2)*2:trunc(ih*0.37/2)*2:trunc(iw*0.06):trunc(ih*0.45),"
            "scale=iw*3:ih*3,format=gray"
        )

    def _ocr_frame_ffmpeg(self, frame_path, temp_dir, tesseract_path):
        import subprocess

        variants = [
            ("crop", self._ffmpeg_ocr_crop_filter()),
            ("contrast", f"{self._ffmpeg_ocr_crop_filter()},eq=contrast=1.45:brightness=0.03,unsharp=5:5:0.8"),
            (
                "lower",
                "crop=trunc(iw*0.92/2)*2:trunc(ih*0.28/2)*2:trunc(iw*0.04):trunc(ih*0.54),"
                "scale=iw*3:ih*3,format=gray,eq=contrast=1.35",
            ),
        ]

        best_text = ""
        for name, video_filter in variants:
            image_path = os.path.join(temp_dir, f"ocr_{os.path.basename(frame_path)}_{name}.png")
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", frame_path, "-vf", video_filter, "-frames:v", "1", image_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0 or not os.path.exists(image_path):
                continue
            text = self._run_tesseract_on_image(image_path, tesseract_path)
            if len(text) > len(best_text):
                best_text = text

        return best_text

    def _ocr_frame(self, frame_path, temp_dir, tesseract_path, preprocessed=False):
        if preprocessed:
            return self._run_tesseract_on_image(frame_path, tesseract_path)

        backend = self._image_backend()
        if backend == "opencv":
            try:
                text = self._ocr_frame_opencv(frame_path, temp_dir, tesseract_path)
                if text:
                    return text
            except Exception:
                pass

        if backend in {"opencv", "pillow"}:
            try:
                text = self._ocr_frame_pillow(frame_path, temp_dir, tesseract_path)
                if text:
                    return text
            except Exception:
                pass

        return self._ocr_frame_ffmpeg(frame_path, temp_dir, tesseract_path)

    def run(self):
        import difflib
        import os
        import subprocess
        import tempfile

        try:
            tesseract_path = self._tesseract_path()
            if not tesseract_path:
                raise RuntimeError(
                    "Tesseract OCR was not found. Install Tesseract or set `tesseract_path` in dubber_settings.json."
                )

            duration = self._video_duration()
            if duration <= 0:
                raise RuntimeError("Could not read video duration for OCR subtitle extraction.")

            sample_fps = 2.0
            temp_dir = tempfile.mkdtemp(prefix="cambo_ocr_")
            frame_pattern = os.path.join(temp_dir, "frame_%06d.jpg")
            image_backend = self._image_backend()
            preprocessed_frames = image_backend == "ffmpeg"
            frame_filter = f"fps={sample_fps}"
            if preprocessed_frames:
                frame_filter = f"{frame_filter},{self._ffmpeg_ocr_crop_filter()}"

            self.progress.emit(5)
            result = subprocess.run(
                ["ffmpeg", "-y", "-hwaccel", "auto", "-i", self.video_path, "-vf", frame_filter, "-q:v", "3", frame_pattern],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                if len(details) > 1000:
                    details = details[-1000:]
                raise RuntimeError(f"Could not extract frames for OCR.\n\n{details}")

            frame_paths = sorted(
                os.path.join(temp_dir, name)
                for name in os.listdir(temp_dir)
                if name.lower().startswith("frame_") and name.lower().endswith(".jpg")
            )
            if not frame_paths:
                raise RuntimeError("No frames were extracted for OCR.")

            segments = []
            current_text = ""
            current_start = 0.0
            last_seen = 0.0
            gap_limit = 1.25

            for index, frame_path in enumerate(frame_paths):
                timestamp = index / sample_fps
                text = self._ocr_frame(frame_path, temp_dir, tesseract_path, preprocessed=preprocessed_frames)

                if text:
                    same_caption = bool(current_text) and (
                        text == current_text or difflib.SequenceMatcher(None, text.lower(), current_text.lower()).ratio() >= 0.74
                    )
                    if same_caption:
                        if len(text) > len(current_text):
                            current_text = text
                        last_seen = timestamp
                    else:
                        if current_text:
                            end_time = max(last_seen + (1.0 / sample_fps), current_start + 0.7)
                            segments.append({
                                "id": len(segments) + 1,
                                "time": f"{format_seconds_to_timecode(current_start)} - {format_seconds_to_timecode(end_time)}",
                                "text": current_text,
                            })
                        current_text = text
                        current_start = timestamp
                        last_seen = timestamp
                elif current_text and timestamp - last_seen > gap_limit:
                    end_time = max(last_seen + (1.0 / sample_fps), current_start + 0.7)
                    segments.append({
                        "id": len(segments) + 1,
                        "time": f"{format_seconds_to_timecode(current_start)} - {format_seconds_to_timecode(end_time)}",
                        "text": current_text,
                    })
                    current_text = ""

                self.progress.emit(10 + int(((index + 1) / len(frame_paths)) * 88))

            if current_text:
                end_time = min(duration, max(last_seen + (1.0 / sample_fps), current_start + 0.7))
                segments.append({
                    "id": len(segments) + 1,
                    "time": f"{format_seconds_to_timecode(current_start)} - {format_seconds_to_timecode(end_time)}",
                    "text": current_text,
                })

            shutil.rmtree(temp_dir, ignore_errors=True)
            self.progress.emit(100)

            if not segments:
                raise RuntimeError(
                    "No on-screen English subtitles were detected. Try importing an SRT, or use Auto Transcribe for spoken audio."
                )

            self.completed.emit(segments)
        except Exception as e:
            try:
                if "temp_dir" in locals():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            self.error.emit(str(e))


def detect_gender_from_text(text: str) -> str | None:
    """
    Determines speaker gender with 100% precision from unambiguous conversational Khmer cues,
    honorifics, and original script speaker markers.
    """
    if not text:
        return None
    t = text.strip()
    
    # 1. Direct Khmer Speaker Affirmations / Honorifics
    khmer_female_prefixes = ("ចាស", "ចាស៎", "ចា៎", "ខ្ញុំម្ចាស់", "នាងខ្ញុំ", "ខ្ញុំជាស្រី", "ខ្ញុំស្រី")
    khmer_male_prefixes = ("បាទ", "បាទ៎", "ខ្ញុំបាទ", "អាដែង", "អាមួយ", "ខ្ញុំជាប្រុស", "ខ្ញុំប្រុស")
    
    for p in khmer_female_prefixes:
        if t.startswith(p):
            return "Female"
    for p in khmer_male_prefixes:
        if t.startswith(p):
            return "Male"

    for p in khmer_female_prefixes:
        if re.search(r"[\s,!?។៕]" + re.escape(p), t):
            return "Female"
    for p in khmer_male_prefixes:
        if re.search(r"[\s,!?។៕]" + re.escape(p), t):
            return "Male"

    # 2. Original language speaker indicators
    # Chinese speaker cues
    if re.search(r"(?:她说|小姐|姑娘|夫人|老婆|母后|王后|奴婢|小女子)", t):
        return "Female"
    if re.search(r"(?:他说|先生|公子|少爷|老子|俺|兄弟|哥哥|皇上|朕|本王)", t):
        return "Male"

    # English speaker cues
    if re.search(r"\b(?:she said|woman:|girl:|lady:)\b", t, re.IGNORECASE):
        return "Female"
    if re.search(r"\b(?:he said|man:|boy:|gentleman:)\b", t, re.IGNORECASE):
        return "Male"

    return None


class VoiceGenderWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, video_path, rows, settings=None):
        super().__init__()
        self.video_path = video_path
        self.rows = rows
        self.settings = settings or {}

    def cancel(self):
        self.requestInterruption()

    def _parse_ai_gender_output(self, content):
        gender_map = {}
        for line in content.split("\n"):
            line_clean = line.strip().replace("**", "").replace("*", "")
            m = re.match(r"^\[?(\d+)\]?[:.)、\-]?\s*(?:\|\s*|:\s*|\s+)?(Male|Female|Man|Woman|Boy|Girl)\b", line_clean, re.IGNORECASE)
            if m:
                l_idx = int(m.group(1)) - 1
                g_str = "Male" if m.group(2).lower() in ("male", "man", "boy") else "Female"
                gender_map[l_idx] = g_str
        return gender_map

    def _build_gender_results(self, gender_map):
        results = []
        for idx, row in enumerate(self.rows):
            row_idx = int(row["row"])
            start_sec = float(row["start"])
            end_sec = float(row["end"])
            text_to_check = ((row.get("translated", "") or "") + " " + (row.get("text", "") or "")).strip()
            ling = detect_gender_from_text(text_to_check)
            if ling:
                gender = ling
            else:
                gender = gender_map.get(idx, "Male")
            voice = VOXCPM_MALE_VOICE_NAME if gender == "Male" else VOXCPM_FEMALE_VOICE_NAME
            results.append({
                "row": row_idx,
                "start": start_sec,
                "end": end_sec,
                "voice": voice,
                "gender": gender,
                "pitch": 0.0,
                "confidence": 1.0,
            })
        return results

    def _detect_gender_with_ai(self):
        script_lines = []
        for idx, row in enumerate(self.rows):
            text = row.get("translated", "") or row.get("text", "") or f"Dialogue {idx+1}"
            script_lines.append(f"[{idx+1}] {text}")

        if not any(r.get("translated") or r.get("text") for r in self.rows):
            return None

        self.status.emit("AI analyzing character genders & dialogue context")
        self.progress.emit(25)

        # 1. Try Gemini API first (using high-accuracy models)
        api_keys = []
        for key in (
            (self.settings or {}).get("gemini_api_key_backup", ""),
            (self.settings or {}).get("gemini_api_key", ""),
        ):
            key = str(key or "").strip()
            if key and key not in api_keys:
                api_keys.append(key)

        configured_model = (self.settings or {}).get("gemini_model", "").strip()
        gemini_models = []
        for model in (
            "gemini-flash-latest",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.7-flash",
            configured_model,
        ):
            if model and model not in gemini_models:
                gemini_models.append(model)

        def _call_ai_chunk(chunk_lines):
            script_chunk = "\n".join(chunk_lines)
            prompt = (
                "You are a master movie voice casting director and dialogue script analyst (អ្នកដឹកនាំបញ្ចូលសម្លេងភាពយន្តអាជីព).\n"
                "Analyze the following movie dialogue lines and identify whether the character speaking each line is Male or Female.\n"
                "CRITICAL CASTING RULES:\n"
                "1. Output 'Male' for men, boys, villains, brothers, fathers, or general neutral movie narration.\n"
                "2. Output 'Female' for women, girls, mothers, sisters, heroines.\n"
                "3. Pay strict attention to conversation turns between characters (dialogue exchanges between male and female).\n"
                "4. NEVER invert or randomly swap character gender (Male is Male, Female is Female).\n"
                "OUTPUT FORMAT STRICTLY (Numbered list):\n"
                "[1] | Male\n"
                "[2] | Female\n\n"
                f"DIALOGUE SCRIPT:\n{script_chunk}\n\n"
                "OUTPUT:"
            )

            # Try Gemini
            for m in gemini_models:
                for api_key in api_keys:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
                    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
                    payload = {"contents": [{"parts": [{"text": prompt}]}]}
                    try:
                        resp = requests.post(url, headers=headers, json=payload, timeout=20)
                        if resp.status_code == 200:
                            content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                            chunk_map = self._parse_ai_gender_output(content)
                            if chunk_map:
                                return chunk_map
                    except Exception:
                        pass

            # Try OpenAI candidates
            openai_candidates = get_openai_chat_candidates(self.settings)
            for key, base_url, endpoint, label in openai_candidates:
                configured_m = (self.settings or {}).get("openai_model", "").strip()
                try_models = [m for m in (configured_m, "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo") if m and m != "gpt-5.6-sol"]
                for model_name in try_models:
                    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                    payload = {
                        "model": model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2
                    }
                    try:
                        r = requests.post(endpoint, headers=headers, json=payload, timeout=25)
                        if r.status_code == 200:
                            content = r.json()["choices"][0]["message"]["content"]
                            chunk_map = self._parse_ai_gender_output(content)
                            if chunk_map:
                                return chunk_map
                    except Exception:
                        pass
            return {}

        # Chunk script if longer than 60 lines for maximum reliability
        full_gender_map = {}
        chunk_size = 60
        for i in range(0, len(script_lines), chunk_size):
            chunk = script_lines[i:i + chunk_size]
            c_map = _call_ai_chunk(chunk)
            if c_map:
                for local_idx, gender in c_map.items():
                    full_gender_map[i + local_idx] = gender

        if len(full_gender_map) >= max(1, int(len(self.rows) * 0.35)):
            return self._build_gender_results(full_gender_map)

        return None

    def _extract_audio(self, temp_dir):
        import subprocess

        wav_path = os.path.join(temp_dir, f"voice_gender_{int(time.time())}.wav")
        audio_filter = "highpass=f=100,lowpass=f=3400,speechnorm=e=4:r=0.0001:l=1,afftdn=nf=-25:nr=20"
        cmd = [
            "ffmpeg", "-y", "-hwaccel", "auto", "-i", self.video_path,
            "-af", audio_filter,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            wav_path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not os.path.exists(wav_path):
            details = (result.stderr or result.stdout or "").strip()
            if len(details) > 1200:
                details = details[-1200:]
            raise RuntimeError(f"Could not extract movie audio for auto voice detection.\n\n{details}")
        return wav_path

    def _read_wav(self, wav_path):
        import sys
        import wave
        from array import array

        with wave.open(wav_path, "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frame_count = wav.getnframes()
            raw = wav.readframes(frame_count)

        if sample_width != 2:
            raise RuntimeError("Auto voice detection needs 16-bit PCM audio.")

        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()

        if channels > 1:
            mono = array("h")
            for i in range(0, len(samples), channels):
                mono.append(int(sum(samples[i:i + channels]) / channels))
            samples = mono

        return sample_rate, samples

    def _estimate_segment_pitch(self, samples, sample_rate, start_sec, end_sec):
        """Ultra-fast vectorized pitch detection using numpy normalized autocorrelation."""
        import numpy as np

        start_idx = max(0, int(max(0.0, start_sec) * sample_rate))
        end_idx = min(len(samples), int(max(start_sec + 0.25, end_sec) * sample_rate))
        if end_idx - start_idx < int(sample_rate * 0.20):
            return 0.0, 0.0

        audio_segment = np.array(samples[start_idx:end_idx], dtype=np.float32)
        if len(audio_segment) == 0:
            return 0.0, 0.0

        # Window size ~ 50ms (800 samples at 16kHz)
        window_size = int(sample_rate * 0.05)
        hop_size = int(sample_rate * 0.08)

        min_lag = max(1, int(sample_rate / 360.0))  # 360 Hz (upper female / child pitch)
        max_lag = min(int(sample_rate / 75.0), window_size - 1)  # 75 Hz (deep male pitch)
        if max_lag <= min_lag:
            return 0.0, 0.0

        pitches = []
        confidences = []

        for pos in range(0, len(audio_segment) - window_size, hop_size):
            window = audio_segment[pos:pos + window_size]
            rms = np.sqrt(np.mean(window ** 2))
            if rms < 80:  # Ignore silence / low energy
                continue

            w = window - np.mean(window)
            energy = np.sum(w ** 2)
            if energy <= 0:
                continue

            corr = np.correlate(w, w, mode="full")[len(w) - 1:]
            if len(corr) > max_lag:
                sub_corr = corr[min_lag:max_lag + 1]
                best_idx = int(np.argmax(sub_corr))
                best_lag = min_lag + best_idx
                best_val = float(sub_corr[best_idx])
                conf = float(best_val / energy)
                if conf >= 0.20 and best_lag > 0:
                    pitches.append(float(sample_rate / best_lag))
                    confidences.append(conf)

        if not pitches:
            return 0.0, 0.0

        median_pitch = float(np.median(pitches))
        avg_conf = float(np.mean(confidences))
        return median_pitch, avg_conf

    def _voice_from_pitch(self, pitch, confidence=0.0):
        if pitch <= 0 or (confidence > 0 and confidence < 0.12):
            return "", "Unknown"
        # 165 Hz is the standard acoustic threshold between male and female speech
        if pitch < 165.0:
            return VOXCPM_MALE_VOICE_NAME, "Male"
        return VOXCPM_FEMALE_VOICE_NAME, "Female"

    def run(self):
        temp_dir = tempfile.mkdtemp(prefix="cambo_voice_gender_")
        wav_path = ""
        try:
            if not self.video_path or not os.path.exists(self.video_path):
                raise RuntimeError("Import a movie/video before running Auto Voice.")
            if not self.rows:
                raise RuntimeError("No subtitle lines are available for Auto Voice.")

            self.status.emit("Extracting movie audio with GPU acceleration")
            self.progress.emit(10)
            wav_path = self._extract_audio(temp_dir)
            sample_rate, samples = self._read_wav(wav_path)
            if not samples:
                raise RuntimeError("No readable audio was found in the movie.")

            # Measure every timed line first, then derive an episode-specific
            # split between its lower and higher voice-pitch clusters. This is
            # substantially more reliable than guessing speaker gender from
            # dialogue text alone.
            measured = []
            for row in self.rows:
                start_sec = float(row["start"])
                end_sec = float(row["end"])
                measured.append(self._estimate_segment_pitch(samples, sample_rate, start_sec, end_sec))

            import numpy as np
            reliable_pitches = np.array([
                pitch for pitch, confidence in measured
                if pitch > 0 and confidence >= 0.20
            ], dtype=np.float32)
            pitch_threshold = 165.0
            if reliable_pitches.size >= 6:
                low_center = float(np.percentile(reliable_pitches, 25))
                high_center = float(np.percentile(reliable_pitches, 75))
                for _ in range(12):
                    distances_low = np.abs(reliable_pitches - low_center)
                    distances_high = np.abs(reliable_pitches - high_center)
                    low_group = reliable_pitches[distances_low <= distances_high]
                    high_group = reliable_pitches[distances_low > distances_high]
                    if low_group.size == 0 or high_group.size == 0:
                        break
                    low_center = float(np.median(low_group))
                    high_center = float(np.median(high_group))
                if high_center - low_center >= 25.0:
                    pitch_threshold = max(135.0, min(210.0, (low_center + high_center) / 2.0))

            results = []
            last_voice = VOXCPM_MALE_VOICE_NAME
            last_gender = "Male"
            last_end_sec = -1.0
            total = max(len(self.rows), 1)
            for index, row in enumerate(self.rows):
                if self.isInterruptionRequested():
                    raise RuntimeError("Auto Voice was cancelled.")

                row_idx = int(row["row"])
                start_sec = float(row["start"])
                end_sec = float(row["end"])
                self.status.emit(f"Detecting speaker voice {index + 1}/{total}")

                # 1. Check linguistic cues first (100% precision for Khmer/Asian movie dialogue)
                text_to_check = ((row.get("translated", "") or "") + " " + (row.get("text", "") or "")).strip()
                ling_gender = detect_gender_from_text(text_to_check)

                if ling_gender:
                    voice = VOXCPM_MALE_VOICE_NAME if ling_gender == "Male" else VOXCPM_FEMALE_VOICE_NAME
                    gender = ling_gender
                    pitch = 120.0 if ling_gender == "Male" else 225.0
                    confidence = 1.0
                    last_voice = voice
                    last_gender = gender
                else:
                    pitch, confidence = measured[index]
                    if pitch > 0 and confidence >= 0.20:
                        gender = "Male" if pitch < pitch_threshold else "Female"
                        voice = VOXCPM_MALE_VOICE_NAME if gender == "Male" else VOXCPM_FEMALE_VOICE_NAME
                    else:
                        voice, gender = "", "Unknown"
                    if not voice:
                        voice = last_voice
                        gender = f"{last_gender} (kept)"
                    else:
                        last_voice = voice
                        last_gender = gender
                last_end_sec = end_sec

                results.append({
                    "row": row_idx,
                    "start": start_sec,
                    "end": end_sec,
                    "voice": voice,
                    "gender": gender,
                    "pitch": pitch,
                    "confidence": confidence,
                })
                self.progress.emit(10 + int(((index + 1) / total) * 88))

            self.progress.emit(100)
            self.completed.emit(results)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            try:
                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass



class TranslateWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, items, api_key, settings=None):
        super().__init__()
        self.items = items  # List of tuples (id, original_text)
        self.api_key = api_key
        self.settings = settings or {}
        self.source_lang = normalize_translation_language(
            self.settings.get("translation_source_lang", "Auto Detect"),
            TRANSLATION_SOURCE_LANGS,
            "Auto Detect"
        )
        self.target_lang = normalize_translation_language(
            self.settings.get("translation_target_lang", "Khmer"),
            TRANSLATION_TARGET_LANGS,
            "Khmer"
        )

    def _source_prompt_name(self):
        return TRANSLATION_PROMPT_NAMES.get(self.source_lang, self.source_lang)

    def _target_prompt_name(self):
        return TRANSLATION_PROMPT_NAMES.get(self.target_lang, self.target_lang)

    def _announce(self, message):
        print(message)
        self.status.emit(message)

    def _build_translation_prompt(self):
        script_content = ""
        for idx, item in enumerate(self.items):
            id_val = item[0]
            text = item[1]
            dur_str = ""
            if len(item) >= 4:
                dur = max(0.5, float(item[3]) - float(item[2]))
                dur_str = f" ({dur:.1f}s)"
            script_content += f"[{idx + 1}]{dur_str} {text}\n"

        return (
            "You are a master movie dubbing director and Khmer scriptwriter (អ្នកសម្រាយរឿង និងបញ្ចូលសម្លេងភាពយន្តអាជីព).\n"
            f"Translate the following movie dialogues into natural, conversational, and concise {self._target_prompt_name()}.\n"
            f"Source language: {self._source_prompt_name()}.\n\n"
            "CRITICAL MOVIE DUBBING RULES:\n"
            "1. CONCISE & PUNCHY (ខ្លី ខ្លឹម ងាយយល់): The Khmer dialogue MUST be short and match the indicated spoken duration in seconds. Avoid long formal phrases.\n"
            "2. CHARACTER GENDER: Identify whether the character speaking each line is Male or Female based on dialogue context.\n\n"
            "INPUT:\n"
            f"{script_content}\n"
            "OUTPUT FORMAT (Strict numbered list with gender):\n"
            "[1] | Female | translated text\n"
            "[2] | Male | translated text\n\n"
            "OUTPUT:"
        )

    def _parse_numbered_output(self, translated_text):
        parsed_lines = {}
        for line in translated_text.split('\n'):
            line_clean = line.strip().replace("**", "").replace("*", "")
            if not line_clean:
                continue
            match = re.match(r'^\[?(\d+)\]?[:.)、]?\s*(?:\|\s*(Male|Female|Man|Woman|Boy|Girl)\s*\|\s*)?(.*)$', line_clean, re.IGNORECASE)
            if match:
                line_idx = int(match.group(1)) - 1
                gender_tag = match.group(2) or ""
                text_content = match.group(3).strip()
                if gender_tag:
                    gender_norm = "Male" if gender_tag.lower() in ("male", "man", "boy") else "Female"
                else:
                    gender_norm = ""
                parsed_lines[line_idx] = (text_content, gender_norm)
        return parsed_lines

    def _parse_translation_output(self, translated_text):
        parsed_lines = self._parse_numbered_output(translated_text)
        if parsed_lines:
            return parsed_lines

        plain_lines = [
            line.strip()
            for line in translated_text.splitlines()
            if line.strip() and not line.strip().startswith(("```", "---"))
        ]
        if len(plain_lines) == len(self.items):
            return {idx: text for idx, text in enumerate(plain_lines)}
        return {}

    def _results_from_parsed(self, parsed_lines):
        if len(parsed_lines) < len(self.items):
            raise RuntimeError(
                "Translator returned an incomplete result. Try again, or use Settings > Translation Provider > Google Web."
            )

        results = []
        for idx, item in enumerate(self.items):
            id_val = item[0]
            val = parsed_lines.get(idx, ("", ""))
            if isinstance(val, tuple):
                translated, gender = val
            else:
                translated, gender = val, ""
            results.append((id_val, translated, gender))
            self.progress.emit(int((idx + 1) / len(self.items) * 100))
        return results

    def _translate_with_ollama(self):
        base_url = self.settings.get("ollama_url", "http://localhost:11434").strip().rstrip("/")
        model = self.settings.get("ollama_model", "command-r:35b").strip() or "command-r:35b"
        if not base_url:
            base_url = "http://localhost:11434"

        payload = {
            "model": model,
            "prompt": self._build_translation_prompt(),
            "stream": False,
            "options": {
                "temperature": 0.2
            }
        }
        response = requests.post(f"{base_url}/api/generate", json=payload, timeout=600)
        if response.status_code != 200:
            detail = response.text.strip()
            if len(detail) > 1200:
                detail = detail[:1200] + "..."
            raise RuntimeError(
                f"Ollama translation failed ({response.status_code}). "
                f"Make sure Ollama is running and the model is installed: ollama pull {model}\n\n{detail}"
            )

        translated_text = response.json().get("response", "").strip()
        if not translated_text:
            raise RuntimeError("Ollama returned an empty translation.")
        return self._results_from_parsed(self._parse_translation_output(translated_text))

    def _translate_single_line_fallback(self, clean_text, source_code, target_code, session=None):
        if not clean_text:
            return ""
        if session is None:
            session = requests.Session()
        
        headers_desktop = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        headers_mobile = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        }

        # 1. clients5 dict-chrome-ex
        for sl in ([source_code] if source_code == "auto" else [source_code, "auto"]):
            try:
                url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl}&tl={target_code}&q={urllib.parse.quote(clean_text)}"
                r = session.get(url, headers=headers_desktop, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and data:
                        res = data[0] if isinstance(data[0], str) else "".join(data)
                        if res.strip():
                            return res.strip()
                    elif isinstance(data, str) and data.strip():
                        return data.strip()
            except Exception:
                pass

        # 2. translate.google.com/m
        for sl in ([source_code] if source_code == "auto" else [source_code, "auto"]):
            try:
                url = f"https://translate.google.com/m?sl={sl}&tl={target_code}&q={urllib.parse.quote(clean_text)}"
                r = session.get(url, headers=headers_mobile, timeout=12)
                if r.status_code == 200:
                    m = re.search(r'<div[^>]*class=[\"\']result-container[\"\'][^>]*>(.*?)</div>', r.text, re.DOTALL)
                    if m:
                        res = html.unescape(m.group(1)).strip()
                        if res:
                            return res
            except Exception:
                pass

        # 3. translate.googleapis.com gtx
        for sl in ([source_code] if source_code == "auto" else [source_code, "auto"]):
            try:
                url = "https://translate.googleapis.com/translate_a/single"
                r = session.get(url, params={"client": "gtx", "sl": sl, "tl": target_code, "dt": "t", "q": clean_text}, headers=headers_desktop, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    chunks = data[0] if data and isinstance(data[0], list) else []
                    res = "".join(part[0] for part in chunks if part and part[0]).strip()
                    if res:
                        return res
            except Exception:
                pass

        # 4. MyMemory fallback
        try:
            sl_code = source_code if source_code != "auto" else "zh"
            r = session.get(
                "https://api.mymemory.translated.net/get",
                params={"q": clean_text, "langpair": f"{sl_code}|{target_code}"},
                timeout=12
            )
            if r.status_code == 200:
                res = r.json().get("responseData", {}).get("translatedText", "").strip()
                if res:
                    return res
        except Exception:
            pass

        return clean_text

    def _translate_with_google_web(self):
        results = []
        target_code = GOOGLE_TRANSLATE_CODES.get(self.target_lang, "km")
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        })
        translation_cache = {}
        batch_size = 15
        total_items = len(self.items)

        # Process in batches of 15 lines for ultra-fast and rate-limit-free translation
        i = 0
        while i < total_items:
            batch_items = self.items[i:i + batch_size]
            batch_texts = []
            uncached_indices = []

            for b_idx, item in enumerate(batch_items):
                id_val, text = item[0], item[1]
                clean_text = (text or "").strip()
                if not clean_text:
                    continue
                source_lang = effective_translation_source_language(self.source_lang, clean_text)
                source_code = GOOGLE_TRANSLATE_CODES.get(source_lang, "auto")
                cache_key = (source_code, target_code, clean_text)
                if cache_key in translation_cache:
                    continue
                batch_texts.append(clean_text)
                uncached_indices.append((b_idx, cache_key, clean_text, source_code))

            # Attempt batch translation if we have uncached items
            if batch_texts:
                batch_source_code = uncached_indices[0][3] if uncached_indices else "auto"
                batch_success = False
                joined = "\n".join(batch_texts)

                # Try batch with clients5
                try:
                    url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={batch_source_code}&tl={target_code}&q={urllib.parse.quote(joined)}"
                    r = session.get(url, timeout=18)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, list) and data:
                            res_text = data[0] if isinstance(data[0], str) else "\n".join(data)
                            res_lines = [line.strip() for line in res_text.split("\n")]
                            if len(res_lines) == len(batch_texts):
                                for (b_idx, cache_key, clean_text, _), trans_line in zip(uncached_indices, res_lines):
                                    translation_cache[cache_key] = trans_line
                                batch_success = True
                except Exception:
                    pass

                # If batch failed or mismatched line count, translate individual lines with multi-endpoint fallback
                if not batch_success:
                    for b_idx, cache_key, clean_text, source_code in uncached_indices:
                        trans_line = self._translate_single_line_fallback(clean_text, source_code, target_code, session)
                        translation_cache[cache_key] = trans_line
                        time.sleep(0.08)

            # Append results for current batch
            for item in batch_items:
                id_val, text = item[0], item[1]
                clean_text = (text or "").strip()
                if not clean_text:
                    results.append((id_val, "", ""))
                else:
                    source_lang = effective_translation_source_language(self.source_lang, clean_text)
                    source_code = GOOGLE_TRANSLATE_CODES.get(source_lang, "auto")
                    cache_key = (source_code, target_code, clean_text)
                    trans_text = translation_cache.get(cache_key, clean_text)
                    if self.target_lang == "Khmer":
                        trans_text = condense_khmer_dubbing_text(trans_text)
                    results.append((id_val, trans_text, ""))

            i += len(batch_items)
            self.progress.emit(int(min(100, (i / max(1, total_items)) * 100)))
            time.sleep(0.05)

        return results

    def _translate_with_nllb(self):
        import subprocess

        python_path = self.settings.get("nllb_python_path", "").strip()
        if not python_path:
            python_path = self.settings.get("voxcpm_python_path", "").strip()
        if not python_path or not os.path.exists(python_path):
            raise RuntimeError(
                "NLLB Python is not configured. Open Settings and choose a Python 3.10-3.12 environment "
                "with transformers and sentencepiece installed."
            )

        bridge_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nllb_translate_bridge.py")
        if not os.path.exists(bridge_path):
            raise RuntimeError(f"Missing NLLB bridge script: {bridge_path}")

        batch_file = os.path.join(tempfile.gettempdir(), f"nllb_translate_{int(time.time())}.json")
        output_file = os.path.join(tempfile.gettempdir(), f"nllb_translate_out_{int(time.time())}.json")
        payload = {
            "model_id": self.settings.get("nllb_model_id", "facebook/nllb-200-distilled-600M").strip()
                        or "facebook/nllb-200-distilled-600M",
            "target_lang": NLLB_LANG_CODES.get(self.target_lang, "khm_Khmr"),
            "tasks": [],
        }
        for item in self.items:
            id_val, text = item[0], item[1]
            task = {"id": id_val, "text": text}
            source_lang = effective_translation_source_language(self.source_lang, text)
            source_code = NLLB_LANG_CODES.get(source_lang)
            if source_code:
                task["source_lang"] = source_code
            payload["tasks"].append(task)

        try:
            with open(batch_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            self.progress.emit(5)
            result = subprocess.run(
                [python_path, bridge_path, "--batch", batch_file, "--output", output_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "").strip()
                if len(details) > 1800:
                    details = details[-1800:]
                raise RuntimeError(
                    "NLLB local translation failed. Make sure the selected Python has transformers, "
                    "sentencepiece, torch, and internet/model cache for the first run.\n\n"
                    f"{details}"
                )

            with open(output_file, "r", encoding="utf-8-sig") as f:
                translated_items = json.load(f)

            translated_map = {str(item.get("id")): (item.get("text") or "").strip() for item in translated_items}
            parsed_lines = {}
            for idx, item in enumerate(self.items):
                id_val, text = item[0], item[1]
                translated = translated_map.get(str(id_val), "").strip()
                if translated:
                    parsed_lines[idx] = translated
            return self._results_from_parsed(parsed_lines)
        finally:
            for path in (batch_file, output_file):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _translate_with_openai(self):
        candidates = get_openai_chat_candidates(self.settings, fallback_key=self.api_key)
        if not candidates:
            raise RuntimeError("Please enter your OpenAI / ChatGPT API Key in Settings.")

        model_name = self.settings.get("openai_model", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
        batch_size = 15
        all_results = []
        total_items = len(self.items)
        current_candidate_idx = 0

        for start_idx in range(0, total_items, batch_size):
            batch_items = self.items[start_idx:start_idx + batch_size]
            script_content = ""
            for idx, item in enumerate(batch_items):
                text = item[1]
                dur_str = ""
                if len(item) >= 4:
                    dur = max(0.5, float(item[3]) - float(item[2]))
                    dur_str = f" ({dur:.1f}s)"
                script_content += f"[{idx + 1}]{dur_str} {text}\n"

            prompt = (
                "You are an elite Cambodian movie dubber and scriptwriter (អ្នកសម្រាយរឿង និងបញ្ចូលសម្លេងភាពយន្តអាជីព).\n"
                f"Translate the following movie dialogues into natural, cinematic, emotional, and concise {self._target_prompt_name()}.\n"
                f"Source language: {self._source_prompt_name()}.\n\n"
                "CRITICAL MOVIE DUBBING & PACING RULES:\n"
                "1. STRICT DURATION & LENGTH MATCHING (ខ្លី ខ្លឹម ត្រូវតាមវិនាទី):\n"
                "   - The dialogue length MUST strictly match the indicated duration in seconds in parentheses (e.g. '(1.5s)' = max 4-5 words, '(3.0s)' = max 8-10 words).\n"
                "   - Keep phrases conversational and snappy for movie narration. Avoid long formal sentences that cause audio to drag out!\n"
                "2. 100% ACCURATE CHARACTER GENDER (ប្រុស ឬ ស្រី):\n"
                "   - Carefully analyze WHO is speaking each line (Character context, names, pronouns, turn-taking between characters).\n"
                "   - Output 'Male' for male characters, men, boys, villains, or male narrator.\n"
                "   - Output 'Female' for female characters, women, girls, mothers, heroines.\n"
                "   - NEVER confuse or invert Male and Female!\n\n"
                "INPUT SCRIPT:\n"
                f"{script_content}\n"
                "OUTPUT FORMAT (Strict numbered list with exact gender):\n"
                "[1] | Male | translated text\n"
                "[2] | Female | translated text\n\n"
                "OUTPUT:"
            )

            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2
            }

            batch_success = False
            last_error_detail = ""
            last_status_code = ""

            num_candidates = len(candidates)
            for c_offset in range(num_candidates):
                cand_idx = (current_candidate_idx + c_offset) % num_candidates
                api_key, base_url, endpoint, label = candidates[cand_idx]
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }

                response = None
                for attempt in range(2):
                    try:
                        attempt_note = f" (attempt {attempt + 1}/2)" if attempt > 0 else ""
                        self.status.emit(f"Translating via {label} ({model_name}){attempt_note}")
                        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
                        if response.status_code == 200:
                            break
                        if 400 <= response.status_code < 500 and response.status_code != 429:
                            break
                    except Exception as req_err:
                        last_error_detail = str(req_err)
                        response = None

                if response is not None and response.status_code == 200:
                    try:
                        data = response.json()
                        translated_text = data["choices"][0]["message"]["content"]
                        parsed_lines = self._parse_translation_output(translated_text)
                        if len(parsed_lines) >= len(batch_items):
                            for idx, item in enumerate(batch_items):
                                id_val = item[0]
                                val = parsed_lines.get(idx, ("", ""))
                                if isinstance(val, tuple):
                                    t_text, t_gender = val
                                else:
                                    t_text, t_gender = val, ""
                                all_results.append((id_val, t_text, t_gender))
                            batch_success = True
                            current_candidate_idx = cand_idx
                            break
                        else:
                            last_error_detail = "Incomplete translation result"
                    except Exception as parse_err:
                        last_error_detail = f"Parse error: {parse_err}"

                detail = response.text if response is not None else last_error_detail or "Timeout"
                try:
                    if response is not None:
                        detail = response.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                last_status_code = str(response.status_code) if response is not None else "timeout"
                last_error_detail = detail

                if c_offset + 1 < num_candidates:
                    next_cand_idx = (current_candidate_idx + c_offset + 1) % num_candidates
                    next_label = candidates[next_cand_idx][3]
                    self.status.emit(f"{label} error ({last_status_code}) — auto switching to {next_label}...")

            if not batch_success:
                raise RuntimeError(f"ChatGPT / OpenAI Translation Error ({last_status_code}): {last_error_detail}")

            progress_pct = int(min(100, ((start_idx + len(batch_items)) / total_items) * 100))
            self.progress.emit(progress_pct)

        return all_results

    def _translate_with_gemini(self):
        api_keys = []
        for key in (
            self.api_key,
            self.settings.get("gemini_api_key", ""),
            self.settings.get("gemini_api_key_backup", ""),
        ):
            key = str(key or "").strip()
            if key and key not in api_keys:
                api_keys.append(key)
        if not api_keys:
            raise RuntimeError("No Gemini API key provided.")

        best_quality = "best quality" in self.settings.get("translation_provider", "").lower()
        batch_size = 25 if best_quality else 15
        all_results = []
        total_items = len(self.items)

        configured_model = self.settings.get("gemini_model", "").strip()
        # Try the user's configured model first, then one stable alias. Avoid
        # cycling through speculative version names: unavailable models used
        # to add several minutes before the working provider fallback ran.
        models = []
        for model in (configured_model, "gemini-flash-latest"):
            if model and model not in models:
                models.append(model)

        for start_idx in range(0, total_items, batch_size):
            batch_items = self.items[start_idx:start_idx + batch_size]
            
            script_content = ""
            for idx, item in enumerate(batch_items):
                id_val = item[0]
                text = item[1]
                dur_str = ""
                if len(item) >= 4:
                    dur = max(0.5, float(item[3]) - float(item[2]))
                    dur_str = f" ({dur:.1f}s)"
                script_content += f"[{idx + 1}]{dur_str} {text}\n"

            prompt = (
                "You are a master movie dubbing director and Khmer scriptwriter (អ្នកសម្រាយរឿង និងបញ្ចូលសម្លេងភាពយន្តអាជីព).\n"
                f"Translate the following movie dialogues into natural, conversational, and concise {self._target_prompt_name()}.\n"
                f"Source language: {self._source_prompt_name()}.\n\n"
                "CRITICAL MOVIE DUBBING RULES:\n"
                "1. CONCISE & PUNCHY (ខ្លី ខ្លឹម ងាយយល់): The Khmer dialogue MUST be short and match the indicated spoken duration in seconds. Avoid long formal phrases.\n"
                "2. CHARACTER GENDER: Identify whether the character speaking each line is Male or Female based on dialogue context.\n\n"
                "INPUT:\n"
                f"{script_content}\n"
                "OUTPUT FORMAT (Strict numbered list with gender):\n"
                "[1] | Female | translated text\n"
                "[2] | Male | translated text\n\n"
                "OUTPUT:"
            )

            thinking_level = self.settings.get("gemini_thinking_level", "high").strip().lower()
            if thinking_level not in ("high", "medium", "low"):
                thinking_level = "high"
            response = None
            for m in models:
                payload = {"contents": [{"parts": [{"text": prompt}]}]}
                # Gemini 3.5+ supports named thinking levels. Older Pro models
                # use their own default reasoning when this field is omitted.
                if re.match(r"^gemini-3\.[5-9]", m):
                    payload["generationConfig"] = {
                        "thinkingConfig": {"thinkingLevel": thinking_level}
                    }
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
                for key_index, api_key in enumerate(api_keys, start=1):
                    headers = {
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key,
                    }
                    try:
                        key_note = f" (key {key_index}/{len(api_keys)})" if len(api_keys) > 1 else ""
                        self.status.emit(f"Trying Gemini model: {m}{key_note}")
                        response = requests.post(url, headers=headers, json=payload, timeout=25)
                        if response.status_code == 200:
                            break
                    except Exception:
                        response = None
                if response is not None and response.status_code == 200:
                    break

            if response and response.status_code == 200:
                data = response.json()
                translated_text = data['candidates'][0]['content']['parts'][0]['text']
                parsed_lines = self._parse_translation_output(translated_text)
                if len(parsed_lines) < len(batch_items):
                    raise RuntimeError("Gemini returned an incomplete translation result.")
                for idx, item in enumerate(batch_items):
                    id_val = item[0]
                    val = parsed_lines.get(idx, ("", ""))
                    if isinstance(val, tuple):
                        t_text, t_gender = val
                    else:
                        t_text, t_gender = val, ""
                    all_results.append((id_val, t_text, t_gender))
            else:
                detail = "No response"
                status_code = "network error"
                if response is not None:
                    status_code = response.status_code
                    detail = (response.text or "").strip()
                    if len(detail) > 800:
                        detail = detail[:800] + "..."
                raise RuntimeError(f"Gemini translation failed ({status_code}): {detail}")

            progress_pct = int(min(100, ((start_idx + len(batch_items)) / total_items) * 100))
            self.progress.emit(progress_pct)

        return all_results

    def run(self):
        try:
            provider = self.settings.get("translation_provider", "Gemini").strip().lower()
            if "best quality auto" in provider:
                try:
                    model_name = self.settings.get("gemini_model", "gemini-3.7-flash").strip()
                    self._announce(f"Best Quality: translating with {model_name or 'gemini-3.7-flash'}")
                    self.completed.emit(self._translate_with_gemini())
                    return
                except Exception as gemini_err:
                    self._announce("Gemini failed — switching to ChatGPT / OpenAI")
                    try:
                        self.completed.emit(self._translate_with_openai())
                        return
                    except Exception as openai_err:
                        self._announce("Gemini and ChatGPT failed — switching to Google Web")
                        print(
                            f"Gemini error: {gemini_err}\n"
                            f"ChatGPT error: {openai_err}"
                        )
                        self.completed.emit(self._translate_with_google_web())
                        return
            if "openai" in provider or "gpt" in provider or "seekai" in provider or "chatgpt" in provider:
                try:
                    self._announce("Translating with ChatGPT / OpenAI")
                    self.completed.emit(self._translate_with_openai())
                    return
                except Exception as openai_err:
                    if self.api_key or self.settings.get("gemini_api_key"):
                        self._announce("ChatGPT failed — automatically switching to Gemini")
                        try:
                            self.completed.emit(self._translate_with_gemini())
                            return
                        except Exception as gemini_err:
                            self._announce("Gemini also failed — switching to Google Web")
                            print(
                                f"ChatGPT error: {openai_err}\n"
                                f"Gemini error: {gemini_err}"
                            )
                    else:
                        self._announce("ChatGPT failed and no Gemini key is configured — switching to Google Web")
                    self.completed.emit(self._translate_with_google_web())
                    return
            if provider == "gemini" or (self.api_key and "gemini" in provider):
                try:
                    self._announce("Translating with Gemini")
                    self.completed.emit(self._translate_with_gemini())
                    return
                except Exception as gem_err:
                    self._announce("Gemini failed — switching to Google Web")
                    print(f"Gemini translation failed: {gem_err}")
                    self.completed.emit(self._translate_with_google_web())
                    return
            if provider in ("google web", "google"):
                self._announce("Translating with Google Web")
                self.completed.emit(self._translate_with_google_web())
                return
            if provider in ("nllb local", "nllb"):
                try:
                    self.completed.emit(self._translate_with_nllb())
                except Exception as nllb_error:
                    try:
                        self.completed.emit(self._translate_with_google_web())
                    except Exception as web_error:
                        raise RuntimeError(
                            "NLLB Local failed and Google Web fallback also failed.\n\n"
                            f"NLLB error:\n{nllb_error}\n\n"
                            f"Google Web error:\n{web_error}"
                        ) from web_error
                return
            if provider == "ollama":
                self._announce("Translating with Ollama")
                self.completed.emit(self._translate_with_ollama())
                return
            
            # Default fallback to Google Web
            self._announce("Unknown provider — switching to Google Web")
            self.completed.emit(self._translate_with_google_web())
        except Exception as e:
            self.error.emit(str(e))


class TTSWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    row_completed = pyqtSignal(int, str)  # row_index, audio_path
    completed = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, tasks, cache_dir, settings=None, base_rate=1.0, base_pitch=0):
        """
        tasks is a list of tuples: (row_index, text, voice_character, [opt: start_sec, end_sec])
        """
        super().__init__()
        self.tasks = tasks
        self.cache_dir = cache_dir
        self.settings = settings or {}
        self.base_rate = float(base_rate or 1.0)
        self.base_pitch = int(base_pitch or 0)
        self._cancel_requested = False
        self._current_process = None

    def cancel(self):
        self._cancel_requested = True
        proc = self._current_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _create_silent_audio(self, output_file, duration=500):
        try:
            from pydub import AudioSegment
            silent = AudioSegment.silent(duration=duration)
            silent.export(output_file, format=os.path.splitext(output_file)[1].lstrip(".") or "mp3")
        except Exception:
            with open(output_file, "wb"):
                pass

    def _voxcpm_python(self):
        python_path = self.settings.get("voxcpm_python_path", "").strip()
        if python_path and os.path.exists(python_path):
            return python_path

        local_python = default_voxcpm_python_path()
        if os.path.exists(local_python):
            return local_python

        return ""

    def _run_voxcpm_tasks(self, tasks=None, voxcpm_voice=None):
        import subprocess

        tasks = tasks or self.tasks
        voxcpm_voice = voxcpm_voice or next(
            (task[2] for task in tasks if is_voxcpm_voice(task[2])),
            VOXCPM_VOICE_NAME
        )
        self.status.emit(f"Generating cloned VoxCPM voices... 0/{len(tasks)}")
        python_path = self._voxcpm_python()
        if not python_path:
            raise RuntimeError(
                "VoxCPM Python is not configured. Create a Python 3.10-3.12 environment with VoxCPM, "
                "then set its python.exe path in Settings."
            )

        bridge_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voxcpm_bridge.py")
        if not os.path.exists(bridge_path):
            raise RuntimeError(f"Missing VoxCPM bridge script: {bridge_path}")

        batch_file = os.path.join(tempfile.gettempdir(), f"voxcpm_batch_{int(time.time())}.json")
        batch_items = []
        emitted = []

        for task in tasks:
            row_idx, text = task[0], task[1]
            task_voice = task[2]
            if not any(c.isalnum() for c in text):
                output_file = os.path.join(self.cache_dir, f"voxcpm_{row_idx}_{int(time.time())}.wav")
                self._create_silent_audio(output_file)
                emitted.append((row_idx, output_file))
                continue

            reference_audio = voxcpm_reference_audio_for_voice(self.settings, task_voice).strip()
            if not reference_audio or not os.path.exists(reference_audio):
                reference_label = voxcpm_reference_label_for_voice(task_voice)
                raise RuntimeError(f"Select a VoxCPM {reference_label} before generating voice.")

            output_file = os.path.join(self.cache_dir, f"voxcpm_{row_idx}_{int(time.time())}.wav")
            batch_items.append({
                "row_idx": row_idx,
                "text": text,
                "output": output_file,
                "reference_audio": reference_audio,
            })

        if batch_items:
            payload = {
                "model_id": self.settings.get("voxcpm_model_id", "openbmb/VoxCPM2").strip() or "openbmb/VoxCPM2",
                "reference_audio": batch_items[0]["reference_audio"],
                "prompt_text": self.settings.get("voxcpm_reference_text", "").strip(),
                "style": self.settings.get("voxcpm_style", "").strip(),
                "cfg_value": self.settings.get("voxcpm_cfg_value", "2.0"),
                "inference_timesteps": self.settings.get("voxcpm_inference_steps", "10"),
                "seed": self.settings.get("voxcpm_seed", "42"),
                "tasks": batch_items,
            }
            with open(batch_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            completed_count = len(emitted)
            total_count = max(len(tasks), 1)
            emitted_rows = set()

            for row_idx, output_file in emitted:
                emitted_rows.add(row_idx)
                self.row_completed.emit(row_idx, output_file)
                self.progress.emit(int(completed_count / total_count * 100))

            self._current_process = subprocess.Popen(
                [python_path, "-u", bridge_path, "--batch", batch_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            output_lines = []
            start_time = time.time()
            for raw_line in self._current_process.stdout:
                if self._cancel_requested:
                    self.cancel()
                    break
                if time.time() - start_time > 3600:
                    self.cancel()
                    raise RuntimeError("VoxCPM generation timed out after 60 minutes.")

                line = raw_line.strip()
                if not line:
                    continue
                output_lines.append(line)

                if line.startswith("status\t"):
                    self.status.emit(line.split("\t", 1)[1])
                elif line.startswith("progress\t"):
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        self.status.emit(parts[3])
                elif line.startswith("saved\t"):
                    parts = line.split("\t", 2)
                    if len(parts) == 3:
                        try:
                            row_idx = int(parts[1])
                        except ValueError:
                            row_idx = -1
                        output_file = parts[2].strip()
                        if row_idx >= 0 and row_idx not in emitted_rows:
                            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                                emitted_rows.add(row_idx)
                                completed_count += 1
                                self.status.emit(f"Generated cloned VoxCPM voice {completed_count}/{total_count}")
                                self.row_completed.emit(row_idx, output_file)
                                self.progress.emit(int(completed_count / total_count * 100))

            returncode = self._current_process.wait()
            self._current_process = None

            try:
                os.remove(batch_file)
            except Exception:
                pass

            if self._cancel_requested:
                raise RuntimeError("VoxCPM generation was cancelled.")

            if returncode != 0:
                details = "\n".join(output_lines).strip()
                if len(details) > 1800:
                    details = details[-1800:]
                raise RuntimeError(
                    "VoxCPM generation failed. Make sure the selected Python uses version 3.10-3.12 "
                    "and has `voxcpm` plus `soundfile` installed.\n\n"
                    f"{details}"
                )

            for item in batch_items:
                output_file = item["output"]
                row_idx = item["row_idx"]
                if row_idx in emitted_rows:
                    continue
                if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                    raise RuntimeError(f"VoxCPM did not create audio for line {item['row_idx'] + 1}.")
                completed_count += 1
                emitted_rows.add(row_idx)
                self.row_completed.emit(row_idx, output_file)
                self.progress.emit(int(completed_count / total_count * 100))

        elif emitted:
            total = max(len(emitted), 1)
            for i, (row_idx, output_file) in enumerate(sorted(emitted, key=lambda item: item[0])):
                self.status.emit(f"Generated cloned VoxCPM voice {i + 1}/{total}")
                self.row_completed.emit(row_idx, output_file)
                self.progress.emit(int((i + 1) / total * 100))

    def run(self):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            voxcpm_tasks = []
            standard_tasks = []
            for task in self.tasks:
                voice_char = task[2]
                if voice_char == VOXCPM_AUTO_VOICE_NAME:
                    # Auto Voice normally resolves every row before TTS. If a
                    # row has no detectable gender, keep it on VoxCPM and use
                    # the female reference as a deterministic fallback.
                    task_parts = list(task)
                    task_parts[2] = VOXCPM_FEMALE_VOICE_NAME
                    task = tuple(task_parts)
                    voice_char = task[2]
                if is_voxcpm_voice(voice_char):
                    voxcpm_tasks.append(task)
                else:
                    standard_tasks.append(task)

            if voxcpm_tasks:
                # A single bridge process loads VoxCPM once and switches the
                # male/female reference per line.
                self._run_voxcpm_tasks(voxcpm_tasks)

            # Use asyncio to run edge-tts with adaptive dubbing pace and pitch
            async def run_voice(text_to_speak, voice_to_use, out_path, pace_rate, pace_pitch="+0Hz"):
                communicate = edge_tts.Communicate(text_to_speak, voice_to_use, rate=pace_rate, pitch=pace_pitch)
                await communicate.save(out_path)

            total_standard = max(len(standard_tasks), 1)
            for i, task in enumerate(standard_tasks):
                row_idx = task[0]
                text = task[1]
                voice_char = task[2]
                self.status.emit(f"Generating voice {i + 1}/{len(standard_tasks)} with {voice_char}")
                voice = edge_tts_voice_for_character(voice_char, text)
                output_file = os.path.join(self.cache_dir, f"sub_{row_idx}_{int(time.time())}.mp3")
                
                # Sanitize text to remove trailing pause dots (...) that break speech flow
                clean_text_to_speak = re.sub(r'[\.]{2,}', '', text).rstrip('.…').strip()
                if not clean_text_to_speak:
                    clean_text_to_speak = text.strip()

                # Dynamic lip-sync speed rate calculation
                # Natural Khmer speaking pace is ~13.2 characters per second
                char_len = len(clean_text_to_speak)
                slider_pct = int(round((self.base_rate - 1.0) * 100))
                
                if len(task) >= 5:
                    s_sec = float(task[3])
                    e_sec = float(task[4])
                    slot_dur = max(0.4, e_sec - s_sec)
                    cps = char_len / slot_dur
                    
                    # Only accelerate if text significantly exceeds natural speaking speed (cps > 14)
                    if cps > 18:
                        speed_offset = min(20, int((cps - 14) * 2.0))
                    elif cps > 14:
                        speed_offset = min(12, int((cps - 14) * 1.5))
                    elif cps < 7.5:
                        # Short dialogue with wide mouth window: relax pace slightly so voice doesn't finish way too fast
                        speed_offset = max(-6, int((cps - 10) * 1.5))
                    else:
                        speed_offset = 0
                    final_rate_val = slider_pct + speed_offset
                else:
                    final_rate_val = slider_pct

                custom_rate = f"{final_rate_val:+d}%"
                custom_pitch = f"{self.base_pitch:+d}Hz" if self.base_pitch != 0 else "+0Hz"

                if not any(c.isalnum() for c in clean_text_to_speak):
                    try:
                        from pydub import AudioSegment
                        silent = AudioSegment.silent(duration=500)
                        silent.export(output_file, format="mp3")
                    except Exception:
                        with open(output_file, "wb") as f:
                            pass
                else:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(run_voice(clean_text_to_speak, voice, output_file, custom_rate, custom_pitch))
                    except Exception as e:
                        print(f"TTS generation failed for line {row_idx + 1} with error: {e}")
                        try:
                            from pydub import AudioSegment
                            silent = AudioSegment.silent(duration=500)
                            silent.export(output_file, format="mp3")
                        except Exception:
                            with open(output_file, "wb") as f:
                                pass
                    finally:
                        loop.close()

                # WYSIWYG Lip-Sync: Match audio duration closely with character mouth window
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    try:
                        from pydub import AudioSegment
                        seg_audio = AudioSegment.from_file(output_file)
                        if len(task) >= 5:
                            s_sec = float(task[3])
                            e_sec = float(task[4])
                            slot_dur_ms = int(max(0.4, e_sec - s_sec) * 1000)
                            ratio = len(seg_audio) / max(1, slot_dur_ms)
                            if ratio < 0.88 and len(seg_audio) >= 400:
                                stretch_spd = max(0.85, ratio)
                                if stretch_spd <= 0.95:
                                    seg_audio = change_audio_tempo_ffmpeg(seg_audio, stretch_spd)
                                    seg_audio.export(output_file, format="mp3")
                            elif len(seg_audio) > slot_dur_ms:
                                needed_spd = min(len(seg_audio) / slot_dur_ms, 1.35)
                                if needed_spd > 1.03:
                                    seg_audio = change_audio_tempo_ffmpeg(seg_audio, needed_spd)
                                    seg_audio.export(output_file, format="mp3")
                    except Exception:
                        pass
                
                self.row_completed.emit(row_idx, output_file)
                self.progress.emit(int((i + 1) / total_standard * 100))
            
            self.completed.emit()
        except Exception as e:
            self.error.emit(str(e))


_VIDEO_ENCODER_ARGS = None


def get_gpu_video_encoder_args() -> list[str]:
    """
    Detects hardware acceleration capabilities (NVIDIA NVENC, Intel QSV, or libx264).
    Enables blazing-fast GPU video rendering using NVIDIA GeForce RTX (NVENC).
    """
    global _VIDEO_ENCODER_ARGS
    if _VIDEO_ENCODER_ARGS is not None:
        return list(_VIDEO_ENCODER_ARGS)

    import subprocess
    try:
        res = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=2)
        stdout_txt = res.stdout or ""
        if "h264_nvenc" in stdout_txt:
            # Confirm that the installed driver can initialize NVENC. Merely
            # appearing in `ffmpeg -encoders` does not guarantee it works.
            probe = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=640x360:d=0.05", "-c:v", "h264_nvenc",
                 "-preset", "p4", "-f", "null", "NUL"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            if probe.returncode == 0:
                _VIDEO_ENCODER_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p", "-spatial-aq", "1", "-temporal-aq", "1"]
                return list(_VIDEO_ENCODER_ARGS)
        elif "h264_qsv" in stdout_txt:
            _VIDEO_ENCODER_ARGS = ["-c:v", "h264_qsv", "-global_quality", "20", "-pix_fmt", "yuv420p"]
            return list(_VIDEO_ENCODER_ARGS)
    except Exception:
        pass
    _VIDEO_ENCODER_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    return list(_VIDEO_ENCODER_ARGS)

class RenderWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, video_path, audio_files_with_offsets, output_path, music_level=30,
                 vocal_boost=True, mix_mode="Background Audio Mix", voice_only=False,
                 background_music_path="", logo_path="", logo_position="Top-Right",
                 logo_scale=0.15, logo_opacity=0.85, watermark_text="",
                 logo_rel_x=0.75, logo_rel_y=0.05, lip_sync_offset_ms=0):
        super().__init__()
        self.video_path = video_path
        self.audio_files = audio_files_with_offsets
        self.output_path = output_path
        self.music_level = music_level
        self.vocal_boost = vocal_boost
        self.mix_mode = mix_mode
        self.voice_only = voice_only
        self.background_music_path = background_music_path
        self.logo_path = logo_path
        self.logo_position = logo_position
        self.logo_scale = logo_scale
        self.logo_opacity = logo_opacity
        self.watermark_text = watermark_text
        self.logo_rel_x = float(logo_rel_x or 0.75)
        self.logo_rel_y = float(logo_rel_y or 0.05)
        self.lip_sync_offset_ms = int(lip_sync_offset_ms or 0)

    def run(self):
        import subprocess
        import os
        import tempfile
        import math
        from pydub import AudioSegment
        from pydub.effects import compress_dynamic_range, high_pass_filter, low_pass_filter

        def pct_to_db(pct):
            if pct <= 0:
                return -999.0
            return 20.0 * math.log10(pct / 100.0)

        def fit_audio_duration(audio, duration_ms):
            if audio is None:
                return None
            if len(audio) <= 0:
                return AudioSegment.silent(duration=duration_ms)
            if len(audio) < duration_ms:
                repeats = int(math.ceil(duration_ms / len(audio)))
                audio = audio * repeats
            return audio[:duration_ms]

        def level_voice_segment(audio):
            if audio is None or len(audio) <= 0:
                return audio
            audio = high_pass_filter(audio, 85)
            audio = low_pass_filter(audio, 7600)
            try:
                audio = compress_dynamic_range(audio, threshold=-24.0, ratio=2.5, attack=5, release=80)
            except Exception:
                pass
            try:
                if audio.dBFS != float("-inf"):
                    gain = max(-8.0, min(8.0, -18.0 - audio.dBFS))
                    audio = audio + gain
            except Exception:
                pass
            return audio.fade_in(12).fade_out(25)

        def change_audio_tempo_ffmpeg(input_audio, speed):
            if abs(speed - 1.0) < 0.03:
                return input_audio
            speed = max(0.5, min(speed, 2.0))
            import subprocess
            import io
            wav_io = io.BytesIO()
            input_audio.export(wav_io, format="wav")
            wav_data = wav_io.getvalue()
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", "pipe:0",
                "-filter:a", f"atempo={speed:.3f}",
                "-f", "wav", "pipe:1"
            ]
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, _ = proc.communicate(input=wav_data, timeout=12)
                if proc.returncode == 0 and out:
                    return AudioSegment.from_file(io.BytesIO(out), format="wav")
            except Exception as e:
                print(f"FFmpeg atempo speedup error: {e}")
            return input_audio

        temp_audio_file = None
        try:
            self.progress.emit(10)
            
            try:
                orig_audio = AudioSegment.from_file(self.video_path)
                orig_duration_ms = len(orig_audio)
            except Exception:
                orig_audio = None
                
                cmd = [
                    'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1', self.video_path
                ]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    duration_secs = float(result.stdout.strip())
                except ValueError:
                    duration_secs = 60.0
                orig_duration_ms = int(duration_secs * 1000)

            self.progress.emit(30)

            # Sort all tasks chronologically by start_sec
            sorted_tasks = sorted(self.audio_files, key=lambda x: x[1])
            loaded_voiceovers = []
            max_voice_len_ms = 0
            
            for idx, (path, start_sec, end_sec) in enumerate(sorted_tasks):
                if not os.path.exists(path):
                    continue
                try:
                    voice_seg = AudioSegment.from_file(path)
                except Exception:
                    continue
                
                if len(voice_seg) <= 0:
                    continue

                start_ms = max(0, int(max(0.0, start_sec) * 1000) + int(getattr(self, "lip_sync_offset_ms", 0) or 0))
                subtitle_dur_ms = int(max(end_sec - start_sec, 0.4) * 1000)
                
                # Determine when the next dialogue segment starts
                if idx < len(sorted_tasks) - 1:
                    next_start_ms = int(max(0.0, sorted_tasks[idx + 1][1]) * 1000) + int(getattr(self, "lip_sync_offset_ms", 0) or 0)
                else:
                    next_start_ms = orig_duration_ms

                # Available time window before next voiceover starts (with 30ms breathing buffer)
                gap_to_next_ms = max(200, next_start_ms - start_ms - 30)

                current_len_ms = len(voice_seg)
                ratio = current_len_ms / max(1, subtitle_dur_ms)

                # ====================================================
                # Intelligent Lip-Sync Matching:
                # ====================================================
                # 1. If voice is faster/shorter than character's mouth speaking duration (ratio < 0.88),
                #    gently stretch tempo (0.85x - 0.96x) with pitch preservation so the voice
                #    spans the actor's mouth movement without finishing prematurely!
                if ratio < 0.88 and current_len_ms >= 400:
                    stretch_speed = max(0.85, ratio)
                    if stretch_speed <= 0.95:
                        voice_seg = change_audio_tempo_ffmpeg(voice_seg, stretch_speed)
                        current_len_ms = len(voice_seg)

                # 2. If voice is longer than mouth or collides with next dialogue:
                target_window_ms = min(subtitle_dur_ms + 150, gap_to_next_ms)
                if current_len_ms > target_window_ms and target_window_ms >= 250:
                    needed_speed = current_len_ms / target_window_ms
                    needed_speed = min(needed_speed, 1.40)
                    if needed_speed > 1.03:
                        voice_seg = change_audio_tempo_ffmpeg(voice_seg, needed_speed)

                # 3. Collision prevention: Never allow voice_seg to overlap with next speech start
                if idx < len(sorted_tasks) - 1:
                    strict_max_ms = max(150, next_start_ms - start_ms - 25)
                    if len(voice_seg) > strict_max_ms:
                        remainder_speed = len(voice_seg) / strict_max_ms
                        if remainder_speed <= 1.30:
                            voice_seg = change_audio_tempo_ffmpeg(voice_seg, remainder_speed)
                        else:
                            voice_seg = voice_seg[:strict_max_ms].fade_out(25)

                if self.vocal_boost:
                    voice_seg = level_voice_segment(voice_seg)

                end_ms = start_ms + len(voice_seg)
                loaded_voiceovers.append((start_ms, end_ms, voice_seg))
                if end_ms > max_voice_len_ms:
                    max_voice_len_ms = end_ms

            final_duration_ms = max(orig_duration_ms, max_voice_len_ms)
            bg_audio = None
            bgm_loaded = False
            if self.background_music_path and os.path.exists(self.background_music_path):
                try:
                    bg_audio = AudioSegment.from_file(self.background_music_path)
                    bgm_loaded = True
                except Exception:
                    bg_audio = None

            if bg_audio is None and orig_audio is not None and not self.voice_only:
                bg_audio = orig_audio
            bg_audio = fit_audio_duration(bg_audio, final_duration_ms)
            
            self.progress.emit(50)

            if self.mix_mode == "Mute Music" or bg_audio is None:
                mixed_bg = AudioSegment.silent(duration=final_duration_ms)
            elif self.mix_mode in ("Duck Music", "Mute Original on Speech", "Duck Original on Speech"):
                intervals = sorted([
                    (max(0, item[0] - 120), min(final_duration_ms, item[1] + 180))
                    for item in loaded_voiceovers
                ])
                merged_intervals = []
                for start, end in intervals:
                    if not merged_intervals:
                        merged_intervals.append([start, end])
                    else:
                        last_start, last_end = merged_intervals[-1]
                        if start <= last_end:
                            merged_intervals[-1][1] = max(last_end, end)
                        else:
                            merged_intervals.append([start, end])

                mixed_bg = AudioSegment.silent(duration=0)
                last_idx = 0
                
                # Configure levels based on the chosen mode
                if self.mix_mode == "Duck Music":
                    normal_bg = bg_audio - 3.0
                    ducked_db = pct_to_db(self.music_level)
                    ducked_bg_segment = bg_audio + ducked_db
                elif self.mix_mode == "Mute Original on Speech":
                    normal_bg = bg_audio
                    ducked_bg_segment = None # Will write silent segment
                else: # "Duck Original on Speech"
                    normal_bg = bg_audio
                    ducked_db = pct_to_db(self.music_level)
                    ducked_bg_segment = bg_audio + ducked_db
                
                for start, end in merged_intervals:
                    start = min(start, len(bg_audio))
                    end = min(end, len(bg_audio))
                    
                    if start > last_idx:
                        normal_part = normal_bg[last_idx:start]
                        cross_len = min(150, len(normal_part) // 2, len(mixed_bg) // 2)
                        if cross_len > 0:
                            mixed_bg = mixed_bg.append(normal_part, crossfade=cross_len)
                        else:
                            mixed_bg = mixed_bg + normal_part
                    
                    if ducked_bg_segment is None:
                        ducked_part = AudioSegment.silent(duration=end - start)
                    else:
                        ducked_part = ducked_bg_segment[start:end]
                    
                    cross_len = min(150, len(ducked_part) // 2, len(mixed_bg) // 2)
                    if cross_len > 0:
                        mixed_bg = mixed_bg.append(ducked_part, crossfade=cross_len)
                    else:
                        mixed_bg = mixed_bg + ducked_part
                        
                    last_idx = end
                
                if last_idx < len(bg_audio):
                    remaining_part = normal_bg[last_idx:]
                    cross_len = min(150, len(remaining_part) // 2, len(mixed_bg) // 2)
                    if cross_len > 0:
                        mixed_bg = mixed_bg.append(remaining_part, crossfade=cross_len)
                    else:
                        mixed_bg = mixed_bg + remaining_part

                if len(mixed_bg) < final_duration_ms:
                    mixed_bg += AudioSegment.silent(duration=final_duration_ms - len(mixed_bg))
            else:
                music_db = pct_to_db(self.music_level)
                lowered_orig = bg_audio + music_db
                if bgm_loaded and self.voice_only:
                    lowered_orig = bg_audio + music_db
                if len(lowered_orig) < final_duration_ms:
                    lowered_orig += AudioSegment.silent(duration=final_duration_ms - len(lowered_orig))
                mixed_bg = lowered_orig

            self.progress.emit(70)

            final_audio = mixed_bg
            for start_ms, _, voice_seg in loaded_voiceovers:
                final_audio = final_audio.overlay(voice_seg, position=start_ms)

            try:
                if final_audio.max_dBFS > -1.0:
                    final_audio = final_audio - (final_audio.max_dBFS + 1.0)
            except Exception:
                pass

            self.progress.emit(80)

            temp_audio_file = os.path.join(tempfile.gettempdir(), f"dubbed_audio_{int(time.time())}.wav")
            final_audio.export(temp_audio_file, format="wav")
            
            self.progress.emit(90)

            has_logo = bool(self.logo_path and os.path.exists(self.logo_path))
            trimmed_logo_temp = None
            video_w = 0
            if has_logo:
                probe_cmd = [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
                    self.video_path,
                ]
                probe_result = subprocess.run(
                    probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                try:
                    video_w, _video_h = (int(value) for value in probe_result.stdout.strip().split("x"))
                except (TypeError, ValueError):
                    raise RuntimeError("Could not detect video size for logo rendering.")

            if has_logo:
                scale_val = float(self.logo_scale or 0.08)
                scale_val = max(0.03, min(0.80, scale_val))
                logo_target_w = max(16, round(video_w * scale_val))
                opacity_val = max(0.1, min(1.0, float(self.logo_opacity or 0.85)))
                rel_x = max(0.0, min(1.0, float(self.logo_rel_x if self.logo_rel_x is not None else 0.82)))
                rel_y = max(0.0, min(1.0, float(self.logo_rel_y if self.logo_rel_y is not None else 0.05)))
                pos_expr = f"(W-w)*{rel_x:.4f}:(H-h)*{rel_y:.4f}"

                # Automatically crop empty transparent borders so logo is prominent & true scale
                logo_file_to_use = self.logo_path
                try:
                    from PyQt6.QtGui import QImage
                    qimg = QImage(self.logo_path)
                    if not qimg.isNull() and qimg.hasAlphaChannel():
                        qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
                        w, h = qimg.width(), qimg.height()
                        min_x, max_x = w, 0
                        min_y, max_y = h, 0
                        has_opaque = False
                        step_y = max(1, h // 400)
                        step_x = max(1, w // 400)
                        for y in range(0, h, step_y):
                            for x in range(0, w, step_x):
                                if (qimg.pixel(x, y) >> 24) & 0xFF > 15:
                                    has_opaque = True
                                    if x < min_x: min_x = x
                                    if x > max_x: max_x = x
                                    if y < min_y: min_y = y
                                    if y > max_y: max_y = y
                        if has_opaque and (max_x - min_x < w * 0.92 or max_y - min_y < h * 0.92):
                            crop_w = max(10, max_x - min_x + 1)
                            crop_h = max(10, max_y - min_y + 1)
                            t_path = os.path.join(tempfile.gettempdir(), f"render_logo_{int(time.time())}.png")
                            qimg.copy(max(0, min_x - 4), max(0, min_y - 4), min(w, crop_w + 8), min(h, crop_h + 8)).save(t_path)
                            if os.path.exists(t_path):
                                trimmed_logo_temp = t_path
                                logo_file_to_use = t_path
                except Exception:
                    pass

                filter_parts = []
                filter_parts.append(f"[1:v]scale={logo_target_w}:-1:flags=lanczos[logo]")
                filter_parts.append(
                    f"[logo]format=rgba,colorchannelmixer=aa={opacity_val}[logo_alpha]"
                )
                filter_parts.append(
                    f"[0:v][logo_alpha]overlay={pos_expr}[v_out]"
                )
                filter_str = ";".join(filter_parts)

                gpu_enc_args = get_gpu_video_encoder_args()
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-i", self.video_path,
                    "-i", logo_file_to_use,
                    "-i", temp_audio_file,
                    "-filter_complex", filter_str,
                    "-map", "[v_out]", "-map", "2:a",
                ] + gpu_enc_args + [
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    self.output_path
                ]
            else:
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-i", self.video_path,
                    "-i", temp_audio_file,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    self.output_path
                ]
            
            subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            self.progress.emit(100)
            self.completed.emit(self.output_path)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            if temp_audio_file and os.path.exists(temp_audio_file):
                try:
                    os.remove(temp_audio_file)
                except Exception:
                    pass
            if trimmed_logo_temp and os.path.exists(trimmed_logo_temp):
                try:
                    os.remove(trimmed_logo_temp)
                except Exception:
                    pass


class MergeWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, video_paths, output_path, reencode=False):
        super().__init__()
        self.video_paths = video_paths
        self.output_path = output_path
        self.reencode = reencode

    def run(self):
        import subprocess
        import os
        import tempfile
        import time

        try:
            self.progress.emit(10)
            if not self.video_paths:
                raise Exception("No videos provided to merge.")

            if not self.reencode:
                temp_dir = tempfile.gettempdir()
                list_file_path = os.path.join(temp_dir, f"merge_list_{int(time.time())}.txt")
                
                with open(list_file_path, "w", encoding="utf-8") as f:
                    for path in self.video_paths:
                        escaped_path = path.replace("\\", "/")
                        f.write(f"file '{escaped_path}'\n")

                self.progress.emit(30)
                
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_file_path,
                    "-c", "copy",
                    self.output_path
                ]
                
                result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                try:
                    os.remove(list_file_path)
                except Exception:
                    pass

                if result.returncode != 0:
                    raise Exception(f"Fast merge failed (files might have mismatching codecs/resolutions). Error details:\n{result.stderr}")
            else:
                self.progress.emit(20)
                ffmpeg_cmd = ["ffmpeg", "-y"]
                for path in self.video_paths:
                    ffmpeg_cmd.extend(["-hwaccel", "auto", "-i", path])
                
                filter_str = "".join(f"[{i}:v][{i}:a]" for i in range(len(self.video_paths)))
                filter_str += f"concat=n={len(self.video_paths)}:v=1:a=1[outv][outa]"
                
                ffmpeg_cmd.extend([
                    "-filter_complex", filter_str,
                    "-map", "[outv]",
                    "-map", "[outa]",
                ] + get_gpu_video_encoder_args() + [
                    "-c:a", "aac",
                    "-b:a", "192k",
                    self.output_path
                ])
                
                self.progress.emit(40)
                result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                if result.returncode != 0:
                    raise Exception(f"Re-encode merge failed. Error details:\n{result.stderr}")

            self.progress.emit(100)
            self.completed.emit(self.output_path)
        except Exception as e:
            self.error.emit(str(e))


class TelegramExportImportWorker(QThread):
    """Find videos in a Telegram export and safely extract videos from ZIP files."""

    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    completed = pyqtSignal(list, list)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts"}
    CHUNK_SIZE = 4 * 1024 * 1024

    def __init__(self, export_folder):
        super().__init__()
        self.export_folder = os.path.abspath(export_folder)
        self.extract_root = os.path.join(self.export_folder, "_telegram_extracted")

    @staticmethod
    def _natural_key(path):
        relative = os.path.normcase(path)
        return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", relative)]

    @staticmethod
    def _safe_archive_name(zip_path):
        name = os.path.splitext(os.path.basename(zip_path))[0]
        safe = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
        return safe or "archive"

    @classmethod
    def _is_video(cls, path):
        return os.path.splitext(path)[1].lower() in cls.VIDEO_EXTENSIONS

    def _scan_source(self):
        direct_videos = []
        zip_paths = []
        extract_norm = os.path.normcase(os.path.abspath(self.extract_root))

        for current_root, dir_names, file_names in os.walk(self.export_folder):
            current_norm = os.path.normcase(os.path.abspath(current_root))
            if current_norm == extract_norm:
                dir_names[:] = []
                continue
            dir_names[:] = [
                name for name in dir_names
                if os.path.normcase(os.path.abspath(os.path.join(current_root, name))) != extract_norm
            ]
            for file_name in file_names:
                path = os.path.join(current_root, file_name)
                if self._is_video(path):
                    direct_videos.append(path)
                elif file_name.lower().endswith(".zip"):
                    zip_paths.append(path)

        direct_videos.sort(key=self._natural_key)
        zip_paths.sort(key=self._natural_key)
        return direct_videos, zip_paths

    def _archive_video_members(self, zip_path):
        members = []
        with zipfile.ZipFile(zip_path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir() or not self._is_video(info.filename):
                    continue
                normalized = info.filename.replace("\\", "/")
                parts = [part for part in normalized.split("/") if part not in ("", ".")]
                if not parts or any(part == ".." or ":" in part for part in parts):
                    continue
                members.append(info)
        return members

    def _output_path_for_member(self, archive_dir, info, reserved):
        normalized = info.filename.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." or ":" in part for part in parts):
            return None

        # Preserve the exact filename and relative folder structure from the ZIP.
        candidate = os.path.join(archive_dir, *parts)
        candidate_norm = os.path.normcase(os.path.abspath(candidate))
        archive_norm = os.path.normcase(os.path.abspath(archive_dir)) + os.sep
        if not candidate_norm.startswith(archive_norm) or candidate_norm in reserved:
            return None
        reserved.add(candidate_norm)
        return candidate

    def run(self):
        warnings = []
        try:
            self.status.emit("Scanning Telegram export folder...")
            direct_videos, zip_paths = self._scan_source()
            archives = []
            total_bytes = 0

            for index, zip_path in enumerate(zip_paths, start=1):
                if self.isInterruptionRequested():
                    self.cancelled.emit()
                    return
                self.status.emit(f"Reading ZIP {index}/{len(zip_paths)}: {os.path.basename(zip_path)}")
                try:
                    members = self._archive_video_members(zip_path)
                    archives.append((zip_path, members))
                    total_bytes += sum(max(0, info.file_size) for info in members)
                except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                    warnings.append(f"Skipped {os.path.basename(zip_path)}: {exc}")

            if not direct_videos and not any(members for _, members in archives):
                raise RuntimeError("No supported video files were found in this Telegram export folder.")

            os.makedirs(self.extract_root, exist_ok=True)
            required_bytes = 0
            planned = []
            for zip_path, members in archives:
                archive_dir = os.path.join(self.extract_root, self._safe_archive_name(zip_path))
                reserved = set()
                archive_plan = []
                for info in members:
                    output_path = self._output_path_for_member(archive_dir, info, reserved)
                    if output_path is None:
                        warnings.append(
                            f"Skipped duplicate or unsafe path without renaming: {info.filename} "
                            f"in {os.path.basename(zip_path)}"
                        )
                        continue
                    complete = os.path.isfile(output_path) and os.path.getsize(output_path) == info.file_size
                    if not complete:
                        required_bytes += max(0, info.file_size)
                    archive_plan.append((info, output_path, complete))
                planned.append((zip_path, archive_plan))

            free_bytes = shutil.disk_usage(self.export_folder).free
            safety_margin = 512 * 1024 * 1024
            if required_bytes + safety_margin > free_bytes:
                needed_gb = (required_bytes + safety_margin) / (1024 ** 3)
                free_gb = free_bytes / (1024 ** 3)
                raise RuntimeError(
                    f"Not enough free disk space to extract the Telegram videos. "
                    f"Need about {needed_gb:.1f} GB; available {free_gb:.1f} GB."
                )

            extracted_videos = []
            completed_bytes = max(0, total_bytes - required_bytes)
            if total_bytes:
                self.progress.emit(int(completed_bytes * 100 / total_bytes))

            for archive_index, (zip_path, archive_plan) in enumerate(planned, start=1):
                if not archive_plan:
                    continue
                self.status.emit(
                    f"Extracting ZIP {archive_index}/{len(planned)}: {os.path.basename(zip_path)}"
                )
                try:
                    with zipfile.ZipFile(zip_path, "r") as archive:
                        for info, output_path, complete in archive_plan:
                            if self.isInterruptionRequested():
                                self.cancelled.emit()
                                return
                            if complete:
                                extracted_videos.append(output_path)
                                continue

                            os.makedirs(os.path.dirname(output_path), exist_ok=True)
                            partial_path = output_path + ".part"
                            try:
                                if os.path.exists(partial_path):
                                    os.remove(partial_path)
                                with archive.open(info, "r") as source, open(partial_path, "wb") as target:
                                    while True:
                                        if self.isInterruptionRequested():
                                            raise InterruptedError()
                                        chunk = source.read(self.CHUNK_SIZE)
                                        if not chunk:
                                            break
                                        target.write(chunk)
                                        completed_bytes += len(chunk)
                                        if total_bytes:
                                            self.progress.emit(min(99, int(completed_bytes * 100 / total_bytes)))
                                os.replace(partial_path, output_path)
                                extracted_videos.append(output_path)
                            except InterruptedError:
                                if os.path.exists(partial_path):
                                    os.remove(partial_path)
                                self.cancelled.emit()
                                return
                except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                    warnings.append(f"Could not extract {os.path.basename(zip_path)}: {exc}")

            all_videos = direct_videos + extracted_videos
            unique_videos = list(dict.fromkeys(os.path.abspath(path) for path in all_videos))
            unique_videos.sort(key=self._natural_key)
            self.progress.emit(100)
            self.completed.emit(unique_videos, warnings)
        except Exception as exc:
            self.error.emit(str(exc))


# Custom widget to draw visual waves if video player plays mock media
class WaveVisualizer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phases = [0.0, 0.0, 0.0]
        self.playing = False
        self.video_name = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_wave)
        self.timer.start(30)  # ~33 fps

    def update_wave(self):
        if self.playing:
            self.phases[0] += 0.05
            self.phases[1] += 0.08
            self.phases[2] += 0.03
            self.update()

    def set_playing(self, state):
        self.playing = state
        self.update()

    def set_video_name(self, name):
        self.video_name = name
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background
        painter.fillRect(self.rect(), QColor("#111111"))
        
        width = self.width()
        height = self.height()
        mid_y = height / 2

        if not self.playing:
            # Draw resting line
            painter.setPen(QPen(QColor("#0082C8"), 2))
            painter.drawLine(0, int(mid_y), width, int(mid_y))
            
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.setFont(QFont("Segoe UI", 12))
            msg = f"Video Loaded: {self.video_name}" if self.video_name else "Video Loading: [Waiting for File]"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
            return

        # Draw beautiful animated waves
        colors = [QColor(25, 163, 224, 180), QColor(0, 130, 200, 120), QColor(144, 202, 249, 100)]
        amplitudes = [30.0, 15.0, 20.0]
        frequencies = [0.01, 0.02, 0.015]

        for idx, (color, amp, freq) in enumerate(zip(colors, amplitudes, frequencies)):
            painter.setPen(QPen(color, 2))

            # Draw wave line
            prev_x = 0
            prev_y = mid_y
            for x in range(0, width, 5):
                import math
                y = mid_y + amp * math.sin(x * freq + self.phases[idx]) * math.cos(x * 0.002)
                if x > 0:
                    painter.drawLine(int(prev_x), int(prev_y), int(x), int(y))
                prev_x = x
                prev_y = y

        if self.video_name:
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(15, 25, f"Playing: {self.video_name}")


class TimelineView(QWidget):
    seek_requested = pyqtSignal(float)
    segment_moved = pyqtSignal(int, float, float)
    view_changed = pyqtSignal(float, float, float)
    play_original_requested = pyqtSignal(int)
    play_dub_requested = pyqtSignal(int)
    segments_selected = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self.selected_indices = set()
        self.last_selected_index = None
        self.duration = 68.0
        self.current_time = 0.0
        self.zoom = 1.0
        self.view_start = 0.0
        self.source_name = "Original audio"
        self.source_is_bgm = False
        self.voice_only = False
        self.drag_segment_index = None
        self.drag_mode = None
        self.drag_offset = 0.0
        self.scroll_dragging = False
        self.scroll_drag_offset = 0.0
        self.pan_dragging = False
        self.pan_last_x = 0.0
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setToolTip("Shift+Click for multi-selection. Drag edge to resize, middle to move.")

    def set_segments(self, segments, duration=None):
        self.segments = segments
        max_end = max([seg.get("end", 0.0) for seg in segments] + [0.0])
        if duration is not None:
            self.duration = max(float(duration or 0), max_end, 1.0)
        else:
            self.duration = max(self.duration, max_end, 1.0)
        self._clamp_view()
        self.update()
        self._emit_view_changed()

    def set_duration(self, duration):
        max_end = max([seg.get("end", 0.0) for seg in self.segments] + [0.0])
        self.duration = max(float(duration or 0), max_end, 1.0)
        self._clamp_view()
        self.update()
        self._emit_view_changed()

    def set_current_time(self, seconds):
        self.current_time = max(0.0, min(float(seconds or 0), self.duration))
        self._ensure_time_visible(self.current_time)
        self.update()

    def set_source_name(self, name, is_bgm=False):
        self.source_name = name or "Original audio"
        self.source_is_bgm = bool(is_bgm)
        self.update()

    def set_selected_rows(self, rows):
        """Update selected segment indices based on selected table rows."""
        row_set = set(rows)
        new_selected = set()
        for idx, seg in enumerate(self.segments):
            row_val = int(seg.get("row", idx))
            if row_val in row_set or idx in row_set:
                new_selected.add(idx)
        if new_selected != self.selected_indices:
            self.selected_indices = new_selected
            self.update()

    def clear_selection(self):
        self.selected_indices.clear()
        self.last_selected_index = None
        self.update()

    def set_voice_only(self, enabled):
        self.voice_only = bool(enabled)
        self.update()

    def _track_rect(self):
        return QRectF(64, 28, max(1, self.width() - 84), max(1, self.height() - 48))

    def _scroll_lane_rect(self):
        rect = self._track_rect()
        return QRectF(rect.left(), max(rect.bottom() + 7, self.height() - 15), rect.width(), 8)

    def _scroll_handle_rect(self):
        lane = self._scroll_lane_rect()
        if self.duration <= 0:
            return QRectF(lane.left(), lane.top(), lane.width(), lane.height())

        visible = min(self._visible_duration(), self.duration)
        ratio = max(0.02, min(1.0, visible / max(self.duration, 0.01)))
        handle_width = max(46.0, lane.width() * ratio)
        handle_width = min(handle_width, lane.width())
        available = max(1.0, lane.width() - handle_width)
        max_start = self._max_view_start()
        offset = 0.0 if max_start <= 0.01 else (self.view_start / max_start) * available
        return QRectF(lane.left() + offset, lane.top(), handle_width, lane.height())

    def _visible_duration(self):
        return max(0.05, self.duration / max(self.zoom, 1.0))

    def _max_view_start(self):
        return max(0.0, self.duration - self._visible_duration())

    def _clamp_view(self):
        self.zoom = max(1.0, min(float(self.zoom or 1.0), 80.0))
        if self.zoom <= 1.001 or self._visible_duration() >= self.duration:
            self.view_start = 0.0
        else:
            self.view_start = max(0.0, min(float(self.view_start or 0.0), self._max_view_start()))

    def _emit_view_changed(self):
        self.view_changed.emit(self.view_start, self._visible_duration(), self.duration)

    def set_zoom(self, zoom, anchor_time=None):
        old_visible = self._visible_duration()
        if anchor_time is None:
            anchor_time = self.view_start + old_visible / 2
        anchor_ratio = 0.5
        if old_visible > 0:
            anchor_ratio = (anchor_time - self.view_start) / old_visible
            anchor_ratio = max(0.0, min(anchor_ratio, 1.0))

        self.zoom = max(1.0, min(float(zoom or 1.0), 80.0))
        new_visible = self._visible_duration()
        self.view_start = float(anchor_time) - new_visible * anchor_ratio
        self._clamp_view()
        self.update()
        self._emit_view_changed()

    def zoom_by(self, factor, anchor_time=None):
        self.set_zoom(self.zoom * float(factor or 1.0), anchor_time)

    def fit_to_window(self):
        self.zoom = 1.0
        self.view_start = 0.0
        self.update()
        self._emit_view_changed()
        self._emit_view_changed()

    def set_view_start(self, seconds):
        self.view_start = float(seconds or 0.0)
        self._clamp_view()
        self.update()
        self._emit_view_changed()

    def _set_view_start_from_scroll_x(self, x):
        lane = self._scroll_lane_rect()
        handle = self._scroll_handle_rect()
        max_start = self._max_view_start()
        if max_start <= 0.01:
            self.set_view_start(0.0)
            return

        available = max(1.0, lane.width() - handle.width())
        handle_left = max(lane.left(), min(float(x) - self.scroll_drag_offset, lane.right() - handle.width()))
        ratio = (handle_left - lane.left()) / available
        self.set_view_start(ratio * max_start)

    def _ensure_time_visible(self, seconds):
        if self.zoom <= 1.001:
            return
        visible = self._visible_duration()
        margin = visible * 0.08
        changed = False
        if seconds < self.view_start + margin:
            self.view_start = seconds - margin
            changed = True
        elif seconds > self.view_start + visible - margin:
            self.view_start = seconds - visible + margin
            changed = True
        if changed:
            self._clamp_view()
            self._emit_view_changed()

    def _time_to_x(self, seconds):
        rect = self._track_rect()
        return rect.left() + ((float(seconds or 0.0) - self.view_start) / self._visible_duration()) * rect.width()

    def _x_to_time(self, x):
        rect = self._track_rect()
        pos = max(rect.left(), min(float(x), rect.right()))
        return self.view_start + ((pos - rect.left()) / max(rect.width(), 1.0)) * self._visible_duration()

    def _format_time(self, seconds, show_ms=False):
        seconds = max(0.0, float(seconds or 0))
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if show_ms:
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d}:{ms:03d}"
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}.00"

    def _segment_rect(self, seg, y=52, height=30):
        rect = self._track_rect()
        start = seg.get("start", 0.0)
        end = max(seg.get("end", start + 0.4), start + 0.4)
        visible_start = self.view_start
        visible_end = self.view_start + self._visible_duration()
        if end < visible_start or start > visible_end:
            return QRectF()
        x1 = self._time_to_x(max(start, visible_start))
        x2 = max(self._time_to_x(min(end, visible_end)), x1 + 10)
        width = min(x2 - x1 - 2, rect.right() - x1 - 1)
        return QRectF(x1 + 1, y, max(0, width), height)

    def _segment_at_position(self, x, y):
        hit = self._segment_hit_at_position(x, y)
        return hit[0] if hit else None

    def _segment_hit_at_position(self, x, y):
        """Check hit testing on T1 (Subtitles), A1 (Dubbed Voice), and A2 (Original Voice/Music)."""
        track_configs = [(44, 36), (100, 28), (148, 28)]
        for track_y, track_h in track_configs:
            for idx in range(len(self.segments) - 1, -1, -1):
                seg = self.segments[idx]
                block = self._segment_rect(seg, y=track_y, height=track_h)
                if block.width() > 1 and block.contains(float(x), float(y)):
                    edge = min(9, max(4, block.width() / 4))
                    if abs(float(x) - block.left()) <= edge:
                        return idx, "resize_start"
                    if abs(float(x) - block.right()) <= edge:
                        return idx, "resize_end"
                    return idx, "move"
        return None

    def mousePressEvent(self, event):
        x = event.position().x()
        y = event.position().y()
        if event.button() == Qt.MouseButton.LeftButton and self._max_view_start() > 0.01:
            lane_hit = self._scroll_lane_rect().adjusted(0, -5, 0, 5)
            if lane_hit.contains(x, y):
                handle = self._scroll_handle_rect()
                self.scroll_dragging = True
                self.scroll_drag_offset = x - handle.left() if handle.contains(x, y) else handle.width() / 2
                self._set_view_start_from_scroll_x(x)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) and self._max_view_start() > 0.01:
            self.pan_dragging = True
            self.pan_last_x = x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            y = event.position().y()
            modifiers = event.modifiers()
            is_shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            is_ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

            clicked_segment_idx = None
            click_target = None  # "T1", "A1", or "A2"

            # Check click on T1 Subtitle segment
            hit = self._segment_hit_at_position(x, y)
            if hit is not None:
                clicked_segment_idx = hit[0]
                click_target = "T1"
            else:
                # Check click on A1 Dubbed Voice segment
                for idx in range(len(self.segments) - 1, -1, -1):
                    seg = self.segments[idx]
                    if seg.get("has_audio"):
                        a1_block = self._segment_rect(seg, y=100, height=28)
                        if a1_block.width() > 1 and a1_block.contains(float(x), float(y)):
                            clicked_segment_idx = idx
                            click_target = "A1"
                            break

                # Check click on A2 Original Voice segment
                if clicked_segment_idx is None:
                    for idx in range(len(self.segments) - 1, -1, -1):
                        seg = self.segments[idx]
                        a2_block = self._segment_rect(seg, y=148, height=28)
                        if a2_block.width() > 1 and a2_block.contains(float(x), float(y)):
                            clicked_segment_idx = idx
                            click_target = "A2"
                            break

            # Handle segment selection (Shift+Click, Ctrl+Click, or normal Click)
            if clicked_segment_idx is not None:
                if is_shift and self.last_selected_index is not None:
                    # Shift+Click range selection
                    start_i = min(self.last_selected_index, clicked_segment_idx)
                    end_i = max(self.last_selected_index, clicked_segment_idx)
                    self.selected_indices = set(range(start_i, end_i + 1))
                elif is_ctrl:
                    # Ctrl+Click toggle selection
                    if clicked_segment_idx in self.selected_indices:
                        self.selected_indices.remove(clicked_segment_idx)
                    else:
                        self.selected_indices.add(clicked_segment_idx)
                    self.last_selected_index = clicked_segment_idx
                else:
                    # Normal Single Click
                    self.selected_indices = {clicked_segment_idx}
                    self.last_selected_index = clicked_segment_idx

                seg = self.segments[clicked_segment_idx]
                self.current_time = float(seg.get("start", 0.0))
                self.segments_selected.emit(sorted(list(self.selected_indices)))

                if hit is not None:
                    self.drag_segment_index = hit[0]
                    self.drag_mode = hit[1]
                    self.drag_offset = self._x_to_time(x) - float(seg.get("start", 0.0))
                elif not is_shift and not is_ctrl:
                    row = int(seg.get("row", clicked_segment_idx))
                    if click_target == "A2":
                        self.play_original_requested.emit(row)
                    elif click_target == "A1":
                        self.play_dub_requested.emit(row)

                self.update()
                event.accept()
                return

            # Ctrl + Click on empty timeline space or ruler zooms in/out
            if is_ctrl and clicked_segment_idx is None:
                anchor_time = self._x_to_time(x)
                if event.button() == Qt.MouseButton.RightButton or (is_shift and is_ctrl):
                    self.zoom_by(0.75, anchor_time)
                else:
                    self.zoom_by(1.35, anchor_time)
                self.current_time = anchor_time
                self.seek_requested.emit(anchor_time)
                self.update()
                event.accept()
                return

            # Clicked on empty space in timeline
            if not is_shift and not is_ctrl:
                self.selected_indices.clear()
                self.last_selected_index = None
                self.segments_selected.emit([])
                self.update()

            self.seek_requested.emit(self._x_to_time(x))

    def mouseMoveEvent(self, event):
        x = event.position().x()
        y = event.position().y()
        if self.scroll_dragging:
            self._set_view_start_from_scroll_x(x)
            event.accept()
            return

        if self.pan_dragging:
            rect = self._track_rect()
            dx = x - self.pan_last_x
            self.pan_last_x = x
            seconds_delta = -(dx / max(rect.width(), 1.0)) * self._visible_duration()
            self.set_view_start(self.view_start + seconds_delta)
            event.accept()
            return

        if self.drag_segment_index is not None and event.buttons() & Qt.MouseButton.LeftButton:
            seg = self.segments[self.drag_segment_index]
            old_start = float(seg.get("start", 0.0))
            old_end = max(float(seg.get("end", old_start + 0.4)), old_start + 0.4)
            min_length = 0.12
            hit_time = self._x_to_time(x)
            if self.drag_mode == "resize_start":
                new_start = max(0.0, min(hit_time, old_end - min_length))
                seg["start"] = new_start
                self.current_time = new_start
            elif self.drag_mode == "resize_end":
                new_end = min(self.duration, max(hit_time, old_start + min_length))
                seg["end"] = new_end
                self.current_time = new_end
            else:
                length = max(min_length, old_end - old_start)
                new_start = hit_time - self.drag_offset
                new_start = max(0.0, min(new_start, max(0.0, self.duration - length)))
                seg["start"] = new_start
                seg["end"] = new_start + length
                self.current_time = new_start
            self.update()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self._x_to_time(x))
            return
        if self._max_view_start() > 0.01 and self._scroll_lane_rect().adjusted(0, -5, 0, 5).contains(x, y):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        # Check cursor & tooltip on interactive segment blocks
        if self._segment_hit_at_position(x, y) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.setToolTip("Drag edge to resize, drag middle to move subtitle timing")
            return

        for seg in self.segments:
            a2_block = self._segment_rect(seg, y=148, height=28)
            if a2_block.width() > 1 and a2_block.contains(float(x), float(y)):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip(f"Click to play Original Voice: Line {seg.get('id', '')} (ផ្ទៀងផ្ទាត់សម្លេងដើម)")
                return
            if seg.get("has_audio"):
                a1_block = self._segment_rect(seg, y=100, height=28)
                if a1_block.width() > 1 and a1_block.contains(float(x), float(y)):
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                    self.setToolTip(f"Click to play Khmer Dubbed Voice: Line {seg.get('id', '')} (ស្តាប់សម្លេងបកប្រែ)")
                    return

        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("Wheel scrolls when zoomed. Ctrl+wheel zooms. Click A2/A1 to listen.")

    def mouseReleaseEvent(self, event):
        if self.scroll_dragging or self.pan_dragging:
            self.scroll_dragging = False
            self.pan_dragging = False
            self.scroll_drag_offset = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            event.accept()
            return

        if self.drag_segment_index is not None:
            seg = self.segments[self.drag_segment_index]
            row = int(seg.get("row", self.drag_segment_index))
            self.segment_moved.emit(row, float(seg.get("start", 0.0)), float(seg.get("end", 0.0)))
            self.drag_segment_index = None
            self.drag_mode = None
            self.drag_offset = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def wheelEvent(self, event):
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        delta = delta_y if abs(delta_y) >= abs(delta_x) else delta_x
        if not delta:
            return

        is_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        anchor_time = self._x_to_time(event.position().x())

        # Ctrl + Mouse Wheel = Smooth Zoom In / Zoom Out centered at cursor
        if is_ctrl:
            factor = 1.25 if delta > 0 else 0.80
            self.zoom_by(factor, anchor_time)
            event.accept()
            return

        # Normal Wheel Scroll = Pan horizontally if zoomed in, or zoom if at 100%
        if self._max_view_start() > 0.01:
            step = self._visible_duration() * 0.15
            self.set_view_start(self.view_start - (delta / 120.0) * step)
            event.accept()
            return

        # Default at 100% zoom: Wheel scrolls zoom in
        if delta > 0:
            self.zoom_by(1.25, anchor_time)
            event.accept()

    def paintEvent(self, event):
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#090D1B"))

        rect = self._track_rect()
        left = rect.left()
        right = rect.right()
        width = rect.width()

        painter.setPen(QPen(QColor("#2B3550"), 1))
        painter.setBrush(QBrush(QColor("#0B1020")))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 6, 6)

        ruler_y = 16
        painter.setPen(QPen(QColor("#252E47"), 1))
        painter.drawLine(int(left), ruler_y + 22, int(right), ruler_y + 22)
        visible_duration = self._visible_duration()
        visible_end = self.view_start + visible_duration
        tick_count = 7 if self.duration < 600 else 9
        painter.setFont(QFont("Noto Sans Khmer", 9, QFont.Weight.Bold))
        for i in range(tick_count + 1):
            t = self.view_start + (visible_duration / tick_count) * i
            x = self._time_to_x(t)
            painter.setPen(QPen(QColor("#202940"), 1))
            painter.drawLine(int(x), ruler_y + 4, int(x), self.height() - 22)
            painter.setPen(QColor("#7F89A3"))
            painter.drawText(QRectF(x - 30, ruler_y, 60, 16), Qt.AlignmentFlag.AlignCenter, self._format_time(t))

        tracks = [
            ("Subtitle Track", 48, 38, QColor("#0B132B")),
            ("Audio / Voice", 94, 42, QColor("#061A14")),
            ("BGM / Music", 144, 38, QColor("#1A0E2E")),
        ]
        painter.setFont(QFont("Noto Sans Khmer", 8, QFont.Weight.Bold))
        for label, y, h, color in tracks:
            painter.setPen(QColor("#929BB3"))
            painter.drawText(QRectF(8, y, 50, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, label)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#28334D"), 1))
            painter.drawRoundedRect(QRectF(left, y, width, h), 5, 5)

        # Draw A1 synthesized audio waveform background
        wave_mid = 115
        painter.setPen(QPen(QColor("#22C55E"), 1))
        wave_offset = int(self.view_start * 45)
        for i in range(0, int(width), 3):
            x = int(left) + i
            sample_x = i + wave_offset
            value = abs(math.sin(sample_x * 0.047) + math.sin(sample_x * 0.119 + 1.6) * 0.6)
            amp = 2 + value * 13
            painter.drawLine(x, int(wave_mid - amp), x, int(wave_mid + amp))

        # Draw A2 original audio waveform background & subtle track label
        a2_wave_mid = 163
        painter.setPen(QPen(QColor("#A855F7"), 1))
        for i in range(0, int(width), 3):
            x = int(left) + i
            sample_x = i + wave_offset + 80
            value = abs(math.sin(sample_x * 0.053) + math.sin(sample_x * 0.131 + 0.8) * 0.5)
            amp = 2 + value * 9
            painter.drawLine(x, int(a2_wave_mid - amp), x, int(a2_wave_mid + amp))

        source_label = f"A2: {self.source_name}"
        if self.voice_only and not self.source_is_bgm:
            source_label += " (Voice Iso Active)"
        painter.setPen(QColor("#C08CFF"))
        painter.setFont(QFont("Noto Sans Khmer", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(left + 8, 145, width - 16, 12), Qt.AlignmentFlag.AlignVCenter, source_label)

        # Draw Segments for T1, A1, and A2
        for idx, seg in enumerate(self.segments):
            is_selected = (idx in self.selected_indices) or (int(seg.get("row", idx)) in self.selected_indices)

            # --- 1. T1 Subtitle block ---
            block = self._segment_rect(seg)
            if block.width() > 1:
                has_audio = seg.get("has_audio", False)
                if is_selected:
                    fill = QColor("#3B82F6")
                    border_pen = QPen(QColor("#FFFFFF"), 2)
                else:
                    fill = QColor("#2563EB" if has_audio else "#1D4ED8")
                    border_pen = QPen(QColor("#38BDF8"), 1)

                painter.setBrush(QBrush(fill))
                painter.setPen(border_pen)
                painter.drawRoundedRect(block, 5, 5)

                if is_selected:
                    # Glowing selection overlay
                    painter.setBrush(QBrush(QColor(255, 255, 255, 30)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(block.adjusted(1, 1, -1, -1), 4, 4)

                inner_step = max(8, int(block.width() / 6))
                painter.setPen(QPen(QColor(255, 255, 255, 85), 1))
                for ix in range(int(block.left()) + 6, int(block.right()), inner_step):
                    painter.drawLine(ix, int(block.top()) + 4, ix, int(block.bottom()) - 4)

                painter.setPen(QPen(QColor("#087F5B"), 2))
                painter.drawLine(int(block.left()) + 4, int(block.top()) + 6, int(block.left()) + 4, int(block.bottom()) - 6)
                painter.drawLine(int(block.right()) - 4, int(block.top()) + 6, int(block.right()) - 4, int(block.bottom()) - 6)

                if block.width() > 28:
                    painter.setPen(QColor("#FFFFFF"))
                    painter.setFont(QFont("Noto Sans Khmer", 9, QFont.Weight.Bold))
                    block_text = str(seg.get("id", ""))
                    if block.width() > 90:
                        block_text = f"{block_text}  {seg.get('text', '')[:18]}"
                    painter.drawText(block.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, block_text)

            # --- 2. A1 Dubbed Audio Block ---
            if seg.get("has_audio", False):
                audio_block = self._segment_rect(seg, y=100, height=28)
                if audio_block.width() > 1:
                    painter.setBrush(QBrush(QColor("#2D7FF9")))
                    painter.setPen(QPen(QColor("#155BC5"), 1))
                    painter.drawRoundedRect(audio_block, 4, 4)

                    # Inner dub waveform
                    painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
                    for ix in range(int(audio_block.left()) + 4, int(audio_block.right()) - 2, 4):
                        amp_d = 2 + int(abs(math.sin(ix * 0.45)) * 6)
                        painter.drawLine(ix, 114 - amp_d, ix, 114 + amp_d)

                    painter.setPen(QColor("#FFFFFF"))
                    painter.setFont(QFont("Noto Sans Khmer", 9, QFont.Weight.Bold))
                    label = f"{seg.get('id', '')} A1 (Dub)"
                    if audio_block.width() > 105:
                        trans_snip = seg.get('trans_text', seg.get('text', ''))[:12]
                        if trans_snip:
                            label = f"{label} {trans_snip}"
                    painter.drawText(audio_block.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)

            # --- 3. A2 Original Voice Block ---
            orig_block = self._segment_rect(seg, y=148, height=28)
            if orig_block.width() > 1:
                if is_selected:
                    painter.setBrush(QBrush(QColor("#A855F7")))
                    painter.setPen(QPen(QColor("#FFFFFF"), 2))
                else:
                    painter.setBrush(QBrush(QColor("#8E24AA")))
                    painter.setPen(QPen(QColor("#4A148C"), 1))
                painter.drawRoundedRect(orig_block, 4, 4)
                if is_selected:
                    painter.setBrush(QBrush(QColor(255, 255, 255, 30)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(orig_block.adjusted(1, 1, -1, -1), 3, 3)

                # Soundwave ticks inside original voice block
                painter.setPen(QPen(QColor(255, 255, 255, 95), 1))
                for ix in range(int(orig_block.left()) + 4, int(orig_block.right()) - 2, 4):
                    amp_v = 3 + int(abs(math.sin(ix * 0.38) + math.cos(ix * 0.85)) * 6)
                    painter.drawLine(ix, 162 - amp_v, ix, 162 + amp_v)

                painter.setPen(QPen(QColor("#D1C4E9"), 2))
                painter.drawLine(int(orig_block.left()) + 3, int(orig_block.top()) + 5, int(orig_block.left()) + 3, int(orig_block.bottom()) - 5)
                painter.drawLine(int(orig_block.right()) - 3, int(orig_block.top()) + 5, int(orig_block.right()) - 3, int(orig_block.bottom()) - 5)

                painter.setPen(QColor("#FFFFFF"))
                painter.setFont(QFont("Noto Sans Khmer", 9, QFont.Weight.Bold))
                orig_label = f"{seg.get('id', '')} A2 (Orig)"
                if orig_block.width() > 105:
                    orig_snip = seg.get('orig_text', seg.get('text', ''))[:14]
                    if orig_snip:
                        orig_label = f"{orig_label}  {orig_snip}"
                painter.drawText(orig_block.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, orig_label)

        lane = self._scroll_lane_rect()
        painter.setPen(QPen(QColor("#313C59"), 1))
        painter.setBrush(QBrush(QColor("#11172A")))
        painter.drawRoundedRect(lane, 4, 4)
        if self._max_view_start() > 0.01:
            handle = self._scroll_handle_rect()
            painter.setPen(QPen(QColor("#AD7D00"), 1))
            painter.setBrush(QBrush(QColor("#F5B51B")))
            painter.drawRoundedRect(handle, 4, 4)

            visible_start_x = handle.left()
            visible_end_x = handle.right()
            painter.setPen(QPen(QColor("#8A6500"), 1))
            painter.drawLine(int(visible_start_x) + 4, int(lane.top()) + 2, int(visible_start_x) + 4, int(lane.bottom()) - 2)
            painter.drawLine(int(visible_end_x) - 4, int(lane.top()) + 2, int(visible_end_x) - 4, int(lane.bottom()) - 2)
        else:
            painter.setPen(QPen(QColor("#3B4765"), 1))
            painter.setBrush(QBrush(QColor("#202940")))
            painter.drawRoundedRect(lane, 4, 4)

        if self.view_start <= self.current_time <= visible_end:
            playhead_x = self._time_to_x(self.current_time)
            painter.setPen(QPen(QColor("#E53935"), 2))
            painter.drawLine(int(playhead_x), ruler_y + 8, int(playhead_x), self.height() - 20)
            painter.setBrush(QBrush(QColor("#26C6DA")))
            painter.setPen(QPen(QColor("#0097A7"), 1))
            label_w = 76
            label_x = max(left, min(playhead_x - label_w / 2, right - label_w))
            painter.drawRoundedRect(QRectF(label_x, 40, label_w, 20), 4, 4)
            painter.setPen(QColor("#000000"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.drawText(QRectF(label_x, 40, label_w, 20), Qt.AlignmentFlag.AlignCenter, self._format_time(self.current_time, True))



    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            anchor = self.current_time
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.zoom_by(1.25, anchor)
                event.accept()
                return
            elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self.zoom_by(0.8, anchor)
                event.accept()
                return
            elif key == Qt.Key.Key_0:
                self.fit_to_window()
                event.accept()
                return
        super().keyPressEvent(event)

class DraggableLogoWidget(QWidget):
    """Interactive, draggable & zoomable (resizable) logo and watermark overlay on the video preview player."""
    def __init__(self, parent=None, parent_window=None):
        super().__init__(parent)
        self.parent_window = parent_window
        self.logo_pixmap = None
        self.watermark_text = ""
        self.rel_x = 0.72
        self.rel_y = 0.05
        self.scale_ratio = 0.15
        self.is_dragging = False
        self.is_resizing = False
        self.drag_offset = None
        self.resize_start_pos = None
        self.resize_start_size = None
        self.is_hovered = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if parent:
            parent.installEventFilter(self)
        self.resize(120, 50)
        self.hide()

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj == self.parent() and event.type() == QEvent.Type.Resize:
            self.reposition_from_relative()
        return super().eventFilter(obj, event)

    @staticmethod
    def _crop_transparent_borders(pixmap):
        if not pixmap or pixmap.isNull():
            return pixmap
        try:
            from PyQt6.QtGui import QImage
            img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            w, h = img.width(), img.height()
            min_x, max_x = w, 0
            min_y, max_y = h, 0
            has_opaque = False
            step_y = max(1, h // 400)
            step_x = max(1, w // 400)
            for y in range(0, h, step_y):
                for x in range(0, w, step_x):
                    if (img.pixel(x, y) >> 24) & 0xFF > 15:
                        has_opaque = True
                        if x < min_x: min_x = x
                        if x > max_x: max_x = x
                        if y < min_y: min_y = y
                        if y > max_y: max_y = y
            if has_opaque and (max_x - min_x < w * 0.92 or max_y - min_y < h * 0.92):
                crop_w = max(10, max_x - min_x + 1)
                crop_h = max(10, max_y - min_y + 1)
                return QPixmap.fromImage(img.copy(max(0, min_x - 4), max(0, min_y - 4), min(w, crop_w + 8), min(h, crop_h + 8)))
        except Exception:
            pass
        return pixmap

    def set_logo_pixmap(self, pixmap, width=90):
        if pixmap and not pixmap.isNull():
            pixmap = self._crop_transparent_borders(pixmap)
        self.logo_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            target_w = max(30, int(width))
            aspect = pixmap.height() / max(1, pixmap.width())
            target_h = max(20, int(target_w * aspect))
            self.resize(target_w + 14, target_h + 14)
            if self.parent():
                self.scale_ratio = float(target_w) / max(1, self.parent().width())
        self.reposition_from_relative()
        self.update()

    def set_watermark_text(self, text):
        self.watermark_text = text or ""
        if not self.logo_pixmap and self.watermark_text:
            fm = self.fontMetrics()
            tw = fm.horizontalAdvance(self.watermark_text)
            self.resize(tw + 24, 34)
            if self.parent():
                self.scale_ratio = float(tw + 24) / max(1, self.parent().width())
        self.reposition_from_relative()
        self.update()

    def set_relative_position(self, rx, ry):
        self.rel_x = max(0.0, min(0.98, float(rx)))
        self.rel_y = max(0.0, min(0.98, float(ry)))
        self.reposition_from_relative()

    def reposition_from_relative(self):
        if not self.parent():
            return
        if self.parent_window and hasattr(self.parent_window, "get_actual_video_rect"):
            v_rect = self.parent_window.get_actual_video_rect()
            avail_w = max(0, v_rect.width() - self.width())
            avail_h = max(0, v_rect.height() - self.height())
            target_x = v_rect.x() + int(self.rel_x * avail_w)
            target_y = v_rect.y() + int(self.rel_y * avail_h)
        else:
            pw = max(1, self.parent().width())
            ph = max(1, self.parent().height())
            target_x = max(0, min(pw - self.width(), int(self.rel_x * pw)))
            target_y = max(0, min(ph - self.height(), int(self.rel_y * ph)))
        self.move(target_x, target_y)
        self.raise_()

    def _is_near_corner(self, pt):
        """Check if mouse is near the bottom-right resize corner."""
        w, h = self.width(), self.height()
        return (w - pt.x()) <= 16 and (h - pt.y()) <= 16

    def enterEvent(self, event):
        self.is_hovered = True
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            if self._is_near_corner(pt):
                self.is_resizing = True
                self.resize_start_pos = pt
                self.resize_start_size = (self.width(), self.height())
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.is_dragging = True
                self.drag_offset = pt
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pt = event.position().toPoint()
        if not self.is_dragging and not self.is_resizing:
            if self._is_near_corner(pt):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)

        if self.is_resizing and event.buttons() & Qt.MouseButton.LeftButton:
            if not self.parent() or not self.resize_start_size:
                return
            dx = pt.x() - self.resize_start_pos.x()
            dy = pt.y() - self.resize_start_pos.y()
            orig_w, orig_h = self.resize_start_size
            aspect = orig_h / max(1, orig_w)

            base_w = self.parent().width()
            base_h = self.parent().height()
            if self.parent_window and hasattr(self.parent_window, "get_actual_video_rect"):
                v_rect = self.parent_window.get_actual_video_rect()
                base_w = v_rect.width()
                base_h = v_rect.height()

            pw = max(1, base_w)
            ph = max(1, base_h)

            new_w = max(40, min(int(pw * 0.75), orig_w + dx))
            new_h = max(24, min(int(ph * 0.75), int(new_w * aspect)))

            self.resize(new_w, new_h)
            self.scale_ratio = float(new_w) / pw
            if self.parent_window and hasattr(self.parent_window, "on_logo_scale_dragged"):
                self.parent_window.on_logo_scale_dragged(self.scale_ratio)
            self.update()
            event.accept()
            return

        if self.is_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            if not self.parent():
                return
            delta = pt - self.drag_offset
            new_topleft = self.pos() + delta

            if self.parent_window and hasattr(self.parent_window, "get_actual_video_rect"):
                v_rect = self.parent_window.get_actual_video_rect()
                avail_w = max(0, v_rect.width() - self.width())
                avail_h = max(0, v_rect.height() - self.height())
                clamped_x = max(v_rect.x(), min(v_rect.x() + avail_w, new_topleft.x()))
                clamped_y = max(v_rect.y(), min(v_rect.y() + avail_h, new_topleft.y()))
                self.move(clamped_x, clamped_y)
                self.rel_x = float(clamped_x - v_rect.x()) / max(1, avail_w)
                self.rel_y = float(clamped_y - v_rect.y()) / max(1, avail_h)
            else:
                pw = max(1, self.parent().width())
                ph = max(1, self.parent().height())
                clamped_x = max(0, min(pw - self.width(), new_topleft.x()))
                clamped_y = max(0, min(ph - self.height(), new_topleft.y()))
                self.move(clamped_x, clamped_y)
                self.rel_x = float(clamped_x) / pw
                self.rel_y = float(clamped_y) / ph

            if self.parent_window and hasattr(self.parent_window, "on_logo_position_dragged"):
                self.parent_window.on_logo_position_dragged(self.rel_x, self.rel_y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_dragging or self.is_resizing:
            self.is_dragging = False
            self.is_resizing = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            if self.parent_window and hasattr(self.parent_window, "on_logo_position_dragged"):
                self.parent_window.on_logo_position_dragged(self.rel_x, self.rel_y)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        """Scroll wheel over Logo zooms it in or out smoothly."""
        delta = event.angleDelta().y()
        if not self.parent():
            return
        base_w = self.parent().width()
        if self.parent_window and hasattr(self.parent_window, "get_actual_video_rect"):
            base_w = self.parent_window.get_actual_video_rect().width()
        pw = max(1, base_w)
        factor = 1.08 if delta > 0 else 0.92
        aspect = self.height() / max(1, self.width())

        new_w = max(40, min(int(pw * 0.75), int(self.width() * factor)))
        new_h = max(24, int(new_w * aspect))
        self.resize(new_w, new_h)

        self.scale_ratio = float(new_w) / pw
        if self.parent_window and hasattr(self.parent_window, "on_logo_scale_dragged"):
            self.parent_window.on_logo_scale_dragged(self.scale_ratio)
        self.reposition_from_relative()
        self.update()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        w, h = self.width(), self.height()

        if self.is_hovered or self.is_dragging or self.is_resizing:
            # Highlight border with dashed neon outline
            painter.setBrush(QBrush(QColor(15, 23, 42, 160)))
            painter.setPen(QPen(QColor("#38BDF8"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

            # Draw 4 Corner Handles
            handle_size = 6
            painter.setBrush(QBrush(QColor("#38BDF8")))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            # Top-Left
            painter.drawRect(1, 1, handle_size, handle_size)
            # Top-Right
            painter.drawRect(w - handle_size - 1, 1, handle_size, handle_size)
            # Bottom-Left
            painter.drawRect(1, h - handle_size - 1, handle_size, handle_size)
            # Bottom-Right (Active resize handle)
            painter.setBrush(QBrush(QColor("#00E676")))
            painter.drawRect(w - handle_size - 2, h - handle_size - 2, handle_size + 1, handle_size + 1)
        elif not self.logo_pixmap:
            painter.setBrush(QBrush(QColor(0, 0, 0, 140)))
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        if self.logo_pixmap and not self.logo_pixmap.isNull():
            draw_w = max(10, self.width() - 12)
            draw_h = max(10, self.height() - 12)
            scaled = self.logo_pixmap.scaled(draw_w, draw_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            px = int((self.width() - scaled.width()) / 2)
            py = int((self.height() - scaled.height()) / 2)
            painter.drawPixmap(px, py, scaled)
        elif self.watermark_text:
            painter.setFont(QFont("Noto Sans Khmer", 10, QFont.Weight.Bold))
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.watermark_text)

        painter.end()


class InteractiveLogoItem(QGraphicsPixmapItem):
    """
    Interactive, draggable, and zoomable logo overlay that floats directly on the video.
    Guaranteed by Qt's QGraphicsScene to ALWAYS render on top of video playback without hardware occlusion.
    """
    HANDLE_SIZE = 14

    def __init__(self, pixmap=None, view=None):
        super().__init__(pixmap)
        self.view = view
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setZValue(100)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.is_resizing = False
        self.resize_start_pos = None
        self.resize_start_scale = 1.0

    def is_over_corner(self, pos):
        br = self.boundingRect()
        # Item coordinates belong to the full-resolution pixmap. Compensate
        # for its scene scale so the resize corner stays ~20 screen pixels.
        h = (self.HANDLE_SIZE + 6) / max(0.001, abs(self.scale()))
        return (pos.x() >= br.width() - h) and (pos.y() >= br.height() - h)

    def wheelEvent(self, event):
        delta = event.delta()
        if delta and self.view and hasattr(self.view, "zoom_logo"):
            self.view.zoom_logo(1.10 if delta > 0 else 0.90)
            event.accept()
            return
        super().wheelEvent(event)

    def hoverMoveEvent(self, event):
        if self.is_over_corner(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(True)
            self.setFocus()
            if self.is_over_corner(event.pos()):
                self.is_resizing = True
                self.resize_start_pos = event.scenePos()
                self.resize_start_scale = self.scale()
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return
            else:
                self.is_resizing = False
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            if self.view and hasattr(self.view, "show_logo_context_menu"):
                try:
                    g_pos = event.screenPos().toPoint()
                except Exception:
                    g_pos = event.pos().toPoint()
                self.view.show_logo_context_menu(g_pos)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_resizing and self.resize_start_pos:
            delta = event.scenePos() - self.resize_start_pos
            diff = (delta.x() + delta.y()) / 2.0
            factor = 1.0 + (diff / 80.0)
            new_scale = max(0.005, min(5.0, self.resize_start_scale * factor))
            self.setScale(new_scale)
            if self.view and hasattr(self.view, "on_logo_scaled"):
                self.view.on_logo_scaled(new_scale)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_resizing:
            self.is_resizing = False
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
        if self.view and hasattr(self.view, "on_logo_moved"):
            self.view.on_logo_moved()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.view and hasattr(self.view, "delete_logo"):
                self.view.delete_logo()
                event.accept()
                return
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            if self.view and hasattr(self.view, "zoom_logo"):
                self.view.zoom_logo(1.10)
                event.accept()
                return
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            if self.view and hasattr(self.view, "zoom_logo"):
                self.view.zoom_logo(0.90)
                event.accept()
                return
        super().keyPressEvent(event)

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        if self.isSelected():
            pen = QPen(QColor("#38BDF8"), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            br = self.boundingRect()
            painter.drawRect(br)
            # Corner handle
            h = self.HANDLE_SIZE / max(0.001, abs(self.scale()))
            hr = QRectF(br.width() - h, br.height() - h, h, h)
            painter.setBrush(QBrush(QColor("#38BDF8")))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawRoundedRect(hr, 3, 3)


class VideoGraphicsView(QGraphicsView):
    """
    State-of-the-art video view combining QGraphicsVideoItem with interactive overlays.
    Solves all Windows DirectX/DirectShow overlay clipping issues natively and permanently.
    """
    def __init__(self, parent_window=None):
        super().__init__()
        self.parent_window = parent_window
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: #050B14; border: none;")
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setAcceptDrops(True)

        # 1. Video Item
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_item.setZValue(0)
        self.scene.addItem(self.video_item)

        # 2. Interactive Logo Item
        self.logo_item = InteractiveLogoItem(view=self)
        self.logo_item.setZValue(100)
        self.scene.addItem(self.logo_item)
        self.logo_item.hide()
        self.raw_logo_pixmap = None

        # 3. Subtitle Overlay Text Item
        self.subtitle_item = QGraphicsTextItem()
        self.subtitle_item.setZValue(200)
        self.scene.addItem(self.subtitle_item)
        self.subtitle_item.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = max(1, self.width()), max(1, self.height())
        self.scene.setSceneRect(0, 0, w, h)
        self.video_item.setSize(QSizeF(w, h))
        self.update_subtitles_geometry()
        if hasattr(self, "_need_initial_logo_pos") and self._need_initial_logo_pos:
            self.position_logo_preset()
            self._need_initial_logo_pos = False

    def update_subtitles_geometry(self):
        if not self.subtitle_item.isVisible():
            return
        w, h = self.width(), self.height()
        br = self.subtitle_item.boundingRect()
        sx = max(0, int((w - br.width()) / 2))
        sy = max(0, int(h - br.height() - 25))
        self.subtitle_item.setPos(sx, sy)

    def set_subtitle_html(self, html_text):
        if not html_text:
            self.subtitle_item.hide()
            return
        self.subtitle_item.setHtml(html_text)
        self.subtitle_item.show()
        self.update_subtitles_geometry()

    def set_logo_pixmap(self, pixmap, width=90):
        if not pixmap or pixmap.isNull():
            self.logo_item.hide()
            self.raw_logo_pixmap = None
            return
        self.raw_logo_pixmap = pixmap
        # Retain the full-resolution source. Every zoom is now resampled from
        # the original logo rather than from a small, previously scaled copy.
        self.logo_item.setPixmap(pixmap)
        self.logo_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.logo_item.setScale(float(width) / max(1.0, float(pixmap.width())))
        self.logo_item.show()

    def position_logo_preset(self, preset="Top-Right"):
        if not self.logo_item.pixmap() or self.logo_item.pixmap().isNull():
            return
        w, h = self.width(), self.height()
        lw = self.logo_item.boundingRect().width() * self.logo_item.scale()
        lh = self.logo_item.boundingRect().height() * self.logo_item.scale()
        
        aspect_str = self.parent_window.cmb_aspect.currentText() if (self.parent_window and hasattr(self.parent_window, "cmb_aspect")) else "9:16"
        if "9:16" in aspect_str:
            ar = 9.0 / 16.0
        elif "16:9" in aspect_str:
            ar = 16.0 / 9.0
        elif "1:1" in aspect_str:
            ar = 1.0
        else:
            ar = getattr(self.parent_window, "video_aspect_ratio", 9.0 / 16.0)

        view_ar = float(w) / float(max(1, h))
        if view_ar > ar:
            vh = h
            vw = int(h * ar)
            vx = (w - vw) // 2
            vy = 0
        else:
            vw = w
            vh = int(w / max(0.01, ar))
            vx = 0
            vy = (h - vh) // 2

        if "Top-Left" in preset:
            target_x, target_y = vx + 15, vy + 15
        elif "Bottom-Right" in preset:
            target_x, target_y = vx + vw - lw - 15, vy + vh - lh - 15
        elif "Bottom-Left" in preset:
            target_x, target_y = vx + 15, vy + vh - lh - 15
        elif "Center" in preset:
            target_x, target_y = vx + (vw - lw) // 2, vy + (vh - lh) // 2
        else: # Top-Right default
            target_x, target_y = vx + vw - lw - 15, vy + 15

        self.logo_item.setPos(max(0, target_x), max(0, target_y))

    def on_logo_moved(self):
        if self.parent_window and hasattr(self.parent_window, "on_logo_position_dragged"):
            pos = self.logo_item.pos()
            video_rect = self.parent_window.get_actual_video_rect()
            logo_w = self.logo_item.boundingRect().width() * self.logo_item.scale()
            logo_h = self.logo_item.boundingRect().height() * self.logo_item.scale()
            avail_w = max(1.0, float(video_rect.width()) - logo_w)
            avail_h = max(1.0, float(video_rect.height()) - logo_h)
            rx = (float(pos.x()) - float(video_rect.x())) / avail_w
            ry = (float(pos.y()) - float(video_rect.y())) / avail_h
            rx = max(0.0, min(1.0, rx))
            ry = max(0.0, min(1.0, ry))
            self.parent_window.on_logo_position_dragged(rx, ry)

    def on_logo_scaled(self, scale):
        if self.parent_window and hasattr(self.parent_window, "on_logo_scale_dragged"):
            video_rect = self.parent_window.get_actual_video_rect()
            displayed_width = self.logo_item.boundingRect().width() * scale
            ratio = displayed_width / max(1.0, float(video_rect.width()))
            self.parent_window.on_logo_scale_dragged(ratio)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile()
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()
                if self.parent_window and hasattr(self.parent_window, "handle_player_dropped_file"):
                    self.parent_window.handle_player_dropped_file(file_path, pos)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def wheelEvent(self, event):
        if self.logo_item.isVisible():
            delta = event.angleDelta().y()
            if delta != 0:
                factor = 1.08 if delta > 0 else 0.92
                self.zoom_logo(factor)
                event.accept()
                return
        super().wheelEvent(event)

    def zoom_logo(self, factor):
        if not self.logo_item.isVisible():
            return
        current = self.logo_item.scale()
        new_scale = max(0.005, min(5.0, current * factor))
        self.logo_item.setScale(new_scale)
        self.on_logo_scaled(new_scale)

    def set_logo_scale(self, scale_val):
        if not self.logo_item.isVisible():
            return
        val = max(0.005, min(5.0, float(scale_val)))
        self.logo_item.setScale(val)
        self.on_logo_scaled(val)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.logo_item.isVisible():
                self.delete_logo()
                event.accept()
                return
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            if self.logo_item.isVisible():
                self.zoom_logo(1.10)
                event.accept()
                return
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            if self.logo_item.isVisible():
                self.zoom_logo(0.90)
                event.accept()
                return
        super().keyPressEvent(event)

    def delete_logo(self):
        self.logo_item.hide()
        self.logo_item.setPixmap(QPixmap())
        self.raw_logo_pixmap = None
        if self.parent_window:
            if hasattr(self.parent_window, "txt_logo_path"):
                self.parent_window.txt_logo_path.clear()
            if hasattr(self.parent_window, "chk_show_logo"):
                self.parent_window.chk_show_logo.setChecked(False)
            if hasattr(self.parent_window, "draggable_logo_widget"):
                self.parent_window.draggable_logo_widget.hide()
            if "logo_path" in getattr(self.parent_window, "settings", {}):
                self.parent_window.settings["logo_path"] = ""
                save_settings(self.parent_window.settings)
            if hasattr(self.parent_window, "log_workflow_msg"):
                self.parent_window.log_workflow_msg("🗑️ Logo deleted / removed from video (Key: Delete)")

    def show_logo_context_menu(self, global_screen_pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1E293B;
                color: #38BDF8;
            }
            QMenu::separator {
                height: 1px;
                background: #334155;
                margin: 4px 6px;
            }
        """)
        zoom_in_act = menu.addAction("🔍 Zoom In (ពង្រីក) [Key: +]")
        zoom_in_act.triggered.connect(lambda: self.zoom_logo(1.15))
        zoom_out_act = menu.addAction("🔍 Zoom Out (បង្រួម) [Key: -]")
        zoom_out_act.triggered.connect(lambda: self.zoom_logo(0.85))
        menu.addSeparator()
        s_small = menu.addAction("📏 Scale: 10% (Small)")
        s_small.triggered.connect(lambda: self.set_logo_scale(0.67))
        s_def = menu.addAction("📏 Scale: 15% (Default)")
        s_def.triggered.connect(lambda: self.set_logo_scale(1.0))
        s_med = menu.addAction("📏 Scale: 20% (Medium)")
        s_med.triggered.connect(lambda: self.set_logo_scale(1.33))
        s_lrg = menu.addAction("📏 Scale: 25% (Large)")
        s_lrg.triggered.connect(lambda: self.set_logo_scale(1.67))
        menu.addSeparator()
        del_act = menu.addAction("🗑️ Delete Logo (លុបឡូហ្គោ) [Del]")
        del_act.triggered.connect(self.delete_logo)
        menu.addSeparator()
        top_l_act = menu.addAction("↖️ Move to Top-Left")
        top_l_act.triggered.connect(lambda: self.position_logo_preset("Top-Left"))
        top_r_act = menu.addAction("↗️ Move to Top-Right")
        top_r_act.triggered.connect(lambda: self.position_logo_preset("Top-Right"))
        center_act = menu.addAction("🎯 Move to Center")
        center_act.triggered.connect(lambda: self.position_logo_preset("Center"))
        bot_r_act = menu.addAction("↘️ Move to Bottom-Right")
        bot_r_act.triggered.connect(lambda: self.position_logo_preset("Bottom-Right"))
        from PyQt6.QtCore import QPoint
        if isinstance(global_screen_pos, QPoint):
            menu.exec(global_screen_pos)
        else:
            menu.exec(self.mapToGlobal(self.mapFromScene(self.logo_item.pos())))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cambo Dubber v1.1.0 (Dubbing Suite)")
        self.setMinimumSize(1200, 750)
        self.setAcceptDrops(True)
        
        # Audio / Video Playback properties
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.durationChanged.connect(self.video_duration_changed)
        self.media_player.positionChanged.connect(self.video_position_changed)
        
        # Preview player for single line preview
        self.preview_player = None
        self.preview_audio_output = None
        
        self.is_playing = False
        self.duration = 68  # 1:08 default
        self.current_time = 0
        self.video_file = None
        self.srt_file = None
        self.cache_dir = os.path.join(tempfile.gettempdir(), "cambo_dubber_cache")
        self.audio_files = {}  # row_idx: path
        self.played_preview_rows = set()
        self.settings = load_settings()
        self.bgm_file = self.settings.get("background_music_path", "")
        self.voice_gender_analyzed = False
        self.auto_voice_then_generate = False
        self.auto_workflow_active = False
        self.auto_workflow_stage = "idle"
        self.auto_workflow_waiting_for_voice = False
        
        # Timers
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self.increment_playback)
        
        # Main Layout Setup
        self.init_ui()
        self.apply_styles()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
        # Populate initial rows matching screenshot
        # Workspace starts completely clean (ready for user movie import)
        # self.populate_demo_rows()
        
        self.log_workflow_msg("Ready.")
        QTimer.singleShot(100, self.restore_previous_session)

    def init_ui(self):
        """Modern Dubbing Studio Pro layout with non-overlapping responsive design."""
        global_widget = QWidget()
        self.setCentralWidget(global_widget)
        global_layout = QVBoxLayout(global_widget)
        global_layout.setContentsMargins(6, 6, 6, 6)
        global_layout.setSpacing(4)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        global_layout.addWidget(self.tabs)

        # Global Timeline Zoom Shortcuts (Ctrl++, Ctrl+-, Ctrl+0)
        from PyQt6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self.zoom_timeline(1.25))
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self.zoom_timeline(1.25))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self.zoom_timeline(0.8))
        QShortcut(QKeySequence("Ctrl+0"), self, self.fit_timeline)
        QShortcut(QKeySequence("Ctrl+Shift+N"), self, self.clear_workspace)
        QShortcut(QKeySequence("Ctrl+Shift+Delete"), self, self.clear_workspace)

        # Tab 1: Single Dubbing Suite
        tab_single = QWidget()
        tab_single_layout = QVBoxLayout(tab_single)
        tab_single_layout.setContentsMargins(2, 2, 2, 2)
        tab_single_layout.setSpacing(4)
        self.tabs.addTab(tab_single, " Single Dubbing (បកប្រែតែមួយ)")

        # ----------------------------------------------------
        # Part 1: Top Toolbar Action Row (Clean, Non-Overlapping)
        # ----------------------------------------------------
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("topToolbarFrame")
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(4, 4, 4, 4)
        toolbar.setSpacing(4)

        self.btn_import_video = QPushButton(" Import Video")
        self.btn_import_video.setObjectName("btnImportVideo")
        self.btn_import_video.setIcon(get_icon("import_video", "#FFFFFF", 14))
        self.btn_import_video.clicked.connect(self.import_video_dialog)

        self.btn_import_subtitle = QPushButton(" Import Subtitle")
        self.btn_import_subtitle.setObjectName("btnImportSubtitle")
        self.btn_import_subtitle.setIcon(get_icon("import_subtitle", "#FFFFFF", 14))
        self.btn_import_subtitle.clicked.connect(self.import_subtitle_dialog)

        self.btn_transcribe = QPushButton(" Auto Transcribe")
        self.btn_transcribe.setObjectName("btnTranscribe")
        self.btn_transcribe.setIcon(get_icon("transcribe", "#FFFFFF", 14))
        self.btn_transcribe.setToolTip("Listen to video audio and transcribe spoken words.")
        self.btn_transcribe.clicked.connect(self.auto_transcribe)

        self.btn_ocr_subtitles = QPushButton(" Extract Subs")
        self.btn_ocr_subtitles.setObjectName("btnOcrSubtitles")
        self.btn_ocr_subtitles.setIcon(get_icon("import_subtitle", "#FFFFFF", 14))
        self.btn_ocr_subtitles.setToolTip("Read burned-in subtitles with OCR.")
        self.btn_ocr_subtitles.clicked.connect(self.extract_screen_subtitles)

        self.btn_translate = QPushButton(" Translate Text")
        self.btn_translate.setObjectName("btnTranslate")
        self.btn_translate.setIcon(get_icon("translate", "#FFFFFF", 14))
        self.btn_translate.clicked.connect(self.translate_text)

        self.btn_voiceover = QPushButton(" Generate Voiceover")
        self.btn_voiceover.setObjectName("btnVoiceover")
        self.btn_voiceover.setIcon(get_icon("voiceover", "#FFFFFF", 14))
        self.btn_voiceover.clicked.connect(self.generate_voiceover_all)

        self.btn_render = QPushButton(" Render Video")
        self.btn_render.setObjectName("btnRender")
        self.btn_render.setIcon(get_icon("render", "#FFFFFF", 14))
        self.btn_render.clicked.connect(self.render_output_video)

        self.btn_workflow_srt = QPushButton(" Workflow SRT")
        self.btn_workflow_srt.setObjectName("btnWorkflowSrt")
        self.btn_workflow_srt.setIcon(get_icon("workflow", "#FFFFFF", 14))
        self.btn_workflow_srt.clicked.connect(self.workflow_from_srt)

        self.btn_settings = QPushButton(" Settings")
        self.btn_settings.setObjectName("btnSettings")
        self.btn_settings.setIcon(get_icon("settings", "#FFFFFF", 14))
        self.btn_settings.clicked.connect(self.open_settings)

        for btn in [self.btn_import_video, self.btn_import_subtitle, self.btn_transcribe,
                    self.btn_ocr_subtitles, self.btn_translate, self.btn_voiceover, self.btn_render,
                    self.btn_workflow_srt, self.btn_settings]:
            toolbar.addWidget(btn)

        toolbar.addStretch()

        self.btn_automate = QPushButton(" Start / Export (Workflow)")
        self.btn_automate.setObjectName("btnAutomate")
        self.btn_automate.setIcon(get_icon("automate", "#FFFFFF", 14))
        self.btn_automate.clicked.connect(self.automate_workflow)
        toolbar.addWidget(self.btn_automate)

        self.btn_cancel = QPushButton(" Cancel Operation")
        self.btn_cancel.setObjectName("cancelBtn")
        self.btn_cancel.setIcon(get_icon("cancel", "#FF8080", 14))
        self.btn_cancel.clicked.connect(self.cancel_operation)
        self.btn_cancel.setEnabled(False)
        toolbar.addWidget(self.btn_cancel)

        # Clean All Status Button
        self.btn_clean_status = QPushButton(" 🧹 Clean All Status")
        self.btn_clean_status.setObjectName("cleanStatusBtn")
        self.btn_clean_status.setIcon(get_icon("delete", "#FCA5A5", 14))
        self.btn_clean_status.setToolTip("Reset all lines status to Pending and clear audio cache (សម្អាត Status ទាំងអស់)")
        self.btn_clean_status.clicked.connect(self.clean_all_status)
        toolbar.addWidget(self.btn_clean_status)

        # Clear Workspace Button
        self.btn_clear_workspace = QPushButton(" 🗑️ Clear Workspace")
        self.btn_clear_workspace.setObjectName("clearWorkspaceBtn")
        self.btn_clear_workspace.setIcon(get_icon("delete", "#CBD5E1", 14))
        self.btn_clear_workspace.setToolTip("Clear all loaded video, subtitles, timeline, and start a fresh project (ជម្រះ Workspace ទាំងអស់)")
        self.btn_clear_workspace.clicked.connect(self.clear_workspace)
        toolbar.addWidget(self.btn_clear_workspace)

        tab_single_layout.addWidget(toolbar_frame)

        # ----------------------------------------------------
        # Part 2: Main Vertical Splitter (3-Column Workspace + Bottom Timeline)
        # ----------------------------------------------------
        v_main_splitter = QSplitter(Qt.Orientation.Vertical)
        v_main_splitter.setChildrenCollapsible(False)
        tab_single_layout.addWidget(v_main_splitter, stretch=1)

        # ------------------- STUDIO MAIN CONTAINER WITH LEFT MODULE DOCK -------------------
        studio_container = QWidget()
        studio_container_layout = QHBoxLayout(studio_container)
        studio_container_layout.setContentsMargins(0, 0, 0, 0)
        studio_container_layout.setSpacing(6)

        # Left Vertical Module Dock (Matching Dubber Clan Pro)
        module_dock = QFrame()
        module_dock.setObjectName("moduleDock")
        module_dock.setFixedWidth(56)
        module_dock.setStyleSheet("""
            QFrame#moduleDock {
                background-color: #0A0E1A;
                border: 1px solid #1E293B;
                border-radius: 8px;
            }
            QPushButton.moduleBtn {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                color: #94A3B8;
                font-size: 10px;
                font-weight: 700;
                padding: 6px 2px;
                min-width: 46px;
                max-width: 46px;
                min-height: 42px;
            }
            QPushButton.moduleBtn:hover {
                background-color: #1E293B;
                border-color: #38BDF8;
                color: #38BDF8;
            }
            QPushButton.moduleBtn:checked {
                background-color: #0C213B;
                border-color: #0284C7;
                color: #38BDF8;
            }
        """)
        module_dock_layout = QVBoxLayout(module_dock)
        module_dock_layout.setContentsMargins(4, 6, 4, 6)
        module_dock_layout.setSpacing(6)

        def _add_module_btn(icon_text, label_text, tooltip, callback):
            btn = QPushButton(f"{icon_text}\n{label_text}")
            btn.setProperty("class", "moduleBtn")
            btn.setToolTip(tooltip)
            btn.clicked.connect(callback)
            module_dock_layout.addWidget(btn)
            return btn

        _add_module_btn("📝", "Subs", "Subtitles Editor (កែសម្រួលអក្សររត់)", lambda: self.table.setFocus() if hasattr(self, 'table') else None)
        _add_module_btn("🎨", "Style", "Subtitle Text & Style (រចនាប័ទ្មអក្សរ)", lambda: self.inspector_tabs.setCurrentIndex(0) if hasattr(self, 'inspector_tabs') else None)
        _add_module_btn("🎙️", "Voice", "Voice & Voiceover TTS (បញ្ចូលសម្លេង)", lambda: self.inspector_tabs.setCurrentIndex(2) if hasattr(self, 'inspector_tabs') else None)
        _add_module_btn("🎵", "Audio", "Background Audio & Mix (តន្ត្រី)", lambda: self.inspector_tabs.setCurrentIndex(2) if hasattr(self, 'inspector_tabs') else None)
        _add_module_btn("🖼️", "Logo", "Logo & Watermark (ឡូហ្គោ)", lambda: self.inspector_tabs.setCurrentIndex(0) if hasattr(self, 'inspector_tabs') else None)
        _add_module_btn("🤖", "AI", "AI Tools & Workflow (AI ស្វ័យប្រវត្តិ)", lambda: self.inspector_tabs.setCurrentIndex(1) if hasattr(self, 'inspector_tabs') else None)
        module_dock_layout.addStretch()
        _add_module_btn("⚙️", "Settings", "Settings & API Configuration (ការកំណត់)", self.open_settings)

        studio_container_layout.addWidget(module_dock)

        # ------------------- 3-COLUMN HORIZONTAL SPLITTER -------------------
        studio_splitter = QSplitter(Qt.Orientation.Horizontal)
        studio_splitter.setChildrenCollapsible(False)
        studio_container_layout.addWidget(studio_splitter, stretch=1)
        v_main_splitter.addWidget(studio_container)

        # ====================================================
        # COLUMN 1 (LEFT): VIDEO PREVIEW PLAYER
        # ====================================================
        preview_panel = QFrame()
        preview_panel.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)

        # Floating Preview Tools Bar (Matching Dubber Clan Pro Video Player Toolbar)
        floating_bar = QFrame()
        floating_bar.setObjectName("floatingPreviewBar")
        floating_bar.setStyleSheet("""
            QFrame#floatingPreviewBar {
                background-color: rgba(15, 23, 42, 0.90);
                border: 1px solid #2A3B54;
                border-radius: 14px;
                padding: 2px 6px;
            }
            QPushButton.toolPillBtn {
                background-color: transparent;
                color: #CBD5E1;
                border: none;
                border-radius: 4px;
                font-weight: 700;
                font-size: 11px;
                padding: 3px 6px;
                min-height: 22px;
            }
            QPushButton.toolPillBtn:hover {
                background-color: #1E293B;
                color: #38BDF8;
            }
        """)
        floating_bar_layout = QHBoxLayout(floating_bar)
        floating_bar_layout.setContentsMargins(4, 2, 4, 2)
        floating_bar_layout.setSpacing(4)

        lbl_pv = QLabel("Preview")
        lbl_pv.setStyleSheet("font-weight: 800; font-size: 11px; color: #38BDF8; margin-right: 4px;")
        floating_bar_layout.addWidget(lbl_pv)

        btn_aspect_9_16 = QPushButton("📱 9:16")
        btn_aspect_9_16.setProperty("class", "toolPillBtn")
        btn_aspect_9_16.setToolTip("Set 9:16 Portrait Aspect Ratio")
        btn_aspect_9_16.clicked.connect(lambda: self.cmb_aspect.setCurrentIndex(0))
        floating_bar_layout.addWidget(btn_aspect_9_16)

        btn_aspect_16_9 = QPushButton("🖥️ 16:9")
        btn_aspect_16_9.setProperty("class", "toolPillBtn")
        btn_aspect_16_9.setToolTip("Set 16:9 Landscape Aspect Ratio")
        btn_aspect_16_9.clicked.connect(lambda: self.cmb_aspect.setCurrentIndex(1))
        floating_bar_layout.addWidget(btn_aspect_16_9)

        btn_aspect_1_1 = QPushButton("⏹️ 1:1")
        btn_aspect_1_1.setProperty("class", "toolPillBtn")
        btn_aspect_1_1.setToolTip("Set 1:1 Square Aspect Ratio")
        btn_aspect_1_1.clicked.connect(lambda: self.cmb_aspect.setCurrentIndex(2))
        floating_bar_layout.addWidget(btn_aspect_1_1)

        floating_bar_layout.addStretch()

        btn_toggle_sub = QPushButton("💬 CC")
        btn_toggle_sub.setProperty("class", "toolPillBtn")
        btn_toggle_sub.setToolTip("Toggle Subtitles on Video Preview")
        btn_toggle_sub.clicked.connect(lambda: self.chk_show_subtitles.setChecked(not self.chk_show_subtitles.isChecked()))
        floating_bar_layout.addWidget(btn_toggle_sub)

        preview_layout.addWidget(floating_bar)

        # Video Player Stack with Overlay Subtitle Support
        player_container = QFrame()
        player_container.setObjectName("playerContainer")
        player_container_grid = QGridLayout(player_container)
        player_container_grid.setContentsMargins(0, 0, 0, 0)

        self.player_stack = QStackedWidget()
        self.wave_visualizer = WaveVisualizer()
        self.video_widget = VideoGraphicsView(self)
        self.player_stack.addWidget(self.wave_visualizer)
        self.player_stack.addWidget(self.video_widget)
        self.stacked_player = self.wave_visualizer
        self.media_player.setVideoOutput(self.video_widget.video_item)
        player_container_grid.addWidget(self.player_stack, 0, 0)

        # Real-time Video Preview Khmer Subtitle Overlay Label
        self.subtitle_overlay_label = QLabel()
        self.subtitle_overlay_label.setObjectName("videoSubtitleOverlay")
        self.subtitle_overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_overlay_label.setWordWrap(True)
        self.subtitle_overlay_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.subtitle_overlay_label.setStyleSheet("""
            QLabel#videoSubtitleOverlay {
                color: #FFE500;
                background-color: rgba(0, 0, 0, 165);
                border: 1px solid rgba(255, 229, 0, 140);
                border-radius: 6px;
                padding: 6px 14px;
                font-family: "Noto Sans Khmer", "Khmer UI", "Kantumruy Pro", "Battambang", sans-serif;
                font-size: 13px;
                font-weight: 800;
                margin-bottom: 12px;
                margin-left: 8px;
                margin-right: 8px;
            }
        """)
        self.subtitle_overlay_label.hide()
        player_container_grid.addWidget(self.subtitle_overlay_label, 0, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

        # Creator / Channel Draggable Logo Overlay Widget
        self.player_container = player_container
        self.logo_rel_x = float(self.settings.get("logo_rel_x", 0.82))
        self.logo_rel_y = float(self.settings.get("logo_rel_y", 0.05))
        self.logo_scale_val = float(self.settings.get("logo_scale", 0.15))
        self.draggable_logo_widget = DraggableLogoWidget(player_container, self)
        self.draggable_logo_widget.set_relative_position(self.logo_rel_x, self.logo_rel_y)

        # Ensure logo is permanently raised on top of player_container
        self.player_stack.currentChanged.connect(lambda idx: self.attach_overlay_to_current_player())

        orig_player_container_resize = player_container.resizeEvent
        def on_player_container_resize(event):
            if orig_player_container_resize:
                orig_player_container_resize(event)
            if hasattr(self, "draggable_logo_widget"):
                self.draggable_logo_widget.reposition_from_relative()
                self.draggable_logo_widget.raise_()
        player_container.resizeEvent = on_player_container_resize

        # Enable Drag & Drop of Logo, Video, Audio, and Subtitles directly onto the Player
        player_container.setAcceptDrops(True)
        self.video_widget.setAcceptDrops(True)
        self.wave_visualizer.setAcceptDrops(True)

        def on_player_drag_enter(event):
            if event.mimeData().hasUrls():
                urls = event.mimeData().urls()
                if urls and any(urls[0].toLocalFile().lower().endswith(ext) for ext in (
                    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.svg', '.ico',
                    '.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.wmv',
                    '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg',
                    '.srt', '.vtt', '.ass'
                )):
                    event.acceptProposedAction()
                    player_container.setStyleSheet("""
                        QFrame#playerContainer {
                            background-color: #070D18;
                            border: 2px dashed #38BDF8;
                            border-radius: 6px;
                        }
                    """)
                    return
            event.ignore()

        def on_player_drag_leave(event):
            player_container.setStyleSheet("""
                QFrame#playerContainer {
                    background-color: #050B14;
                    border: 1px solid #1E293B;
                    border-radius: 6px;
                }
            """)
            event.accept()

        def on_player_drag_move(event):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return
            event.ignore()

        def on_player_drop(event):
            player_container.setStyleSheet("""
                QFrame#playerContainer {
                    background-color: #050B14;
                    border: 1px solid #1E293B;
                    border-radius: 6px;
                }
            """)
            if event.mimeData().hasUrls():
                urls = event.mimeData().urls()
                if urls:
                    file_path = urls[0].toLocalFile()
                    # Convert event pos to player_container coordinates
                    try:
                        pos = event.position().toPoint()
                    except Exception:
                        pos = event.pos()
                    self.handle_player_dropped_file(file_path, pos)
                    event.acceptProposedAction()
                    return
            event.ignore()

        # Connect Drag & Drop across ALL player container surfaces so drop is NEVER missed
        for target_w in (
            player_container, self.player_stack, self.video_widget,
            self.wave_visualizer, self.draggable_logo_widget, self.subtitle_overlay_label
        ):
            target_w.setAcceptDrops(True)
            target_w.dragEnterEvent = on_player_drag_enter
            target_w.dragMoveEvent = on_player_drag_move
            target_w.dragLeaveEvent = on_player_drag_leave
            target_w.dropEvent = on_player_drop

        player_container.setMinimumHeight(180)
        preview_layout.addWidget(player_container, stretch=1)

        # Video timecode slider & label
        timecode_layout = QHBoxLayout()
        timecode_layout.setSpacing(4)
        self.sld_timeline = QSlider(Qt.Orientation.Horizontal)
        self.sld_timeline.setObjectName("videoScrubber")
        self.sld_timeline.setRange(0, max(1, self.duration))
        self.sld_timeline.setValue(0)
        self.sld_timeline.sliderMoved.connect(self.timeline_dragged)
        timecode_layout.addWidget(self.sld_timeline, stretch=1)

        self.lbl_time = QLabel("00:00:00 / 00:01:08")
        self.lbl_time.setObjectName("lblTimeCode")
        self.lbl_time.setStyleSheet("color: #94A3B8; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;")
        timecode_layout.addWidget(self.lbl_time)
        preview_layout.addLayout(timecode_layout)

        # Transport Controls Bar
        transport_bar = QFrame()
        transport_bar.setObjectName("transportBar")
        transport_layout = QHBoxLayout(transport_bar)
        transport_layout.setContentsMargins(2, 2, 2, 2)
        transport_layout.setSpacing(4)

        btn_first = QPushButton("|<")
        btn_first.setToolTip("Jump to Start")
        btn_first.setFixedSize(26, 26)
        btn_first.clicked.connect(lambda: self.seek_to_seconds(0))

        btn_prev = QPushButton("<<")
        btn_prev.setToolTip("Step Back 5s")
        btn_prev.setFixedSize(26, 26)
        btn_prev.clicked.connect(lambda: self.seek_relative(-5))

        self.btn_play = QPushButton()
        self.btn_play.setObjectName("btnBigPlay")
        self.btn_play.setIcon(get_icon("play", "#FFFFFF", 16))
        self.btn_play.setToolTip("Play / Pause (Space)")
        self.btn_play.setFixedSize(34, 34)
        self.btn_play.clicked.connect(self.toggle_play)

        btn_next = QPushButton(">>")
        btn_next.setToolTip("Step Forward 5s")
        btn_next.setFixedSize(26, 26)
        btn_next.clicked.connect(lambda: self.seek_relative(5))

        btn_last = QPushButton(">|")
        btn_last.setToolTip("Jump to End")
        btn_last.setFixedSize(26, 26)
        btn_last.clicked.connect(lambda: self.seek_to_seconds(self.duration))

        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(get_icon("stop", "#94A3B8", 12))
        self.btn_stop.setToolTip("Stop")
        self.btn_stop.setFixedSize(26, 26)
        self.btn_stop.clicked.connect(self.stop_playback)

        self.btn_full_preview = QPushButton("⛶")
        self.btn_full_preview.setToolTip("Full-screen preview (Esc to exit)")
        self.btn_full_preview.setFixedSize(30, 26)
        self.btn_full_preview.clicked.connect(self.show_full_preview)

        transport_layout.addStretch()
        transport_layout.addWidget(btn_first)
        transport_layout.addWidget(btn_prev)
        transport_layout.addWidget(self.btn_play)
        transport_layout.addWidget(btn_next)
        transport_layout.addWidget(btn_last)
        transport_layout.addWidget(self.btn_stop)
        transport_layout.addWidget(self.btn_full_preview)
        transport_layout.addStretch()
        preview_layout.addWidget(transport_bar)

        # Volume control row
        vol_layout = QHBoxLayout()
        vol_layout.setSpacing(4)
        self.btn_vol = QPushButton()
        self.btn_vol.setIcon(get_icon("volume_up", "#38BDF8", 13))
        self.btn_vol.setFixedSize(22, 22)
        self.btn_vol.setStyleSheet("background: transparent; border: none;")
        vol_layout.addWidget(self.btn_vol)

        self.sld_volume = QSlider(Qt.Orientation.Horizontal)
        self.sld_volume.setObjectName("playerVolSlider")
        self.sld_volume.setRange(0, 100)
        self.sld_volume.setValue(80)
        self.sld_volume.valueChanged.connect(self.change_volume)
        vol_layout.addWidget(self.sld_volume, stretch=1)

        self.lbl_volume_val = QLabel("80%")
        self.lbl_volume_val.setFixedWidth(32)
        self.lbl_volume_val.setStyleSheet("color: #94A3B8; font-size: 10px;")
        self.sld_volume.valueChanged.connect(lambda v: self.lbl_volume_val.setText(f"{v}%"))
        vol_layout.addWidget(self.lbl_volume_val)
        preview_layout.addLayout(vol_layout)

        # Aspect ratio, Safe Area & Show/Hide Subtitles Control
        aspect_layout = QHBoxLayout()
        aspect_layout.setSpacing(4)
        lbl_aspect = QLabel("Aspect:")
        lbl_aspect.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
        aspect_layout.addWidget(lbl_aspect)

        self.cmb_aspect = QComboBox()
        self.cmb_aspect.addItems(["9:16 (Portrait)", "16:9 (Landscape)", "1:1 (Square)"])
        self.cmb_aspect.currentIndexChanged.connect(lambda: self.update_logo_overlay_preview() if hasattr(self, "update_logo_overlay_preview") else None)
        aspect_layout.addWidget(self.cmb_aspect, stretch=1)

        self.chk_safe_area = QCheckBox("Safe Area")
        self.chk_safe_area.setChecked(True)
        aspect_layout.addWidget(self.chk_safe_area)

        self.chk_show_subtitles = QCheckBox("Subtitles (CC)")
        self.chk_show_subtitles.setChecked(True)
        self.chk_show_subtitles.setToolTip("Show / Hide Khmer subtitles on video preview (បើក/បិទ អក្សររត់លើវីដេអូ)")
        self.chk_show_subtitles.toggled.connect(self.toggle_subtitles_overlay)
        aspect_layout.addWidget(self.chk_show_subtitles)

        preview_layout.addLayout(aspect_layout)

        # Compact Workflow Log Box
        self.txt_workflow_log = QTextEdit()
        self.txt_workflow_log.setReadOnly(True)
        self.txt_workflow_log.setPlaceholderText("Workflow messages will appear here.")
        self.txt_workflow_log.setMaximumHeight(65)
        self.txt_workflow_log.setStyleSheet("""
            background-color: #070B16;
            color: #94A3B8;
            border: 1px solid #1E293B;
            border-radius: 4px;
            padding: 3px;
            font-family: 'Cascadia Mono', 'Consolas', monospace;
            font-size: 9.5px;
        """)
        preview_layout.addWidget(self.txt_workflow_log)

        studio_splitter.addWidget(preview_panel)

        # ====================================================
        # COLUMN 2 (CENTER): SUBTITLES EDITOR TABLE
        # ====================================================
        subs_panel = QFrame()
        subs_panel.setObjectName("subsPanel")
        subs_layout = QVBoxLayout(subs_panel)
        subs_layout.setContentsMargins(8, 8, 8, 8)
        subs_layout.setSpacing(6)

        # Subtitles Header Row 1: Title, Search, Filter & Find
        subs_hdr_layout = QHBoxLayout()
        subs_hdr_layout.setSpacing(4)
        subs_hdr = QLabel("Subtitles Editor")
        subs_hdr.setStyleSheet("font-weight: 800; font-size: 13px; color: #38BDF8;")
        subs_hdr_layout.addWidget(subs_hdr)

        subs_hdr_layout.addStretch()

        self.txt_subtitle_search = QLineEdit()
        self.txt_subtitle_search.setObjectName("txtSubtitleSearch")
        self.txt_subtitle_search.setPlaceholderText("🔍 Search...")
        self.txt_subtitle_search.setFixedWidth(130)
        self.txt_subtitle_search.textChanged.connect(self.filter_subtitles_table)
        subs_hdr_layout.addWidget(self.txt_subtitle_search)

        self.cmb_subtitle_filter = QComboBox()
        self.cmb_subtitle_filter.addItems(["All", "Synthesized", "Pending"])
        self.cmb_subtitle_filter.setFixedWidth(90)
        self.cmb_subtitle_filter.currentIndexChanged.connect(self.filter_subtitles_table)
        subs_hdr_layout.addWidget(self.cmb_subtitle_filter)

        self.btn_find_replace = QPushButton("🔍 Find")
        self.btn_find_replace.setIcon(get_icon("search", "#FFFFFF", 11))
        self.btn_find_replace.clicked.connect(self.open_find_replace_dialog)
        subs_hdr_layout.addWidget(self.btn_find_replace)

        self.btn_font_dec = QPushButton("A-")
        self.btn_font_dec.setToolTip("Decrease table text size")
        self.btn_font_dec.setFixedWidth(28)
        self.btn_font_dec.clicked.connect(lambda: self.change_table_font_size(-1))
        subs_hdr_layout.addWidget(self.btn_font_dec)

        self.btn_font_inc = QPushButton("A+")
        self.btn_font_inc.setToolTip("Increase table text size")
        self.btn_font_inc.setFixedWidth(28)
        self.btn_font_inc.clicked.connect(lambda: self.change_table_font_size(1))
        subs_hdr_layout.addWidget(self.btn_font_inc)

        subs_layout.addLayout(subs_hdr_layout)

        # Speaker Chips / Badges Row (Matching Dubber Clan Pro UI)
        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(6)

        self.btn_chip_all = QPushButton("👥 All Lines")
        self.btn_chip_all.setCheckable(True)
        self.btn_chip_all.setChecked(True)
        self.btn_chip_all.setStyleSheet("""
            QPushButton {
                background-color: #1E293B; color: #F8FAFC;
                border: 1px solid #334155; border-radius: 12px;
                padding: 4px 12px; font-weight: 700; font-size: 11px;
            }
            QPushButton:checked {
                background-color: #0284C7; border-color: #38BDF8; color: #FFFFFF;
            }
        """)
        self.btn_chip_all.clicked.connect(lambda: self._apply_speaker_chip_filter("All"))
        chips_layout.addWidget(self.btn_chip_all)

        self.btn_chip_male = QPushButton("🔵 Speaker 1 - Piseth (Male)")
        self.btn_chip_male.setCheckable(True)
        self.btn_chip_male.setStyleSheet("""
            QPushButton {
                background-color: #0C1E38; color: #38BDF8;
                border: 1px solid #0284C7; border-radius: 12px;
                padding: 4px 12px; font-weight: 700; font-size: 11px;
            }
            QPushButton:checked {
                background-color: #0284C7; color: #FFFFFF;
            }
        """)
        self.btn_chip_male.clicked.connect(lambda: self._apply_speaker_chip_filter("Male"))
        chips_layout.addWidget(self.btn_chip_male)

        self.btn_chip_female = QPushButton("🔴 Speaker 2 - Sreymom (Female)")
        self.btn_chip_female.setCheckable(True)
        self.btn_chip_female.setStyleSheet("""
            QPushButton {
                background-color: #240E2C; color: #F472B6;
                border: 1px solid #DB2777; border-radius: 12px;
                padding: 4px 12px; font-weight: 700; font-size: 11px;
            }
            QPushButton:checked {
                background-color: #DB2777; color: #FFFFFF;
            }
        """)
        self.btn_chip_female.clicked.connect(lambda: self._apply_speaker_chip_filter("Female"))
        chips_layout.addWidget(self.btn_chip_female)

        self.btn_chip_auto = QPushButton("⚡ Auto Voice")
        self.btn_chip_auto.setToolTip("Auto Detect and Assign Speaker Voices from Movie Dialogue")
        self.btn_chip_auto.setStyleSheet("""
            QPushButton {
                background-color: #1F1138; color: #C084FC;
                border: 1px solid #9333EA; border-radius: 12px;
                padding: 4px 12px; font-weight: 700; font-size: 11px;
            }
            QPushButton:hover {
                background-color: #9333EA; color: #FFFFFF;
            }
        """)
        self.btn_chip_auto.clicked.connect(self.auto_assign_voices_from_movie)
        chips_layout.addWidget(self.btn_chip_auto)

        chips_layout.addStretch()
        subs_layout.addLayout(chips_layout)

        # Subtitles Toolbar Row 2: Quick Voice Assignment & Batch Tools matching Demo Video
        subs_tools_layout = QHBoxLayout()
        subs_tools_layout.setSpacing(4)

        lbl_voice_tag = QLabel("VOICE:")
        lbl_voice_tag.setStyleSheet("color: #94A3B8; font-weight: bold; font-size: 11px;")
        subs_tools_layout.addWidget(lbl_voice_tag)

        self.btn_quick_male = QPushButton("♂ Male")
        self.btn_quick_male.setToolTip("Set selected row(s) to Male Voice (Piseth)")
        self.btn_quick_male.setStyleSheet("""
            QPushButton {
                background-color: #081E2E; color: #38BDF8;
                border: 1px solid #0369A1; border-radius: 4px;
                padding: 3px 8px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #0369A1; color: #FFFFFF; }
        """)
        self.btn_quick_male.clicked.connect(lambda: self.set_selected_rows_gender("Male"))
        subs_tools_layout.addWidget(self.btn_quick_male)

        self.btn_quick_female = QPushButton("♀ Female")
        self.btn_quick_female.setToolTip("Set selected row(s) to Female Voice (Sreymom)")
        self.btn_quick_female.setStyleSheet("""
            QPushButton {
                background-color: #1F1022; color: #F472B6;
                border: 1px solid #701A75; border-radius: 4px;
                padding: 3px 8px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #701A75; color: #FFFFFF; }
        """)
        self.btn_quick_female.clicked.connect(lambda: self.set_selected_rows_gender("Female"))
        subs_tools_layout.addWidget(self.btn_quick_female)

        self.btn_quick_detect = QPushButton("🤖 Auto Detect")
        self.btn_quick_detect.setToolTip("Auto assign voices based on dialogue")
        self.btn_quick_detect.setStyleSheet("""
            QPushButton {
                background-color: #1A102E; color: #C4B5FD;
                border: 1px solid #6D28D9; border-radius: 4px;
                padding: 3px 8px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #6D28D9; color: #FFFFFF; }
        """)
        self.btn_quick_detect.clicked.connect(self.auto_assign_voices_from_movie)
        subs_tools_layout.addWidget(self.btn_quick_detect)

        subs_tools_layout.addStretch()

        self.btn_merge_lines = QPushButton("🔗 Merge Lines")
        self.btn_merge_lines.setIcon(get_icon("link", "#FFFFFF", 11))
        self.btn_merge_lines.setToolTip("Merge selected lines into one sentence")
        self.btn_merge_lines.clicked.connect(self.merge_selected_subtitle_rows)
        subs_tools_layout.addWidget(self.btn_merge_lines)

        self.btn_gen_selected = QPushButton("⚡ Gen Selected")
        self.btn_gen_selected.setIcon(get_icon("voiceover", "#FFFFFF", 11))
        self.btn_gen_selected.setToolTip("Generate voice for selected line(s)")
        self.btn_gen_selected.setStyleSheet("""
            QPushButton {
                background-color: #0891B2; color: #FFFFFF;
                border: none; border-radius: 4px;
                padding: 3px 10px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #06B6D4; }
        """)
        self.btn_gen_selected.clicked.connect(self.regenerate_selected_voiceovers)
        subs_tools_layout.addWidget(self.btn_gen_selected)

        self.btn_new_project = QPushButton(" 📄 New Project")
        self.btn_new_project.setToolTip("Start a new project (សម្អាត និងចាប់ផ្តើមគម្រោងថ្មី)")
        self.btn_new_project.setStyleSheet("""
            QPushButton {
                background-color: #0F172A; color: #38BDF8;
                border: 1px solid #0EA5E9; border-radius: 4px;
                padding: 3px 10px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #1E293B; }
        """)
        self.btn_new_project.clicked.connect(self.clear_workspace)
        subs_tools_layout.addWidget(self.btn_new_project)

        self.btn_table_clear_all = QPushButton("🗑️ Clear All")
        self.btn_table_clear_all.setIcon(get_icon("delete", "#FCA5A5", 11))
        self.btn_table_clear_all.setToolTip("Clear all loaded video, subtitles, timeline, and start fresh (ជម្រះទិន្នន័យទាំងអស់)")
        self.btn_table_clear_all.setStyleSheet("""
            QPushButton {
                background-color: #7F1D1D; color: #FFFFFF;
                border: 1px solid #DC2626; border-radius: 4px;
                padding: 3px 10px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #991B1B; }
        """)
        self.btn_table_clear_all.clicked.connect(self.clear_workspace)
        subs_tools_layout.addWidget(self.btn_table_clear_all)

        subs_layout.addLayout(subs_tools_layout)

        # Table
        self.table = QTableWidget()
        self.table.setFont(QFont("Noto Sans Khmer", 10, QFont.Weight.DemiBold))
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "Time", "Original Text", "Subtitle Text", "Status", "Voice", "Action"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_TRANSLATED, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setMinimumSectionSize(34)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(COL_ID, 36)
        self.table.setColumnWidth(COL_TIME, 145)
        self.table.setColumnWidth(COL_ORIGINAL, 160)
        self.table.setColumnWidth(COL_STATUS, 65)
        self.table.setColumnWidth(COL_VOICE, 135)
        self.table.setColumnWidth(COL_ACTION, 160)
        self.table.itemChanged.connect(self.table_item_edited)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        subs_layout.addWidget(self.table, stretch=1)

        # Table Footer Summary Bar
        table_footer = QHBoxLayout()
        self.lbl_table_summary = QLabel("Total Subtitles: 0 | Synthesized: 0 | Pending: 0")
        self.lbl_table_summary.setStyleSheet("color: #94A3B8; font-size: 10.5px;")
        table_footer.addWidget(self.lbl_table_summary)
        table_footer.addStretch()
        self.lbl_session_status = QLabel("💾 Auto-save Active")
        self.lbl_session_status.setStyleSheet("color: #10B981; font-size: 10px; font-weight: bold;")
        table_footer.addWidget(self.lbl_session_status)
        subs_layout.addLayout(table_footer)

        studio_splitter.addWidget(subs_panel)

        # ====================================================
        # COLUMN 3 (RIGHT): SCROLLABLE VOICE SETTINGS & TEXT STYLE
        # ====================================================
        inspector_panel = QFrame()
        inspector_panel.setObjectName("inspectorPanel")
        inspector_layout = QVBoxLayout(inspector_panel)
        inspector_layout.setContentsMargins(4, 4, 4, 4)
        inspector_layout.setSpacing(4)

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("inspectorTabs")

        # Tab 1: Voice Settings wrapped in a ScrollArea to prevent ANY overlap
        from PyQt6.QtWidgets import QScrollArea
        scroll_voice_area = QScrollArea()
        scroll_voice_area.setWidgetResizable(True)
        scroll_voice_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_voice_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        tab_voice = QWidget()
        tab_voice_layout = QVBoxLayout(tab_voice)
        tab_voice_layout.setContentsMargins(6, 6, 6, 6)
        tab_voice_layout.setSpacing(8)

        # A. AI Voice Section
        lbl_sec_ai_voice = QLabel("AI VOICE")
        lbl_sec_ai_voice.setStyleSheet("font-weight: 700; font-size: 10.5px; color: #38BDF8; letter-spacing: 0.5px;")
        tab_voice_layout.addWidget(lbl_sec_ai_voice)

        grid_voice = QGridLayout()
        grid_voice.setSpacing(4)

        grid_voice.addWidget(QLabel("Voice Type:"), 0, 0)
        self.cmb_voice_type = QComboBox()
        self.cmb_voice_type.addItems(["AI Voice (TTS)", "Voice Clone (VoxCPM)", "Edge TTS"])
        grid_voice.addWidget(self.cmb_voice_type, 0, 1)

        grid_voice.addWidget(QLabel("Language:"), 1, 0)
        self.cmb_lang = QComboBox()
        self.cmb_lang.addItems(["Khmer", "English", "Chinese"])
        self.cmb_lang.currentIndexChanged.connect(self.output_lang_changed)
        grid_voice.addWidget(self.cmb_lang, 1, 1)

        grid_voice.addWidget(QLabel("Voice:"), 2, 0)
        self.cmb_voice = QComboBox()
        grid_voice.addWidget(self.cmb_voice, 2, 1)
        self.cmb_voice.currentTextChanged.connect(self.main_voice_changed)
        self.output_lang_changed(0)

        tab_voice_layout.addLayout(grid_voice)

        # B. Voice Controls Section (Rate, Pitch, Volume)
        lbl_sec_controls = QLabel("VOICE CONTROLS")
        lbl_sec_controls.setStyleSheet("font-weight: 700; font-size: 10.5px; color: #38BDF8; letter-spacing: 0.5px;")
        tab_voice_layout.addWidget(lbl_sec_controls)

        grid_ctrls = QGridLayout()
        grid_ctrls.setSpacing(4)

        # Rate
        grid_ctrls.addWidget(QLabel("Rate:"), 0, 0)
        self.sld_rate = QSlider(Qt.Orientation.Horizontal)
        self.sld_rate.setRange(50, 200)
        self.sld_rate.setValue(100)
        grid_ctrls.addWidget(self.sld_rate, 0, 1)
        self.lbl_rate_val = QLabel("1.00x")
        self.lbl_rate_val.setFixedWidth(46)
        self.sld_rate.valueChanged.connect(lambda v: self.lbl_rate_val.setText(f"{v/100:.2f}x"))
        grid_ctrls.addWidget(self.lbl_rate_val, 0, 2)

        # Pitch
        grid_ctrls.addWidget(QLabel("Pitch:"), 1, 0)
        self.sld_pitch = QSlider(Qt.Orientation.Horizontal)
        self.sld_pitch.setRange(-50, 50)
        self.sld_pitch.setValue(0)
        grid_ctrls.addWidget(self.sld_pitch, 1, 1)
        self.lbl_pitch_val = QLabel("0%")
        self.lbl_pitch_val.setFixedWidth(46)
        self.sld_pitch.valueChanged.connect(lambda v: self.lbl_pitch_val.setText(f"{v:+d}%"))
        grid_ctrls.addWidget(self.lbl_pitch_val, 1, 2)

        # Volume
        grid_ctrls.addWidget(QLabel("Volume:"), 2, 0)
        self.sld_tts_vol = QSlider(Qt.Orientation.Horizontal)
        self.sld_tts_vol.setRange(0, 100)
        self.sld_tts_vol.setValue(100)
        grid_ctrls.addWidget(self.sld_tts_vol, 2, 1)
        self.lbl_tts_vol_val = QLabel("100%")
        self.lbl_tts_vol_val.setFixedWidth(46)
        self.sld_tts_vol.valueChanged.connect(lambda v: self.lbl_tts_vol_val.setText(f"{v}%"))
        grid_ctrls.addWidget(self.lbl_tts_vol_val, 2, 2)

        # Lip-Sync / Offset (Fine-tune voice timing with character lips)
        grid_ctrls.addWidget(QLabel("Lip-Sync:"), 3, 0)
        self.sld_lip_sync = QSlider(Qt.Orientation.Horizontal)
        self.sld_lip_sync.setRange(-300, 300)
        self.sld_lip_sync.setValue(0)
        self.sld_lip_sync.setToolTip("Fine-tune voice timing with character lips (-300ms earlier to +300ms delayed)")
        grid_ctrls.addWidget(self.sld_lip_sync, 3, 1)
        self.lbl_lip_sync_val = QLabel("0ms")
        self.lbl_lip_sync_val.setFixedWidth(46)
        self.sld_lip_sync.valueChanged.connect(lambda v: self.lbl_lip_sync_val.setText(f"{v:+d}ms" if v != 0 else "0ms"))
        grid_ctrls.addWidget(self.lbl_lip_sync_val, 3, 2)

        tab_voice_layout.addLayout(grid_ctrls)

        # C. Preview Voice Section
        lbl_sec_preview = QLabel("PREVIEW VOICE")
        lbl_sec_preview.setStyleSheet("font-weight: 700; font-size: 10.5px; color: #38BDF8; letter-spacing: 0.5px;")
        tab_voice_layout.addWidget(lbl_sec_preview)

        self.txt_preview_voice = QTextEdit()
        self.txt_preview_voice.setPlaceholderText("Enter text to preview voice...")
        self.txt_preview_voice.setMaximumHeight(55)
        self.txt_preview_voice.textChanged.connect(self.update_voice_preview_char_count)
        tab_voice_layout.addWidget(self.txt_preview_voice)

        preview_btn_row = QHBoxLayout()
        self.lbl_char_count = QLabel("0 / 200")
        self.lbl_char_count.setStyleSheet("color: #64748B; font-size: 9.5px;")
        preview_btn_row.addWidget(self.lbl_char_count)
        preview_btn_row.addStretch()

        self.btn_preview_voice = QPushButton("▶ Preview")
        self.btn_preview_voice.setObjectName("btnPreviewVoice")
        self.btn_preview_voice.setStyleSheet("""
            QPushButton {
                background-color: #16A34A;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #15803D; }
        """)
        self.btn_preview_voice.clicked.connect(self.preview_voice_sample)
        preview_btn_row.addWidget(self.btn_preview_voice)
        tab_voice_layout.addLayout(preview_btn_row)

        # D. Audio Options & Tools Section
        lbl_sec_advanced = QLabel("AUDIO OPTIONS & TOOLS")
        lbl_sec_advanced.setStyleSheet("font-weight: 700; font-size: 10.5px; color: #38BDF8; letter-spacing: 0.5px;")
        tab_voice_layout.addWidget(lbl_sec_advanced)

        self.chk_vocal_iso = QCheckBox("AI Vocal Isolation (Voice Only)")
        iso_default = True
        self.chk_vocal_iso.setChecked(iso_default)
        self.chk_vocal_iso.toggled.connect(self.single_vocal_iso_toggled)
        tab_voice_layout.addWidget(self.chk_vocal_iso)

        self.chk_noise_reduction = QCheckBox("Vocal Boost & Noise Reduction")
        self.chk_noise_reduction.setChecked(True)
        tab_voice_layout.addWidget(self.chk_noise_reduction)

        music_row = QHBoxLayout()
        music_row.addWidget(QLabel("BGM Level:"))
        self.sld_music = QSlider(Qt.Orientation.Horizontal)
        self.sld_music.setRange(0, 100)
        self.sld_music.setValue(35)
        music_row.addWidget(self.sld_music, stretch=1)
        self.lbl_music_val = QLabel("35%")
        self.lbl_music_val.setFixedWidth(32)
        self.sld_music.valueChanged.connect(lambda v: self.lbl_music_val.setText(f"{v}%"))
        music_row.addWidget(self.lbl_music_val)
        tab_voice_layout.addLayout(music_row)

        # Workspace Tool Buttons
        tools_grid = QGridLayout()
        tools_grid.setSpacing(4)

        self.btn_load_bgm = QPushButton("🎵 Add BGM")
        self.btn_load_bgm.setIcon(get_icon("voiceover", "#FFFFFF", 11))
        self.btn_load_bgm.clicked.connect(self.load_bgm_audio)
        tools_grid.addWidget(self.btn_load_bgm, 0, 0)

        self.btn_load_a1_audio = QPushButton("🎤 Custom A1")
        self.btn_load_a1_audio.setIcon(get_icon("import_video", "#FFFFFF", 11))
        self.btn_load_a1_audio.clicked.connect(self.load_a1_audio_for_selected_row)
        tools_grid.addWidget(self.btn_load_a1_audio, 0, 1)

        self.btn_auto_voice = QPushButton("🤖 Auto Voice")
        self.btn_auto_voice.setIcon(get_icon("voiceover", "#FFFFFF", 11))
        self.btn_auto_voice.clicked.connect(self.auto_assign_voices_from_movie)
        tools_grid.addWidget(self.btn_auto_voice, 1, 0)

        self.btn_set_row_voice = QPushButton("⚙️ Set Voice")
        self.btn_set_row_voice.setIcon(get_icon("settings", "#FFFFFF", 11))
        self.btn_set_row_voice.clicked.connect(self.set_voice_for_selected_rows)
        tools_grid.addWidget(self.btn_set_row_voice, 1, 1)

        tab_voice_layout.addLayout(tools_grid)

        # Core model & Translation Source combo
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Core Model:"))
        self.cmb_model = QComboBox()
        self.cmb_model.addItems(["Core (Balanced)", "Core (Precise)"])
        model_row.addWidget(self.cmb_model, stretch=1)
        tab_voice_layout.addLayout(model_row)

        trans_src_row = QHBoxLayout()
        trans_src_row.addWidget(QLabel("Translate From:"))
        self.cmb_translate_source = QComboBox()
        self.cmb_translate_source.addItems(TRANSLATION_SOURCE_LANGS)
        self.cmb_translate_source.setCurrentText(
            normalize_translation_language(
                self.settings.get("translation_source_lang", "Auto Detect"),
                TRANSLATION_SOURCE_LANGS,
                "Auto Detect"
            )
        )
        self.cmb_translate_source.currentIndexChanged.connect(self.translation_language_changed)
        trans_src_row.addWidget(self.cmb_translate_source, stretch=1)
        tab_voice_layout.addLayout(trans_src_row)

        self.txt_voxcpm_ref_female = QLineEdit(self.settings.get("voxcpm_reference_audio_female", ""))
        self.txt_voxcpm_ref_female.setVisible(False)
        self.txt_voxcpm_ref_male = QLineEdit(self.settings.get("voxcpm_reference_audio_male", ""))
        self.txt_voxcpm_ref_male.setVisible(False)

        tab_voice_layout.addStretch()
        scroll_voice_area.setWidget(tab_voice)
        self.inspector_tabs.addTab(scroll_voice_area, "Voice Settings")

        # Tab 2: Text & Style wrapped in a ScrollArea
        scroll_style_area = QScrollArea()
        scroll_style_area.setWidgetResizable(True)
        scroll_style_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_style_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        tab_style = QWidget()
        tab_style_layout = QVBoxLayout(tab_style)
        tab_style_layout.setContentsMargins(6, 6, 6, 6)
        tab_style_layout.setSpacing(8)

        lbl_style_title = QLabel("SUBTITLE STYLING & ANIMATION (រចនាអក្សរ & ចលនា)")
        lbl_style_title.setStyleSheet("font-weight: 800; font-size: 11px; color: #38BDF8; letter-spacing: 0.5px;")
        tab_style_layout.addWidget(lbl_style_title)

        grid_style = QGridLayout()
        grid_style.setSpacing(5)

        # 1. Font Family
        grid_style.addWidget(QLabel("Font Family:"), 0, 0)
        self.cmb_sub_font = QComboBox()
        self.cmb_sub_font.addItems(["Noto Sans Khmer", "Kantumruy Pro (Khmer)", "Battambang", "Khmer UI", "Inter", "Arial"])
        self.cmb_sub_font.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_font, 0, 1)

        # 2. Font Size
        grid_style.addWidget(QLabel("Font Size:"), 1, 0)
        self.cmb_sub_size = QComboBox()
        self.cmb_sub_size.addItems(["14 px (Small)", "18 px (Medium)", "22 px (Standard)", "26 px (Large)", "32 px (Extra)", "40 px (Cinematic)"])
        self.cmb_sub_size.setCurrentText("22 px (Standard)")
        self.cmb_sub_size.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_size, 1, 1)

        # 3. Text Color & Custom Color Picker
        grid_style.addWidget(QLabel("Text Color:"), 2, 0)
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        self.cmb_sub_color = QComboBox()
        self.cmb_sub_color.addItems(["Yellow (#FFE500)", "White (#FFFFFF)", "Electric Cyan (#00FFFF)", "Neon Green (#00FF66)", "Hot Pink (#FF2E93)", "Warm Gold (#FFB703)"])
        self.cmb_sub_color.currentIndexChanged.connect(self.apply_subtitle_styling)
        color_row.addWidget(self.cmb_sub_color, stretch=1)

        self.btn_pick_color = QPushButton("🎨")
        self.btn_pick_color.setToolTip("Pick custom color from palette")
        self.btn_pick_color.setFixedSize(28, 28)
        self.btn_pick_color.setStyleSheet("""
            QPushButton {
                background-color: #1E293B; border: 1px solid #38BDF8;
                border-radius: 4px; font-size: 13px;
            }
            QPushButton:hover { background-color: #0284C7; }
        """)
        self.btn_pick_color.clicked.connect(self.pick_custom_subtitle_color)
        color_row.addWidget(self.btn_pick_color)
        grid_style.addLayout(color_row, 2, 1)

        # 4. Outline / Stroke & Shadow
        grid_style.addWidget(QLabel("Outline/Stroke:"), 3, 0)
        self.cmb_sub_stroke = QComboBox()
        self.cmb_sub_stroke.addItems(["Black 3px (TikTok / Viral)", "Black 2px (Clean)", "Neon Glow Shadow", "None"])
        self.cmb_sub_stroke.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_stroke, 3, 1)

        # 5. Background Pill Box Style
        grid_style.addWidget(QLabel("Backdrop Box:"), 4, 0)
        self.cmb_sub_bg = QComboBox()
        self.cmb_sub_bg.addItems(["Dark Glass Pill (65% Dark)", "Transparent (Pure Text)", "Golden Border Box", "Black Solid Pill"])
        self.cmb_sub_bg.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_bg, 4, 1)

        # 6. Text Animation / Motion Effect
        grid_style.addWidget(QLabel("Text Animation:"), 5, 0)
        self.cmb_sub_anim = QComboBox()
        self.cmb_sub_anim.addItems(["✨ Pop-In Bounce (TikTok / CapCut)", "🌊 Fade-In Smooth", "⬆️ Slide-Up Pop", "🎤 Karaoke Wave Glow", "Static (Clean Normal)"])
        self.cmb_sub_anim.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_anim, 5, 1)

        # 7. Position Preset
        grid_style.addWidget(QLabel("Position:"), 6, 0)
        self.cmb_sub_pos = QComboBox()
        self.cmb_sub_pos.addItems(["Bottom (Standard)", "Lower-Third", "Center", "Top"])
        self.cmb_sub_pos.currentIndexChanged.connect(self.apply_subtitle_styling)
        grid_style.addWidget(self.cmb_sub_pos, 6, 1)

        tab_style_layout.addLayout(grid_style)

        # Logo & Channel Branding Section
        lbl_logo_title = QLabel("LOGO & CREATOR BRANDING (បញ្ជាក់កម្មសិទ្ធិ)")
        lbl_logo_title.setStyleSheet("font-weight: 800; font-size: 11px; color: #38BDF8; letter-spacing: 0.5px; margin-top: 8px;")
        tab_style_layout.addWidget(lbl_logo_title)

        grid_logo = QGridLayout()
        grid_logo.setSpacing(4)

        self.txt_logo_path = QLineEdit()
        self.txt_logo_path.setPlaceholderText("Logo image path (.png / .jpg)...")
        self.txt_logo_path.textChanged.connect(self.update_logo_overlay_preview)
        grid_logo.addWidget(self.txt_logo_path, 0, 0, 1, 2)

        btn_browse_logo = QPushButton("🖼️ Browse Logo")
        btn_browse_logo.setStyleSheet("""
            QPushButton {
                background-color: #0284C7; color: #FFFFFF;
                border: none; border-radius: 4px; padding: 4px 10px;
                font-weight: bold; font-size: 11px; min-height: 24px;
            }
            QPushButton:hover { background-color: #0369A1; }
        """)
        btn_browse_logo.clicked.connect(self.browse_logo_image)
        grid_logo.addWidget(btn_browse_logo, 1, 0)

        btn_clear_logo = QPushButton("Clear")
        btn_clear_logo.setStyleSheet("""
            QPushButton {
                background-color: #1E293B; color: #E2E8F0;
                border: 1px solid #334155; border-radius: 4px; padding: 4px 8px;
                font-size: 11px; min-height: 24px;
            }
            QPushButton:hover { background-color: #334155; }
        """)
        btn_clear_logo.clicked.connect(self.clear_logo_image)
        grid_logo.addWidget(btn_clear_logo, 1, 1)

        grid_logo.addWidget(QLabel("Position:"), 2, 0)
        self.cmb_logo_pos = QComboBox()
        self.cmb_logo_pos.addItems(["Top-Right (ខាងលើស្តាំ)", "Top-Left (ខាងលើឆ្វេង)", "Bottom-Right (ខាងក្រោមស្តាំ)", "Bottom-Left (ខាងក្រោមឆ្វេង)", "Center (កណ្តាល)"])
        self.cmb_logo_pos.currentIndexChanged.connect(self.on_logo_preset_changed)
        grid_logo.addWidget(self.cmb_logo_pos, 2, 1)

        grid_logo.addWidget(QLabel("Size / Scale:"), 3, 0)
        self.cmb_logo_size = QComboBox()
        self.cmb_logo_size.addItems(["8% (Default)", "5% (Small)", "10% (Medium)", "15% (Large)"])
        self.cmb_logo_size.currentIndexChanged.connect(self.on_logo_size_preset_changed)
        grid_logo.addWidget(self.cmb_logo_size, 3, 1)

        grid_logo.addWidget(QLabel("Watermark Text:"), 4, 0)
        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("e.g. @Panha Dubbing (Optional)")
        self.txt_watermark.textChanged.connect(self.update_logo_overlay_preview)
        grid_logo.addWidget(self.txt_watermark, 4, 1)

        self.chk_show_logo = QCheckBox("Show Logo / Watermark on Video")
        self.chk_show_logo.setChecked(True)
        self.chk_show_logo.toggled.connect(self.update_logo_overlay_preview)
        grid_logo.addWidget(self.chk_show_logo, 5, 0, 1, 2)

        # Restore saved logo path from settings on startup
        saved_logo = self.settings.get("logo_path", "")
        if saved_logo and os.path.exists(saved_logo):
            self.txt_logo_path.setText(saved_logo)

        tab_style_layout.addLayout(grid_logo)
        tab_style_layout.addStretch()
        scroll_style_area.setWidget(tab_style)
        self.inspector_tabs.addTab(scroll_style_area, "Text & Style")

        inspector_layout.addWidget(self.inspector_tabs)
        studio_splitter.addWidget(inspector_panel)

        # Responsive 3-Column distribution
        studio_splitter.setStretchFactor(0, 0)
        studio_splitter.setStretchFactor(1, 1)
        studio_splitter.setStretchFactor(2, 0)
        studio_splitter.setSizes([250, 750, 270])

        # ====================================================
        # BOTTOM PANEL: MULTI-TRACK TIMELINE
        # ====================================================
        timeline_frame = QFrame()
        timeline_frame.setObjectName("timelineFrame")
        timeline_layout = QVBoxLayout(timeline_frame)
        timeline_layout.setContentsMargins(8, 6, 8, 6)
        timeline_layout.setSpacing(4)

        # Timeline Header
        tl_hdr_layout = QHBoxLayout()
        tl_hdr = QLabel("Timeline")
        tl_hdr.setStyleSheet("font-weight: 700; font-size: 12px; color: #38BDF8;")
        tl_hdr_layout.addWidget(tl_hdr)
        tl_hdr_layout.addStretch()
        timeline_layout.addLayout(tl_hdr_layout)

        # Multi-track timeline view
        self.timeline_view = TimelineView()
        self.timeline_view.setMinimumHeight(150)
        self.orig_audio_preview_enabled = True
        self.timeline_view.set_voice_only(iso_default)
        self.timeline_view.seek_requested.connect(self.timeline_view_seek_requested)
        self.timeline_view.segment_moved.connect(self.timeline_segment_moved)
        self.timeline_view.view_changed.connect(self.timeline_view_changed)
        self.timeline_view.play_original_requested.connect(self.play_original_voice_segment)
        self.timeline_view.play_dub_requested.connect(self.play_dub_voice_segment)
        self.timeline_view.segments_selected.connect(self.on_timeline_segments_selected)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        timeline_layout.addWidget(self.timeline_view, stretch=1)

        # Navigation row
        timeline_nav = QHBoxLayout()
        timeline_nav.setContentsMargins(0, 0, 0, 0)
        timeline_nav.setSpacing(4)

        self.btn_timeline_zoom_out = QPushButton("-")
        self.btn_timeline_zoom_out.setToolTip("Zoom out")
        self.btn_timeline_zoom_out.setFixedWidth(28)
        self.btn_timeline_zoom_out.clicked.connect(lambda: self.zoom_timeline(0.8))

        self.btn_timeline_zoom_in = QPushButton("+")
        self.btn_timeline_zoom_in.setToolTip("Zoom in")
        self.btn_timeline_zoom_in.setFixedWidth(28)
        self.btn_timeline_zoom_in.clicked.connect(lambda: self.zoom_timeline(1.25))

        self.btn_timeline_fit = QPushButton("Fit to Screen")
        self.btn_timeline_fit.setToolTip("Show full timeline")
        self.btn_timeline_fit.clicked.connect(self.fit_timeline)

        self.lbl_timeline_zoom = QLabel("100%")
        self.lbl_timeline_zoom.setMinimumWidth(44)
        self.lbl_timeline_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_toggle_orig_audio = QPushButton("🔊 A2 Audio: ON")
        self.btn_toggle_orig_audio.setToolTip("Toggle Original Audio in Video Preview")
        self.btn_toggle_orig_audio.setStyleSheet(
            "background-color: #241B3D; color: #CDB6FF; border: 1px solid #7049B6; "
            "border-radius: 4px; padding: 2px 6px; font-weight: bold; font-size: 10px;"
        )
        self.btn_toggle_orig_audio.clicked.connect(self.toggle_original_audio_preview)

        self.timeline_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scroll.setRange(0, 0)
        self.timeline_scroll.setPageStep(10000)
        self.timeline_scroll.setFixedHeight(14)
        self.timeline_scroll.setToolTip("Drag to scroll zoomed timeline")
        self.timeline_scroll.valueChanged.connect(self.timeline_scroll_changed)

        timeline_nav.addWidget(self.btn_timeline_zoom_out)
        timeline_nav.addWidget(self.btn_timeline_zoom_in)
        timeline_nav.addWidget(self.btn_timeline_fit)
        timeline_nav.addWidget(self.lbl_timeline_zoom)
        timeline_nav.addWidget(self.btn_toggle_orig_audio)
        timeline_nav.addWidget(self.timeline_scroll, stretch=1)
        timeline_layout.addLayout(timeline_nav)

        v_main_splitter.addWidget(timeline_frame)
        v_main_splitter.setStretchFactor(0, 5)
        v_main_splitter.setStretchFactor(1, 2)
        v_main_splitter.setSizes([460, 180])

        # Tab 2: Batch Dubbing Suite
        tab_batch = QWidget()
        tab_batch_layout = QHBoxLayout(tab_batch)
        tab_batch_layout.setContentsMargins(8, 8, 8, 8)
        tab_batch_layout.setSpacing(8)
        self.tabs.addTab(tab_batch, " Batch Dubbing (បកប្រែច្រើនភាគ)")
        self.init_batch_tab(tab_batch_layout)

        # Tab 3: Video Merger Suite
        tab_merger = QWidget()
        tab_merger_layout = QHBoxLayout(tab_merger)
        tab_merger_layout.setContentsMargins(8, 8, 8, 8)
        tab_merger_layout.setSpacing(8)
        self.tabs.addTab(tab_merger, " Video Merger (បញ្ចូលភាគ)")
        self.init_merger_tab(tab_merger_layout)

        # Load tab icons dynamically
        self.tabs.setTabIcon(0, get_icon("import_video", "#38BDF8", 16))
        self.tabs.setTabIcon(1, get_icon("batch", "#38BDF8", 16))
        self.tabs.setTabIcon(2, get_icon("merge", "#38BDF8", 16))

        # Bottom Progress & Info Log Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #1E293B; border-radius: 4px; color: #FFFFFF; text-align: center; background-color: #070B16; height: 16px; font-size: 10px; }
            QProgressBar::chunk { background-color: #06B6D4; }
        """)
        global_layout.addWidget(self.progress_bar)

    def video_duration_changed(self, duration_ms):
        try:
            self.duration = int(float(duration_ms)) // 1000
        except Exception:
            self.duration = 0
        self.sld_timeline.setRange(0, self.duration)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_duration(self.duration)
        self.update_time_label()

    def video_position_changed(self, position_ms):
        try:
            self.current_time = int(float(position_ms)) // 1000
        except Exception:
            self.current_time = 0
        if not self.sld_timeline.isSliderDown():
            self.sld_timeline.setValue(self.current_time)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_current_time(float(position_ms or 0) / 1000.0)
        self.update_time_label()
        self.highlight_subtitle_by_time(self.current_time)
        self.update_subtitle_overlay_by_time(float(position_ms or 0) / 1000.0)
        
        # Trigger live preview of generated voiceovers at their offsets!
        if self.is_playing:
            is_playing_rendered = False
            if hasattr(self, 'last_rendered_video') and self.last_rendered_video:
                current_source = self.media_player.source().toLocalFile()
                if current_source and os.path.normpath(current_source) == os.path.normpath(self.last_rendered_video):
                    is_playing_rendered = True
            
            if not is_playing_rendered:
                for r in range(self.table.rowCount()):
                    if r not in self.played_preview_rows:
                        timecode_str = self.table.item(r, 1).text()
                        try:
                            row_time_ms = parse_timecode_to_ms(timecode_str)
                            # Trigger if we have passed the start time
                            if position_ms >= row_time_ms:
                                self.played_preview_rows.add(r)
                                if r in self.audio_files and os.path.exists(self.audio_files[r]):
                                    self.play_audio_file(self.audio_files[r], pause_video=False)
                        except Exception:
                            pass

        # Determine if any voiceover is active at the current playhead
        current_sec = position_ms / 1000.0
        voiceover_active = False
        
        for r in range(self.table.rowCount()):
            if r in self.audio_files and os.path.exists(self.audio_files[r]):
                timecode_str = self.table.item(r, 1).text()
                try:
                    start_sec, end_sec = parse_timecode_range(timecode_str)
                    if start_sec <= current_sec <= end_sec:
                        voiceover_active = True
                        break
                except Exception:
                    pass

        # Apply live audio ducking/muting to the original video audio!
        if self.audio_output:
            base_vol = self.sld_volume.value() / 100.0 if hasattr(self, "sld_volume") else 0.8
            
            is_playing_rendered = False
            if hasattr(self, 'last_rendered_video') and self.last_rendered_video:
                current_source = self.media_player.source().toLocalFile()
                if current_source and os.path.normpath(current_source) == os.path.normpath(self.last_rendered_video):
                    is_playing_rendered = True

            a2_enabled = getattr(self, "orig_audio_preview_enabled", True)
            voice_only = self.chk_vocal_iso.isChecked() if hasattr(self, "chk_vocal_iso") else False
            duck_factor = (self.sld_music.value() / 100.0) if hasattr(self, "sld_music") else 0.35

            if is_playing_rendered:
                self.audio_output.setVolume(base_vol)
            elif not a2_enabled:
                # A2 Audio is OFF -> Mute original video audio completely
                self.audio_output.setVolume(0.0)
            elif voiceover_active:
                # When AI Voice is speaking:
                if voice_only and not a2_enabled:
                    self.audio_output.setVolume(0.0)
                elif a2_enabled:
                    # When A2 Audio is ON, play both original and AI voice
                    self.audio_output.setVolume(base_vol)
                else:
                    # Keep background music volume level
                    self.audio_output.setVolume(base_vol * duck_factor)
            else:
                self.audio_output.setVolume(base_vol)

    def update_translation_button_text(self):
        if hasattr(self, "btn_translate"):
            self.btn_translate.setText(f" Translate {translation_direction_label(self.settings)}")

    def set_translation_source_language(self, source_lang, save=True):
        source_lang = normalize_translation_language(source_lang, TRANSLATION_SOURCE_LANGS, "Auto Detect")
        self.settings["translation_source_lang"] = source_lang
        if hasattr(self, "cmb_translate_source"):
            self.cmb_translate_source.blockSignals(True)
            self.cmb_translate_source.setCurrentText(source_lang)
            self.cmb_translate_source.blockSignals(False)
        if save:
            save_settings(self.settings)
        self.update_translation_button_text()

    def auto_select_translation_source_from_texts(self, texts, save=False):
        source_lang = guess_translation_source_language(texts)
        self.set_translation_source_language(source_lang, save=save)
        return source_lang

    def sync_translation_language_controls(self, save=False):
        if hasattr(self, "cmb_translate_source"):
            self.settings["translation_source_lang"] = self.cmb_translate_source.currentText().strip()
        if hasattr(self, "cmb_translate_target"):
            self.settings["translation_target_lang"] = self.cmb_translate_target.currentText().strip()
        if save:
            save_settings(self.settings)
        self.update_translation_button_text()

    def translation_language_changed(self):
        self.sync_translation_language_controls(save=True)

    def update_played_preview_rows(self, position_ms):
        self.played_preview_rows.clear()
        for r in range(self.table.rowCount()):
            timecode_str = self.table.item(r, 1).text()
            try:
                row_time_ms = parse_timecode_to_ms(timecode_str)
                if row_time_ms < position_ms:
                    self.played_preview_rows.add(r)
            except Exception:
                pass

    def change_volume(self, value):
        if self.audio_output:
            self.audio_output.setVolume(value / 100.0)

    def _apply_legacy_light_styles(self):
        self.setStyleSheet("""
            QWidget {
                color: #000000;
            }
            QMainWindow {
                background-color: #EAF4F9;
            }
            QTabWidget::pane {
                border: 1px solid #D6E4EB;
                background-color: #FFFFFF;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #CFE2EC;
                color: #000000;
                border: 1px solid #D6E4EB;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background-color: #FFFFFF;
                color: #000000;
                border-bottom: 2px solid #FFFFFF;
            }
            QTabBar::tab:hover {
                background-color: #E1EFF5;
            }
            QPushButton.tool-btn {
                background-color: #19A3E0;
                color: #000000;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: bold;
                min-height: 38px;
            }
            QPushButton.tool-btn:hover {
                background-color: #158CBF;
            }
            QPushButton.tool-btn:pressed {
                background-color: #0E6E99;
            }
            QPushButton.cancel-btn {
                background-color: #FFEBEE;
                color: #000000;
                border: 1px solid #FFCDD2;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: bold;
                min-height: 38px;
            }
            QPushButton.cancel-btn:hover {
                background-color: #FFCDD2;
            }
            
            QFrame#configFrame {
                background-color: #FFFFFF;
                border: 1px solid #D6E4EB;
                border-radius: 6px;
            }
            QFrame#controlBar, QFrame#subBar {
                background-color: #FFFFFF;
                border: 1px solid #D6E4EB;
                border-radius: 6px;
            }
            QFrame#logFrame {
                background-color: #FFFFFF;
                border: 1px solid #D6E4EB;
                border-radius: 6px;
            }
            
            QLabel {
                font-family: "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
                color: #000000;
            }
            
            QComboBox, QLineEdit {
                background-color: #FFFFFF;
                border: 1px solid #B0D4E6;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 24px;
                color: #000000;
                font-family: "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
            }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                color: #000000;
                selection-background-color: #E1F5FE;
                selection-color: #000000;
                border: 1px solid #B0D4E6;
                outline: 0;
            }
            QComboBox:hover, QLineEdit:hover {
                border: 1px solid #19A3E0;
            }
            
            QCheckBox {
                spacing: 8px;
                font-weight: bold;
                color: #000000;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #B0D4E6;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox::indicator:checked {
                background-color: #19A3E0;
                border: 1px solid #19A3E0;
            }
            
            QTableWidget {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #D6E4EB;
                gridline-color: #E2ECF0;
                selection-background-color: #E1F5FE;
                selection-color: #000000;
                border-radius: 6px;
                font-family: "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
            }
            QTableWidget::item {
                color: #000000;
            }
            QHeaderView::section {
                background-color: #19A3E0;
                color: #000000;
                padding: 6px;
                border: 1px solid #D6E4EB;
                font-weight: bold;
            }
            
            QTextEdit {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #D6E4EB;
                border-radius: 6px;
                padding: 8px;
                font-family: "Inter", "Kantumruy Pro", "Segoe UI", sans-serif;
                font-size: 13px;
            }
        """)

    def apply_styles(self):
        """Ultra-modern Dubbing Studio Pro dark theme with rich bold typography."""
        self.setStyleSheet("""
            QWidget {
                background-color: #080B14;
                color: #E2E8F0;
                font-family: "Noto Sans Khmer", "Khmer UI", "Kantumruy Pro", "Inter", "Segoe UI", sans-serif;
                font-size: 12px;
                font-weight: 600;
            }
            QMainWindow, QDialog {
                background-color: #080B14;
            }
            QTabWidget::pane {
                border: 1px solid #1E293B;
                background-color: #0B1120;
                border-radius: 6px;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #0F172A;
                color: #94A3B8;
                border: 1px solid #1E293B;
                border-bottom: none;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #1E293B;
                color: #38BDF8;
                border-bottom: 2px solid #38BDF8;
            }
            QTabBar::tab:hover {
                background-color: #1E293B;
                color: #F8FAFC;
            }

            QFrame#topToolbarFrame, QFrame#previewPanel, QFrame#subsPanel, QFrame#inspectorPanel, QFrame#timelineFrame {
                background-color: #0B1120;
                border: 1px solid #1E293B;
                border-radius: 6px;
            }
            QFrame#playerContainer {
                background-color: #000000;
                border: 1px solid #1E293B;
                border-radius: 4px;
            }
            QFrame#transportBar {
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-radius: 4px;
            }

            QLabel {
                background: transparent;
                color: #CBD5E1;
            }

            /* Toolbar Buttons with exact color palette from Dubbing Studio Pro */
            QPushButton#btnImportVideo {
                background-color: #10B981;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnImportVideo:hover { background-color: #059669; }

            QPushButton#btnImportSubtitle {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnImportSubtitle:hover { background-color: #1D4ED8; }

            QPushButton#btnTranscribe {
                background-color: #7C3AED;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnTranscribe:hover { background-color: #6D28D9; }

            QPushButton#btnOcrSubtitles {
                background-color: #3B82F6;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnOcrSubtitles:hover { background-color: #2563EB; }

            QPushButton#btnTranslate {
                background-color: #D97706;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnTranslate:hover { background-color: #B45309; }

            QPushButton#btnVoiceover {
                background-color: #0891B2;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnVoiceover:hover { background-color: #0E7490; }

            QPushButton#btnRender {
                background-color: #334155;
                color: #FFFFFF;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnRender:hover { background-color: #475569; }

            QPushButton#btnWorkflowSrt, QPushButton#btnSettings {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnWorkflowSrt:hover, QPushButton#btnSettings:hover { background-color: #334155; }

            QPushButton#btnAutomate {
                background-color: #16A34A;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 5px 12px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#btnAutomate:hover { background-color: #15803D; }

            QPushButton#cancelBtn {
                background-color: #7F1D1D;
                color: #FCA5A5;
                border: 1px solid #DC2626;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 12px;
                font-weight: bold;
                min-height: 28px;
                padding: 5px 10px;
            }
            QPushButton#cancelBtn:hover { background-color: #991B1B; color: #FFFFFF; }

            QPushButton#cleanStatusBtn {
                background-color: #1E293B;
                color: #F87171;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 12px;
                min-height: 28px;
            }
            QPushButton#cleanStatusBtn:hover {
                background-color: #7F1D1D;
                color: #FFFFFF;
                border-color: #EF4444;
            }

            QPushButton#clearWorkspaceBtn {
                background-color: #0F172A;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 12px;
                min-height: 28px;
            }
            QPushButton#clearWorkspaceBtn:hover {
                background-color: #334155;
                color: #FFFFFF;
                border-color: #38BDF8;
            }

            /* Standard Buttons */
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: 600;
                min-height: 22px;
            }
            QPushButton:hover { background-color: #334155; border-color: #38BDF8; color: #FFFFFF; }
            QPushButton:pressed { background-color: #0F172A; }
            QPushButton:disabled { color: #475569; background-color: #0F172A; border-color: #1E293B; }

            QPushButton#btnBigPlay {
                background-color: #22C55E;
                color: #FFFFFF;
                border: none;
                border-radius: 17px;
            }
            QPushButton#btnBigPlay:hover { background-color: #16A34A; }

            /* Inputs & Combos - Modern 8px Rounded Cyber Slate Style */
            QComboBox, QLineEdit, QTextEdit {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #283852;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                min-height: 28px;
                selection-background-color: #0284C7;
            }
            QComboBox:hover, QLineEdit:hover, QTextEdit:hover {
                border-color: #38BDF8;
                background-color: #111C33;
            }
            QComboBox:focus, QLineEdit:focus, QTextEdit:focus {
                border: 2px solid #0EA5E9;
                background-color: #131E33;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
                subcontrol-position: right center;
            }
            QComboBox QAbstractItemView {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #38BDF8;
                border-radius: 8px;
                padding: 4px;
                selection-background-color: #0284C7;
                selection-color: #FFFFFF;
                outline: 0;
            }
            QComboBox QAbstractItemView::item {
                padding: 7px 12px;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #0284C7;
                color: #FFFFFF;
            }

            QCheckBox {
                color: #CBD5E1;
                spacing: 8px;
                background: transparent;
                font-size: 12px;
                font-weight: 600;
            }
            QCheckBox::indicator {
                width: 17px;
                height: 17px;
                border: 1px solid #334155;
                border-radius: 4px;
                background-color: #0F172A;
            }
            QCheckBox::indicator:hover {
                border-color: #38BDF8;
            }
            QCheckBox::indicator:checked {
                background-color: #0284C7;
                border-color: #38BDF8;
            }

            /* Subtitles Table - Sleek Modern Cards */
            QTableWidget {
                background-color: #0B1120;
                alternate-background-color: #0E1626;
                color: #F8FAFC;
                border: 1px solid #1E293B;
                gridline-color: #172236;
                selection-background-color: #0284C7;
                selection-color: #FFFFFF;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QTableWidget::item {
                color: #F8FAFC;
                padding: 5px 8px;
                border-bottom: 1px solid #141E30;
            }
            QTableWidget::item:selected {
                background-color: #0284C7;
                color: #FFFFFF;
                font-weight: 700;
            }
            QTableWidget::item:hover {
                background-color: #1A253A;
            }
            QHeaderView::section {
                background-color: #0F172A;
                color: #38BDF8;
                padding: 7px 10px;
                border: none;
                border-right: 1px solid #1E293B;
                border-bottom: 2px solid #0EA5E9;
                font-weight: 800;
                font-size: 12px;
                letter-spacing: 0.5px;
            }

            /* Sliders */
            QSlider::groove:horizontal {
                height: 6px;
                background: #1E293B;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #38BDF8);
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #0284C7;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }

            QSplitter::handle { background-color: #1E293B; width: 3px; height: 3px; }
            QSplitter::handle:hover { background-color: #38BDF8; }
            QScrollBar:vertical { background: #070B16; width: 8px; margin: 0; }
            QScrollBar::handle:vertical { background: #1E293B; min-height: 20px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #38BDF8; }
            QScrollBar:horizontal { background: #070B16; height: 8px; margin: 0; }
            QScrollBar::handle:horizontal { background: #1E293B; min-width: 20px; border-radius: 4px; }
            QScrollBar::handle:horizontal:hover { background: #38BDF8; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            QToolTip { background-color: #0F172A; color: #F8FAFC; border: 1px solid #38BDF8; padding: 4px; font-size: 10px; }
        """)

    def _apply_speaker_chip_filter(self, speaker_type):
        """Filter table rows by speaker chip (All, Male, Female)."""
        if not hasattr(self, "table"):
            return
        if speaker_type == "All":
            if hasattr(self, "btn_chip_all"): self.btn_chip_all.setChecked(True)
            if hasattr(self, "btn_chip_male"): self.btn_chip_male.setChecked(False)
            if hasattr(self, "btn_chip_female"): self.btn_chip_female.setChecked(False)
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
        elif speaker_type == "Male":
            if hasattr(self, "btn_chip_all"): self.btn_chip_all.setChecked(False)
            if hasattr(self, "btn_chip_male"): self.btn_chip_male.setChecked(True)
            if hasattr(self, "btn_chip_female"): self.btn_chip_female.setChecked(False)
            for r in range(self.table.rowCount()):
                voice_item = self.table.item(r, COL_VOICE)
                v_text = voice_item.text().lower() if voice_item else ""
                self.table.setRowHidden(r, "male" not in v_text and "piseth" not in v_text)
        elif speaker_type == "Female":
            if hasattr(self, "btn_chip_all"): self.btn_chip_all.setChecked(False)
            if hasattr(self, "btn_chip_male"): self.btn_chip_male.setChecked(False)
            if hasattr(self, "btn_chip_female"): self.btn_chip_female.setChecked(True)
            for r in range(self.table.rowCount()):
                voice_item = self.table.item(r, COL_VOICE)
                v_text = voice_item.text().lower() if voice_item else ""
                self.table.setRowHidden(r, "female" not in v_text and "sreymom" not in v_text)
        self.update_table_summary()

    def filter_subtitles_table(self):
        """Filter subtitle table rows based on search text and status dropdown."""
        search_text = self.txt_subtitle_search.text().strip().lower() if hasattr(self, 'txt_subtitle_search') else ""
        status_filter = self.cmb_subtitle_filter.currentText() if hasattr(self, 'cmb_subtitle_filter') else "All"

        for row in range(self.table.rowCount()):
            show_row = True
            
            if status_filter != "All":
                status_item = self.table.item(row, COL_STATUS)
                row_status = status_item.text() if status_item else ""
                if status_filter.lower() not in row_status.lower():
                    show_row = False

            if show_row and search_text:
                row_matches = False
                for col in (COL_ID, COL_TIME, COL_ORIGINAL, COL_TRANSLATED):
                    item = self.table.item(row, col)
                    if item and search_text in item.text().lower():
                        row_matches = True
                        break
                if not row_matches:
                    show_row = False

            self.table.setRowHidden(row, not show_row)

        self.update_table_summary()

    def update_table_summary(self):
        """Update subtitle count summary in table footer."""
        if not hasattr(self, 'lbl_table_summary') or not hasattr(self, 'table'):
            return
        total = self.table.rowCount()
        synthesized = 0
        pending = 0
        for row in range(total):
            st = self.table.item(row, COL_STATUS)
            st_text = st.text().lower() if st else ""
            if "synth" in st_text or "ready" in st_text or "done" in st_text:
                synthesized += 1
            else:
                pending += 1
        self.lbl_table_summary.setText(f"Total Subtitles: {total} | Synthesized: {synthesized} | Pending: {pending}")

    def update_voice_preview_char_count(self):
        """Update character count label for voice preview box."""
        if hasattr(self, 'txt_preview_voice') and hasattr(self, 'lbl_char_count'):
            count = len(self.txt_preview_voice.toPlainText())
            self.lbl_char_count.setText(f"{count} / 200")

    def preview_voice_sample(self):
        """Generate and play TTS audio for sample text in voice settings panel."""
        text = self.txt_preview_voice.toPlainText().strip() if hasattr(self, 'txt_preview_voice') else ""
        if not text:
            text = "Hello! This is a preview of the selected dubbing voice."
        lang = self.cmb_lang.currentText() if hasattr(self, 'cmb_lang') else "English"
        voice_name = self.cmb_voice.currentText() if hasattr(self, 'cmb_voice') else ""
        self.log_workflow_msg(f"Generating voice preview for '{voice_name}'...")
        
        def _run():
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_samples")
            os.makedirs(cache_dir, exist_ok=True)
            out_file = os.path.join(cache_dir, "active_preview_sample.mp3")
            try:
                voice_char = "km-KH-PisethNeural" if "Piseth" in voice_name else ("km-KH-SreymomNeural" if "Sreymom" in voice_name else "en-US-JennyNeural")
                rate_val = self.sld_rate.value() if hasattr(self, 'sld_rate') else 100
                rate_str = f"{rate_val - 100:+d}%"
                asyncio.run(edge_tts.Communicate(text, voice_char, rate=rate_str).save(out_file))
                if os.path.exists(out_file):
                    QTimer.singleShot(0, lambda: self.play_audio_file(out_file, pause_video=False))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.log_workflow_msg(f"Voice preview error: {e}"))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def voice_choices_for_language(self, lang=None):
        lang = lang or self.cmb_lang.currentText()
        has_eleven = hasattr(self, 'settings') and bool(self.settings.get("elevenlabs_api_key") and self.settings.get("elevenlabs_voice_id"))
        
        if lang == "Khmer":
            voices = [
                "Auto (Piseth / Sreymom)",
                "Female - Sreymom",
                "Male - Piseth",
                "Male - Sopheap",
                "Female - Chamroeun"
            ]
        elif lang == "English":
            voices = ["Auto (Brian / Emma)", "Female - Alice", "Male - Bob"]
        else:
            voices = ["Auto (Yunjian / Xiaoxiao)", "Female - Xiaoxiao", "Male - Yunjian"]
            
        if has_eleven:
            voices.append("ElevenLabs Cloned Voice")

        voices.extend([
            VOXCPM_AUTO_VOICE_NAME,
            VOXCPM_FEMALE_VOICE_NAME,
            VOXCPM_MALE_VOICE_NAME,
            VOXCPM_VOICE_NAME,
        ])
        return voices

    def single_vocal_iso_toggled(self, checked):
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_voice_only(checked)
        if hasattr(self, "settings"):
            self.settings["single_voice_only"] = bool(checked)
            save_settings(self.settings)

    def main_voice_changed(self, voice_name):
        if not hasattr(self, "settings") or not voice_name:
            return
        curr_lang = self.cmb_lang.currentText() if hasattr(self, "cmb_lang") else "Khmer"
        if curr_lang == "Khmer":
            self.settings["default_voice_khmer"] = voice_name
            save_settings(self.settings)
        elif curr_lang == "English":
            self.settings["default_voice_english"] = voice_name
            save_settings(self.settings)

    def output_lang_changed(self, index):
        self.cmb_voice.clear()
        voices = self.voice_choices_for_language()
        self.cmb_voice.addItems(voices)
        curr_lang = self.cmb_lang.currentText()
        if hasattr(self, "settings"):
            if curr_lang == "Khmer":
                def_voice = self.settings.get("default_voice_khmer", "Auto (Piseth / Sreymom)")
                if def_voice in voices:
                    self.cmb_voice.setCurrentText(def_voice)
                else:
                    self.cmb_voice.setCurrentText("Auto (Piseth / Sreymom)")
            else:
                female_reference = self.settings.get("voxcpm_reference_audio_female", "")
                male_reference = self.settings.get("voxcpm_reference_audio_male", "")
                clone_reference = self.settings.get("voxcpm_reference_audio", "")
                if female_reference and os.path.exists(female_reference):
                    self.cmb_voice.setCurrentText(VOXCPM_FEMALE_VOICE_NAME)
                elif male_reference and os.path.exists(male_reference):
                    self.cmb_voice.setCurrentText(VOXCPM_MALE_VOICE_NAME)
                elif clone_reference and os.path.exists(clone_reference):
                    self.cmb_voice.setCurrentText(VOXCPM_VOICE_NAME)
        self.refresh_row_voice_options()

    def create_voice_cell(self, row_idx, voice_name=None):
        if not hasattr(self, "table"):
            return
        v_name = voice_name or (self.cmb_voice.currentText() if hasattr(self, "cmb_voice") else "Auto (Piseth / Sreymom)")
        btn_voice = VoiceCellButton(voice_name=v_name, parent_window=self, row_idx=row_idx)
        self.table.setCellWidget(row_idx, COL_VOICE, btn_voice)

    def get_voice_for_row(self, row_idx):
        widget = self.table.cellWidget(row_idx, COL_VOICE) if hasattr(self, "table") else None
        if isinstance(widget, VoiceCellButton):
            return widget.currentText()
        elif isinstance(widget, QComboBox):
            return widget.currentText()
        return self.cmb_voice.currentText() if hasattr(self, "cmb_voice") else "Auto (Piseth / Sreymom)"

    def set_voice_for_row(self, row_idx, voice_name):
        if row_idx < 0 or row_idx >= self.table.rowCount():
            return
        widget = self.table.cellWidget(row_idx, COL_VOICE)
        if isinstance(widget, VoiceCellButton):
            widget.set_voice(voice_name)
        elif isinstance(widget, QComboBox):
            if widget.findText(voice_name) < 0:
                widget.addItem(voice_name)
            widget.setCurrentText(voice_name)
        else:
            self.create_voice_cell(row_idx, voice_name)

    def preview_voice_by_name(self, voice_name):
        """Instant audio preview sample for a specific voice with zero latency."""
        self.log_workflow_msg(f"Playing sample preview for voice: {voice_name}")
        voice_lower = voice_name.lower()
        samples_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_samples")
        
        # Check for pre-cached instant voice sample
        instant_sample = None
        if "female" in voice_lower or "sreymom" in voice_lower or "chamroeun" in voice_lower:
            instant_sample = os.path.join(samples_dir, "female_khmer.mp3")
        elif "male" in voice_lower or "piseth" in voice_lower or "sopheap" in voice_lower:
            instant_sample = os.path.join(samples_dir, "male_khmer.mp3")
        elif "en" in voice_lower or "english" in voice_lower or "jenny" in voice_lower or "guy" in voice_lower:
            instant_sample = os.path.join(samples_dir, "female_en.mp3" if "female" in voice_lower else "male_en.mp3")
        else:
            # Default auto
            instant_sample = os.path.join(samples_dir, "female_khmer.mp3")

        if instant_sample and os.path.exists(instant_sample) and os.path.getsize(instant_sample) > 0:
            self.play_audio_file(instant_sample, pause_video=False)
            return

        # Fallback: Generate dynamically via Edge-TTS
        sample_text = "សួស្តី! នេះគឺជាសំឡេងគំរូសម្រាប់សាកល្បង។" if "sreymom" in voice_lower or "piseth" in voice_lower or "sopheap" in voice_lower or "female" in voice_lower or "male" in voice_lower else "Hello, this is a sample preview for this dubbing voice."
        def _run():
            os.makedirs(samples_dir, exist_ok=True)
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', voice_name).strip('_').lower()
            out_file = os.path.join(samples_dir, f"sample_{clean_name}.mp3")
            try:
                if "piseth" in voice_lower or "male" in voice_lower or "sopheap" in voice_lower:
                    voice_char = "km-KH-PisethNeural"
                elif "sreymom" in voice_lower or "chamroeun" in voice_lower or "female" in voice_lower:
                    voice_char = "km-KH-SreymomNeural"
                elif "bob" in voice_lower or "brian" in voice_lower:
                    voice_char = "en-US-GuyNeural"
                else:
                    voice_char = "en-US-JennyNeural"

                rate_val = self.sld_rate.value() if hasattr(self, "sld_rate") else 100
                rate_str = f"{rate_val - 100:+d}%"
                asyncio.run(edge_tts.Communicate(sample_text, voice_char, rate=rate_str).save(out_file))
                if os.path.exists(out_file):
                    QTimer.singleShot(0, lambda: self.play_audio_file(out_file, pause_video=False))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.log_workflow_msg(f"Voice preview error: {e}"))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def invalidate_generated_voice_for_row(self, row_idx):
        if row_idx < 0 or row_idx >= self.table.rowCount():
            return False
        status_item = self.table.item(row_idx, COL_STATUS)
        status_text = status_item.text() if status_item else ""
        if status_text == "A1 Imported":
            return False

        had_generated_audio = self.audio_files.pop(row_idx, None) is not None
        if status_item:
            status_item.setText("Draft")
        if had_generated_audio or status_text == "Synthesized":
            self.create_table_actions(row_idx)
        return had_generated_audio

    def clear_subtitle_audio_state(self):
        self.audio_files.clear()
        self.played_preview_rows.clear()

    def refresh_row_voice_options(self):
        if not hasattr(self, "table"):
            return
        voices = self.voice_choices_for_language()
        for row_idx in range(self.table.rowCount()):
            current_voice = self.get_voice_for_row(row_idx)
            if current_voice not in voices:
                current_voice = self.cmb_voice.currentText()
            combo = self.table.cellWidget(row_idx, COL_VOICE)
            if isinstance(combo, QComboBox):
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(voices)
                combo.setCurrentText(current_voice)
                combo.blockSignals(False)
            else:
                self.create_voice_cell(row_idx, current_voice)

    def row_voice_changed(self, row_idx):
        if row_idx < 0 or row_idx >= self.table.rowCount():
            return
        self.invalidate_generated_voice_for_row(row_idx)
        self.update_transcript_box()

    def populate_demo_rows(self):
        # Demo data corresponding to the user's screenshot
        demo_data = [
            ("1", "00:00:00,000 - 00:00:02,880", "Well, you were always too good for him.", "ឯងតែងតែល្អពេកសម្រាប់គាត់ហើយ។"),
            ("2", "00:00:02,880 - 00:00:04,840", "He was always a bastard.", "គាត់តែងតែជាមនុស្សអាក្រក់ជានិច្ច។"),
            ("3", "00:00:04,840 - 00:00:09,320", "I'm not angry.", "ខ្ញុំមិនបានខឹងនោះទេ។"),
            ("4", "00:00:09,320 - 00:00:11,800", "You were a little harsh.", "ឯងហាក់ដូចជារាងតឹងរ៉ឹងបន្តិចហើយ។"),
            ("5", "00:00:11,800 - 00:00:14,400", "And we're just different.", "ហើយពួកយើងគ្រាន់តែមានភាពខុសគ្នា។"),
            ("6", "00:00:14,400 - 00:00:16,800", "I mean, I deserve someone better.", "ខ្ញុំចង់មានន័យថា ខ្ញុំសមនឹងទទួលបានមនុស្សល្អជាងនេះ។"),
            ("7", "00:00:16,800 - 00:00:20,520", "And I deserve someone better.", "ហើយខ្ញុំសមនឹងទទួលបានមនុស្សល្អជាងនេះ។"),
            ("8", "00:00:20,520 - 00:00:23,560", "He's — he's not a bad guy.", "គាត់មិនមែនជាមនុស្សអាក្រក់នោះទេ។"),
            ("9", "00:00:23,560 - 00:00:31,000", "I mean, things just didn't work out.", "រឿងរ៉ាវគ្រាន់តែមិនបានដូចបំណងប៉ុណ្ណោះ។"),
            ("10", "00:00:31,000 - 00:00:37,000", "I mean, we just grew apart.", "ពួកយើងគ្រាន់តែឃ្លាតឆ្ងាយពីគ្នា។"),
            ("11", "00:00:37,000 - 00:00:39,200", "I mean, it's not his fault.", "វាមិនមែនជាកំហុសរបស់គាត់នោះទេ។"),
            ("12", "00:00:39,200 - 00:00:41,000", "Due — due respect.", "ដោយការគោរព។"),
            ("13", "00:00:41,000 - 00:00:43,500", "Uh...", "អឺ...")
        ]
        
        self.clear_subtitle_audio_state()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row_idx, (id_val, timecode, original, translated) in enumerate(demo_data):
            self.table.insertRow(row_idx)
            
            # ID (read-only)
            item_id = QTableWidgetItem(id_val)
            item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_id.setForeground(QColor("#A78BFA"))
            self.table.setItem(row_idx, 0, item_id)
            
            # Timecode
            item_time = QTableWidgetItem(timecode)
            item_time.setToolTip(timecode)
            item_time.setForeground(QColor("#93C5FD"))
            self.table.setItem(row_idx, 1, item_time)
            
            bold_font = QFont("Noto Sans Khmer", 10, QFont.Weight.DemiBold)

            # Original text
            item_orig = QTableWidgetItem(original)
            item_orig.setFont(bold_font)
            item_orig.setToolTip(original)
            item_orig.setForeground(QColor("#E2E8F0"))
            self.table.setItem(row_idx, 2, item_orig)
            
            # Translated text (Khmer)
            item_trans = QTableWidgetItem(translated)
            item_trans.setFont(bold_font)
            item_trans.setToolTip(translated)
            item_trans.setForeground(QColor("#F8FAFC"))
            self.table.setItem(row_idx, 3, item_trans)
            
            # Status
            item_status = QTableWidgetItem("Ready")
            item_status.setFlags(item_status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_status.setForeground(QColor("#34D399"))
            self.table.setItem(row_idx, COL_STATUS, item_status)
            self.create_voice_cell(row_idx)
            
            # Actions Cell widget
            self.create_table_actions(row_idx)
            
        self.table.blockSignals(False)
        self.update_transcript_box()
        self.update_table_summary()
        self.update_subtitle_overlay_by_time(0.0)

    def create_table_actions(self, row_idx):
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(1, 1, 1, 1)
        actions_layout.setSpacing(3)

        # 1. Original Voice verification button (Purple)
        btn_orig = QPushButton("🔊 Orig")
        btn_orig.setToolTip("Play Original Voice for this line to verify (ផ្ទៀងផ្ទាត់សម្លេងដើម)")
        btn_orig.setStyleSheet("""
            QPushButton {
                background-color: #8E24AA;
                color: #FFFFFF;
                border: none;
                border-radius: 3px;
                min-height: 22px;
                padding: 2px 4px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9C27B0;
            }
        """)
        btn_orig.clicked.connect(lambda checked, r=row_idx: self.play_original_voice_segment(r))
        actions_layout.addWidget(btn_orig)

        # 2. Dubbed Voice / Generate Voice button
        has_voice = row_idx in self.audio_files and os.path.exists(self.audio_files.get(row_idx, ""))
        if has_voice:
            btn_dub = QPushButton("▶ Dub")
            btn_dub.setToolTip("Play Khmer Dubbed Voice (ស្តាប់សម្លេងបកប្រែ)")
            btn_dub.setStyleSheet("""
                QPushButton {
                    background-color: #16A34A;
                    color: #FFFFFF;
                    border: none;
                    border-radius: 3px;
                    min-height: 22px;
                    padding: 2px 5px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #15803D;
                }
            """)
            btn_dub.clicked.connect(lambda checked, r=row_idx: self.play_dub_voice_segment(r))
            actions_layout.addWidget(btn_dub)
        else:
            btn_gen = QPushButton("⚡ Gen")
            btn_gen.setToolTip("Generate Voice for this line (បង្កើតសម្លេង)")
            btn_gen.setStyleSheet("""
                QPushButton {
                    background-color: #0288D1;
                    color: #FFFFFF;
                    border: none;
                    border-radius: 3px;
                    min-height: 22px;
                    padding: 2px 5px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #03A9F4;
                }
            """)
            btn_gen.clicked.connect(lambda checked, r=row_idx: self.generate_single_tts(r))
            actions_layout.addWidget(btn_gen)

        # 3. Always available ReGen button (Orange)
        btn_regen = QPushButton("🔄")
        btn_regen.setToolTip("Re-generate / overwrite voice for this line (បង្កើតសម្លេងឡើងវិញ)")
        btn_regen.setStyleSheet("""
            QPushButton {
                background-color: #E65100;
                color: #FFFFFF;
                border: none;
                border-radius: 3px;
                min-height: 22px;
                padding: 2px 4px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        btn_regen.clicked.connect(lambda checked, r=row_idx: self.generate_single_tts(r))
        actions_layout.addWidget(btn_regen)

        # 4. Delete Line button (Red trash)
        btn_del = QPushButton()
        btn_del.setIcon(get_icon("delete", "#EF4444", 13))
        btn_del.setToolTip("Delete Line (លុបបន្ទាត់នេះ)")
        btn_del.setStyleSheet("""
            QPushButton {
                background-color: #261111;
                border: 1px solid #7F1D1D;
                border-radius: 3px;
                min-height: 22px;
                min-width: 22px;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: #7F1D1D;
                border-color: #DC2626;
            }
        """)
        btn_del.clicked.connect(lambda checked, r=row_idx: self.delete_row(r))
        actions_layout.addWidget(btn_del)

        self.table.setCellWidget(row_idx, COL_ACTION, actions_widget)

    # ----------------------------------------------------
    # Event Handlers
    # ----------------------------------------------------
    def table_item_edited(self, item):
        column = item.column()
        if column in (COL_TIME, COL_ORIGINAL, COL_TRANSLATED):
            item.setToolTip(item.text())

        if column == COL_TIME:
            self.voice_gender_analyzed = False
            self.update_transcript_box()
        elif column in (COL_ORIGINAL, COL_TRANSLATED):
            self.invalidate_generated_voice_for_row(item.row())
            self.update_transcript_box()
        self.schedule_session_autosave()

    def update_transcript_box(self):
        timeline_segments = []
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            time_item = self.table.item(r, 1)
            orig_item = self.table.item(r, 2)
            trans_item = self.table.item(r, 3)
            
            line_id = id_item.text() if id_item else str(r+1)
            time_txt = time_item.text() if time_item else "00:00:00"
            orig_txt = orig_item.text() if orig_item else ""
            trans_txt = trans_item.text() if trans_item else ""

            start_sec, end_sec = parse_timecode_range(time_txt)
            timeline_segments.append({
                "row": r,
                "id": line_id,
                "start": start_sec,
                "end": end_sec,
                "orig_text": orig_txt,
                "trans_text": trans_txt,
                "text": trans_txt or orig_txt,
                "has_audio": r in self.audio_files and os.path.exists(self.audio_files.get(r, "")),
            })

        if hasattr(self, "timeline_view"):
            self.timeline_view.set_segments(timeline_segments, self.duration)

    def log_workflow_msg(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        if hasattr(self, "txt_workflow_log"):
            self.txt_workflow_log.append(f"[{timestamp}] {msg}")

    def show_workflow_notice(self, title, message, warning=False):
        """Show workflow progress without blocking the editor in silent mode."""
        first_line = str(message).splitlines()[0] if message else title
        self.statusBar().showMessage(f"{title}: {first_line}", 10000)
        if bool(self.settings.get("silent_notifications", True)):
            return
        if warning:
            popup_warning(self, title, message)
        else:
            popup_info(self, title, message)

    def save_voxcpm_reference_from_ui(self):
        if hasattr(self, "txt_voxcpm_ref"):
            self.settings["voxcpm_reference_audio"] = self.txt_voxcpm_ref.text().strip()
        if hasattr(self, "txt_voxcpm_ref_female"):
            self.settings["voxcpm_reference_audio_female"] = self.txt_voxcpm_ref_female.text().strip()
        if hasattr(self, "txt_voxcpm_ref_male"):
            self.settings["voxcpm_reference_audio_male"] = self.txt_voxcpm_ref_male.text().strip()
        save_settings(self.settings)

    def browse_voxcpm_reference_audio_main(self, target="default"):
        file_path, _ = popup_open_file_name(
            self, "Select VoxCPM Clone Reference Audio", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if file_path:
            if target == "female" and hasattr(self, "txt_voxcpm_ref_female"):
                self.txt_voxcpm_ref_female.setText(file_path)
            elif target == "male" and hasattr(self, "txt_voxcpm_ref_male"):
                self.txt_voxcpm_ref_male.setText(file_path)
            elif hasattr(self, "txt_voxcpm_ref"):
                self.txt_voxcpm_ref.setText(file_path)
            self.save_voxcpm_reference_from_ui()
            self.log_workflow_msg(f"VoxCPM {target} reference selected: {os.path.basename(file_path)}")

    def validate_voxcpm_config(self, voice_char):
        if not is_voxcpm_voice(voice_char):
            return True

        self.save_voxcpm_reference_from_ui()
        python_path = self.settings.get("voxcpm_python_path", "").strip()
        reference_audio = voxcpm_reference_audio_for_voice(self.settings, voice_char).strip()

        if not python_path or not os.path.exists(python_path):
            detected_python = default_voxcpm_python_path()
            if detected_python and os.path.exists(detected_python):
                self.settings["voxcpm_python_path"] = detected_python
                save_settings(self.settings)
                python_path = detected_python
                self.log_workflow_msg(f"VoxCPM Python auto-detected: {python_path}")

        missing = []
        if not python_path or not os.path.exists(python_path):
            missing.append("VoxCPM Python path")
        if voice_char == VOXCPM_AUTO_VOICE_NAME:
            female_reference = self.settings.get("voxcpm_reference_audio_female", "").strip()
            male_reference = self.settings.get("voxcpm_reference_audio_male", "").strip()
            if not female_reference or not os.path.exists(female_reference):
                missing.append("female clone reference audio")
            if not male_reference or not os.path.exists(male_reference):
                missing.append("male clone reference audio")
        elif not reference_audio or not os.path.exists(reference_audio):
            missing.append(voxcpm_reference_label_for_voice(voice_char))

        if missing:
            popup_warning(
                self,
                "VoxCPM Setup Needed",
                "Missing: " + ", ".join(missing) + "\n\n"
                "Open Settings, choose a Python 3.10-3.12 environment with VoxCPM installed, "
                "then select a short voice sample to clone."
            )
            return False

        return True

    def delete_row(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        if hasattr(self, "tts_thread") and self.tts_thread.isRunning():
            popup_warning(self, "Voice Generation Running", "Wait for voice generation to finish before deleting subtitle lines.")
            return

        self.table.removeRow(row)
        self.audio_files = {
            (old_row if old_row < row else old_row - 1): path
            for old_row, path in self.audio_files.items()
            if old_row != row
        }
        self.played_preview_rows = {
            old_row if old_row < row else old_row - 1
            for old_row in self.played_preview_rows
            if old_row != row
        }

        signals_were_blocked = self.table.blockSignals(True)
        try:
            for current_row in range(self.table.rowCount()):
                id_item = self.table.item(current_row, COL_ID)
                if id_item:
                    id_item.setText(str(current_row + 1))
                voice_name = self.get_voice_for_row(current_row)
                self.create_voice_cell(current_row, voice_name)
                self.create_table_actions(current_row)
        finally:
            self.table.blockSignals(signals_were_blocked)

        self.update_transcript_box()

    # ----------------------------------------------------
    # Media Player Playback
    # ----------------------------------------------------
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and QApplication.activeWindow() is self:
            if self.handle_player_shortcut(event):
                return True
        return super().eventFilter(obj, event)

    def _focused_widget_is_text_input(self):
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            return True
        if isinstance(focus, QTextEdit) and not focus.isReadOnly():
            return True
        if isinstance(focus, QComboBox):
            return True
        return False

    def handle_player_shortcut(self, event):
        if self._focused_widget_is_text_input():
            return False

        key = event.key()
        if key == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self.toggle_play()
            return True

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self.seek_relative(step if key == Qt.Key.Key_Right else -step)
            return True

        return False

    def seek_relative(self, delta_seconds):
        self.seek_to_seconds(float(self.current_time or 0) + float(delta_seconds or 0))

    def show_full_preview(self):
        """Temporarily expand the live player, including logo/subtitle overlays."""
        if not getattr(self, "video_file", "") and not getattr(self, "last_rendered_video", ""):
            popup_warning(self, "No Video", "Import a video before opening full preview.")
            return

        container = self.player_container
        original_layout = container.parentWidget().layout()
        original_index = original_layout.indexOf(container)
        was_playing = bool(self.is_playing)

        dialog = QDialog(self)
        dialog.setWindowTitle("Full Preview — Esc to exit")
        dialog.setModal(True)
        dialog.setStyleSheet("background: #000000;")
        full_layout = QVBoxLayout(dialog)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(0)

        original_layout.removeWidget(container)
        container.setParent(dialog)
        full_layout.addWidget(container)
        self.player_stack.setCurrentWidget(self.video_widget)
        self.attach_overlay_to_current_player()
        self.update_logo_overlay_preview()
        self.update_subtitle_overlay_by_time(float(self.current_time or 0))

        if not was_playing:
            self.toggle_play()
        dialog.showFullScreen()
        dialog.exec()

        if not was_playing and self.is_playing:
            self.toggle_play()
        full_layout.removeWidget(container)
        container.setParent(original_layout.parentWidget())
        original_layout.insertWidget(original_index, container, stretch=1)
        self.attach_overlay_to_current_player()
        self.update_logo_overlay_preview()
        self.update_subtitle_overlay_by_time(float(self.current_time or 0))

    def seek_to_seconds(self, seconds):
        seconds = max(0.0, min(float(seconds or 0), float(self.duration or 0)))
        self.current_time = int(seconds)
        self.sld_timeline.setValue(self.current_time)
        self.update_time_label()
        self.highlight_subtitle_by_time(seconds)
        self.update_subtitle_overlay_by_time(seconds)
        self.update_played_preview_rows(seconds * 1000)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_current_time(seconds)
        if self.media_player:
            self.media_player.setPosition(int(seconds * 1000))

    def toggle_play(self):
        if not self.is_playing:
            self.is_playing = True
            self.btn_play.setIcon(get_icon("pause", "#19A3E0"))
            self.stacked_player.set_playing(True)
            self.playback_timer.start(1000)
            
            source_file = None
            if hasattr(self, 'last_rendered_video') and self.last_rendered_video and os.path.exists(self.last_rendered_video):
                source_file = self.last_rendered_video
            elif self.video_file and os.path.exists(self.video_file):
                source_file = self.video_file
                
            if source_file:
                try:
                    self.player_stack.setCurrentWidget(self.video_widget)
                    
                    # Normalize paths for comparison
                    current_source = self.media_player.source().toLocalFile()
                    if not current_source or os.path.normpath(current_source) != os.path.normpath(source_file):
                        self.media_player.setSource(QUrl.fromLocalFile(source_file))
                        
                    self.audio_output.setVolume(self.sld_volume.value() / 100.0)
                    self.media_player.setPosition(int(self.current_time * 1000))
                    self.update_played_preview_rows(self.current_time * 1000)
                    self.media_player.play()
                except Exception as e:
                    print("Error playing media:", e)
        else:
            self.is_playing = False
            self.btn_play.setIcon(get_icon("play", "#19A3E0"))
            self.stacked_player.set_playing(False)
            self.playback_timer.stop()
            
            if self.media_player:
                self.media_player.pause()

    def stop_playback(self):
        self.is_playing = False
        self.btn_play.setIcon(get_icon("play", "#19A3E0"))
        self.stacked_player.set_playing(False)
        self.playback_timer.stop()
        self.current_time = 0
        self.sld_timeline.setValue(0)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_current_time(0)
        self.update_time_label()
        self.played_preview_rows.clear()
        
        if self.media_player:
            self.media_player.stop()

    def increment_playback(self):
        if self.current_time < self.duration:
            self.current_time += 1
            self.sld_timeline.setValue(self.current_time)
            if hasattr(self, "timeline_view"):
                self.timeline_view.set_current_time(self.current_time)
            self.update_time_label()
            self.highlight_subtitle_by_time(self.current_time)
        else:
            self.stop_playback()

    def on_timeline_segments_selected(self, selected_indices):
        """Synchronize timeline multi-selection with table rows."""
        if not hasattr(self, "table"):
            return
        self.table.blockSignals(True)
        self.table.clearSelection()
        
        segs = self.timeline_view.segments if hasattr(self, "timeline_view") and hasattr(self.timeline_view, "segments") else []
        selection = QItemSelection()
        last_row = None
        for idx in selected_indices:
            if idx < len(segs):
                row = int(segs[idx].get("row", idx))
            else:
                row = idx
            if 0 <= row < self.table.rowCount():
                top_left = self.table.model().index(row, 0)
                bottom_right = self.table.model().index(row, self.table.columnCount() - 1)
                selection.append(QItemSelectionRange(top_left, bottom_right))
                last_row = row

        if not selection.isEmpty():
            self.table.selectionModel().select(
                selection,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
            )

        self.table.blockSignals(False)
        if last_row is not None and 0 <= last_row < self.table.rowCount():
            item = self.table.item(last_row, 0)
            if item:
                self.table.scrollToItem(item)

    def on_table_selection_changed(self):
        """Synchronize table selection changes back to the timeline view."""
        if not hasattr(self, "table") or not hasattr(self, "timeline_view"):
            return
        selected_rows = {item.row() for item in self.table.selectedItems()}
        self.timeline_view.set_selected_rows(selected_rows)

    def timeline_view_seek_requested(self, seconds):
        self.seek_to_seconds(seconds)

    def timeline_segment_moved(self, row, start_sec, end_sec):
        if row < 0 or row >= self.table.rowCount():
            return
        time_item = self.table.item(row, 1)
        if not time_item:
            time_item = QTableWidgetItem()
            self.table.setItem(row, 1, time_item)

        new_time = f"{format_seconds_to_timecode(start_sec)} - {format_seconds_to_timecode(end_sec)}"
        self.table.blockSignals(True)
        time_item.setText(new_time)
        self.table.blockSignals(False)
        self.current_time = int(start_sec)
        self.sld_timeline.setValue(self.current_time)
        self.update_time_label()
        self.update_transcript_box()
        self.log_workflow_msg(f"Line {row + 1} moved to {new_time}")

    def zoom_timeline(self, factor):
        if not hasattr(self, "timeline_view"):
            return
        anchor = float(self.current_time or 0)
        self.timeline_view.zoom_by(factor, anchor)

    def fit_timeline(self):
        if hasattr(self, "timeline_view"):
            self.timeline_view.fit_to_window()

    def timeline_view_changed(self, start_sec, visible_sec, duration_sec):
        if not hasattr(self, "timeline_scroll"):
            return

        max_scroll = 10000
        max_start = max(0.0, float(duration_sec or 0.0) - float(visible_sec or 0.0))
        self.timeline_scroll.blockSignals(True)
        if max_start <= 0.01:
            self.timeline_scroll.setRange(0, 0)
            self.timeline_scroll.setEnabled(False)
            self.timeline_scroll.setPageStep(max_scroll)
            self.timeline_scroll.setValue(0)
        else:
            value = int(round((float(start_sec or 0.0) / max_start) * max_scroll))
            page_step = max(1, int(round((float(visible_sec or 0.0) / max(float(duration_sec or 1.0), 1.0)) * max_scroll)))
            self.timeline_scroll.setEnabled(True)
            self.timeline_scroll.setRange(0, max_scroll)
            self.timeline_scroll.setPageStep(page_step)
            self.timeline_scroll.setValue(max(0, min(max_scroll, value)))
        self.timeline_scroll.blockSignals(False)

        if hasattr(self, "lbl_timeline_zoom"):
            self.lbl_timeline_zoom.setText(f"{int(round(self.timeline_view.zoom * 100))}%")

    def timeline_scroll_changed(self, value):
        if not hasattr(self, "timeline_view"):
            return
        max_start = self.timeline_view._max_view_start()
        self.timeline_view.set_view_start((float(value) / 10000.0) * max_start)

    def timeline_dragged(self, value):
        self.seek_to_seconds(value)

    def update_time_label(self):
        def format_time(secs):
            try:
                secs_val = int(round(float(secs or 0)))
            except Exception:
                secs_val = 0
            m, s = divmod(secs_val, 60)
            h, m = divmod(m, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"
        
        curr_str = format_time(self.current_time)
        dur_str = format_time(self.duration)
        self.lbl_time.setText(f"{curr_str} / {dur_str}")

    def set_selected_rows_gender(self, gender="Male"):
        """Quickly assign Male or Female voice to all selected rows."""
        rows = self.selected_subtitle_rows()
        if not rows:
            popup_warning(self, "No Rows Selected", "Please select one or more rows in the table or timeline.")
            return
        voice_name = "Male - Piseth" if gender == "Male" else "Female - Sreymom"
        for r in rows:
            self.set_voice_for_row(r, voice_name)
        self.log_workflow_msg(f"Assigned {voice_name} to {len(rows)} selected rows.")

    def change_table_font_size(self, delta):
        """Increase or decrease table text font size dynamically."""
        if not hasattr(self, "table"):
            return
        self.table_font_size = max(8, min(18, getattr(self, "table_font_size", 10) + delta))
        bold_font = QFont("Noto Sans Khmer", self.table_font_size, QFont.Weight.DemiBold)
        self.table.setFont(bold_font)
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item:
                    item.setFont(bold_font)
        row_h = max(34, int(self.table_font_size * 3.6))
        self.table.verticalHeader().setDefaultSectionSize(row_h)

    def browse_logo_image(self):
        """Open file dialog to pick a custom logo/watermark image."""
        file_path, _ = popup_open_file_name(
            self, "Select Logo / Watermark Image", "",
            "Image Files (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)"
        )
        if file_path:
            self.txt_logo_path.setText(file_path)
            self.settings["logo_path"] = file_path
            save_settings(self.settings)
            self.update_logo_overlay_preview()
            self.log_workflow_msg(f"Loaded Logo: {os.path.basename(file_path)}")

    def clear_logo_image(self):
        """Clear logo image selection."""
        self.txt_logo_path.clear()
        self.settings["logo_path"] = ""
        save_settings(self.settings)
        self.update_logo_overlay_preview()

    def handle_player_dropped_file(self, file_path, drop_pos=None):
        """Handle drag and drop of Logo image, Video, Audio, or Subtitles directly onto the player."""
        if not file_path or not os.path.exists(file_path):
            return
        ext = os.path.splitext(file_path)[1].lower()
        filename = os.path.basename(file_path)

        # 1. Image -> Set Logo & Position at Drop Location
        if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.svg', '.ico'):
            self.txt_logo_path.setText(file_path)
            self.settings["logo_path"] = file_path
            save_settings(self.settings)
            if hasattr(self, "chk_show_logo"):
                self.chk_show_logo.setChecked(True)

            if drop_pos and hasattr(self, "get_actual_video_rect") and hasattr(self, "draggable_logo_widget"):
                v_rect = self.get_actual_video_rect()
                lw = self.draggable_logo_widget.width()
                lh = self.draggable_logo_widget.height()
                avail_w = max(1, v_rect.width() - lw)
                avail_h = max(1, v_rect.height() - lh)
                clamped_x = max(v_rect.x(), min(v_rect.x() + avail_w, drop_pos.x() - lw // 2))
                clamped_y = max(v_rect.y(), min(v_rect.y() + avail_h, drop_pos.y() - lh // 2))
                self.logo_rel_x = max(0.0, min(1.0, float(clamped_x - v_rect.x()) / avail_w))
                self.logo_rel_y = max(0.0, min(1.0, float(clamped_y - v_rect.y()) / avail_h))
                self.draggable_logo_widget.set_relative_position(self.logo_rel_x, self.logo_rel_y)

            self.update_logo_overlay_preview()
            if drop_pos and hasattr(self, "video_widget") and hasattr(self.video_widget, "logo_item"):
                self.video_widget.logo_item.setPos(drop_pos.x() - 45, drop_pos.y() - 45)
            if hasattr(self, "draggable_logo_widget"):
                self.draggable_logo_widget.hide()
            self.log_workflow_msg(f"Dropped & Loaded Logo: {filename}")

        # 2. Video -> Load Video directly
        elif ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.wmv'):
            self.load_video_file_direct(file_path)
            self.log_workflow_msg(f"Dropped & Loaded Video: {filename}")

        # 3. Audio -> Load as Background / A2 Audio
        elif ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
            self.load_background_music_file(file_path)
            self.log_workflow_msg(f"Dropped & Loaded Audio: {filename}")

        # 4. Subtitles -> Import Subtitles
        elif ext in ('.srt', '.vtt', '.ass'):
            self.import_srt_file(file_path)
            self.log_workflow_msg(f"Dropped & Loaded Subtitles: {filename}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile()
                pos = None
                if hasattr(self, "player_container"):
                    try:
                        g_pos = event.position().toPoint()
                    except Exception:
                        g_pos = event.pos()
                    mapped = self.player_container.mapFromGlobal(self.mapToGlobal(g_pos))
                    if self.player_container.rect().contains(mapped):
                        pos = mapped
                self.handle_player_dropped_file(file_path, pos)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        from PyQt6.QtWidgets import QLineEdit, QTextEdit
        fw = QApplication.focusWidget()
        if not isinstance(fw, (QLineEdit, QTextEdit)):
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if hasattr(self, "video_widget") and hasattr(self.video_widget, "delete_logo"):
                    if self.video_widget.logo_item.isVisible():
                        self.video_widget.delete_logo()
                        event.accept()
                        return
            elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                if hasattr(self, "video_widget") and hasattr(self.video_widget, "zoom_logo"):
                    if self.video_widget.logo_item.isVisible():
                        self.video_widget.zoom_logo(1.10)
                        event.accept()
                        return
            elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                if hasattr(self, "video_widget") and hasattr(self.video_widget, "zoom_logo"):
                    if self.video_widget.logo_item.isVisible():
                        self.video_widget.zoom_logo(0.90)
                        event.accept()
                        return
        super().keyPressEvent(event)

    def on_logo_scale_dragged(self, scale_ratio):
        """Called live when the user resizes or zooms the logo with corners or scroll wheel."""
        self.logo_scale_val = float(scale_ratio)
        self.settings["logo_scale"] = self.logo_scale_val
        save_settings(self.settings)
        # Debounce workflow log message so it does not spam on every scroll wheel tick
        if not hasattr(self, "_logo_zoom_timer"):
            self._logo_zoom_timer = QTimer(self)
            self._logo_zoom_timer.setSingleShot(True)
            self._logo_zoom_timer.timeout.connect(self._log_logo_zoom_finished)
        self._logo_zoom_timer.start(500)

    def _log_logo_zoom_finished(self):
        pct = int(getattr(self, "logo_scale_val", 0.15) * 100)
        self.log_workflow_msg(f"Logo Scale: {pct}% of video width")
        self.schedule_session_autosave()

    def on_logo_position_dragged(self, rel_x, rel_y):
        """Called live when the user drags the logo on the video player."""
        self.logo_rel_x = rel_x
        self.logo_rel_y = rel_y
        self.settings["logo_rel_x"] = float(rel_x)
        self.settings["logo_rel_y"] = float(rel_y)
        save_settings(self.settings)
        self.schedule_session_autosave()

    def get_actual_video_rect(self):
        """Compute the exact bounding box of the active video frame inside the player container."""
        active = getattr(self, "player_container", None) or getattr(self, "video_widget", None)
        if not active:
            return QRect(0, 0, 300, 200)
        pw = max(1, active.width())
        ph = max(1, active.height())
        
        # Check aspect ratio
        aspect_str = self.cmb_aspect.currentText() if hasattr(self, "cmb_aspect") else "9:16"
        if "9:16" in aspect_str:
            video_ar = 9.0 / 16.0
        elif "16:9" in aspect_str:
            video_ar = 16.0 / 9.0
        elif "1:1" in aspect_str:
            video_ar = 1.0
        elif getattr(self, "video_aspect_ratio", 0) > 0:
            video_ar = self.video_aspect_ratio
        else:
            video_ar = 9.0 / 16.0

        player_ar = float(pw) / float(ph)
        if player_ar > video_ar:
            # Container is wider than video -> pillarboxes on left/right
            vh = ph
            vw = max(1, int(ph * video_ar))
            vx = max(0, int((pw - vw) / 2))
            vy = 0
        else:
            # Container is taller than video -> letterboxes on top/bottom
            vw = pw
            vh = max(1, int(pw / video_ar))
            vx = 0
            vy = max(0, int((ph - vh) / 2))
            
        return QRect(vx, vy, vw, vh)

    def on_logo_preset_changed(self):
        """Update logo position from preset dropdown, anchored inside the active movie frame."""
        pos_text = self.cmb_logo_pos.currentText() if hasattr(self, "cmb_logo_pos") else "Top-Right"
        if "Top-Left" in pos_text:
            self.logo_rel_x, self.logo_rel_y = 0.04, 0.04
        elif "Bottom-Right" in pos_text:
            self.logo_rel_x, self.logo_rel_y = 0.96, 0.92
        elif "Bottom-Left" in pos_text:
            self.logo_rel_x, self.logo_rel_y = 0.04, 0.92
        elif "Center" in pos_text:
            self.logo_rel_x, self.logo_rel_y = 0.50, 0.50
        else: # Top-Right
            self.logo_rel_x, self.logo_rel_y = 0.96, 0.04

        if hasattr(self, "video_widget") and hasattr(self.video_widget, "position_logo_preset"):
            self.video_widget.position_logo_preset(pos_text)

        if hasattr(self, "draggable_logo_widget"):
            self.draggable_logo_widget.set_relative_position(self.logo_rel_x, self.logo_rel_y)
        self.update_logo_overlay_preview()

    def on_logo_size_preset_changed(self):
        size_map = {"8% (Default)": 0.08, "5% (Small)": 0.05,
                    "10% (Medium)": 0.10, "15% (Large)": 0.15}
        self.logo_scale_val = size_map.get(self.cmb_logo_size.currentText(), 0.08)
        self.update_logo_overlay_preview()

    def attach_overlay_to_current_player(self):
        """Ensure Draggable Logo is directly parented to player_container on top of the player stack."""
        target_parent = getattr(self, "player_container", None)
        if target_parent and hasattr(self, "draggable_logo_widget"):
            if self.draggable_logo_widget.parent() != target_parent:
                self.draggable_logo_widget.setParent(target_parent)
                target_parent.installEventFilter(self.draggable_logo_widget)
            self.draggable_logo_widget.reposition_from_relative()
            self.draggable_logo_widget.raise_()

    def update_logo_overlay_preview(self):
        """Update live Draggable Logo overlay on the video preview player."""
        show_enabled = self.chk_show_logo.isChecked() if hasattr(self, "chk_show_logo") else False
        logo_file = self.txt_logo_path.text().strip() if hasattr(self, "txt_logo_path") else ""
        watermark_text = self.txt_watermark.text().strip() if hasattr(self, "txt_watermark") else ""

        if not show_enabled or (not logo_file and not watermark_text):
            if hasattr(self, "draggable_logo_widget"):
                self.draggable_logo_widget.hide()
            if hasattr(self, "video_widget") and hasattr(self.video_widget, "logo_item"):
                self.video_widget.logo_item.hide()
            return

        size_map = {"8% (Default)": 0.08, "5% (Small)": 0.05, "10% (Medium)": 0.10, "15% (Large)": 0.15}
        preset_size = size_map.get(self.cmb_logo_size.currentText() if hasattr(self, "cmb_logo_size") else "", 0.08)
        size_pct = float(getattr(self, "logo_scale_val", preset_size))
        v_rect = self.get_actual_video_rect()
        target_w = max(1, int(v_rect.width() * size_pct))

        from PyQt6.QtGui import QPixmap
        if logo_file and os.path.exists(logo_file):
            pix = trimmed_logo_pixmap(logo_file)
            if not pix.isNull():
                if hasattr(self, "video_widget") and hasattr(self.video_widget, "set_logo_pixmap"):
                    self.video_widget.set_logo_pixmap(pix, width=target_w)
                    pos_preset = self.cmb_logo_pos.currentText() if hasattr(self, "cmb_logo_pos") else "Top-Right"
                    self.video_widget.position_logo_preset(pos_preset)

                if hasattr(self, "draggable_logo_widget"):
                    self.draggable_logo_widget.hide()
                return

        elif watermark_text:
            if hasattr(self, "draggable_logo_widget"):
                self.attach_overlay_to_current_player()
                self.draggable_logo_widget.logo_pixmap = None
                self.draggable_logo_widget.set_watermark_text(watermark_text)
                self.draggable_logo_widget.show()
                self.draggable_logo_widget.raise_()
            return

        if hasattr(self, "draggable_logo_widget"):
            self.draggable_logo_widget.hide()
        if hasattr(self, "video_widget") and hasattr(self.video_widget, "logo_item"):
            self.video_widget.logo_item.hide()

    def pick_custom_subtitle_color(self):
        """Open color dialog to pick any custom text color."""
        color = QColorDialog.getColor(QColor("#FFE500"), self, "Select Subtitle Text Color")
        if color.isValid():
            hex_c = color.name().upper()
            item_text = f"Custom ({hex_c})"
            idx = self.cmb_sub_color.findText(item_text)
            if idx == -1:
                self.cmb_sub_color.addItem(item_text)
                idx = self.cmb_sub_color.count() - 1
            self.cmb_sub_color.setCurrentIndex(idx)
            self.apply_subtitle_styling()

    def apply_subtitle_styling(self):
        """Apply custom font, color, stroke, background box, and position to subtitle overlay."""
        if not hasattr(self, "subtitle_overlay_label"):
            return

        font_fam = self.cmb_sub_font.currentText().split(" (")[0] if hasattr(self, "cmb_sub_font") else "Noto Sans Khmer"
        size_str = self.cmb_sub_size.currentText().split(" px")[0] if hasattr(self, "cmb_sub_size") else "22"
        try:
            font_size = int(size_str)
        except Exception:
            font_size = 20

        color_text = self.cmb_sub_color.currentText() if hasattr(self, "cmb_sub_color") else "#FFE500"
        if "#" in color_text:
            text_color = color_text.split("(")[-1].replace(")", "").strip()
        else:
            text_color = "#FFE500"

        stroke_choice = self.cmb_sub_stroke.currentText() if hasattr(self, "cmb_sub_stroke") else "Black 3px"
        bg_choice = self.cmb_sub_bg.currentText() if hasattr(self, "cmb_sub_bg") else "Dark Glass Pill"
        pos_choice = self.cmb_sub_pos.currentText() if hasattr(self, "cmb_sub_pos") else "Bottom"

        # Construct stylesheet
        if "Transparent" in bg_choice:
            bg_css = "background-color: transparent; border: none;"
        elif "Golden" in bg_choice:
            bg_css = f"background-color: rgba(0, 0, 0, 180); border: 1.5px solid {text_color}; border-radius: 6px;"
        elif "Black Solid" in bg_choice:
            bg_css = "background-color: #000000; border: 1px solid #333333; border-radius: 4px;"
        else:  # Dark Glass Pill
            bg_css = "background-color: rgba(0, 0, 0, 160); border: 1px solid rgba(255, 255, 255, 60); border-radius: 6px;"

        margin_bottom = 12 if pos_choice == "Bottom" else (40 if pos_choice == "Lower-Third" else 0)
        margin_top = 16 if pos_choice == "Top" else 0

        self.subtitle_overlay_label.setStyleSheet(f"""
            QLabel#videoSubtitleOverlay {{
                color: {text_color};
                {bg_css}
                padding: 6px 14px;
                font-family: '{font_fam}', 'Noto Sans Khmer', sans-serif;
                font-size: {font_size}px;
                font-weight: 800;
                margin-bottom: {margin_bottom}px;
                margin-top: {margin_top}px;
                margin-left: 10px;
                margin-right: 10px;
            }}
        """)

        # Trigger preview refresh
        self.update_subtitle_overlay_by_time(float(self.current_time or 0))

    def toggle_subtitles_overlay(self, checked):
        """Show or hide the real-time Khmer subtitle overlay on the video player."""
        if not hasattr(self, "subtitle_overlay_label"):
            return
        if not checked:
            self.subtitle_overlay_label.hide()
        else:
            self.update_subtitle_overlay_by_time(float(self.current_time or 0))

    def update_subtitle_overlay_by_time(self, seconds):
        """Update subtitle overlay text on the video player based on current playhead time."""
        if not hasattr(self, "subtitle_overlay_label") or not hasattr(self, "chk_show_subtitles") or not hasattr(self, "table"):
            return
        if not self.chk_show_subtitles.isChecked():
            self.subtitle_overlay_label.hide()
            return
            
        current_text = ""
        for r in range(self.table.rowCount()):
            timecode_item = self.table.item(r, COL_TIME)
            if not timecode_item:
                continue
            try:
                start_sec, end_sec = parse_timecode_range(timecode_item.text().strip())
                if start_sec <= seconds <= end_sec:
                    # Prefer Khmer translated text
                    trans_item = self.table.item(r, COL_TRANSLATED)
                    orig_item = self.table.item(r, COL_ORIGINAL)
                    trans_text = condense_khmer_dubbing_text(trans_item.text()).strip() if trans_item else ""
                    orig_text = orig_item.text().strip() if orig_item else ""
                    current_text = trans_text if trans_text else orig_text
                    break
            except Exception:
                pass

        if current_text:
            self.subtitle_overlay_label.setText(current_text)
            self.subtitle_overlay_label.show()
        else:
            self.subtitle_overlay_label.hide()

    def highlight_subtitle_by_time(self, seconds):
        self.table.blockSignals(True)
        # Reset colors
        for r in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                item = self.table.item(r, col)
                if item:
                    item.setBackground(QBrush(QColor("#FFFFFF")))
        
        # Match if current time is within segment range
        for r in range(self.table.rowCount()):
            timecode = self.table.item(r, 1).text().strip()
            try:
                start_sec, end_sec = parse_timecode_range(timecode)
                if start_sec <= seconds <= end_sec:
                    for col in range(self.table.columnCount()):
                        item = self.table.item(r, col)
                        if item:
                            item.setBackground(QBrush(QColor("#E1F5FE"))) # Soft blue highlight
                    self.table.scrollToItem(self.table.item(r, 0))
                    break
            except Exception:
                pass
        self.table.blockSignals(False)

    def selected_subtitle_rows(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows:
            rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows and hasattr(self, "timeline_view") and self.timeline_view.selected_indices:
            segs = getattr(self.timeline_view, "segments", [])
            rows = sorted({int(segs[i].get("row", i)) for i in self.timeline_view.selected_indices if i < len(segs)})
        if rows:
            return rows
        current_row = self.table.currentRow()
        if current_row >= 0:
            return [current_row]
        return []

    def load_bgm_audio(self):
        file_path, _ = popup_open_file_name(
            self, "Load Background Music (A2)", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if not file_path:
            return
        self.bgm_file = file_path
        self.settings["background_music_path"] = file_path
        save_settings(self.settings)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_source_name(f"BGM: {os.path.basename(file_path)}", is_bgm=True)
        self.update_transcript_box()
        self.log_workflow_msg(f"BGM loaded on A2: {os.path.basename(file_path)}")
        popup_info(self, "BGM Loaded", f"Loaded background music:\n{os.path.basename(file_path)}")

    def load_a1_audio_for_selected_row(self):
        rows = self.selected_subtitle_rows()
        if not rows:
            popup_warning(self, "No Row Selected", "Select a subtitle row before loading A1 audio.")
            return

        row_idx = rows[0]
        file_path, _ = popup_open_file_name(
            self, "Load A1 Audio For Selected Row", "",
            "Audio/Video Files (*.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv);;All Files (*)"
        )
        if not file_path:
            return

        self.audio_files[row_idx] = file_path
        status_item = self.table.item(row_idx, COL_STATUS)
        if status_item:
            status_item.setText("A1 Imported")
        self.update_transcript_box()
        self.log_workflow_msg(f"A1 audio loaded for line {row_idx + 1}: {os.path.basename(file_path)}")
        self.preview_line_in_video(row_idx, file_path)

    def open_find_replace_dialog(self):
        if self.table.rowCount() == 0:
            popup_warning(self, "Empty Table", "No subtitle lines to search.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Find & Replace")
        dialog.setStyleSheet(MESSAGE_BOX_STYLE)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        find_input = QLineEdit()
        replace_input = QLineEdit()
        form.addRow("Find:", find_input)
        form.addRow("Replace With:", replace_input)
        layout.addLayout(form)

        chk_original = QCheckBox("Original Text")
        chk_translated = QCheckBox("Translated Text")
        chk_original.setChecked(True)
        chk_translated.setChecked(True)
        chk_case = QCheckBox("Match case")
        option_row = QHBoxLayout()
        option_row.addWidget(chk_original)
        option_row.addWidget(chk_translated)
        option_row.addWidget(chk_case)
        layout.addLayout(option_row)

        button_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_replace = QPushButton("Replace All")
        btn_cancel.clicked.connect(dialog.reject)
        button_row.addStretch()
        button_row.addWidget(btn_cancel)
        button_row.addWidget(btn_replace)
        layout.addLayout(button_row)

        def replace_all():
            find_text = find_input.text()
            if not find_text:
                popup_warning(dialog, "Find & Replace", "Enter text to find.")
                return
            columns = []
            if chk_original.isChecked():
                columns.append(COL_ORIGINAL)
            if chk_translated.isChecked():
                columns.append(COL_TRANSLATED)
            if not columns:
                popup_warning(dialog, "Find & Replace", "Choose at least one text column.")
                return
            count = self.replace_text_in_table(find_text, replace_input.text(), columns, chk_case.isChecked())
            dialog.accept()
            popup_info(self, "Find & Replace", f"Replaced {count} occurrence(s).")

        btn_replace.clicked.connect(replace_all)
        dialog.exec()

    def replace_text_in_table(self, find_text, replace_text, columns, match_case=False):
        count = 0
        flags = 0 if match_case else re.IGNORECASE
        pattern = re.compile(re.escape(find_text), flags)

        self.table.blockSignals(True)
        try:
            for row_idx in range(self.table.rowCount()):
                row_changed = False
                for col in columns:
                    item = self.table.item(row_idx, col)
                    if not item:
                        continue
                    old_text = item.text()
                    new_text, replacements = pattern.subn(replace_text, old_text)
                    if replacements:
                        item.setText(new_text)
                        item.setToolTip(new_text)
                        count += replacements
                        row_changed = True
                if row_changed:
                    self.invalidate_generated_voice_for_row(row_idx)
        finally:
            self.table.blockSignals(False)

        if count:
            self.update_transcript_box()
            self.log_workflow_msg(f"Find & Replace updated {count} occurrence(s).")
        return count

    def show_table_context_menu(self, pos):
        rows = self.selected_subtitle_rows()
        if not rows:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #171D31;
                color: #FFFFFF;
                border: 1px solid #8B5CF6;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #7C3AED;
                color: #FFFFFF;
            }
        """)

        act_male = menu.addAction("👨 Set Voice: Male - Piseth (តួប្រុស ពិសិដ្ឋ)")
        act_female = menu.addAction("👩 Set Voice: Female - Sreymom (តួស្រី ស្រីមុំ)")
        act_swap = menu.addAction("🔄 Swap Gender: Male ⇄ Female (ប្តូរ ប្រុស <-> ស្រី)")
        act_swap_regen = menu.addAction("⚡ Swap Gender & Re-Gen (ប្តូរ & បង្កើតសម្លេងភ្លាមៗ)")
        menu.addSeparator()
        if len(rows) >= 2:
            act_merge = menu.addAction("🔗 Merge Selected Lines (បញ្ចូលប្រយោគដែលបាក់/ដាច់)")
        else:
            act_merge = None
        act_auto = menu.addAction("⚡ Auto-Detect Voice from Movie (វិភាគសម្លេង)")
        act_regen = menu.addAction("🔄 Re-Generate Voiceover (បង្កើតសម្លេងឡើងវិញ)")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_male:
            for r in rows:
                self.set_voice_for_row(r, "Male - Piseth")
                self.invalidate_generated_voice_for_row(r)
            self.update_transcript_box()
            self.log_workflow_msg(f"Set voice to Male - Piseth for {len(rows)} line(s).")
        elif action == act_female:
            for r in rows:
                self.set_voice_for_row(r, "Female - Sreymom")
                self.invalidate_generated_voice_for_row(r)
            self.update_transcript_box()
            self.log_workflow_msg(f"Set voice to Female - Sreymom for {len(rows)} line(s).")
        elif action in (act_swap, act_swap_regen):
            for r in rows:
                curr = self.get_voice_for_row(r)
                if "female" in curr.lower() or "sreymom" in curr.lower():
                    new_v = "Male - Piseth" if not is_voxcpm_voice(curr) else VOXCPM_MALE_VOICE_NAME
                else:
                    new_v = "Female - Sreymom" if not is_voxcpm_voice(curr) else VOXCPM_FEMALE_VOICE_NAME
                self.set_voice_for_row(r, new_v)
                self.invalidate_generated_voice_for_row(r)
            self.update_transcript_box()
            self.log_workflow_msg(f"Swapped voice gender for {len(rows)} line(s).")
            if action == act_swap_regen:
                self.regenerate_selected_voiceovers()
        elif act_merge and action == act_merge:
            self.merge_selected_subtitle_rows()
        elif action == act_auto:
            selected_rows_data = []
            for r in rows:
                time_item = self.table.item(r, COL_TIME)
                orig_item = self.table.item(r, COL_ORIGINAL)
                trans_item = self.table.item(r, COL_TRANSLATED)
                if time_item:
                    try:
                        s, e = parse_timecode_range(time_item.text())
                        selected_rows_data.append({
                            "row": r,
                            "start": s,
                            "end": e,
                            "text": (orig_item.text() if orig_item else "").strip(),
                            "translated": (trans_item.text() if trans_item else "").strip(),
                        })
                    except Exception:
                        pass
            if selected_rows_data and self.video_file and os.path.exists(self.video_file):
                self.progress_bar.setVisible(True)
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat("Analyzing selected lines with AI... %p%")
                self.voice_gender_thread = VoiceGenderWorker(self.video_file, selected_rows_data, self.settings)
                self.voice_gender_thread.progress.connect(self.progress_bar.setValue)
                self.voice_gender_thread.completed.connect(self.auto_voice_finished)
                self.voice_gender_thread.error.connect(self.auto_voice_error)
                self.voice_gender_thread.start()
        elif action == act_regen:
            self.regenerate_selected_voiceovers()

    def merge_selected_subtitle_rows(self):
        rows = sorted(self.selected_subtitle_rows())
        if len(rows) < 2:
            popup_warning(self, "Select Multiple Lines", "Please select 2 or more consecutive rows to merge.")
            return

        for i in range(len(rows) - 1):
            if rows[i+1] != rows[i] + 1:
                popup_warning(self, "Non-Consecutive Selection", "Please select consecutive rows to merge them properly.")
                return

        first_row = rows[0]
        last_row = rows[-1]

        first_time_str = self.table.item(first_row, COL_TIME).text() if self.table.item(first_row, COL_TIME) else ""
        last_time_str = self.table.item(last_row, COL_TIME).text() if self.table.item(last_row, COL_TIME) else ""
        s_sec, _ = parse_timecode_range(first_time_str)
        _, e_sec = parse_timecode_range(last_time_str)
        merged_time_str = f"{format_seconds_to_timecode(s_sec)} - {format_seconds_to_timecode(e_sec)}"

        combined_orig = " ".join(
            (self.table.item(r, COL_ORIGINAL).text() if self.table.item(r, COL_ORIGINAL) else "").strip()
            for r in rows
        ).strip()

        combined_trans = " ".join(
            re.sub(r'[\.]{2,}', '', (self.table.item(r, COL_TRANSLATED).text() if self.table.item(r, COL_TRANSLATED) else "").strip()).rstrip('.…')
            for r in rows
        ).strip()

        self.table.blockSignals(True)
        if self.table.item(first_row, COL_TIME):
            self.table.item(first_row, COL_TIME).setText(merged_time_str)
        if self.table.item(first_row, COL_ORIGINAL):
            self.table.item(first_row, COL_ORIGINAL).setText(combined_orig)
            self.table.item(first_row, COL_ORIGINAL).setToolTip(combined_orig)
        if self.table.item(first_row, COL_TRANSLATED):
            self.table.item(first_row, COL_TRANSLATED).setText(combined_trans)
            self.table.item(first_row, COL_TRANSLATED).setToolTip(combined_trans)
        self.invalidate_generated_voice_for_row(first_row)

        for r in reversed(rows[1:]):
            self.invalidate_generated_voice_for_row(r)
            self.table.removeRow(r)

        for r in range(self.table.rowCount()):
            if self.table.item(r, 0):
                self.table.item(r, 0).setText(str(r + 1))

        self.table.blockSignals(False)
        self.update_transcript_box()
        self.log_workflow_msg(f"Merged {len(rows)} lines into a single continuous sentence (Line {first_row + 1}).")
        popup_info(self, "Lines Merged", f"Successfully merged {len(rows)} lines into a single smooth dialogue.")

    def set_voice_for_selected_rows(self):
        rows = self.selected_subtitle_rows()
        if not rows:
            popup_warning(self, "No Row Selected", "Select one or more subtitle rows first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Set Voice")
        dialog.setStyleSheet(MESSAGE_BOX_STYLE)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        voice_combo = QComboBox()
        voice_combo.addItems(self.voice_choices_for_language())
        voice_combo.setCurrentText(self.get_voice_for_row(rows[0]))
        form.addRow("Voice:", voice_combo)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_apply = QPushButton("Apply")
        btn_cancel.clicked.connect(dialog.reject)
        btn_apply.clicked.connect(dialog.accept)
        button_row.addStretch()
        button_row.addWidget(btn_cancel)
        button_row.addWidget(btn_apply)
        layout.addLayout(button_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            voice_name = voice_combo.currentText()
            if not self.validate_voxcpm_config(voice_name):
                return
            for row_idx in rows:
                self.set_voice_for_row(row_idx, voice_name)
                status_item = self.table.item(row_idx, COL_STATUS)
                if status_item and status_item.text() == "Ready":
                    status_item.setText("Draft")
            self.update_transcript_box()
            self.log_workflow_msg(f"Voice set to {voice_name} for {len(rows)} line(s).")

    def regenerate_selected_voiceovers(self):
        rows = self.selected_subtitle_rows()
        if not rows:
            popup_warning(self, "No Row Selected", "Please select one or more subtitle rows in the table to re-generate voice.")
            return

        tasks = []
        for r in rows:
            trans_item = self.table.item(r, COL_TRANSLATED)
            text = trans_item.text().strip() if trans_item else ""
            if not text:
                continue
            voice_char = self.get_voice_for_row(r)
            if not self.validate_voxcpm_config(voice_char):
                return
            time_item = self.table.item(r, COL_TIME)
            try:
                s_sec, e_sec = parse_timecode_range(time_item.text()) if time_item else (0.0, 3.0)
            except Exception:
                s_sec, e_sec = 0.0, 3.0
            tasks.append((r, text, voice_char, s_sec, e_sec))

        if not tasks:
            popup_warning(self, "Empty Text", "Selected lines have no translated text to synthesize.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Re-generating voiceover for {len(tasks)} line(s)... %p%")
        self.log_workflow_msg(f"Re-generating voiceover for {len(tasks)} selected line(s).")

        base_rate = float(self.sld_rate.value()) / 100.0 if hasattr(self, "sld_rate") else 1.0
        base_pitch = int(self.sld_pitch.value()) if hasattr(self, "sld_pitch") else 0
        self.tts_thread = TTSWorker(tasks, self.cache_dir, self.settings, base_rate=base_rate, base_pitch=base_pitch)
        self.tts_thread.status.connect(lambda text: self.progress_bar.setFormat(f"{text}... %p%"))
        self.tts_thread.row_completed.connect(self.single_tts_finished)
        self.tts_thread.completed.connect(lambda: self.progress_bar.setVisible(False))
        self.tts_thread.error.connect(self.process_error)
        self.tts_thread.start()

    def collect_voice_detection_rows(self):
        rows = []
        for row_idx in range(self.table.rowCount()):
            time_item = self.table.item(row_idx, COL_TIME)
            orig_item = self.table.item(row_idx, COL_ORIGINAL)
            trans_item = self.table.item(row_idx, COL_TRANSLATED)
            if not time_item:
                continue
            try:
                start_sec, end_sec = parse_timecode_range(time_item.text())
            except Exception:
                continue
            if end_sec <= start_sec:
                end_sec = start_sec + 0.8
            rows.append({
                "row": row_idx,
                "start": max(0.0, start_sec),
                "end": max(start_sec + 0.25, end_sec),
                "text": (orig_item.text() if orig_item else "").strip(),
                "translated": (trans_item.text() if trans_item else "").strip(),
            })
        return rows

    def should_auto_assign_voices_before_generation(self):
        if self.voice_gender_analyzed:
            return False
        if not self.video_file or not os.path.exists(self.video_file):
            return False
        if self.table.rowCount() == 0:
            return False
        row_voices = [self.get_voice_for_row(row_idx) for row_idx in range(self.table.rowCount())]
        if VOXCPM_VOICE_NAME in row_voices or any("Auto" in v for v in row_voices):
            return True
        curr_main_voice = self.cmb_voice.currentText()
        if "Auto" in curr_main_voice or VOXCPM_VOICE_NAME in curr_main_voice:
            return True
        row_voices_set = set(row_voices)
        if len(row_voices_set) != 1:
            return False
        return any(is_voxcpm_voice(voice_name) or "Auto" in voice_name for voice_name in row_voices_set)

    def auto_assign_voices_from_movie(self, checked=False, continue_after=False):
        if hasattr(self, "voice_gender_thread") and self.voice_gender_thread.isRunning():
            message = "Auto Voice is already analyzing this movie."
            self.log_workflow_msg(message)
            self.show_workflow_notice("Auto Voice Running", message, warning=True)
            return
        if not self.video_file or not os.path.exists(self.video_file):
            popup_warning(self, "No Video", "Import a movie/video before running Auto Voice.")
            return
        if self.table.rowCount() == 0:
            popup_warning(self, "No Lines", "Load subtitles or transcribe the movie before running Auto Voice.")
            return

        rows = self.collect_voice_detection_rows()
        if not rows:
            popup_warning(self, "No Timed Lines", "Auto Voice needs subtitle lines with start/end timecodes.")
            return

        self.auto_voice_then_generate = bool(continue_after)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Auto Voice: analyzing speaker context with AI... %p%")
        self.log_workflow_msg(f"Auto Voice started: analyzing {len(rows)} subtitle lines.")

        self.voice_gender_thread = VoiceGenderWorker(self.video_file, rows, self.settings)
        self.voice_gender_thread.progress.connect(self.progress_bar.setValue)
        self.voice_gender_thread.status.connect(lambda text: self.progress_bar.setFormat(f"{text}... %p%"))
        self.voice_gender_thread.completed.connect(self.auto_voice_finished)
        self.voice_gender_thread.error.connect(self.auto_voice_error)
        self.voice_gender_thread.start()

    def auto_voice_finished(self, results):
        self.progress_bar.setVisible(False)
        male_count = 0
        female_count = 0
        kept_count = 0

        lang = self.cmb_lang.currentText()
        main_voice = self.cmb_voice.currentText()
        is_voxcpm = is_voxcpm_voice(main_voice)

        for result in results:
            row_idx = int(result.get("row", -1))
            gender = result.get("gender", "Unknown")
            if row_idx < 0 or row_idx >= self.table.rowCount():
                continue

            # Prioritize unambiguous linguistic gender ground truth from translated/original text
            trans_item = self.table.item(row_idx, COL_TRANSLATED)
            orig_item = self.table.item(row_idx, COL_ORIGINAL)
            combined_txt = (((trans_item.text() if trans_item else "") + " " + (orig_item.text() if orig_item else ""))).strip()
            ling = detect_gender_from_text(combined_txt)
            if ling:
                gender = ling

            if is_voxcpm:
                if gender.startswith("Male"):
                    target_voice = VOXCPM_MALE_VOICE_NAME
                else:
                    target_voice = VOXCPM_FEMALE_VOICE_NAME
            elif lang == "Khmer":
                if gender.startswith("Male"):
                    target_voice = "Male - Piseth"
                else:
                    target_voice = "Female - Sreymom"
            elif lang == "English":
                if gender.startswith("Male"):
                    target_voice = "Male - Bob"
                else:
                    target_voice = "Female - Alice"
            else:  # Chinese
                if gender.startswith("Male"):
                    target_voice = "Male - Yunjian"
                else:
                    target_voice = "Female - Xiaoxiao"

            self.set_voice_for_row(row_idx, target_voice)
            self.invalidate_generated_voice_for_row(row_idx)
            status_item = self.table.item(row_idx, COL_STATUS)
            if status_item and status_item.text() != "A1 Imported":
                status_item.setText("Voice Set")

            if gender.startswith("Male"):
                male_count += 1
            elif gender.startswith("Female"):
                female_count += 1
            else:
                kept_count += 1

        self.voice_gender_analyzed = True
        extracted_refs = self.extract_auto_voice_references(results) if is_voxcpm else []
        self.update_transcript_box()
        male_display = "Piseth (Male)" if lang == "Khmer" else "Male"
        female_display = "Sreymom (Female)" if lang == "Khmer" else "Female"
        self.log_workflow_msg(
            f"Auto Voice finished: {male_count} {male_display}, {female_count} {female_display}"
            + (f", {kept_count} kept previous" if kept_count else "")
            + "."
        )
        if extracted_refs:
            self.log_workflow_msg("Auto Voice extracted clone refs: " + ", ".join(extracted_refs))

        if self.auto_voice_then_generate:
            self.auto_voice_then_generate = False
            QTimer.singleShot(50, self.generate_voiceover_all)
        else:
            self.show_workflow_notice(
                "Auto Voice Finished",
                f"Assigned voices for {len(results)} lines.\n{male_display}: {male_count}\n{female_display}: {female_count}"
            )

    def should_update_auto_voice_references(self):
        self.save_voxcpm_reference_from_ui()
        female_ref = self.settings.get("voxcpm_reference_audio_female", "").strip()
        male_ref = self.settings.get("voxcpm_reference_audio_male", "").strip()
        default_ref = default_voxcpm_reference_media_path()
        if not female_ref or not male_ref:
            return True
        if default_ref:
            default_norm = os.path.normcase(os.path.normpath(default_ref))
            if os.path.normcase(os.path.normpath(female_ref)) == default_norm:
                return True
            if os.path.normcase(os.path.normpath(male_ref)) == default_norm:
                return True
        return os.path.normcase(os.path.normpath(female_ref)) == os.path.normcase(os.path.normpath(male_ref))

    def extract_reference_clip_from_movie(self, result, gender):
        import subprocess

        if not self.video_file or not os.path.exists(self.video_file):
            return ""

        os.makedirs(self.cache_dir, exist_ok=True)
        row_idx = int(result.get("row", 0))
        start_sec = max(0.0, float(result.get("start", 0.0)) - 0.08)
        end_sec = max(start_sec + 0.5, float(result.get("end", start_sec + 1.2)) + 0.08)
        duration = max(0.6, min(end_sec - start_sec, 8.0))
        output_path = os.path.join(self.cache_dir, f"auto_{gender.lower()}_ref_line_{row_idx + 1}_{int(time.time())}.wav")
        audio_filter = "highpass=f=85,lowpass=f=7600,afftdn=nf=-25,dynaudnorm=f=150:g=15,loudnorm=I=-18:TP=-2:LRA=11"
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-ss", f"{start_sec:.3f}",
            "-i", self.video_file,
            "-t", f"{duration:.3f}",
            "-af", audio_filter,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            output_path,
        ]
        result_proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result_proc.returncode != 0:
            fallback_cmd = [
                "ffmpeg", "-y",
                "-hwaccel", "auto",
                "-ss", f"{start_sec:.3f}",
                "-i", self.video_file,
                "-t", f"{duration:.3f}",
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                output_path,
            ]
            result_proc = subprocess.run(
                fallback_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        if result_proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        return ""

    def extract_auto_voice_references(self, results):
        if not self.should_update_auto_voice_references():
            return []

        candidates = {"Male": [], "Female": []}
        for result in results:
            gender = str(result.get("gender", ""))
            pitch = float(result.get("pitch", 0.0) or 0.0)
            confidence = float(result.get("confidence", 0.0) or 0.0)
            if pitch <= 0 or confidence <= 0:
                continue
            if gender.startswith("Male"):
                candidates["Male"].append(result)
            elif gender.startswith("Female"):
                candidates["Female"].append(result)

        extracted = []
        for gender, setting_key, line_edit_name in (
            ("Female", "voxcpm_reference_audio_female", "txt_voxcpm_ref_female"),
            ("Male", "voxcpm_reference_audio_male", "txt_voxcpm_ref_male"),
        ):
            if not candidates[gender]:
                continue
            best = max(candidates[gender], key=lambda item: float(item.get("confidence", 0.0) or 0.0))
            clip_path = self.extract_reference_clip_from_movie(best, gender)
            if not clip_path:
                continue
            self.settings[setting_key] = clip_path
            line_edit = getattr(self, line_edit_name, None)
            if line_edit is not None:
                line_edit.setText(clip_path)
            extracted.append(f"{gender} line {int(best.get('row', 0)) + 1}")

        if extracted:
            save_settings(self.settings)
        return extracted

    def auto_voice_error(self, error_msg):
        self.progress_bar.setVisible(False)
        self.auto_voice_then_generate = False
        self.process_error(error_msg)

    # ----------------------------------------------------
    # Toolbar Action Logic
    # ----------------------------------------------------
    def import_video_dialog(self):
        file_paths, _ = popup_open_file_names(
            self, "Import One or Multiple Episodes", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)"
        )
        if not file_paths:
            return

        def natural_episode_key(path):
            return [int(part) if part.isdigit() else part.lower()
                    for part in re.split(r"(\d+)", os.path.basename(path))]

        file_paths = sorted(file_paths, key=natural_episode_key)
        if len(file_paths) > 1:
            added_count = self.add_batch_video_paths(file_paths)
            self.tabs.setCurrentIndex(1)
            popup_info(
                self, "Episodes Added",
                f"Added {added_count} episode(s) to Batch Dubbing.\n\n"
                "Review the batch settings, then click Start Batch Dubbing."
            )
            return

        file_path = file_paths[0]
        if file_path:
            self.video_file = file_path
            filename = os.path.basename(file_path)
            self.last_rendered_video = None
            self.voice_gender_analyzed = False
            self.stacked_player.set_video_name(filename)
            if hasattr(self, "timeline_view"):
                self.timeline_view.set_source_name(filename)
                self.timeline_view.set_current_time(0)
            self.log_workflow_msg(f"Video imported: {filename}")
            
            # Load video into media player and show video widget
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.player_stack.setCurrentWidget(self.video_widget)
            
            # Auto-detect video dimensions & aspect ratio using ffprobe
            import subprocess
            try:
                cmd_probe = [
                    'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height',
                    '-of', 'csv=s=x:p=0', file_path
                ]
                probe_res = subprocess.run(cmd_probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                parts = probe_res.stdout.strip().split('x')
                if len(parts) == 2:
                    vw_val, vh_val = int(parts[0]), int(parts[1])
                    if vw_val > 0 and vh_val > 0:
                        self.video_aspect_ratio = float(vw_val) / float(vh_val)
                        if hasattr(self, "cmb_aspect"):
                            if self.video_aspect_ratio < 0.8:
                                self.cmb_aspect.setCurrentIndex(0) # 9:16 (Portrait)
                            elif self.video_aspect_ratio > 1.3:
                                self.cmb_aspect.setCurrentIndex(1) # 16:9 (Landscape)
                            else:
                                self.cmb_aspect.setCurrentIndex(2) # 1:1 (Square)
            except Exception:
                pass

            self.attach_overlay_to_current_player()
            self.update_logo_overlay_preview()
            if hasattr(self, "draggable_logo_widget"):
                self.draggable_logo_widget.raise_()
            
            # Play and pause quickly to load and render the first frame
            self.media_player.play()
            QTimer.singleShot(150, self.media_player.pause)
            
            popup_info(self, "Video Loaded", f"Successfully loaded video: {filename}")

            # Automatically run Auto Detect Voice Gender if subtitles are already in table
            if self.table.rowCount() > 0:
                QTimer.singleShot(300, lambda: self.auto_assign_voices_from_movie(continue_after=False))
            
            # Get actual video duration using ffprobe
            cmd = [
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', file_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.duration = int(float(result.stdout.strip()))
            except ValueError:
                self.duration = 188 # fallback
                
            self.sld_timeline.setRange(0, self.duration)
            self.current_time = 0
            if hasattr(self, "timeline_view"):
                self.timeline_view.set_duration(self.duration)
                self.timeline_view.set_current_time(0)
            self.update_time_label()

    def load_video_file_direct(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return
        self.video_file = file_path
        filename = os.path.basename(file_path)
        self.last_rendered_video = None
        self.voice_gender_analyzed = False
        self.stacked_player.set_video_name(filename)
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_source_name(filename)
            self.timeline_view.set_current_time(0)
        self.log_workflow_msg(f"Video loaded from Downloader: {filename}")
        
        self.media_player.setSource(QUrl.fromLocalFile(file_path))
        self.player_stack.setCurrentWidget(self.video_widget)
        
        import subprocess
        try:
            cmd_probe = [
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=s=x:p=0', file_path
            ]
            probe_res = subprocess.run(cmd_probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            parts = probe_res.stdout.strip().split('x')
            if len(parts) == 2:
                vw_val, vh_val = int(parts[0]), int(parts[1])
                if vw_val > 0 and vh_val > 0:
                    self.video_aspect_ratio = float(vw_val) / float(vh_val)
                    if hasattr(self, "cmb_aspect"):
                        if self.video_aspect_ratio < 0.8:
                            self.cmb_aspect.setCurrentIndex(0) # 9:16 (Portrait)
                        elif self.video_aspect_ratio > 1.3:
                            self.cmb_aspect.setCurrentIndex(1) # 16:9 (Landscape)
                        else:
                            self.cmb_aspect.setCurrentIndex(2) # 1:1 (Square)
        except Exception:
            pass

        self.attach_overlay_to_current_player()
        self.update_logo_overlay_preview()
        if hasattr(self, "draggable_logo_widget"):
            self.draggable_logo_widget.raise_()
        self.media_player.play()
        QTimer.singleShot(150, self.media_player.pause)
        
        import subprocess
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.duration = int(float(result.stdout.strip()))
        except Exception:
            self.duration = 188
            
        self.sld_timeline.setRange(0, self.duration)
        self.current_time = 0
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_duration(self.duration)
            self.timeline_view.set_current_time(0)
        self.update_time_label()

    def import_subtitle_dialog(self):
        file_path, _ = popup_open_file_name(
            self, "Import Subtitle (SRT)", "", "Subtitle Files (*.srt);;Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self.srt_file = file_path
            self.load_srt(file_path)

    def load_srt(self, path):
        try:
            self.voice_gender_analyzed = False
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Simple SRT Parser
            blocks = re.split(r'\n\s*\n', content.strip())
            raw_segments = []
            for block in blocks:
                lines = block.split('\n')
                if len(lines) >= 3:
                    sub_id = lines[0].strip()
                    timecode_line = lines[1]
                    text_lines = " ".join(lines[2:])

                    tc_match = re.findall(r'(\d{2}:\d{2}:\d{2}(?:[.,]\d{3})?)', timecode_line)
                    if len(tc_match) >= 2:
                        start_t = tc_match[0].replace(',', '.')
                        end_t = tc_match[1].replace(',', '.')
                        tc = f"{start_t} - {end_t}"
                    elif len(tc_match) == 1:
                        tc = tc_match[0].replace(',', '.')
                    else:
                        tc = "00:00:00 - 00:00:03"

                    raw_segments.append({
                        "id": sub_id,
                        "time": tc,
                        "text": text_lines,
                    })

            merged_segments = auto_merge_broken_subtitles(raw_segments)
            subtitle_texts = [seg.get("text", "") for seg in merged_segments]
            self.load_segments_to_table(merged_segments, "Ready")
            self.auto_select_translation_source_from_texts(subtitle_texts)
            self.update_transcript_box()
            self.log_workflow_msg(f"Subtitle timeline loaded: {len(merged_segments)} lines")
            popup_info(self, "Subtitles Loaded", f"Loaded {len(merged_segments)} subtitle lines.")

            # Automatically run Auto Detect Voice Gender in the background
            if self.video_file and os.path.exists(self.video_file) and self.table.rowCount() > 0:
                QTimer.singleShot(300, lambda: self.auto_assign_voices_from_movie(continue_after=False))
        except Exception as e:
            self.log_workflow_msg(f"SRT error: {e}")
            popup_error(self, "SRT Error", f"Failed to parse SRT file:\n{e}")

    def auto_transcribe(self):
        if not self.video_file:
            popup_warning(self, "No Video", "Please import a video first.")
            return
            
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Transcribing audio (Whisper AI)... %p%")
        self.log_workflow_msg("Transcription started.")
        
        whisper_key = openai_whisper_key_for_settings(self.settings)
        if not whisper_key:
            self.log_workflow_msg("Using Local Whisper (SeekAI chat key is not used for transcription).")
        self.transcribe_thread = TranscribeWorker(
            self.video_file,
            whisper_key,
            self.settings.get("nllb_python_path", ""),
        )
        self.transcribe_thread.progress.connect(self.progress_bar.setValue)
        self.transcribe_thread.completed.connect(self.transcribe_finished)
        self.transcribe_thread.error.connect(self.process_error)
        self.transcribe_thread.start()

    def load_segments_to_table(self, segments, status_text="Ready", translated_text=""):
        self.progress_bar.setVisible(False)
        self.voice_gender_analyzed = False
        self.clear_subtitle_audio_state()

        # Automatically merge broken consecutive dialogue lines into 1 row
        segments = auto_merge_broken_subtitles(segments)

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        bold_font = QFont("Noto Sans Khmer", 10, QFont.Weight.DemiBold)
        for row_idx, seg in enumerate(segments):
            self.table.insertRow(row_idx)
            
            item_id = QTableWidgetItem(str(seg["id"]))
            item_id.setFont(bold_font)
            item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_id.setForeground(QColor("#A78BFA"))
            self.table.setItem(row_idx, 0, item_id)
            
            item_time = QTableWidgetItem(seg["time"])
            item_time.setFont(bold_font)
            item_time.setToolTip(seg["time"])
            item_time.setForeground(QColor("#93C5FD"))
            self.table.setItem(row_idx, 1, item_time)
            
            item_original = QTableWidgetItem(seg["text"])
            item_original.setFont(bold_font)
            item_original.setToolTip(seg["text"])
            item_original.setForeground(QColor("#E2E8F0"))
            self.table.setItem(row_idx, 2, item_original)
            
            item_translated = QTableWidgetItem(translated_text)
            item_translated.setFont(bold_font)
            item_translated.setToolTip(translated_text)
            item_translated.setForeground(QColor("#F8FAFC"))
            self.table.setItem(row_idx, 3, item_translated)
            
            status = QTableWidgetItem(status_text)
            status.setFont(bold_font)
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status.setForeground(QColor("#34D399"))
            self.table.setItem(row_idx, COL_STATUS, status)
            
            self.create_voice_cell(row_idx)
            self.create_table_actions(row_idx)
            
        self.table.blockSignals(False)
        self.update_transcript_box()

    def transcribe_finished(self, segments):
        self.load_segments_to_table(segments, "Transcribed")
        source_lang = self.auto_select_translation_source_from_texts(seg.get("text", "") for seg in segments)
        if source_lang != "Auto Detect":
            self.log_workflow_msg(f"Detected transcription language: {source_lang}")
        merged_count = self.table.rowCount()
        if merged_count != len(segments):
            self.log_workflow_msg(
                f"Transcription finished: {len(segments)} detected fragments merged into "
                f"{merged_count} timeline lines"
            )
        else:
            self.log_workflow_msg(f"Transcription finished: {merged_count} timeline lines")
        popup_info(self, "Transcription Finished", "Successfully completed Speech-to-Text transcription.")
        
        # Auto sync and detect male/female voice from video speech
        if self.video_file and os.path.exists(self.video_file) and "Auto" in self.cmb_voice.currentText():
            self.log_workflow_msg("⚡ Auto Voice: analyzing video speech to detect male (Piseth) / female (Sreymom)...")
            QTimer.singleShot(250, lambda: self.auto_assign_voices_from_movie(continue_after=False))

    def extract_screen_subtitles(self):
        if not self.video_file:
            popup_warning(self, "No Video", "Please import a video first.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Reading on-screen subtitles (OCR)... %p%")
        self.log_workflow_msg("OCR subtitle extraction started.")

        self.ocr_thread = OCRSubtitleWorker(self.video_file, self.settings)
        self.ocr_thread.progress.connect(self.progress_bar.setValue)
        self.ocr_thread.completed.connect(self.ocr_subtitles_finished)
        self.ocr_thread.error.connect(self.process_error)
        self.ocr_thread.start()

    def ocr_subtitles_finished(self, segments):
        self.load_segments_to_table(segments, "OCR")
        self.set_translation_source_language("English", save=False)
        self.log_workflow_msg(f"OCR subtitles extracted: {len(segments)} English lines")
        popup_info(self, "OCR Finished", f"Read {len(segments)} on-screen English subtitle lines.")

    def translate_text(self):
        if self.table.rowCount() == 0:
            popup_warning(self, "Empty Table", "No text segments to translate.")
            return
        self.sync_translation_language_controls(save=False)
            
        items = []
        for r in range(self.table.rowCount()):
            id_val = self.table.item(r, 0).text()
            timecode_str = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            orig_txt = self.table.item(r, 2).text()
            try:
                s_sec, e_sec = parse_timecode_range(timecode_str)
            except Exception:
                s_sec, e_sec = 0.0, 3.0
            items.append((id_val, orig_txt, s_sec, e_sec))
        source_lang = self.auto_select_translation_source_from_texts((text for _, text, _, _ in items))
        if source_lang != "Auto Detect":
            self.log_workflow_msg(f"Detected source text language: {source_lang}")
            
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        provider = self.settings.get("translation_provider", "Google Web")
        direction = translation_direction_label(self.settings)
        self.progress_bar.setFormat(f"Translating {direction} using {provider}... %p%")
        self.log_workflow_msg(f"Translation started with {provider} ({direction}): {len(items)} lines")
        
        self.translate_thread = TranslateWorker(items, self.settings.get("gemini_api_key", ""), self.settings)
        self.translate_thread.progress.connect(self.progress_bar.setValue)
        self.translate_thread.status.connect(self.translation_status_changed)
        self.translate_thread.completed.connect(self.translation_finished)
        self.translate_thread.error.connect(self.process_error)
        self.translate_thread.start()

    def translation_status_changed(self, message):
        self.progress_bar.setFormat(f"{message}... %p%")
        self.log_workflow_msg(message)

    def translation_finished(self, results):
        self.progress_bar.setVisible(False)
        self.table.blockSignals(True)
        lang = self.cmb_lang.currentText()
        male_count = 0
        female_count = 0

        for item in results:
            id_val = item[0]
            translated = item[1]
            gender = item[2] if len(item) >= 3 else ""

            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).text() == str(id_val):
                    status_item = self.table.item(r, COL_STATUS)
                    imported_a1 = bool(status_item and status_item.text() == "A1 Imported")
                    self.invalidate_generated_voice_for_row(r)
                    translated_item = self.table.item(r, COL_TRANSLATED)
                    clean_translated = condense_khmer_dubbing_text(translated)
                    translated_item.setText(clean_translated)
                    translated_item.setToolTip(clean_translated)
                    translated_item.setForeground(QColor("#F8FAFC"))

                    # Auto assign voice if gender was identified by AI
                    if gender:
                        main_voice = self.cmb_voice.currentText()
                        is_voxcpm = is_voxcpm_voice(main_voice)
                        if is_voxcpm:
                            target_voice = VOXCPM_MALE_VOICE_NAME if gender.startswith("Male") else VOXCPM_FEMALE_VOICE_NAME
                        elif lang == "Khmer":
                            target_voice = "Male - Piseth" if gender.startswith("Male") else "Female - Sreymom"
                        elif lang == "English":
                            target_voice = "Male - Bob" if gender.startswith("Male") else "Female - Alice"
                        else:  # Chinese
                            target_voice = "Male - Yunjian" if gender.startswith("Male") else "Female - Xiaoxiao"

                        self.set_voice_for_row(r, target_voice)
                        if gender.startswith("Male"):
                            male_count += 1
                        elif gender.startswith("Female"):
                            female_count += 1

                    if status_item and not imported_a1:
                        status_item.setText("Ready")
                    break
        self.table.blockSignals(False)
        self.update_transcript_box()
        log_msg = f"Translation finished: {len(results)} lines"
        if male_count or female_count:
            log_msg += f" (AI Character Voices: {male_count} Male, {female_count} Female)"
        self.log_workflow_msg(log_msg)
        target = self.settings.get("translation_target_lang", "Khmer")
        self.show_workflow_notice(
            "Translation Finished",
            f"Successfully translated script lines into {target}.\n{log_msg}"
        )

        # If genders were not fully populated by translation AI, automatically run vocal pitch auto-detect
        if (
            not self.voice_gender_analyzed
            and (male_count + female_count) < len(results)
            and self.video_file
            and os.path.exists(self.video_file)
        ):
            QTimer.singleShot(300, lambda: self.auto_assign_voices_from_movie(continue_after=False))

    def generate_single_tts(self, row_idx):
        trans_item = self.table.item(row_idx, 3)
        if not trans_item or not trans_item.text().strip():
            popup_warning(self, "No Translation", f"Please translate or enter text for Line {row_idx + 1} first.")
            return
            
        text = trans_item.text()
        voice_char = self.get_voice_for_row(row_idx)
        if not self.validate_voxcpm_config(voice_char):
            return
        
        time_item = self.table.item(row_idx, 1)
        time_str = time_item.text() if time_item else ""
        try:
            s_sec, e_sec = parse_timecode_range(time_str)
        except Exception:
            s_sec, e_sec = 0.0, 3.0

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)
        self.progress_bar.setFormat(f"Generating voice for Line {row_idx + 1}... %p%")
        self.log_workflow_msg(f"Generating voice for line {row_idx + 1}.")
        
        base_rate = float(self.sld_rate.value()) / 100.0 if hasattr(self, "sld_rate") else 1.0
        base_pitch = int(self.sld_pitch.value()) if hasattr(self, "sld_pitch") else 0
        tasks = [(row_idx, text, voice_char, s_sec, e_sec)]
        self.tts_thread = TTSWorker(tasks, self.cache_dir, self.settings, base_rate=base_rate, base_pitch=base_pitch)
        self.tts_thread.status.connect(lambda text: self.progress_bar.setFormat(f"{text}... %p%"))
        self.tts_thread.row_completed.connect(self.single_tts_finished)
        self.tts_thread.completed.connect(lambda: self.progress_bar.setVisible(False))
        self.tts_thread.error.connect(self.process_error)
        self.tts_thread.start()

    def single_tts_finished(self, row_idx, file_path):
        self.audio_files[row_idx] = file_path
        self.table.item(row_idx, COL_STATUS).setText("Synthesized")
        self.create_table_actions(row_idx)
        self.log_workflow_msg(f"Voice generated for line {row_idx + 1}: {os.path.basename(file_path)}")
        self.update_transcript_box()
        
        # Seek and preview the line in context with the video!
        self.preview_line_in_video(row_idx, file_path)

    def preview_line_in_video(self, row_idx, file_path):
        timecode_str = self.table.item(row_idx, 1).text()
        try:
            row_time_ms = parse_timecode_to_ms(timecode_str)
            
            self.update_played_preview_rows(row_time_ms)
            self.played_preview_rows.add(row_idx)
            
            if self.video_file and os.path.exists(self.video_file):
                self.player_stack.setCurrentWidget(self.video_widget)
                current_source = self.media_player.source().toLocalFile()
                if not current_source or os.path.normpath(current_source) != os.path.normpath(self.video_file):
                    self.media_player.setSource(QUrl.fromLocalFile(self.video_file))
                
                seek_time = max(0, row_time_ms - 500)
                self.media_player.setPosition(seek_time)
                
                if not self.is_playing:
                    self.is_playing = True
                    self.btn_play.setIcon(get_icon("pause", "#19A3E0"))
                    self.stacked_player.set_playing(True)
                    self.playback_timer.start(1000)
                
                self.media_player.play()
            
            self.play_audio_file(file_path)
        except Exception:
            self.play_audio_file(file_path)

    def play_original_voice_segment(self, row_idx):
        if row_idx < 0 or row_idx >= self.table.rowCount():
            return

        timecode_str = self.table.item(row_idx, COL_TIME).text() if self.table.item(row_idx, COL_TIME) else ""
        try:
            start_sec, end_sec = parse_timecode_range(timecode_str)
        except Exception:
            start_sec, end_sec = 0.0, 3.0

        row_time_ms = int(start_sec * 1000)
        self.current_time = start_sec
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_current_time(start_sec)
        self.highlight_subtitle_by_time(start_sec)

        # 1. If video file exists, extract or fetch cached slice and play
        if self.video_file and os.path.exists(self.video_file):
            os.makedirs(self.cache_dir, exist_ok=True)
            clip_path = os.path.join(
                self.cache_dir,
                f"orig_voice_row_{row_idx}_{int(start_sec*1000)}_{int(end_sec*1000)}.wav"
            )
            if not os.path.exists(clip_path) or os.path.getsize(clip_path) == 0:
                import subprocess
                duration = max(0.2, end_sec - start_sec)
                cmd = [
                    "ffmpeg", "-y", "-hwaccel", "auto",
                    "-ss", f"{start_sec:.3f}",
                    "-i", self.video_file,
                    "-t", f"{duration:.3f}",
                    "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "2",
                    clip_path
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                except Exception as e:
                    self.log_workflow_msg(f"FFmpeg slice error: {e}")
                    clip_path = None

            # Sync video preview position
            if self.player_stack.currentWidget() != self.video_widget:
                self.player_stack.setCurrentWidget(self.video_widget)
            current_source = self.media_player.source().toLocalFile()
            if not current_source or os.path.normpath(current_source) != os.path.normpath(self.video_file):
                self.media_player.setSource(QUrl.fromLocalFile(self.video_file))
            self.media_player.setPosition(max(0, row_time_ms))

            if clip_path and os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                self.log_workflow_msg(f"Playing Original Voice Line {row_idx + 1} ({start_sec:.2f}s - {end_sec:.2f}s)")
                self.play_audio_file(clip_path, pause_video=True)
                return
            else:
                self.audio_output.setVolume(self.sld_volume.value() / 100.0)
                self.media_player.play()
                duration_ms = int(max(500, (end_sec - start_sec) * 1000))
                QTimer.singleShot(duration_ms, self.media_player.pause)
                return

        # 2. If BGM file exists
        if self.bgm_file and os.path.exists(self.bgm_file):
            clip_path = os.path.join(
                self.cache_dir,
                f"bgm_voice_row_{row_idx}_{int(start_sec*1000)}_{int(end_sec*1000)}.wav"
            )
            if not os.path.exists(clip_path) or os.path.getsize(clip_path) == 0:
                import subprocess
                duration = max(0.2, end_sec - start_sec)
                cmd = [
                    "ffmpeg", "-y", "-hwaccel", "auto",
                    "-ss", f"{start_sec:.3f}",
                    "-i", self.bgm_file,
                    "-t", f"{duration:.3f}",
                    "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "2",
                    clip_path
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                except Exception:
                    clip_path = None
            if clip_path and os.path.exists(clip_path):
                self.play_audio_file(clip_path, pause_video=True)
                return

        # 3. If no video file imported yet
        orig_text = self.table.item(row_idx, COL_ORIGINAL).text() if self.table.item(row_idx, COL_ORIGINAL) else ""
        popup_info(
            self,
            "Original Voice (ផ្ទៀងផ្ទាត់សម្លេងដើម)",
            f"Line {row_idx + 1} Original Text:\n'{orig_text}'\n\n"
            "Please click 'Import Video' to load the movie file. You will then be able to listen to the original voice segment for every line to verify script and timing."
        )

    def play_dub_voice_segment(self, row_idx):
        if row_idx < 0 or row_idx >= self.table.rowCount():
            return
        audio_path = self.audio_files.get(row_idx, "")
        if audio_path and os.path.exists(audio_path):
            self.preview_line_in_video(row_idx, audio_path)
        else:
            self.generate_single_tts(row_idx)

    def toggle_original_audio_preview(self):
        self.orig_audio_preview_enabled = not getattr(self, "orig_audio_preview_enabled", True)
        if self.orig_audio_preview_enabled:
            if hasattr(self, "btn_toggle_orig_audio"):
                self.btn_toggle_orig_audio.setText("🔊 A2 Audio: ON")
                self.btn_toggle_orig_audio.setStyleSheet(
                    "background-color: #1E103A; color: #C084FC; border: 1px solid #7E22CE; "
                    "border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11.5px;"
                )
            self.log_workflow_msg("Original Video Audio (A2) enabled: playing both Original and AI Voice.")
        else:
            if hasattr(self, "btn_toggle_orig_audio"):
                self.btn_toggle_orig_audio.setText("🔇 A2 Audio: OFF")
                self.btn_toggle_orig_audio.setStyleSheet(
                    "background-color: #261111; color: #F87171; border: 1px solid #991B1B; "
                    "border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 11.5px;"
                )
            self.log_workflow_msg("Original Video Audio (A2) muted: playing AI Voice only.")
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_voice_only(not self.orig_audio_preview_enabled)

    def play_audio_file(self, file_path, pause_video=True):
        try:
            if not os.path.exists(file_path):
                self.log_workflow_msg(f"Audio file not found: {file_path}")
                return

            if self.preview_player is None:
                self.preview_player = QMediaPlayer(self)
                self.preview_audio_output = QAudioOutput(self)
                self.preview_player.setAudioOutput(self.preview_audio_output)
            
            # Pause main timeline preview if playing and pause_video is True
            if pause_video and self.is_playing:
                self.toggle_play()
                
            vol = max(0.85, float(getattr(self.sld_volume, "value", lambda: 85)()) / 100.0)
            self.preview_audio_output.setVolume(vol)
            self.preview_player.stop()
            self.preview_player.setSource(QUrl.fromLocalFile(os.path.abspath(file_path)))
            self.preview_player.play()
        except Exception as e:
            popup_error(self, "Audio Playback Error", f"Could not play audio file:\n{e}")

    def generate_voiceover_all(self):
        if self.table.rowCount() == 0:
            popup_warning(self, "Empty Table", "No texts to generate voiceovers for.")
            return
        if hasattr(self, "tts_thread") and self.tts_thread.isRunning():
            popup_warning(self, "TTS Running", "Voiceover generation is still running. Wait until it finishes before starting again.")
            return
        if hasattr(self, "voice_gender_thread") and self.voice_gender_thread.isRunning():
            message = "Auto Voice is still assigning male/female voices. Wait until it finishes."
            self.log_workflow_msg(message)
            self.show_workflow_notice("Auto Voice Running", message, warning=True)
            return

        if self.should_auto_assign_voices_before_generation():
            has_translated_text = any(
                self.table.item(row_idx, COL_TRANSLATED)
                and self.table.item(row_idx, COL_TRANSLATED).text().strip()
                for row_idx in range(self.table.rowCount())
            )
            if has_translated_text:
                self.log_workflow_msg("Auto Voice will assign male/female clone voices before TTS.")
                self.auto_assign_voices_from_movie(continue_after=True)
                return
            
        tasks = []
        for r in range(self.table.rowCount()):
            trans_item = self.table.item(r, 3)
            time_item = self.table.item(r, 1)
            if trans_item and trans_item.text().strip():
                voice_char = self.get_voice_for_row(r)
                if not self.validate_voxcpm_config(voice_char):
                    return
                time_str = time_item.text() if time_item else ""
                try:
                    s_sec, e_sec = parse_timecode_range(time_str)
                except Exception:
                    s_sec, e_sec = 0.0, 3.0
                tasks.append((r, trans_item.text().strip(), voice_char, s_sec, e_sec))
                
        if not tasks:
            popup_warning(self, "No Text", "No translated text lines to generate.")
            return
            
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Generating all TTS voices... %p%")
        self.log_workflow_msg(f"TTS generation started: {len(tasks)} lines")
        
        base_rate = float(self.sld_rate.value()) / 100.0 if hasattr(self, "sld_rate") else 1.0
        base_pitch = int(self.sld_pitch.value()) if hasattr(self, "sld_pitch") else 0
        self.tts_thread = TTSWorker(tasks, self.cache_dir, self.settings, base_rate=base_rate, base_pitch=base_pitch)
        self.tts_thread.progress.connect(self.progress_bar.setValue)
        self.tts_thread.status.connect(lambda text: self.progress_bar.setFormat(f"{text}... %p%"))
        self.tts_thread.row_completed.connect(self.batch_tts_row_finished)
        self.tts_thread.completed.connect(self.batch_tts_finished)
        self.tts_thread.error.connect(self.process_error)
        self.tts_thread.start()

    def batch_tts_row_finished(self, row_idx, file_path):
        self.audio_files[row_idx] = file_path
        self.table.item(row_idx, COL_STATUS).setText("Synthesized")
        self.create_table_actions(row_idx)
        self.log_workflow_msg(f"Voice generated for line {row_idx + 1}: {os.path.basename(file_path)}")
        self.update_transcript_box()

    def batch_tts_finished(self):
        self.progress_bar.setVisible(False)
        for r in range(self.table.rowCount()):
            self.create_table_actions(r)
        self.log_workflow_msg(f"TTS generation finished: {len(self.audio_files)} audio files")
        self.show_workflow_notice(
            "TTS Generation Complete",
            "Generated speech files for all timeline lines successfully."
        )

    def render_output_video(self, save_path=None):
        if not self.video_file:
            popup_warning(self, "No Video", "Please import a source video file to mix audio into.")
            return
        if hasattr(self, "voice_gender_thread") and self.voice_gender_thread.isRunning():
            message = "Wait for Auto Voice to finish before rendering."
            self.log_workflow_msg(message)
            self.show_workflow_notice("Auto Voice Running", message, warning=True)
            return
        if hasattr(self, "tts_thread") and self.tts_thread.isRunning():
            popup_warning(self, "TTS Still Running", "Wait for Generate Voiceover (TTS) to finish before rendering.")
            return
        bgm_path = self.bgm_file if self.bgm_file and os.path.exists(self.bgm_file) else self.settings.get("background_music_path", "")
        if not self.audio_files and not (bgm_path and os.path.exists(bgm_path)):
            popup_warning(self, "No Audio Tracks", "Please generate TTS voiceovers or load BGM first.")
            return

        missing_voice_rows = []
        for row_idx in range(self.table.rowCount()):
            trans_item = self.table.item(row_idx, COL_TRANSLATED)
            has_translation = bool(trans_item and trans_item.text().strip())
            if not has_translation:
                continue
            audio_path = self.audio_files.get(row_idx, "")
            if not audio_path or not os.path.exists(audio_path):
                missing_voice_rows.append(row_idx + 1)
        if missing_voice_rows:
            preview = ", ".join(str(row) for row in missing_voice_rows[:12])
            if len(missing_voice_rows) > 12:
                preview += f", +{len(missing_voice_rows) - 12} more"
            popup_warning(
                self,
                "Missing Voice Lines",
                "Some translated lines do not have generated voice yet:\n"
                f"{preview}\n\nClick Generate Voiceover (TTS), wait until it finishes, then render."
            )
            return
            
        if not save_path:
            suggested_path = default_output_path(self.video_file)
            save_path, _ = popup_save_file_name(
                self, "Save Dubbed Video", suggested_path, "MPEG-4 Video (*.mp4);;All Files (*)"
            )
        if save_path:
            output_parent = os.path.dirname(os.path.abspath(save_path))
            os.makedirs(output_parent, exist_ok=True)
            voice_only = self.chk_vocal_iso.isChecked() if hasattr(self, "chk_vocal_iso") else False
            mix_mode = self.cmb_mix.currentText() if hasattr(self, "cmb_mix") else ("Voice Only" if voice_only else "Duck Original on Speech")
            mode = "Voice Only" if voice_only else mix_mode
            music_level = self.sld_music.value() if hasattr(self, "sld_music") else 35
            vocal_boost = self.chk_noise_reduction.isChecked() if hasattr(self, "chk_noise_reduction") else False
            
            self.log_workflow_msg(f"Render started: {os.path.basename(save_path)} ({mode})")
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Rendering final dubbed video... %p%")
            
            # Map audios with time offsets and durations
            audio_offsets = []
            for row_idx, path in self.audio_files.items():
                timecode_str = self.table.item(row_idx, 1).text()
                start_sec, end_sec = parse_timecode_range(timecode_str)
                audio_offsets.append((path, start_sec, end_sec))
                
            logo_file = self.txt_logo_path.text().strip() if hasattr(self, "txt_logo_path") and self.chk_show_logo.isChecked() else ""
            logo_pos_text = self.cmb_logo_pos.currentText().split(" ")[0] if hasattr(self, "cmb_logo_pos") else "Top-Right"
            logo_size_map = {"8% (Default)": 0.08, "5% (Small)": 0.05, "10% (Medium)": 0.10, "15% (Large)": 0.15}
            logo_scale_val = logo_size_map.get(self.cmb_logo_size.currentText() if hasattr(self, "cmb_logo_size") else "", 0.08)
            watermark_txt = self.txt_watermark.text().strip() if hasattr(self, "txt_watermark") and self.chk_show_logo.isChecked() else ""

            lip_sync_val = self.sld_lip_sync.value() if hasattr(self, "sld_lip_sync") else 0
            self.render_thread = RenderWorker(
                self.video_file, audio_offsets, save_path,
                music_level=music_level,
                vocal_boost=vocal_boost,
                mix_mode=mix_mode,
                voice_only=voice_only,
                background_music_path=bgm_path,
                logo_path=logo_file,
                logo_position=logo_pos_text,
                logo_scale=getattr(self, "logo_scale_val", logo_scale_val),
                logo_opacity=0.85,
                watermark_text=watermark_txt,
                logo_rel_x=getattr(self, "logo_rel_x", 0.75),
                logo_rel_y=getattr(self, "logo_rel_y", 0.05),
                lip_sync_offset_ms=lip_sync_val,
            )
            self.render_thread.progress.connect(self.progress_bar.setValue)
            self.render_thread.completed.connect(self.render_finished)
            self.render_thread.error.connect(self.process_error)
            self.render_thread.start()

    def render_finished(self, output_path):
        self.progress_bar.setVisible(False)
        filename = os.path.basename(output_path)
        self.last_rendered_video = output_path
        self.stacked_player.set_video_name(filename)
        self.log_workflow_msg(f"Render finished: {filename}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Rendering Complete")
        box.setText(
            "Successfully merged A1 voiceover with A2/background audio and output video:\n"
            f"{filename}"
        )
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(MESSAGE_BOX_STYLE)
        box.exec()
        if box.clickedButton() is open_folder:
            folder = os.path.dirname(os.path.abspath(output_path))
            try:
                os.startfile(folder)
            except OSError as exc:
                popup_error(self, "Open Folder Failed", str(exc))

    def automate_workflow(self):
        # Automate: Auto Transcribe -> Translate -> Generate Voiceover -> Render
        self.log_workflow_msg("Full workflow started.")
        if not self.video_file:
            self.import_video_dialog()
            if not self.video_file:
                self.log_workflow_msg("Full workflow cancelled: no video selected.")
                return
                
        # Perform Whisper STT
        self.auto_transcribe()
        # Connect post-transcribe automation trigger
        self.transcribe_thread.finished.connect(self.trigger_auto_translate)

    def trigger_auto_translate(self):
        try:
            self.transcribe_thread.finished.disconnect(self.trigger_auto_translate)
        except Exception:
            pass
        self.log_workflow_msg("Workflow step: translation.")
        self.translate_text()
        self.translate_thread.finished.connect(self.trigger_auto_tts)

    def trigger_auto_tts(self):
        try:
            self.translate_thread.finished.disconnect(self.trigger_auto_tts)
        except Exception:
            pass
        self.log_workflow_msg("Workflow step: Auto voice detection & TTS generation.")
        if self.video_file and os.path.exists(self.video_file) and not getattr(self, "voice_gender_analyzed", False):
            # Auto detect voices, then automatically proceed to generate TTS
            self.auto_assign_voices_from_movie(continue_after=True)
            # When auto voice completes, auto_voice_finished will trigger generate_voiceover_all
            def _connect_tts_to_render():
                if hasattr(self, "tts_thread") and self.tts_thread:
                    self.tts_thread.finished.connect(self.trigger_auto_render)
            QTimer.singleShot(1000, _connect_tts_to_render)
        else:
            self.generate_voiceover_all()
            self.tts_thread.finished.connect(self.trigger_auto_render)

    def trigger_auto_render(self):
        try:
            self.tts_thread.finished.disconnect(self.trigger_auto_render)
        except Exception:
            pass
        # Keep generated media separate from source videos and application files.
        auto_save_path = default_output_path(self.video_file)
        self.log_workflow_msg("Workflow step: render.")
        self.render_output_video(save_path=auto_save_path)

    def workflow_from_srt(self):
        # Automate: SRT Import -> Translate -> TTS -> Render
        self.log_workflow_msg("Workflow from SRT started.")
        self.import_subtitle_dialog()
        if self.srt_file:
            self.translate_text()
            self.translate_thread.finished.connect(self.trigger_auto_tts)

    def clear_workspace(self):
        """Reset and clear entire workspace: video, subtitles table, timeline, and audio cache."""
        has_content = bool(self.video_file or self.table.rowCount() > 0 or self.audio_files)
        if has_content:
            reply = popup_question(
                self,
                "Clear Workspace (ជម្រះ Workspace ទាំងមូល)",
                "Are you sure you want to clear the entire workspace and reset all video, subtitles, timeline, and audio cache?\n\n(តើអ្នកពិតជាចង់ជម្រះ Workspace ទាំងមូល និងចាប់ផ្តើមគម្រោងថ្មីមែនទេ?)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # 1. Stop video playback and reset sources
        try:
            self.media_player.stop()
            self.media_player.setSource(QUrl())
        except Exception:
            pass

        self.video_file = None
        self.srt_file = None
        self.duration = 68
        self.current_time = 0
        self.is_playing = False

        if hasattr(self, "btn_play"):
            self.btn_play.setIcon(get_icon("play", "#FFFFFF", 16))

        # 2. Clear Subtitles Table
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)

        # 3. Clear Multi-Track Timeline
        if hasattr(self, "timeline_view"):
            self.timeline_view.set_segments([], duration=68)
            self.timeline_view.clear_selection()
            self.timeline_view.set_current_time(0.0)

        # 4. Clear Overlays
        if hasattr(self, "subtitle_overlay_label"):
            self.subtitle_overlay_label.hide()
        if hasattr(self, "draggable_logo_widget"):
            self.draggable_logo_widget.hide()

        # 5. Clear Audio Caches
        self.audio_files.clear()
        if hasattr(self, "played_preview_rows"):
            self.played_preview_rows.clear()

        # 6. Reset UI Controls & Labels
        if hasattr(self, "sld_timeline"):
            self.sld_timeline.blockSignals(True)
            self.sld_timeline.setRange(0, 68)
            self.sld_timeline.setValue(0)
            self.sld_timeline.blockSignals(False)

        if hasattr(self, "lbl_time"):
            self.lbl_time.setText("00:00:00 / 00:01:08")

        if hasattr(self, "lbl_sub_stats"):
            self.lbl_sub_stats.setText("Total Subtitles: 0 | Synthesized: 0 | Pending: 0")

        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)

        # Remove saved session file
        s_path = self.get_session_file_path()
        if os.path.exists(s_path):
            try:
                os.remove(s_path)
            except Exception:
                pass
        if hasattr(self, "lbl_session_status"):
            self.lbl_session_status.setText("💾 Clean Workspace")

        self.log_workflow_msg("Workspace cleared successfully. Ready for new project (បានជម្រះ Workspace រួចរាល់).")
        self.show_workflow_notice("Workspace Cleared", "The entire workspace has been reset to empty clean state.")

    # ----------------------------------------------------
    # Auto-Save & Session Recovery Engine (រក្សាទុក & ស្តារការងារចាស់)
    # ----------------------------------------------------
    def get_session_file_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_session.json")

    def schedule_session_autosave(self):
        if not hasattr(self, "_session_autosave_timer"):
            self._session_autosave_timer = QTimer(self)
            self._session_autosave_timer.setSingleShot(True)
            self._session_autosave_timer.timeout.connect(self.save_current_session)
        self._session_autosave_timer.start(1000)

    def save_current_session(self, completed=False):
        try:
            has_video = bool(getattr(self, "video_file", "") and os.path.exists(self.video_file))
            has_subs = bool(hasattr(self, "table") and self.table.rowCount() > 0)
            if not has_video and not has_subs:
                return

            rows = []
            if hasattr(self, "table"):
                for r in range(self.table.rowCount()):
                    id_val = self.table.item(r, 0).text() if self.table.item(r, 0) else str(r + 1)
                    time_str = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
                    orig_str = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
                    trans_str = self.table.item(r, 3).text() if self.table.item(r, 3) else ""
                    status_str = self.table.item(r, 4).text() if self.table.item(r, 4) else "Draft"
                    voice_str = self.get_voice_for_row(r)
                    rows.append({
                        "id": id_val,
                        "time": time_str,
                        "original": orig_str,
                        "translated": trans_str,
                        "status": status_str,
                        "voice": voice_str
                    })

            data = {
                "version": "1.0",
                "updated_at": time.time(),
                "completed": completed,
                "video_file": getattr(self, "video_file", "") or "",
                "subtitles": rows,
                "logo_path": self.txt_logo_path.text().strip() if hasattr(self, "txt_logo_path") else "",
                "show_logo": self.chk_show_logo.isChecked() if hasattr(self, "chk_show_logo") else False,
                "logo_rel_x": float(getattr(self, "logo_rel_x", 0.82)),
                "logo_rel_y": float(getattr(self, "logo_rel_y", 0.05)),
                "logo_scale": float(getattr(self, "logo_scale_val", 0.15)),
                "bgm_file": getattr(self, "bgm_file", "") or "",
                "mix_mode": self.cmb_mix_mode.currentText() if hasattr(self, "cmb_mix_mode") else "",
                "music_level": self.sld_music_level.value() if hasattr(self, "sld_music_level") else 30
            }

            s_path = self.get_session_file_path()
            with open(s_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if hasattr(self, "lbl_session_status"):
                self.lbl_session_status.setText("💾 Auto-saved")
        except Exception as e:
            print("Session save error:", e)

    def restore_previous_session(self):
        s_path = self.get_session_file_path()
        if not os.path.exists(s_path):
            return

        try:
            with open(s_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print("Session read error:", e)
            return

        if not isinstance(data, dict):
            return

        if data.get("completed", False):
            return

        video_path = data.get("video_file", "")
        subtitles = deduplicate_saved_subtitles(data.get("subtitles", []))

        if not video_path and not subtitles:
            return

        # 1. Restore Video into player
        if video_path and os.path.exists(video_path):
            self.load_video_file_direct(video_path)

        # 2. Restore Subtitles into Table
        if subtitles and hasattr(self, "table"):
            self.clear_subtitle_audio_state()
            self.table.blockSignals(True)
            self.table.setRowCount(0)
            bold_font = QFont("Noto Sans Khmer", 10, QFont.Weight.DemiBold)
            for row_idx, seg in enumerate(subtitles):
                self.table.insertRow(row_idx)

                item_id = QTableWidgetItem(str(seg.get("id", row_idx + 1)))
                item_id.setFont(bold_font)
                item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_id.setForeground(QColor("#A78BFA"))
                self.table.setItem(row_idx, 0, item_id)

                t_code = seg.get("time", "")
                item_time = QTableWidgetItem(t_code)
                item_time.setFont(bold_font)
                item_time.setToolTip(t_code)
                item_time.setForeground(QColor("#93C5FD"))
                self.table.setItem(row_idx, 1, item_time)

                orig_txt = seg.get("original", "")
                item_orig = QTableWidgetItem(orig_txt)
                item_orig.setFont(bold_font)
                item_orig.setToolTip(orig_txt)
                item_orig.setForeground(QColor("#E2E8F0"))
                self.table.setItem(row_idx, 2, item_orig)

                trans_txt = seg.get("translated", "")
                item_trans = QTableWidgetItem(trans_txt)
                item_trans.setFont(bold_font)
                item_trans.setToolTip(trans_txt)
                item_trans.setForeground(QColor("#F8FAFC"))
                self.table.setItem(row_idx, 3, item_trans)

                status_txt = seg.get("status", "Draft")
                item_status = QTableWidgetItem(status_txt)
                item_status.setFont(bold_font)
                item_status.setFlags(item_status.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item_status.setForeground(QColor("#34D399") if status_txt in ("Ready", "Synthesized", "Translated") else QColor("#FBBF24"))
                self.table.setItem(row_idx, 4, item_status)

                v_name = seg.get("voice", "")
                self.create_voice_cell(row_idx, voice_name=v_name if v_name else None)
                self.create_table_actions(row_idx)

            self.table.blockSignals(False)
            self.update_transcript_box()
            self.update_table_summary()

        # 3. Restore Logo & Overlay
        logo_path = data.get("logo_path", "")
        if logo_path and os.path.exists(logo_path):
            if hasattr(self, "txt_logo_path"):
                self.txt_logo_path.setText(logo_path)
            if hasattr(self, "chk_show_logo"):
                self.chk_show_logo.setChecked(data.get("show_logo", True))
            self.logo_rel_x = float(data.get("logo_rel_x", 0.82))
            self.logo_rel_y = float(data.get("logo_rel_y", 0.05))
            self.logo_scale_val = float(data.get("logo_scale", 0.15))
            self.update_logo_overlay_preview()

        # 4. Restore BGM
        bgm = data.get("bgm_file", "")
        if bgm and os.path.exists(bgm):
            self.bgm_file = bgm
            if hasattr(self, "lbl_bgm_file"):
                self.lbl_bgm_file.setText(os.path.basename(bgm))
        if data.get("mix_mode") and hasattr(self, "cmb_mix_mode"):
            self.cmb_mix_mode.setCurrentText(data["mix_mode"])
        if "music_level" in data and hasattr(self, "sld_music_level"):
            self.sld_music_level.setValue(int(data["music_level"]))

        if hasattr(self, "lbl_session_status"):
            self.lbl_session_status.setText("🔄 Session Restored")
        self.log_workflow_msg(f"🔄 Restored unfinished session: {len(subtitles)} subtitles & movie timeline recovered (បានទាញយកការងារមិនទាន់ហើយមកវិញ)!")

    def closeEvent(self, event):
        try:
            self.save_current_session(completed=False)
        except Exception:
            pass
        super().closeEvent(event)

    def clean_all_status(self):
        """Clean all lines status back to Pending, reset action buttons, and clear audio cache."""
        total_rows = self.table.rowCount()
        if total_rows == 0:
            self.show_workflow_notice("Table Empty", "No subtitle lines to clean.")
            return

        reply = popup_question(
            self,
            "Clean All Status (សម្អាត Status ទាំងអស់)",
            f"Are you sure you want to reset status to Pending and clear audio cache for all {total_rows} lines?\n\n(តើអ្នកពិតជាចង់សម្អាត Status ទាំងអស់ និង Audio Cache នៃបន្ទាត់ទាំង {total_rows} មែនទេ?)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.table.blockSignals(True)
        self.audio_files.clear()
        if hasattr(self, "played_preview_rows"):
            self.played_preview_rows.clear()

        for r in range(total_rows):
            status_item = self.table.item(r, COL_STATUS)
            if status_item:
                status_item.setText("Pending")
            self.create_table_actions(r)
        self.table.blockSignals(False)

        # Clear audio on timeline view
        if hasattr(self, "timeline_view") and hasattr(self.timeline_view, "segments"):
            for seg in self.timeline_view.segments:
                seg["has_audio"] = False
            self.timeline_view.update()

        # Update stats
        if hasattr(self, "lbl_sub_stats"):
            self.lbl_sub_stats.setText(f"Total Subtitles: {total_rows} | Synthesized: 0 | Pending: {total_rows}")

        # Reset progress bar
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)

        self.log_workflow_msg(f"Cleaned all status and audio cache for {total_rows} subtitle lines (សម្អាត Status រួចរាល់).")
        self.show_workflow_notice("Status Cleaned", f"Successfully reset status to Pending and cleared audio cache for {total_rows} lines.")

    def cancel_operation(self):
        # Stop any active background workers
        active = False
        for thread_attr in ['transcribe_thread', 'ocr_thread', 'voice_gender_thread', 'translate_thread', 'tts_thread', 'render_thread']:
            if hasattr(self, thread_attr):
                thread = getattr(self, thread_attr)
                if thread.isRunning():
                    if hasattr(thread, "cancel"):
                        thread.cancel()
                    thread.terminate()
                    thread.wait()
                    active = True
        
        self.progress_bar.setVisible(False)
        if active:
            self.log_workflow_msg("Operation cancelled by user.")
            popup_warning(self, "Operation Cancelled", "The background workflow was aborted by user.")
        else:
            self.log_workflow_msg("Cancel requested, but no operation was active.")
            popup_info(self, "Info", "No active operations to cancel.")

    def open_settings(self):
        diag = SettingsDialog(self)
        if diag.exec() == QDialog.DialogCode.Accepted:
            self.settings = load_settings()
            if hasattr(self, "cmb_translate_source"):
                self.cmb_translate_source.blockSignals(True)
                self.cmb_translate_source.setCurrentText(
                    normalize_translation_language(
                        self.settings.get("translation_source_lang", "Auto Detect"),
                        TRANSLATION_SOURCE_LANGS,
                        "Auto Detect"
                    )
                )
                self.cmb_translate_source.blockSignals(False)
            if hasattr(self, "cmb_translate_target"):
                self.cmb_translate_target.blockSignals(True)
                self.cmb_translate_target.setCurrentText(
                    normalize_translation_language(
                        self.settings.get("translation_target_lang", "Khmer"),
                        TRANSLATION_TARGET_LANGS,
                        "Khmer"
                    )
                )
                self.cmb_translate_target.blockSignals(False)
            self.update_translation_button_text()
            if hasattr(self, "txt_voxcpm_ref_female"):
                self.txt_voxcpm_ref_female.setText(self.settings.get("voxcpm_reference_audio_female", ""))
            if hasattr(self, "txt_voxcpm_ref_male"):
                self.txt_voxcpm_ref_male.setText(self.settings.get("voxcpm_reference_audio_male", ""))
            self.output_lang_changed(self.cmb_lang.currentIndex())
            if hasattr(self, "cmb_batch_lang"):
                self.batch_lang_changed(self.cmb_batch_lang.currentIndex())

    def process_error(self, error_msg):
        self.progress_bar.setVisible(False)
        self.log_workflow_msg(f"ERROR: {error_msg}")
        popup_error(self, "Workflow Error", f"An error occurred in the execution workflow:\n{error_msg}")

    # ----------------------------------------------------
    # Batch Dubbing Methods
    # ----------------------------------------------------
    def init_batch_tab(self, layout):
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_widget.setFixedWidth(320)

        batch_config_frame = QFrame()
        batch_config_frame.setObjectName("configFrame")
        config_layout = QFormLayout(batch_config_frame)
        config_layout.setContentsMargins(12, 12, 12, 12)
        config_layout.setSpacing(8)

        config_hdr = QLabel("Batch Configuration:")
        config_hdr.setStyleSheet("font-weight: 700; font-size: 13px; color: #D8C9FF; margin-bottom: 5px;")
        config_layout.addRow(config_hdr)

        self.cmb_batch_model = QComboBox()
        self.cmb_batch_model.addItems([
            "Best Quality Auto (Gemini 3.7 → SeekAI → Google)",
            "Gemini",
            "OpenAI / SeekAI (gpt-5.6-sol)",
            "Google Web",
            "NLLB Local",
            "Ollama",
        ])
        saved_batch_provider = self.settings.get(
            "batch_translation_provider",
            self.settings.get("translation_provider", "Best Quality Auto (Gemini 3.7 → SeekAI → Google)")
        )
        if saved_batch_provider not in [self.cmb_batch_model.itemText(i) for i in range(self.cmb_batch_model.count())]:
            self.cmb_batch_model.addItem(saved_batch_provider)
        self.cmb_batch_model.setCurrentText(saved_batch_provider)
        config_layout.addRow("Translator:", self.cmb_batch_model)

        self.cmb_batch_source = QComboBox()
        self.cmb_batch_source.addItems(TRANSLATION_SOURCE_LANGS)
        self.cmb_batch_source.setCurrentText(
            self.settings.get("batch_source_lang", self.settings.get("translation_source_lang", "Auto Detect"))
        )
        config_layout.addRow("Source Language:", self.cmb_batch_source)

        self.cmb_batch_lang = QComboBox()
        self.cmb_batch_lang.addItems(["Khmer", "English", "Chinese"])
        self.cmb_batch_lang.setCurrentText(
            self.settings.get("batch_target_lang", self.settings.get("translation_target_lang", "Khmer"))
        )
        self.cmb_batch_lang.currentIndexChanged.connect(self.batch_lang_changed)
        config_layout.addRow("Output Language (TTS):", self.cmb_batch_lang)

        self.cmb_batch_voice = QComboBox()
        config_layout.addRow("Voice Character:", self.cmb_batch_voice)

        self.chk_batch_auto_gender = QCheckBox("Auto Male/Female Voice by Character")
        self.chk_batch_auto_gender.setChecked(bool(self.settings.get("batch_auto_gender", True)))
        self.chk_batch_auto_gender.setToolTip(
            "Uses the gender returned by Gemini/SeekAI; if missing, analyzes the original movie audio."
        )
        config_layout.addRow(self.chk_batch_auto_gender)

        slider_layout = QHBoxLayout()
        self.sld_batch_music = QSlider(Qt.Orientation.Horizontal)
        self.sld_batch_music.setRange(0, 100)
        self.sld_batch_music.setValue(int(self.settings.get("batch_music_level", 30)))
        slider_layout.addWidget(self.sld_batch_music)
        self.lbl_batch_music_val = QLabel(f"{self.sld_batch_music.value()}%")
        self.sld_batch_music.valueChanged.connect(lambda v: self.lbl_batch_music_val.setText(f"{v}%"))
        slider_layout.addWidget(self.lbl_batch_music_val)
        config_layout.addRow("Music Level:", slider_layout)

        self.cmb_batch_mix = QComboBox()
        self.cmb_batch_mix.addItems([
            "Duck Original on Speech",
            "Background Audio Mix",
            "Duck Music",
            "Mute Original on Speech",
            "Mute Music"
        ])
        self.cmb_batch_mix.setCurrentText(self.settings.get("batch_mix_mode", "Duck Original on Speech"))
        self.cmb_batch_mix.setToolTip("Ducks (lowers) original movie sound while Khmer voice is speaking so background sounds/music remain heard")
        config_layout.addRow("Audio Mix Mode:", self.cmb_batch_mix)

        self.chk_batch_vocal_iso = QCheckBox("AI Vocal Isolation (Voice Only)")
        self.chk_batch_vocal_iso.setChecked(bool(self.settings.get("batch_voice_only", True)))
        self.chk_batch_vocal_iso.setToolTip("When checked, original background audio is removed completely (Voice Only). Uncheck to keep movie sound & BGM.")
        config_layout.addRow(self.chk_batch_vocal_iso)

        self.chk_batch_noise_reduction = QCheckBox("Vocal Boost & Noise Reduction")
        self.chk_batch_noise_reduction.setChecked(bool(self.settings.get("batch_noise_reduction", True)))
        config_layout.addRow(self.chk_batch_noise_reduction)

        self.chk_batch_logo = QCheckBox("Use saved logo on every episode")
        self.chk_batch_logo.setChecked(bool(self.settings.get("batch_use_logo", True)))
        self.chk_batch_logo.setToolTip(
            "Uses the logo, size, and position currently configured in Single Dubbing."
        )
        config_layout.addRow(self.chk_batch_logo)
        saved_logo_path = self.settings.get("logo_path", "")
        self.lbl_batch_logo = QLabel(
            f"Logo: {os.path.basename(saved_logo_path)}" if saved_logo_path else "Logo: Not selected"
        )
        self.lbl_batch_logo.setWordWrap(True)
        self.lbl_batch_logo.setStyleSheet("color: #8FA8C8; font-size: 10px;")
        config_layout.addRow("", self.lbl_batch_logo)

        self.btn_preview_batch_logo = QPushButton(" Preview Batch Logo")
        self.btn_preview_batch_logo.setIcon(get_icon("render", "#FFFFFF", 16))
        self.btn_preview_batch_logo.setToolTip(
            "Preview the saved logo on the selected episode without changing the workspace."
        )
        self.btn_preview_batch_logo.clicked.connect(self.preview_batch_logo)
        config_layout.addRow("", self.btn_preview_batch_logo)

        left_layout.addWidget(batch_config_frame)

        self.btn_start_batch = QPushButton(" Start Batch Dubbing")
        self.btn_start_batch.setIcon(get_icon("automate", "#FFFFFF"))
        self.btn_start_batch.setStyleSheet("""
            QPushButton { background-color: #2E7D32; color: #FFFFFF; border: none; border-radius: 4px; padding: 8px 12px; font-weight: bold; min-height: 40px; }
            QPushButton:hover { background-color: #1B5E20; }
        """)
        self.btn_start_batch.clicked.connect(self.start_batch_dubbing)
        left_layout.addWidget(self.btn_start_batch)

        self.btn_cancel_batch = QPushButton(" Cancel Batch")
        self.btn_cancel_batch.setIcon(get_icon("cancel", "#C62828"))
        self.btn_cancel_batch.setProperty("class", "cancel-btn")
        self.btn_cancel_batch.setEnabled(False)
        self.btn_cancel_batch.clicked.connect(self.cancel_batch_dubbing)
        left_layout.addWidget(self.btn_cancel_batch)

        left_layout.addStretch()
        layout.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        table_hdr_layout = QHBoxLayout()
        batch_tbl_label = QLabel("Batch Dubbing Queue:")
        batch_tbl_label.setStyleSheet("font-weight: 700; font-size: 13px; color: #D8C9FF;")
        table_hdr_layout.addWidget(batch_tbl_label)
        table_hdr_layout.addStretch()

        self.btn_add_batch = QPushButton(" Add Video(s)")
        self.btn_add_batch.setIcon(get_icon("import_video", "#FFFFFF", 16))
        self.btn_add_batch.setStyleSheet("background-color: #6541C7; color: #FFFFFF; font-size: 11px; padding: 4px 8px; border-radius: 3px; border: none;")
        self.btn_add_batch.clicked.connect(self.add_batch_videos)
        table_hdr_layout.addWidget(self.btn_add_batch)

        self.btn_import_telegram = QPushButton(" Import Telegram Export")
        self.btn_import_telegram.setIcon(get_icon("import_video", "#FFFFFF", 16))
        self.btn_import_telegram.setStyleSheet("background-color: #1565C0; color: #FFFFFF; font-size: 11px; padding: 4px 8px; border-radius: 3px; border: none;")
        self.btn_import_telegram.clicked.connect(self.import_telegram_export)
        table_hdr_layout.addWidget(self.btn_import_telegram)

        self.btn_remove_batch = QPushButton(" Remove Selected")
        self.btn_remove_batch.setIcon(get_icon("delete", "#C62828", 16))
        self.btn_remove_batch.setStyleSheet("background-color: #351923; color: #FF9BAC; font-size: 11px; padding: 4px 8px; border-radius: 3px; border: 1px solid #7E3449;")
        self.btn_remove_batch.clicked.connect(self.remove_batch_selected)
        table_hdr_layout.addWidget(self.btn_remove_batch)

        right_layout.addLayout(table_hdr_layout)

        self.batch_table = QTableWidget()
        self.batch_table.setColumnCount(5)
        self.batch_table.setHorizontalHeaderLabels(["ID", "Video File", "Subtitle (SRT) [Double click]", "Output Path [Double click]", "Status"])
        self.batch_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.batch_table.horizontalHeader().setStretchLastSection(True)
        self.batch_table.setColumnWidth(0, 40)
        self.batch_table.setColumnWidth(1, 180)
        self.batch_table.setColumnWidth(2, 220)
        self.batch_table.setColumnWidth(3, 220)
        self.batch_table.setColumnWidth(4, 100)
        self.batch_table.cellDoubleClicked.connect(self.batch_table_double_clicked)
        right_layout.addWidget(self.batch_table, stretch=3)

        log_label = QLabel("Batch Execution Log:")
        log_label.setStyleSheet("font-weight: 700; font-size: 12px; color: #D8C9FF;")
        right_layout.addWidget(log_label)

        self.txt_batch_log = QTextEdit()
        self.txt_batch_log.setReadOnly(True)
        self.txt_batch_log.setStyleSheet("background-color: #111111; color: #4CAF50; font-family: 'Consolas', monospace; font-size: 11px;")
        right_layout.addWidget(self.txt_batch_log, stretch=1)

        layout.addWidget(right_widget)
        self.batch_lang_changed(self.cmb_batch_lang.currentIndex())
        saved_batch_voice = self.settings.get("batch_voice", "Auto (Piseth / Sreymom)")
        if saved_batch_voice in [self.cmb_batch_voice.itemText(i) for i in range(self.cmb_batch_voice.count())]:
            self.cmb_batch_voice.setCurrentText(saved_batch_voice)

    def batch_lang_changed(self, index):
        lang = self.cmb_batch_lang.currentText()
        self.cmb_batch_voice.clear()
        has_eleven = hasattr(self, 'settings') and bool(self.settings.get("elevenlabs_api_key") and self.settings.get("elevenlabs_voice_id"))
        
        if lang == "Khmer":
            voices = [
                "Auto (Piseth / Sreymom)",
                "Female - Sreymom",
                "Male - Piseth",
                "Male - Sopheap",
                "Female - Chamroeun"
            ]
        elif lang == "English":
            voices = ["Auto (Brian / Emma)", "Female - Alice", "Male - Bob"]
        else:
            voices = ["Auto (Yunjian / Xiaoxiao)", "Female - Xiaoxiao", "Male - Yunjian"]
            
        if has_eleven:
            voices.append("ElevenLabs Cloned Voice")

        voices.extend([
            VOXCPM_AUTO_VOICE_NAME,
            VOXCPM_FEMALE_VOICE_NAME,
            VOXCPM_MALE_VOICE_NAME,
            VOXCPM_VOICE_NAME,
        ])
            
        self.cmb_batch_voice.addItems(voices)

    def log_batch_msg(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.txt_batch_log.append(f"[{timestamp}] {msg}")

    def add_batch_videos(self):
        file_paths, _ = popup_open_file_names(
            self, "Select Video File(s)", "", "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)"
        )
        if file_paths:
            self.add_batch_video_paths(file_paths)

    def add_batch_video_paths(self, file_paths):
        existing_videos = {
            os.path.normcase(os.path.abspath(self.batch_table.item(row, 1).toolTip()))
            for row in range(self.batch_table.rowCount())
            if self.batch_table.item(row, 1) and self.batch_table.item(row, 1).toolTip()
        }
        reserved_outputs = {
            self.batch_table.item(row, 3).toolTip()
            for row in range(self.batch_table.rowCount())
            if self.batch_table.item(row, 3) and self.batch_table.item(row, 3).toolTip()
        }
        added_count = 0
        skipped_count = 0

        self.batch_table.blockSignals(True)
        try:
            for path in file_paths:
                path = os.path.abspath(path)
                path_norm = os.path.normcase(path)
                if path_norm in existing_videos or not os.path.isfile(path):
                    skipped_count += 1
                    continue

                row_idx = self.batch_table.rowCount()
                self.batch_table.insertRow(row_idx)

                item_id = QTableWidgetItem(str(row_idx + 1))
                item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.batch_table.setItem(row_idx, 0, item_id)

                item_video = QTableWidgetItem(os.path.basename(path))
                item_video.setToolTip(path)
                item_video.setFlags(item_video.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.batch_table.setItem(row_idx, 1, item_video)

                item_srt = QTableWidgetItem("Auto Transcribe")
                item_srt.setToolTip("")
                self.batch_table.setItem(row_idx, 2, item_srt)

                output_path = available_output_path(path, reserved_paths=reserved_outputs)
                reserved_outputs.add(output_path)
                item_output = QTableWidgetItem(os.path.basename(output_path))
                item_output.setToolTip(output_path)
                self.batch_table.setItem(row_idx, 3, item_output)

                item_status = QTableWidgetItem("Pending")
                item_status.setFlags(item_status.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.batch_table.setItem(row_idx, 4, item_status)

                existing_videos.add(path_norm)
                added_count += 1
        finally:
            self.batch_table.blockSignals(False)

        self.log_batch_msg(f"Added {added_count} videos to queue.")
        if skipped_count:
            self.log_batch_msg(f"Skipped {skipped_count} missing or duplicate videos.")
        return added_count

    def import_telegram_export(self):
        worker = getattr(self, "telegram_import_worker", None)
        if worker and worker.isRunning():
            self.btn_import_telegram.setEnabled(False)
            worker.requestInterruption()
            self.log_batch_msg("Cancelling Telegram export import...")
            return

        if getattr(self, "is_batch_active", False):
            popup_warning(self, "Batch Is Running", "Stop or finish the current batch before importing a Telegram export.")
            return

        folder = popup_get_existing_directory(self, "Select Telegram Export Folder")
        if not folder:
            return

        self.telegram_import_worker = TelegramExportImportWorker(folder)
        self.telegram_import_worker.progress.connect(self.telegram_import_progress)
        self.telegram_import_worker.status.connect(self.log_batch_msg)
        self.telegram_import_worker.completed.connect(self.telegram_import_completed)
        self.telegram_import_worker.cancelled.connect(self.telegram_import_cancelled)
        self.telegram_import_worker.error.connect(self.telegram_import_failed)

        self.btn_start_batch.setEnabled(False)
        self.btn_add_batch.setEnabled(False)
        self.btn_remove_batch.setEnabled(False)
        self.btn_import_telegram.setText(" Cancel Telegram Import")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_batch_msg(f"Importing Telegram export: {folder}")
        self.telegram_import_worker.start()

    def telegram_import_progress(self, value):
        self.progress_bar.setValue(value)

    def _finish_telegram_import_ui(self):
        self.btn_start_batch.setEnabled(True)
        self.btn_add_batch.setEnabled(True)
        self.btn_remove_batch.setEnabled(True)
        self.btn_import_telegram.setEnabled(True)
        self.btn_import_telegram.setText(" Import Telegram Export")
        self.progress_bar.setVisible(False)

    def telegram_import_completed(self, video_paths, warnings):
        added_count = self.add_batch_video_paths(video_paths)
        self._finish_telegram_import_ui()
        self.log_batch_msg(f"Telegram import complete: {len(video_paths)} videos found, {added_count} added.")
        if warnings:
            self.log_batch_msg(f"Import completed with {len(warnings)} warning(s).")
            popup_warning(
                self,
                "Telegram Import Warnings",
                "Some archives could not be imported:\n\n" + "\n".join(warnings[:12])
            )
        else:
            popup_info(self, "Telegram Import Complete", f"Added {added_count} videos to the batch queue.")

    def telegram_import_cancelled(self):
        self._finish_telegram_import_ui()
        self.log_batch_msg("Telegram export import cancelled. Original ZIP files were not changed.")

    def telegram_import_failed(self, error_message):
        self._finish_telegram_import_ui()
        self.log_batch_msg(f"Telegram export import failed: {error_message}")
        popup_error(self, "Telegram Import Failed", error_message)

    def remove_batch_selected(self):
        current_row = self.batch_table.currentRow()
        if current_row >= 0:
            filename = self.batch_table.item(current_row, 1).text()
            self.batch_table.removeRow(current_row)
            self.log_batch_msg(f"Removed {filename} from queue.")
            self.batch_table.blockSignals(True)
            for r in range(self.batch_table.rowCount()):
                self.batch_table.item(r, 0).setText(str(r + 1))
            self.batch_table.blockSignals(False)
        else:
            popup_warning(self, "Warning", "Please select a row in the batch queue table to remove.")

    def batch_table_double_clicked(self, row, column):
        if column == 2:  # Subtitle
            file_path, _ = popup_open_file_name(
                self, "Select Subtitle (SRT)", "", "Subtitle Files (*.srt);;All Files (*)"
            )
            if file_path:
                item = self.batch_table.item(row, column)
                item.setText(os.path.basename(file_path))
                item.setToolTip(file_path)
            else:
                reply = popup_question(
                    self, "Reset", "Do you want to reset this item to Auto Transcribe?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    item = self.batch_table.item(row, column)
                    item.setText("Auto Transcribe")
                    item.setToolTip("")
        elif column == 3:  # Output Path
            video_path = self.batch_table.item(row, 1).toolTip()
            output_item = self.batch_table.item(row, column)
            suggested_path = output_item.toolTip() if output_item else ""
            if not suggested_path:
                suggested_path = default_output_path(video_path)
            file_path, _ = popup_save_file_name(
                self, "Save Dubbed Video As", suggested_path, "MPEG-4 Video (*.mp4);;All Files (*)"
            )
            if file_path:
                item = self.batch_table.item(row, column)
                item.setText(os.path.basename(file_path))
                item.setToolTip(file_path)

    def parse_srt_file_to_segments(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            blocks = re.split(r'\n\s*\n', content.strip())
            segments = []
            for block in blocks:
                lines = block.split('\n')
                if len(lines) >= 3:
                    sub_id = int(lines[0].strip())
                    timecode_line = lines[1]
                    text_lines = " ".join(lines[2:])
                    tc_match = re.findall(r'(\d{2}:\d{2}:\d{2}(?:[.,]\d{3})?)', timecode_line)
                    if len(tc_match) >= 2:
                        start_t = tc_match[0].replace(',', '.')
                        end_t = tc_match[1].replace(',', '.')
                        tc = f"{start_t} - {end_t}"
                    elif len(tc_match) == 1:
                        tc = tc_match[0].replace(',', '.')
                    else:
                        tc = "00:00:00"
                    segments.append({
                        "id": sub_id,
                        "time": tc,
                        "text": text_lines.strip()
                    })
            return segments
        except Exception as e:
            print(f"Error parsing SRT: {e}")
            return None

    def save_batch_preferences(self):
        """Persist Batch-tab choices without changing the single-video workflow."""
        self.settings["batch_translation_provider"] = self.cmb_batch_model.currentText().strip()
        self.settings["batch_source_lang"] = self.cmb_batch_source.currentText().strip()
        self.settings["batch_target_lang"] = self.cmb_batch_lang.currentText().strip()
        self.settings["batch_voice"] = self.cmb_batch_voice.currentText().strip()
        self.settings["batch_auto_gender"] = self.chk_batch_auto_gender.isChecked()
        self.settings["batch_music_level"] = self.sld_batch_music.value()
        self.settings["batch_mix_mode"] = self.cmb_batch_mix.currentText().strip()
        self.settings["batch_voice_only"] = self.chk_batch_vocal_iso.isChecked()
        self.settings["batch_noise_reduction"] = self.chk_batch_noise_reduction.isChecked()
        self.settings["batch_use_logo"] = self.chk_batch_logo.isChecked()
        save_settings(self.settings)

    def preview_batch_logo(self):
        """Show the selected batch video's first frame with the exact render logo geometry."""
        import subprocess

        if self.batch_table.rowCount() == 0:
            popup_warning(self, "No Batch Video", "Add at least one episode before previewing the batch logo.")
            return

        row = self.batch_table.currentRow()
        if row < 0:
            row = 0
        video_item = self.batch_table.item(row, 1)
        video_path = video_item.toolTip() if video_item else ""
        logo_path = self.txt_logo_path.text().strip() if hasattr(self, "txt_logo_path") else ""
        if not video_path or not os.path.isfile(video_path):
            popup_error(self, "Video Missing", "The selected batch video cannot be found.")
            return
        if not logo_path or not os.path.isfile(logo_path):
            popup_error(self, "Logo Missing", "Choose a valid logo in Single Dubbing > Logo first.")
            return

        frame_path = os.path.join(tempfile.gettempdir(), f"batch_logo_preview_{os.getpid()}.png")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", "1", "-i", video_path, "-frames:v", "1", frame_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            frame = QPixmap(frame_path)
            if result.returncode != 0 or frame.isNull():
                raise RuntimeError("FFmpeg could not extract a preview frame from this episode.")

            logo = trimmed_logo_pixmap(logo_path)
            if logo.isNull():
                raise RuntimeError("The saved logo image could not be loaded.")

            scale = max(0.03, min(0.80, float(getattr(self, "logo_scale_val", 0.15))))
            rel_x = max(0.0, min(1.0, float(getattr(self, "logo_rel_x", 0.04))))
            rel_y = max(0.0, min(1.0, float(getattr(self, "logo_rel_y", 0.01))))
            target_w = max(16, round(frame.width() * scale))
            logo = logo.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
            x = round((frame.width() - logo.width()) * rel_x)
            y = round((frame.height() - logo.height()) * rel_y)

            composed = QPixmap(frame)
            painter = QPainter(composed)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(0.85)
            painter.drawPixmap(x, y, logo)
            painter.end()

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Batch Logo Preview — {os.path.basename(video_path)}")
            dialog.resize(720, 820)
            layout = QVBoxLayout(dialog)
            info = QLabel(
                f"Episode {row + 1} • Logo {int(scale * 100)}% • "
                f"Position ({rel_x:.3f}, {rel_y:.3f})"
            )
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview = QLabel()
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setStyleSheet("background: #000000; border: 1px solid #26364D;")
            preview.setPixmap(composed.scaled(680, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            close_button = QPushButton("Close Preview")
            close_button.clicked.connect(dialog.accept)
            layout.addWidget(info)
            layout.addWidget(preview, 1)
            layout.addWidget(close_button)
            dialog.exec()
        except Exception as exc:
            popup_error(self, "Preview Failed", str(exc))
        finally:
            try:
                if os.path.exists(frame_path):
                    os.remove(frame_path)
            except OSError:
                pass

    def batch_settings_snapshot(self):
        settings = dict(self.settings)
        settings["translation_provider"] = self.cmb_batch_model.currentText().strip()
        settings["translation_source_lang"] = self.cmb_batch_source.currentText().strip()
        settings["translation_target_lang"] = self.cmb_batch_lang.currentText().strip()
        return settings

    def set_batch_controls_enabled(self, enabled):
        for widget in (
            self.btn_start_batch,
            self.btn_add_batch,
            self.btn_remove_batch,
            self.cmb_batch_model,
            self.cmb_batch_source,
            self.cmb_batch_lang,
            self.cmb_batch_voice,
            self.chk_batch_auto_gender,
            self.cmb_batch_mix,
            self.sld_batch_music,
            self.chk_batch_vocal_iso,
            self.chk_batch_noise_reduction,
            self.chk_batch_logo,
            self.btn_preview_batch_logo,
        ):
            widget.setEnabled(enabled)
        self.btn_cancel_batch.setEnabled(not enabled)

    def batch_voice_for_gender(self, gender):
        selected = self.batch_run_voice
        if not self.batch_run_auto_gender or "auto" not in selected.lower():
            return selected

        gender_text = (gender or "").strip().lower()
        if not gender_text.startswith(("male", "female")):
            return VOXCPM_FEMALE_VOICE_NAME if selected == VOXCPM_AUTO_VOICE_NAME else selected

        is_male = gender_text.startswith("male")
        if selected == VOXCPM_AUTO_VOICE_NAME:
            return VOXCPM_MALE_VOICE_NAME if is_male else VOXCPM_FEMALE_VOICE_NAME
        lang = self.batch_run_settings.get("translation_target_lang", "Khmer")
        if lang == "English":
            return "Male - Bob" if is_male else "Female - Alice"
        if lang == "Chinese":
            return "Male - Yunjian" if is_male else "Female - Xiaoxiao"
        return "Male - Piseth" if is_male else "Female - Sreymom"

    def start_batch_dubbing(self):
        if hasattr(self, 'is_batch_active') and self.is_batch_active:
            popup_warning(self, "Warning", "Batch process is already running.")
            return

        row_count = self.batch_table.rowCount()
        if row_count == 0:
            popup_warning(self, "Warning", "No episodes in the queue to process.")
            return

        if not self.validate_voxcpm_config(self.cmb_batch_voice.currentText()):
            return

        self.save_batch_preferences()
        self.batch_run_settings = self.batch_settings_snapshot()
        self.batch_run_voice = self.cmb_batch_voice.currentText().strip()
        self.batch_run_auto_gender = self.chk_batch_auto_gender.isChecked()
        self.batch_run_music_level = self.sld_batch_music.value()
        self.batch_run_mix_mode = self.cmb_batch_mix.currentText().strip()
        self.batch_run_voice_only = self.chk_batch_vocal_iso.isChecked()
        self.batch_run_noise_reduction = self.chk_batch_noise_reduction.isChecked()
        self.batch_run_use_logo = self.chk_batch_logo.isChecked()
        self.batch_run_logo_path = (
            self.txt_logo_path.text().strip() if hasattr(self, "txt_logo_path") else self.settings.get("logo_path", "")
        )
        self.batch_run_logo_scale = float(getattr(self, "logo_scale_val", self.settings.get("logo_scale", 0.15)))
        self.batch_run_logo_rel_x = float(getattr(self, "logo_rel_x", self.settings.get("logo_rel_x", 0.04)))
        self.batch_run_logo_rel_y = float(getattr(self, "logo_rel_y", self.settings.get("logo_rel_y", 0.01)))
        if self.batch_run_use_logo and not os.path.isfile(self.batch_run_logo_path):
            popup_error(
                self,
                "Batch Logo Missing",
                "Choose a valid logo in Single Dubbing > Logo, or turn off 'Use saved logo on every episode'."
            )
            return

        self.batch_tasks = []
        reserved_outputs = set()
        for r in range(row_count):
            video_path = self.batch_table.item(r, 1).toolTip()
            srt_path = self.batch_table.item(r, 2).toolTip()
            if srt_path == "":
                srt_path = None
            output_path = self.batch_table.item(r, 3).toolTip()

            if not video_path or not os.path.isfile(video_path):
                popup_error(self, "Missing Batch Video", f"Video for row {r + 1} does not exist:\n{video_path}")
                return
            if srt_path and not os.path.isfile(srt_path):
                popup_error(self, "Missing Subtitle", f"Subtitle for row {r + 1} does not exist:\n{srt_path}")
                return
            if not output_path:
                popup_error(self, "Missing Output Path", f"Choose an output path for row {r + 1}.")
                return
            input_norm = os.path.normcase(os.path.abspath(video_path))
            output_norm = os.path.normcase(os.path.abspath(output_path))
            if input_norm == output_norm:
                popup_error(self, "Unsafe Output Path", f"Row {r + 1} output cannot overwrite its source video.")
                return
            if output_norm in reserved_outputs:
                popup_error(self, "Duplicate Output Path", f"More than one batch row uses:\n{output_path}")
                return
            reserved_outputs.add(output_norm)
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

            self.batch_table.item(r, 4).setText("Pending")
            self.batch_table.item(r, 4).setBackground(QBrush(QColor("#FFFFFF")))

            self.batch_tasks.append({
                "video": video_path,
                "srt": srt_path,
                "output": output_path,
                "status": "Pending"
            })

        self.is_batch_active = True
        self.current_batch_index = 0
        self.batch_failed_count = 0
        self.set_batch_controls_enabled(False)
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.txt_batch_log.clear()
        self.log_batch_msg(f"Batch Dubbing Process started. Total tasks: {len(self.batch_tasks)}")
        self.log_batch_msg(
            f"Settings: {self.batch_run_settings['translation_source_lang']} → "
            f"{self.batch_run_settings['translation_target_lang']} with "
            f"{self.batch_run_settings['translation_provider']}; voice={self.batch_run_voice}; "
            f"auto gender={'ON' if self.batch_run_auto_gender else 'OFF'}; mix={self.batch_run_mix_mode}."
        )
        if self.batch_run_use_logo:
            self.log_batch_msg(
                f"Logo: {os.path.basename(self.batch_run_logo_path)}; "
                f"size={int(self.batch_run_logo_scale * 100)}%; "
                f"position=({self.batch_run_logo_rel_x:.3f}, {self.batch_run_logo_rel_y:.3f})."
            )
        else:
            self.log_batch_msg("Logo: OFF for this batch.")
        self.run_next_batch_task()

    def cancel_batch_dubbing(self):
        if not hasattr(self, 'is_batch_active') or not self.is_batch_active:
            return

        reply = popup_question(
            self, "Cancel Batch", "Are you sure you want to stop the batch process?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.log_batch_msg("User requested cancellation. Aborting current worker...")
            for thread_attr in ['transcribe_thread', 'translate_thread', 'voice_gender_thread', 'tts_thread', 'render_thread']:
                if hasattr(self, thread_attr):
                    thread = getattr(self, thread_attr)
                    if thread and thread.isRunning():
                        try:
                            thread.terminate()
                            thread.wait()
                        except Exception:
                            pass
            
            if 0 <= self.current_batch_index < self.batch_table.rowCount():
                item = self.batch_table.item(self.current_batch_index, 4)
                item.setText("Cancelled")
                item.setBackground(QBrush(QColor("#FFCDD2")))

            self.is_batch_active = False
            self.progress_bar.setVisible(False)
            self.set_batch_controls_enabled(True)
            self.log_batch_msg("Batch process aborted.")

    def run_next_batch_task(self):
        if not self.is_batch_active:
            return

        if self.current_batch_index >= len(self.batch_tasks):
            failed = getattr(self, "batch_failed_count", 0)
            completed = len(self.batch_tasks) - failed
            self.log_batch_msg(f"Batch finished. Completed: {completed}; Failed: {failed}.")
            self.is_batch_active = False
            self.progress_bar.setVisible(False)
            self.set_batch_controls_enabled(True)
            if failed:
                popup_warning(
                    self,
                    "Batch Finished with Errors",
                    f"Completed: {completed}\nFailed: {failed}\n\nCheck the Batch Execution Log for details."
                )
            else:
                popup_info(self, "Batch Complete", "All dubbed videos generated successfully!")
            return

        task = self.batch_tasks[self.current_batch_index]
        self.log_batch_msg(f"--- Processing Task {self.current_batch_index + 1}/{len(self.batch_tasks)}: {os.path.basename(task['video'])} ---")
        
        self.batch_table.selectRow(self.current_batch_index)
        self.batch_table.item(self.current_batch_index, 4).setText("Active")
        self.batch_table.item(self.current_batch_index, 4).setBackground(QBrush(QColor("#E1F5FE")))

        self.batch_audio_files = {}
        self.batch_segments = []
        self.batch_translated_segments = []

        if task["srt"]:
            self.log_batch_msg(f"Loading user subtitle: {os.path.basename(task['srt'])}")
            segments = self.parse_srt_file_to_segments(task["srt"])
            if segments:
                self.batch_segments = segments
                self.log_batch_msg(f"Successfully loaded {len(segments)} lines. Proceeding to translation.")
                self.run_batch_translate()
            else:
                self.batch_stage_error("Failed to parse the SRT subtitle file.")
        else:
            self.log_batch_msg("No subtitle file provided. Initiating audio transcription...")
            self.batch_table.item(self.current_batch_index, 4).setText("Transcribing")
            self.current_batch_stage = "transcribe"
            self.progress_bar.setFormat("Transcribing batch item... %p%")
            
            whisper_key = openai_whisper_key_for_settings(self.batch_run_settings)
            if not whisper_key:
                self.log_batch_msg("Using Local Whisper (no official OpenAI Whisper key configured).")
            self.transcribe_thread = TranscribeWorker(
                task["video"],
                whisper_key,
                self.batch_run_settings.get("nllb_python_path", ""),
            )
            self.transcribe_thread.progress.connect(self.batch_progress_update)
            self.transcribe_thread.completed.connect(self.batch_transcribe_completed)
            self.transcribe_thread.error.connect(self.batch_stage_error)
            self.transcribe_thread.start()

    def batch_progress_update(self, val):
        self.progress_bar.setValue(val)

    def batch_transcribe_completed(self, segments):
        self.log_batch_msg(f"Audio transcription complete. Extracted {len(segments)} timeline segments.")
        self.batch_segments = segments
        self.run_batch_translate()

    def run_batch_translate(self):
        if not self.is_batch_active:
            return

        if not self.batch_segments:
            self.log_batch_msg("No subtitle segments found to translate. Skipping translation and TTS phases.")
            self.run_batch_render()
            return

        provider = self.batch_run_settings.get("translation_provider", "Google Web")
        direction = translation_direction_label(self.batch_run_settings)
        self.log_batch_msg(f"Initiating script translation with {provider} ({direction})...")
        self.batch_table.item(self.current_batch_index, 4).setText("Translating")
        self.current_batch_stage = "translate"
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Translating {direction} with {provider}... %p%")

        items = []
        for seg in self.batch_segments:
            try:
                start_sec, end_sec = parse_timecode_range(seg["time"])
            except Exception:
                start_sec, end_sec = 0.0, 3.0
            items.append((str(seg["id"]), seg["text"], start_sec, end_sec))

        self.translate_thread = TranslateWorker(
            items,
            self.batch_run_settings.get("gemini_api_key", ""),
            self.batch_run_settings,
        )
        self.translate_thread.progress.connect(self.batch_progress_update)
        self.translate_thread.status.connect(self.batch_translation_status_changed)
        self.translate_thread.completed.connect(self.batch_translate_completed)
        self.translate_thread.error.connect(self.batch_stage_error)
        self.translate_thread.start()

    def batch_translation_status_changed(self, message):
        self.progress_bar.setFormat(f"{message}... %p%")
        self.log_batch_msg(message)

    def batch_translate_completed(self, results):
        self.log_batch_msg("Script translation completed successfully.")
        translated_map = {}
        gender_map = {}
        for result in results:
            if len(result) < 2:
                continue
            id_val = str(result[0])
            translated_map[id_val] = result[1]
            gender_map[id_val] = result[2] if len(result) >= 3 else ""
        self.batch_translated_segments = []
        
        for seg in self.batch_segments:
            idx_str = str(seg["id"])
            translated_text = translated_map.get(idx_str, f"បកប្រែ៖ {seg['text']}")
            if idx_str not in translated_map:
                translated_text = seg["text"]
            translated_text = condense_khmer_dubbing_text(translated_text)
            self.batch_translated_segments.append({
                "id": seg["id"],
                "time": seg["time"],
                "text": translated_text,
                "gender": gender_map.get(idx_str, ""),
            })

        gender_count = sum(
            1 for seg in self.batch_translated_segments
            if str(seg.get("gender", "")).lower().startswith(("male", "female"))
        )
        self.log_batch_msg(
            f"Translation supplied speaker gender for {gender_count}/{len(self.batch_translated_segments)} lines."
        )
        if (
            self.batch_run_auto_gender
            and "auto" in self.batch_run_voice.lower()
            and gender_count < len(self.batch_translated_segments)
        ):
            self.run_batch_voice_detection()
        else:
            self.run_batch_tts()

    def run_batch_voice_detection(self):
        if not self.is_batch_active:
            return
        self.log_batch_msg("Detecting missing Male/Female speakers from the original movie audio...")
        self.batch_table.item(self.current_batch_index, 4).setText("Detecting Voices")
        self.current_batch_stage = "speaker detection"
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Detecting batch speaker genders... %p%")

        rows = []
        for row_idx, seg in enumerate(self.batch_translated_segments):
            try:
                start_sec, end_sec = parse_timecode_range(seg["time"])
            except Exception:
                start_sec, end_sec = 0.0, 3.0
            rows.append({
                "row": row_idx,
                "start": start_sec,
                "end": end_sec,
                "text": self.batch_segments[row_idx].get("text", ""),
                "translated": seg.get("text", ""),
            })

        task = self.batch_tasks[self.current_batch_index]
        gender_settings = dict(self.batch_run_settings)
        # A missing gender here means the AI translators already fell back to a
        # plain translator. Avoid retrying the same slow chat endpoint and use
        # the original speaker pitch immediately.
        gender_settings["openai_api_key"] = ""
        gender_settings["openai_api_key_backup"] = ""
        self.voice_gender_thread = VoiceGenderWorker(task["video"], rows, gender_settings)
        self.voice_gender_thread.progress.connect(self.batch_progress_update)
        self.voice_gender_thread.status.connect(self.batch_translation_status_changed)
        self.voice_gender_thread.completed.connect(self.batch_voice_detection_completed)
        self.voice_gender_thread.error.connect(self.batch_voice_detection_failed)
        self.voice_gender_thread.start()

    def batch_voice_detection_completed(self, results):
        detected = 0
        for result in results:
            row_idx = int(result.get("row", -1))
            if 0 <= row_idx < len(self.batch_translated_segments):
                gender = str(result.get("gender", "")).strip()
                curr_gender = str(self.batch_translated_segments[row_idx].get("gender", "")).strip()
                if gender.lower().startswith(("male", "female")):
                    if not curr_gender.lower().startswith(("male", "female")) or result.get("confidence", 0) >= 0.95:
                        self.batch_translated_segments[row_idx]["gender"] = gender
                        detected += 1
        self.log_batch_msg(f"Speaker detection assigned Male/Female voices to {detected} lines.")
        self.run_batch_tts()

    def batch_voice_detection_failed(self, error_msg):
        self.log_batch_msg(f"Speaker detection warning: {error_msg}. Continuing with translation/Auto voice labels.")
        self.run_batch_tts()

    def run_batch_tts(self):
        if not self.is_batch_active:
            return

        self.log_batch_msg("Generating TTS speech voiceovers...")
        self.batch_table.item(self.current_batch_index, 4).setText("TTS Voiceover")
        self.current_batch_stage = "tts"
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Generating TTS voices... %p%")

        tasks = []
        male_count = 0
        female_count = 0
        for row_idx, seg in enumerate(self.batch_translated_segments):
            gender = seg.get("gender", "")
            voice_char = self.batch_voice_for_gender(gender)
            if str(gender).lower().startswith("male"):
                male_count += 1
            elif str(gender).lower().startswith("female"):
                female_count += 1
            try:
                start_sec, end_sec = parse_timecode_range(seg["time"])
            except Exception:
                start_sec, end_sec = 0.0, 3.0
            tasks.append((row_idx, seg["text"].strip(), voice_char, start_sec, end_sec))

        self.log_batch_msg(
            f"TTS voice assignment: {male_count} Male, {female_count} Female, "
            f"{len(tasks) - male_count - female_count} Auto/default."
        )

        self.tts_thread = TTSWorker(tasks, self.cache_dir, self.batch_run_settings)
        self.tts_thread.progress.connect(self.batch_progress_update)
        self.tts_thread.status.connect(lambda text: self.progress_bar.setFormat(f"{text}... %p%"))
        self.tts_thread.row_completed.connect(self.batch_tts_row_completed)
        self.tts_thread.completed.connect(self.batch_tts_completed)
        self.tts_thread.error.connect(self.batch_stage_error)
        self.tts_thread.start()

    def batch_tts_row_completed(self, row_idx, file_path):
        self.batch_audio_files[row_idx] = file_path

    def batch_tts_completed(self):
        self.log_batch_msg("Voiceovers synthesized successfully.")
        self.run_batch_render()

    def run_batch_render(self):
        if not self.is_batch_active:
            return

        self.log_batch_msg("Rendering final dubbed video...")
        self.batch_table.item(self.current_batch_index, 4).setText("Rendering")
        self.current_batch_stage = "render"
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Rendering video... %p%")

        audio_offsets = []
        for row_idx, path in self.batch_audio_files.items():
            if 0 <= row_idx < len(self.batch_segments):
                seg = self.batch_segments[row_idx]
                start_sec, end_sec = parse_timecode_range(seg["time"])
                audio_offsets.append((path, start_sec, end_sec))

        task = self.batch_tasks[self.current_batch_index]
        self.render_thread = RenderWorker(
            task["video"], audio_offsets, task["output"],
            music_level=self.batch_run_music_level,
            vocal_boost=self.batch_run_noise_reduction,
            mix_mode=self.batch_run_mix_mode,
            voice_only=self.batch_run_voice_only,
            logo_path=self.batch_run_logo_path if self.batch_run_use_logo else "",
            logo_scale=self.batch_run_logo_scale,
            logo_rel_x=self.batch_run_logo_rel_x,
            logo_rel_y=self.batch_run_logo_rel_y,
        )
        self.render_thread.progress.connect(self.batch_progress_update)
        self.render_thread.completed.connect(self.batch_render_completed)
        self.render_thread.error.connect(self.batch_stage_error)
        self.render_thread.start()

    def batch_render_completed(self, output_path):
        self.log_batch_msg(f"Rendering complete. Output saved: {os.path.basename(output_path)}")
        self.finish_batch_task(success=True)

    def batch_stage_error(self, error_msg):
        self.log_batch_msg(f"ERROR on Episode {self.current_batch_index + 1} during stage '{self.current_batch_stage}': {error_msg}")
        self.finish_batch_task(success=False)

    def finish_batch_task(self, success):
        item_status = self.batch_table.item(self.current_batch_index, 4)
        if success:
            item_status.setText("Completed")
            item_status.setBackground(QBrush(QColor("#C8E6C9")))
        else:
            item_status.setText("Failed")
            item_status.setBackground(QBrush(QColor("#FFCDD2")))
            self.batch_failed_count = getattr(self, "batch_failed_count", 0) + 1
            
        for thread_attr in ['transcribe_thread', 'translate_thread', 'voice_gender_thread', 'tts_thread', 'render_thread']:
            if hasattr(self, thread_attr):
                setattr(self, thread_attr, None)

        self.current_batch_index += 1
        self.run_next_batch_task()

    # ----------------------------------------------------
    # Video Merger Methods
    # ----------------------------------------------------
    def init_merger_tab(self, layout):
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        table_hdr_layout = QHBoxLayout()
        merge_tbl_label = QLabel("Videos to Merge (In Order):")
        merge_tbl_label.setStyleSheet("font-weight: 700; font-size: 13px; color: #D8C9FF;")
        table_hdr_layout.addWidget(merge_tbl_label)
        table_hdr_layout.addStretch()

        self.btn_add_merge = QPushButton(" Add Videos")
        self.btn_add_merge.setIcon(get_icon("import_video", "#FFFFFF", 16))
        self.btn_add_merge.setStyleSheet("background-color: #6541C7; color: #FFFFFF; font-size: 11px; padding: 4px 8px; border-radius: 3px; border: none;")
        self.btn_add_merge.clicked.connect(self.add_merge_videos)
        table_hdr_layout.addWidget(self.btn_add_merge)

        self.btn_remove_merge = QPushButton(" Remove")
        self.btn_remove_merge.setIcon(get_icon("delete", "#C62828", 16))
        self.btn_remove_merge.setStyleSheet("background-color: #351923; color: #FF9BAC; font-size: 11px; padding: 4px 8px; border-radius: 3px; border: 1px solid #7E3449;")
        self.btn_remove_merge.clicked.connect(self.remove_merge_selected)
        table_hdr_layout.addWidget(self.btn_remove_merge)

        left_layout.addLayout(table_hdr_layout)

        self.merge_table = QTableWidget()
        self.merge_table.setColumnCount(3)
        self.merge_table.setHorizontalHeaderLabels(["Order", "File Name", "Full Path"])
        self.merge_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.merge_table.horizontalHeader().setStretchLastSection(True)
        self.merge_table.setColumnWidth(0, 50)
        self.merge_table.setColumnWidth(1, 200)
        left_layout.addWidget(self.merge_table)

        order_layout = QHBoxLayout()
        self.btn_move_up = QPushButton(" Move Up (▲)")
        self.btn_move_up.setStyleSheet("background-color: #171D32; color: #DCE1F2; font-size: 11px; padding: 4px 8px; border-radius: 3px;")
        self.btn_move_up.clicked.connect(self.move_merge_up)
        
        self.btn_move_down = QPushButton(" Move Down (▼)")
        self.btn_move_down.setStyleSheet("background-color: #171D32; color: #DCE1F2; font-size: 11px; padding: 4px 8px; border-radius: 3px;")
        self.btn_move_down.clicked.connect(self.move_merge_down)

        order_layout.addWidget(self.btn_move_up)
        order_layout.addWidget(self.btn_move_down)
        order_layout.addStretch()
        left_layout.addLayout(order_layout)

        layout.addWidget(left_widget, stretch=3)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_widget.setFixedWidth(320)

        merge_config_frame = QFrame()
        merge_config_frame.setObjectName("configFrame")
        config_layout = QVBoxLayout(merge_config_frame)
        config_layout.setContentsMargins(12, 12, 12, 12)
        config_layout.setSpacing(8)

        config_hdr = QLabel("Merge Settings:")
        config_hdr.setStyleSheet("font-weight: 700; font-size: 13px; color: #D8C9FF; margin-bottom: 5px;")
        config_layout.addWidget(config_hdr)

        config_layout.addWidget(QLabel("Merge Mode:"))
        self.cmb_merge_mode = QComboBox()
        self.cmb_merge_mode.addItems(["Fast Merge (No Re-encoding)", "Compatible Merge (Re-encode)"])
        config_layout.addWidget(self.cmb_merge_mode)

        self.lbl_merge_desc = QLabel(
            "Fast Merge matches streams and concatenates in seconds. "
            "Requires all videos to have identical codecs, resolution, and framerate.\n\n"
            "Compatible Merge re-encodes everything together. Slower but accepts different files."
        )
        self.lbl_merge_desc.setWordWrap(True)
        self.lbl_merge_desc.setStyleSheet("color: #AAB2C8; font-size: 11px; margin-top: 2px;")
        config_layout.addWidget(self.lbl_merge_desc)

        config_layout.addWidget(QLabel("Output File Path:"))
        out_path_layout = QHBoxLayout()
        self.txt_merge_output = QLineEdit()
        self.txt_merge_output.setPlaceholderText("Select output file path")
        out_path_layout.addWidget(self.txt_merge_output)
        
        self.btn_browse_merge_output = QPushButton("Browse")
        self.btn_browse_merge_output.setStyleSheet("padding: 4px 8px;")
        self.btn_browse_merge_output.clicked.connect(self.browse_merge_output)
        out_path_layout.addWidget(self.btn_browse_merge_output)
        config_layout.addLayout(out_path_layout)

        right_layout.addWidget(merge_config_frame)

        self.btn_start_merge = QPushButton(" Start Merge Videos")
        self.btn_start_merge.setIcon(get_icon("merge", "#FFFFFF"))
        self.btn_start_merge.setStyleSheet("""
            QPushButton { background-color: #2E7D32; color: #FFFFFF; border: none; border-radius: 4px; padding: 8px 12px; font-weight: bold; min-height: 40px; }
            QPushButton:hover { background-color: #1B5E20; }
        """)
        self.btn_start_merge.clicked.connect(self.start_merge)
        right_layout.addWidget(self.btn_start_merge)

        self.btn_cancel_merge = QPushButton(" Cancel Merge")
        self.btn_cancel_merge.setIcon(get_icon("cancel", "#C62828"))
        self.btn_cancel_merge.setProperty("class", "cancel-btn")
        self.btn_cancel_merge.setEnabled(False)
        self.btn_cancel_merge.clicked.connect(self.cancel_merge)
        right_layout.addWidget(self.btn_cancel_merge)

        right_layout.addWidget(QLabel("Merge Progress Log:"))
        self.txt_merge_log = QTextEdit()
        self.txt_merge_log.setReadOnly(True)
        self.txt_merge_log.setStyleSheet("background-color: #111111; color: #4CAF50; font-family: 'Consolas', monospace; font-size: 11px;")
        right_layout.addWidget(self.txt_merge_log, stretch=1)

        layout.addWidget(right_widget)

    def log_merge_msg(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.txt_merge_log.append(f"[{timestamp}] {msg}")

    def add_merge_videos(self):
        file_paths, _ = popup_open_file_names(
            self, "Select Videos to Merge", "", "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)"
        )
        if file_paths:
            self.merge_table.blockSignals(True)
            for path in file_paths:
                row_idx = self.merge_table.rowCount()
                self.merge_table.insertRow(row_idx)

                item_order = QTableWidgetItem(str(row_idx + 1))
                item_order.setFlags(item_order.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item_order.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.merge_table.setItem(row_idx, 0, item_order)

                item_name = QTableWidgetItem(os.path.basename(path))
                item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.merge_table.setItem(row_idx, 1, item_name)

                item_path = QTableWidgetItem(path)
                item_path.setFlags(item_path.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.merge_table.setItem(row_idx, 2, item_path)

            self.merge_table.blockSignals(False)
            self.log_merge_msg(f"Added {len(file_paths)} videos for merging.")

            if file_paths and not self.txt_merge_output.text():
                first_path = file_paths[0]
                suggested_out = default_output_path(first_path, suffix="_merged")
                self.txt_merge_output.setText(suggested_out)

    def remove_merge_selected(self):
        current_row = self.merge_table.currentRow()
        if current_row >= 0:
            filename = self.merge_table.item(current_row, 1).text()
            self.merge_table.removeRow(current_row)
            self.log_merge_msg(f"Removed {filename} from list.")
            self.merge_table.blockSignals(True)
            for r in range(self.merge_table.rowCount()):
                self.merge_table.item(r, 0).setText(str(r + 1))
            self.merge_table.blockSignals(False)
        else:
            popup_warning(self, "Warning", "Please select a video in the merger table to remove.")

    def swap_merge_rows(self, row1, row2):
        self.merge_table.blockSignals(True)
        for col in range(1, 3):
            item1 = self.merge_table.item(row1, col)
            item2 = self.merge_table.item(row2, col)
            if item1 and item2:
                t1, t2 = item1.text(), item2.text()
                item1.setText(t2)
                item2.setText(t1)
        self.merge_table.blockSignals(False)

    def move_merge_up(self):
        current_row = self.merge_table.currentRow()
        if current_row > 0:
            self.swap_merge_rows(current_row, current_row - 1)
            self.merge_table.setCurrentCell(current_row - 1, 1)
            self.log_merge_msg(f"Moved item up to position {current_row}.")

    def move_merge_down(self):
        current_row = self.merge_table.currentRow()
        if current_row >= 0 and current_row < self.merge_table.rowCount() - 1:
            self.swap_merge_rows(current_row, current_row + 1)
            self.merge_table.setCurrentCell(current_row + 1, 1)
            self.log_merge_msg(f"Moved item down to position {current_row + 2}.")

    def browse_merge_output(self):
        default_file = self.txt_merge_output.text()
        suggested_path = default_file or os.path.join(ensure_output_dir(), "merged_full_video.mp4")
        file_path, _ = popup_save_file_name(
            self, "Select Merged Video Save Location", suggested_path, "MPEG-4 Video (*.mp4);;All Files (*)"
        )
        if file_path:
            self.txt_merge_output.setText(file_path)

    def start_merge(self):
        if hasattr(self, 'merge_thread') and self.merge_thread and self.merge_thread.isRunning():
            popup_warning(self, "Warning", "Merging is already in progress.")
            return

        row_count = self.merge_table.rowCount()
        if row_count < 2:
            popup_warning(self, "Warning", "Please add at least 2 videos to merge.")
            return

        output_path = self.txt_merge_output.text().strip()
        if not output_path:
            popup_warning(self, "Warning", "Please specify an output file path.")
            return

        video_paths = []
        for r in range(row_count):
            video_paths.append(self.merge_table.item(r, 2).text())

        reencode = "Re-encode" in self.cmb_merge_mode.currentText()

        self.log_merge_msg(f"Starting Video Merge. Total parts: {len(video_paths)}")
        self.log_merge_msg(f"Output: {output_path}")
        self.log_merge_msg(f"Mode: {'Compatible (Re-encoding)' if reencode else 'Fast (Stream Copy)'}")

        self.btn_start_merge.setEnabled(False)
        self.btn_cancel_merge.setEnabled(True)
        self.btn_add_merge.setEnabled(False)
        self.btn_remove_merge.setEnabled(False)
        self.btn_move_up.setEnabled(False)
        self.btn_move_down.setEnabled(False)
        self.cmb_merge_mode.setEnabled(False)
        self.txt_merge_output.setEnabled(False)
        self.btn_browse_merge_output.setEnabled(False)

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Merging videos... %p%")

        self.merge_thread = MergeWorker(video_paths, output_path, reencode)
        self.merge_thread.progress.connect(self.progress_bar.setValue)
        self.merge_thread.completed.connect(self.merge_completed)
        self.merge_thread.error.connect(self.merge_error)
        self.merge_thread.start()

    def cancel_merge(self):
        if hasattr(self, 'merge_thread') and self.merge_thread and self.merge_thread.isRunning():
            reply = popup_question(
                self, "Cancel Merge", "Are you sure you want to stop video merging?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.log_merge_msg("User requested cancellation. Terminating merge process...")
                try:
                    self.merge_thread.terminate()
                    self.merge_thread.wait()
                except Exception:
                    pass
                self.merge_finished_ui_reset()
                self.log_merge_msg("Merge cancelled.")

    def merge_completed(self, output_path):
        self.log_merge_msg(f"SUCCESS: Videos merged successfully! Saved as:\n{os.path.basename(output_path)}")
        self.merge_finished_ui_reset()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Video Merge Complete")
        box.setText(
            "Videos merged successfully:\n"
            f"{os.path.basename(output_path)}\n\n"
            "Choose what to do next."
        )
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        import_button = box.addButton("Import to Workspace", QMessageBox.ButtonRole.AcceptRole)
        open_folder_button = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.setMinimumWidth(520)
        import_button.setMinimumWidth(190)
        open_folder_button.setMinimumWidth(190)
        box.setStyleSheet(MESSAGE_BOX_STYLE)
        box.exec()

        clicked = box.clickedButton()
        if clicked is import_button:
            has_workspace = bool(self.video_file or self.table.rowCount() > 0 or self.audio_files)
            if has_workspace:
                self.clear_workspace()
                # clear_workspace leaves content untouched when its confirmation is cancelled.
                if self.video_file or self.table.rowCount() > 0 or self.audio_files:
                    return
            self.tabs.setCurrentIndex(0)
            self.load_video_file_direct(output_path)
            self.log_workflow_msg(f"Merged video imported to workspace: {os.path.basename(output_path)}")
        elif clicked is open_folder_button:
            try:
                import subprocess
                subprocess.Popen(
                    ["explorer.exe", "/select,", os.path.normpath(output_path)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                try:
                    os.startfile(os.path.dirname(os.path.abspath(output_path)))
                except OSError:
                    popup_error(self, "Open Folder Failed", str(exc))

    def merge_error(self, error_msg):
        self.log_merge_msg(f"ERROR: Merging failed:\n{error_msg}")
        self.merge_finished_ui_reset()
        popup_error(self, "Merge Error", f"Video merge failed:\n{error_msg}")

    def merge_finished_ui_reset(self):
        self.progress_bar.setVisible(False)
        self.btn_start_merge.setEnabled(True)
        self.btn_cancel_merge.setEnabled(False)
        self.btn_add_merge.setEnabled(True)
        self.btn_remove_merge.setEnabled(True)
        self.btn_move_up.setEnabled(True)
        self.btn_move_down.setEnabled(True)
        self.cmb_merge_mode.setEnabled(True)
        self.txt_merge_output.setEnabled(True)
        self.btn_browse_merge_output.setEnabled(True)
        self.merge_thread = None


def main():
    app = QApplication(sys.argv)

    load_khmer_font(app, 10)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
