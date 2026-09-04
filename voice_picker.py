# -*- coding: utf-8 -*-
"""
VoiceSelectionPopup and VoiceCellButton module for Dubber Studio Pro
Matching the Facebook Reel reference design:
- Gender badges (♂ Male / ♀ Female)
- Search voice input
- Category filters (All / Male / Female)
- Rich cards with description and inline audio preview (▶)
"""

import os
import sys
import time
import asyncio
import edge_tts
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame, QDialog, QApplication
)

# Standard Khmer & Global Voice Registry with Metadata
VOICE_METADATA = {
    "Auto (Piseth / Sreymom)": {"gender": "Auto", "desc": "Auto Gender Detection • Dynamic", "lang": "Khmer"},
    "Female - Sreymom": {"gender": "Female", "desc": "Khmer Female • Warm & Natural", "lang": "Khmer"},
    "Male - Piseth": {"gender": "Male", "desc": "Khmer Male • Clear & Professional", "lang": "Khmer"},
    "Male - Sopheap": {"gender": "Male", "desc": "Khmer Male • Deep Narrative", "lang": "Khmer"},
    "Female - Chamroeun": {"gender": "Female", "desc": "Khmer Female • Emotional Story", "lang": "Khmer"},
    "Female 1 (Soft)": {"gender": "Female", "desc": "Soft & Conversational", "lang": "Khmer"},
    "Female 2 (Vibrant)": {"gender": "Female", "desc": "Youthful & Energetic", "lang": "Khmer"},
    "Male 1 (Deep Movie)": {"gender": "Male", "desc": "Cinematic Movie Narrator", "lang": "Khmer"},
    "Male 2 (Documentary)": {"gender": "Male", "desc": "Calm & Authoritative", "lang": "Khmer"},
    "VoxCPM Auto Voice": {"gender": "Auto", "desc": "Neural Clone • Dynamic Movie Dub", "lang": "All"},
    "VoxCPM Female Clone": {"gender": "Female", "desc": "Neural Clone • Female Target", "lang": "All"},
    "VoxCPM Male Clone": {"gender": "Male", "desc": "Neural Clone • Male Target", "lang": "All"},
    "VoxCPM Cloned Voice": {"gender": "Auto", "desc": "Custom Reference Audio Dub", "lang": "All"},
    "ElevenLabs Cloned Voice": {"gender": "Auto", "desc": "Custom ElevenLabs Model", "lang": "All"},
    # English
    "Auto (Brian / Emma)": {"gender": "Auto", "desc": "Auto Gender Detection", "lang": "English"},
    "Female - Alice": {"gender": "Female", "desc": "English Female • Story", "lang": "English"},
    "Male - Bob": {"gender": "Male", "desc": "English Male • Confident", "lang": "English"},
    # Chinese
    "Auto (Yunjian / Xiaoxiao)": {"gender": "Auto", "desc": "Auto Gender Detection", "lang": "Chinese"},
    "Female - Xiaoxiao": {"gender": "Female", "desc": "Chinese Female • Expressive", "lang": "Chinese"},
    "Male - Yunjian": {"gender": "Male", "desc": "Chinese Male • Storyteller", "lang": "Chinese"},
}

def get_voice_info(name):
    if name in VOICE_METADATA:
        return VOICE_METADATA[name]
    name_lower = name.lower()
    if "female" in name_lower or "sreymom" in name_lower or "chamroeun" in name_lower or "alice" in name_lower or "xiaoxiao" in name_lower:
        return {"gender": "Female", "desc": "Female Voice Dub", "lang": "Other"}
    elif "male" in name_lower or "piseth" in name_lower or "sopheap" in name_lower or "bob" in name_lower or "yunjian" in name_lower:
        return {"gender": "Male", "desc": "Male Voice Dub", "lang": "Other"}
    return {"gender": "Auto", "desc": "Auto / Custom Voice", "lang": "Other"}


