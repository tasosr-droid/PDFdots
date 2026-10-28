import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from pdfdots.main_window import MainWindow
from pdfdots.resources import resource_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Dots v0.4 by Tasos Rizopoulos 2026")
    app.setWindowIcon(QIcon(str(resource_path("dots.png"))))

    window = MainWindow()
    window.show()

    return app.exec()
