"""Bundled Khmer typography used by every Studio entry point."""

from pathlib import Path


FONT_FAMILY = "Noto Sans Khmer"
FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansKhmer-Variable.ttf"


def _font_classes_for_app(app):
    """Return font classes from the same Qt binding as *app*.

    Importing PyQt5 font classes in a process that already owns a PyQt6
    QApplication (or the reverse) can crash inside the native Qt GUI DLLs.
    Keep this helper independent from ``ui.qt_compat`` because that module's
    preferred binding may not match the caller's application.
    """
    app_module = type(app).__module__
    if app_module.startswith("PyQt6."):
        from PyQt6.QtGui import QFont, QFontDatabase

        return QFont, QFontDatabase
    if app_module.startswith("PyQt5."):
        from PyQt5.QtGui import QFont, QFontDatabase

        return QFont, QFontDatabase
    raise TypeError(f"Unsupported QApplication binding: {app_module}")


def load_khmer_font(app, point_size=10):
    """Load the bundled OFL font and make it the application default."""
    QFont, QFontDatabase = _font_classes_for_app(app)
    family = FONT_FAMILY
    try:
        font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            family = families[0]
    except Exception:
        family = "Khmer UI"

    font = QFont(family, point_size)
    style_strategy = getattr(QFont, "StyleStrategy", QFont)
    font.setStyleStrategy(style_strategy.PreferAntialias)
    app.setFont(font)
    return family