class VoiceSelectionPopup(QDialog):
    """Modern floating voice picker popup dialog matching the reference video."""
    voice_selected = pyqtSignal(str)

    def __init__(self, current_voice, voice_list, parent=None, on_preview=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.current_voice = current_voice
        self.voice_list = voice_list
        self.on_preview = on_preview
        self.active_filter = "All"
        self.search_text = ""
        self.init_ui()

    def init_ui(self):
        self.setFixedWidth(340)
        self.setMaximumHeight(390)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Popup card container
        container = QFrame()
        container.setObjectName("voicePopupContainer")
        container.setStyleSheet("""
            QFrame#voicePopupContainer {
                background-color: #0B1120;
                border: 1px solid #0284C7;
                border-radius: 8px;
            }
        """)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(6)

        # 1. Search Bar
        search_layout = QHBoxLayout()
        search_layout.setSpacing(4)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Search voice...")
        self.txt_search.setStyleSheet("""
            QLineEdit {
                background-color: #070B16;
                color: #F8FAFC;
                border: 1px solid #1E293B;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px; font-weight: 600;
            }
            QLineEdit:focus {
                border-color: #38BDF8;
            }
        """)
        self.txt_search.textChanged.connect(self.on_search_changed)
        search_layout.addWidget(self.txt_search, stretch=1)

        btn_clear = QPushButton("✕")
        btn_clear.setFixedSize(20, 20)
        btn_clear.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #64748B;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover { color: #F8FAFC; }
        """)
        btn_clear.clicked.connect(lambda: self.txt_search.clear())
        search_layout.addWidget(btn_clear)
        container_layout.addLayout(search_layout)

        # 2. Filter Pills Bar (All, Male, Female)
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(4)

        male_count = 0
        female_count = 0
        for v in self.voice_list:
            info = get_voice_info(v)
            if info["gender"] == "Male":
                male_count += 1
            elif info["gender"] == "Female":
                female_count += 1

        self.btn_filter_all = QPushButton(f"All {len(self.voice_list)}")
        self.btn_filter_male = QPushButton(f"♂ Male {male_count}")
        self.btn_filter_female = QPushButton(f"♀ Female {female_count}")

        for btn in [self.btn_filter_all, self.btn_filter_male, self.btn_filter_female]:
            btn.setFixedHeight(22)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.btn_filter_all.clicked.connect(lambda: self.set_filter("All"))
        self.btn_filter_male.clicked.connect(lambda: self.set_filter("Male"))
        self.btn_filter_female.clicked.connect(lambda: self.set_filter("Female"))

        filter_layout.addWidget(self.btn_filter_all)
        filter_layout.addWidget(self.btn_filter_male)
        filter_layout.addWidget(self.btn_filter_female)
        container_layout.addLayout(filter_layout)
        self.update_filter_styles()

        # 3. Scrollable List of Voice Cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: #070B16; width: 6px; border-radius: 3px; margin: 0px 1px 0px 0px; }
            QScrollBar::handle:vertical { background: #334155; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #38BDF8; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        """)

        self.cards_widget = QWidget()
        self.cards_widget.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(2, 2, 8, 2)
        self.cards_layout.setSpacing(3)
        self.scroll_area.setWidget(self.cards_widget)
        container_layout.addWidget(self.scroll_area, stretch=1)

        # 4. Footer tip
        lbl_tip = QLabel("• Click to select  |  ▶ to preview voice")
        lbl_tip.setStyleSheet("color: #64748B; font-size: 10.5px; font-weight: 600; padding: 2px;")
        lbl_tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(lbl_tip)

        main_layout.addWidget(container)
        self.rebuild_voice_cards()

    def update_filter_styles(self):
        base_style = "border-radius: 4px; font-weight: bold; font-size: 11.5px; font-weight: 700; padding: 2px 6px;"
        
        # All
        if self.active_filter == "All":
            self.btn_filter_all.setStyleSheet(f"background-color: #0284C7; color: #FFFFFF; border: none; {base_style}")
        else:
            self.btn_filter_all.setStyleSheet(f"background-color: #0F172A; color: #94A3B8; border: 1px solid #1E293B; {base_style}")

        # Male
        if self.active_filter == "Male":
            self.btn_filter_male.setStyleSheet(f"background-color: #0891B2; color: #FFFFFF; border: none; {base_style}")
        else:
            self.btn_filter_male.setStyleSheet(f"background-color: #081E2E; color: #38BDF8; border: 1px solid #0369A1; {base_style}")

        # Female
        if self.active_filter == "Female":
            self.btn_filter_female.setStyleSheet(f"background-color: #DB2777; color: #FFFFFF; border: none; {base_style}")
        else:
            self.btn_filter_female.setStyleSheet(f"background-color: #1F1022; color: #F472B6; border: 1px solid #701A75; {base_style}")

    def set_filter(self, filter_name):
        self.active_filter = filter_name
        self.update_filter_styles()
        self.rebuild_voice_cards()

    def on_search_changed(self, text):
        self.search_text = text.strip().lower()
        self.rebuild_voice_cards()

    def rebuild_voice_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        filtered_voices = []
        for v in self.voice_list:
            info = get_voice_info(v)
            if self.active_filter != "All" and info["gender"] != self.active_filter and info["gender"] != "Auto":
                continue
            if self.search_text:
                if self.search_text not in v.lower() and self.search_text not in info["desc"].lower():
                    continue
            filtered_voices.append((v, info))

        if not filtered_voices:
            lbl_empty = QLabel("No matching voices found")
            lbl_empty.setStyleSheet("color: #64748B; font-size: 11.5px; font-weight: 700; padding: 12px;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(lbl_empty)
            self.cards_layout.addStretch()
            return

        auto_voices = [x for x in filtered_voices if x[1]["gender"] == "Auto"]
        male_voices = [x for x in filtered_voices if x[1]["gender"] == "Male"]
        female_voices = [x for x in filtered_voices if x[1]["gender"] == "Female"]

        if auto_voices:
            self.add_section_header("⚡ AUTO & CLONE VOICES", "#A78BFA")
            for name, info in auto_voices:
                self.add_voice_card(name, info)

        if male_voices:
            self.add_section_header(f"♂ MALE VOICES ({len(male_voices)})", "#38BDF8")
            for name, info in male_voices:
                self.add_voice_card(name, info)

        if female_voices:
            self.add_section_header(f"♀ FEMALE VOICES ({len(female_voices)})", "#F472B6")
            for name, info in female_voices:
                self.add_voice_card(name, info)

        self.cards_layout.addStretch()

    def add_section_header(self, text, color):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 800; font-weight: bold; padding: 4px 2px 2px 2px;")
        self.cards_layout.addWidget(lbl)

    def add_voice_card(self, name, info):
        card = QFrame()
        card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        is_selected = (name == self.current_voice)
        gender = info["gender"]

        if gender == "Female":
            border_col = "#EC4899" if is_selected else "#331E38"
            bg_col = "#2A142E" if is_selected else "#150A1A"
            icon_char = "♀"
            icon_col = "#F472B6"
        elif gender == "Male":
            border_col = "#0284C7" if is_selected else "#1A2B42"
            bg_col = "#0D2538" if is_selected else "#091422"
            icon_char = "♂"
            icon_col = "#38BDF8"
        else:
            border_col = "#7C3AED" if is_selected else "#281E44"
            bg_col = "#1E1438" if is_selected else "#0E091E"
            icon_char = "🤖"
            icon_col = "#A78BFA"

        card.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 5px;
            }}
            QFrame:hover {{
                border-color: #38BDF8;
                background-color: #1E293B;
            }}
        """)

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(6, 4, 6, 4)
        card_layout.setSpacing(6)

        # Gender badge / avatar
        lbl_gender = QLabel(icon_char)
        lbl_gender.setStyleSheet(f"color: {icon_col}; font-size: 13px; font-weight: bold;")
        lbl_gender.setFixedWidth(16)
        card_layout.addWidget(lbl_gender)

        # Name and description
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        lbl_name = QLabel(name)
        lbl_name.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 12px; font-weight: 600;")
        text_layout.addWidget(lbl_name)

        lbl_desc = QLabel(info["desc"])
        lbl_desc.setStyleSheet("color: #94A3B8; font-size: 10.5px; font-weight: 600;")
        text_layout.addWidget(lbl_desc)

        card_layout.addLayout(text_layout, stretch=1)

        # Selected checkmark
        if is_selected:
            lbl_check = QLabel("✔")
            lbl_check.setStyleSheet(f"color: {icon_col}; font-weight: bold; font-size: 11px;")
            card_layout.addWidget(lbl_check)

        # Preview Button (▶)
        btn_preview = QPushButton("▶")
        btn_preview.setToolTip("Play voice sample (ស្តាប់សាកល្បង)")
        btn_preview.setFixedSize(26, 26)
        btn_preview.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_preview.setStyleSheet("""
            QPushButton {
                background-color: #16A34A;
                color: #FFFFFF;
                border: none;
                border-radius: 13px;
                font-size: 11.5px;
                font-weight: bold;
                padding-left: 2px;
            }
            QPushButton:hover {
                background-color: #22C55E;
            }
            QPushButton:pressed {
                background-color: #15803D;
            }
        """)
        def _on_btn_click(checked, n=name, b=btn_preview):
            b.setText("🔊")
            self.play_preview_sample(n)
            QTimer.singleShot(2500, lambda: b.setText("▶"))

        btn_preview.clicked.connect(_on_btn_click)
        card_layout.addWidget(btn_preview)

        # Click on card selects voice
        card.mousePressEvent = lambda event, n=name: self.select_voice(n)

        self.cards_layout.addWidget(card)

    def select_voice(self, name):
        self.voice_selected.emit(name)
        self.accept()

    def play_preview_sample(self, name):
        if self.on_preview:
            self.on_preview(name)


class VoiceCellButton(QPushButton):
    """Custom interactive table cell button that opens the VoiceSelectionPopup on click."""
    voice_changed = pyqtSignal(str)

    def __init__(self, voice_name="Auto (Piseth / Sreymom)", parent_window=None, row_idx=0):
        super().__init__()
        self.parent_window = parent_window
        self.row_idx = row_idx
        self.voice_name = voice_name
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(28)
        self.update_appearance()
        self.clicked.connect(self.show_popup)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.show_quick_gender_menu(event.globalPosition().toPoint())
            return
        super().mousePressEvent(event)

    def show_quick_gender_menu(self, global_pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #38BDF8;
                padding: 4px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 4px;
                font-weight: 600;
            }
            QMenu::item:selected {
                background-color: #0284C7;
                color: #FFFFFF;
            }
        """)
        curr_info = get_voice_info(self.voice_name)
        curr_g = curr_info.get("gender", "")
        
        act_swap = menu.addAction("🔄 Swap Gender (ប្តូរ ប្រុស <-> ស្រី)")
        menu.addSeparator()
        act_male = menu.addAction("👨 Male - Piseth (តួប្រុស)")
        act_female = menu.addAction("👩 Female - Sreymom (តួស្រី)")
        
        chosen = menu.exec(global_pos)
        if chosen == act_swap:
            if curr_g == "Female":
                self.on_voice_selected("Male - Piseth")
            else:
                self.on_voice_selected("Female - Sreymom")
        elif chosen == act_male:
            self.on_voice_selected("Male - Piseth")
        elif chosen == act_female:
            self.on_voice_selected("Female - Sreymom")

    def set_voice(self, voice_name):
        self.voice_name = voice_name
        self.update_appearance()

    def currentText(self):
        return self.voice_name

    def update_appearance(self):
        info = get_voice_info(self.voice_name)
        gender = info["gender"]

        if gender == "Female":
            icon = "♀"
            border_col = "#701A75"
            bg_col = "#1F1022"
            text_col = "#F472B6"
            hover_bg = "#2A142E"
        elif gender == "Male":
            icon = "♂"
            border_col = "#0369A1"
            bg_col = "#081E2E"
            text_col = "#38BDF8"
            hover_bg = "#0D2538"
        else:
            icon = "🤖"
            border_col = "#6D28D9"
            bg_col = "#1A102E"
            text_col = "#C4B5FD"
            hover_bg = "#241840"

        disp_name = self.voice_name
        if len(disp_name) > 18:
            disp_name = disp_name[:16] + ".."

        self.setText(f" {icon} {disp_name}  ▾")
        self.setToolTip(f"Voice: {self.voice_name} ({info['desc']})\nClick to change voice")

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_col};
                color: {text_col};
                border: 1px solid {border_col};
                border-radius: 4px;
                padding: 2px 6px;
                font-weight: 600;
                font-size: 11.5px; font-weight: 700;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: #38BDF8;
            }}
        """)

    def show_popup(self):
        voices = self.parent_window.voice_choices_for_language() if self.parent_window else list(VOICE_METADATA.keys())
        popup = VoiceSelectionPopup(
            current_voice=self.voice_name,
            voice_list=voices,
            parent=self,
            on_preview=self.handle_preview
        )
        popup.voice_selected.connect(self.on_voice_selected)

        # Position popup directly under the cell button
        btn_pos = self.mapToGlobal(QPoint(0, self.height() + 2))
        popup.move(btn_pos)
        popup.exec()

    def on_voice_selected(self, new_voice):
        if new_voice != self.voice_name:
            self.set_voice(new_voice)
            self.voice_changed.emit(new_voice)
            if self.parent_window and hasattr(self.parent_window, "row_voice_changed"):
                self.parent_window.row_voice_changed(self.row_idx)

    def handle_preview(self, voice_name):
        if self.parent_window and hasattr(self.parent_window, "preview_voice_by_name"):
            self.parent_window.preview_voice_by_name(voice_name)
        elif self.parent_window and hasattr(self.parent_window, "log_workflow_msg"):
            self.parent_window.log_workflow_msg(f"Voice preview: {voice_name}")
