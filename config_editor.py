import json
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Union
import os, signal

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QIntValidator, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

CONFIG_PATH = Path(__file__).with_name("config.json")
LOCK_PATH = Path.home() / ".tr_router.lock"
RELOAD_SIGNAL = signal.SIGUSR1 if hasattr(signal, "SIGUSR1") else signal.SIGHUP

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIVISION_OPTIONS = [
    "1", "1/2", "1/4", "1/8", "1/16", "1/32",
    "1/4d", "1/8d", "1/16d",
    "1/4t", "1/8t", "1/16t",
    "1/4q", "1/8q", "1/16q",
]

STEP_OPTIONS = ["R"] + [str(i) for i in range(1, 9)]


class DragSpinBox(QLineEdit):
    """Numeric input that supports click-drag to change value."""

    def __init__(self, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._validator = QIntValidator(minimum, maximum, self)
        self.setValidator(self._validator)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dragging = False
        self._drag_start_pos: Optional[QPoint] = None
        self._drag_start_val: int = 0
        # single-step increment per 10 px vertical movement
        self._px_per_step = 10

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.globalPosition().toPoint()
            try:
                self._drag_start_val = int(self.text())
            except ValueError:
                self._drag_start_val = self._min
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start_pos is not None:
            dy = self._drag_start_pos.y() - event.globalPosition().toPoint().y()
            steps = dy // self._px_per_step
            new_val = max(self._min, min(self._max, self._drag_start_val + steps))
            self.setText(str(new_val))
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_start_pos = None
            event.accept()
        super().mouseReleaseEvent(event)

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return self._min

    def setValue(self, v: int):
        self.setText(str(max(self._min, min(self._max, v))))


class PatternWidget(QGroupBox):
    """Widget representing a single pattern block."""

    def __init__(self, name: str, cfg: Dict[str, Any], midi_channel: int, parent=None):
        super().__init__(name, parent)
        self.setStyleSheet("QGroupBox { font-weight: bold; margin-top:20px; }")
        self._name = name
        self._data = cfg  # reference maintained
        self._output_channel = midi_channel

        self.top_layout = QHBoxLayout()
        self.length_combo = self._make_combo([str(i) for i in range(1, 17)], str(cfg.get("length", 1)))
        self.octave_combo = self._make_combo([str(i) for i in range(-2, 3)], str(cfg.get("oktawa", 0)))
        self.division_combo = self._make_combo(DIVISION_OPTIONS, cfg.get("division", "1/16"))
        self.channel_combo = self._make_combo([str(i) for i in range(1, 17)], str(midi_channel))

        self.top_layout.addWidget(QLabel("Length"))
        self.top_layout.addWidget(self.length_combo)
        self.top_layout.addWidget(QLabel("Octave"))
        self.top_layout.addWidget(self.octave_combo)
        self.top_layout.addWidget(QLabel("Division"))
        self.top_layout.addWidget(self.division_combo)
        self.top_layout.addWidget(QLabel("MIDI ch"))
        self.top_layout.addWidget(self.channel_combo)
        self.top_layout.addStretch()

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4)

        grid_container = QWidget()
        grid_container.setLayout(self.grid)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(grid_container)

        main_layout = QVBoxLayout()
        main_layout.addLayout(self.top_layout)
        main_layout.addWidget(grid_scroll)
        self.setLayout(main_layout)

        self._build_grid()
        self.length_combo.currentTextChanged.connect(self._build_grid)

    def _make_combo(self, options: List[str], current: str) -> QComboBox:
        box = QComboBox()
        for opt in options:
            box.addItem(opt)
        idx = box.findText(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
        box.setEditable(False)
        return box

    # --------------------------------- grid ---------------------------------

    def _build_grid(self, *_args):
        # clear existing widgets
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        length = int(self.length_combo.currentText())
        # Ensure arrays have correct length
        for key, default_val in (
            ("steps", "R"),
            ("velocity", 100),
            ("v-random", 0),
            ("gate", 100),
        ):
            arr = self._data.get(key, [])
            if len(arr) < length:
                arr += [default_val] * (length - len(arr))
            self._data[key] = arr[:length]
        # Labels row
        headers = [str(i + 1) for i in range(length)]
        for col, lbl in enumerate(headers):
            self.grid.addWidget(QLabel(lbl), 0, col + 1, alignment=Qt.AlignmentFlag.AlignCenter)
        # Steps row
        self.grid.addWidget(QLabel("Steps"), 1, 0)
        for col in range(length):
            box = QComboBox()
            box.addItems(STEP_OPTIONS)
            current = str(self._data["steps"][col])
            idx = box.findText(current)
            if idx >= 0:
                box.setCurrentIndex(idx)
            self.grid.addWidget(box, 1, col + 1)
        # Velocity row
        self.grid.addWidget(QLabel("Velocity"), 2, 0)
        for col in range(length):
            spin = DragSpinBox(1, 127)
            spin.setValue(int(self._data["velocity"][col]))
            self.grid.addWidget(spin, 2, col + 1)
        # v-random row
        self.grid.addWidget(QLabel("V-Random"), 3, 0)
        for col in range(length):
            spin = DragSpinBox(0, 100)
            spin.setValue(int(self._data["v-random"][col]))
            self.grid.addWidget(spin, 3, col + 1)
        # Gate row
        self.grid.addWidget(QLabel("Gate"), 4, 0)
        for col in range(length):
            spin = DragSpinBox(0, 100)
            val = self._data["gate"][col]
            if isinstance(val, str) and val.upper() == "T":
                spin.setText("T")
            else:
                spin.setValue(int(val))
            self.grid.addWidget(spin, 4, col + 1)

    # --------------------------------- export --------------------------------

    def export_data(self) -> Tuple[int, Dict[str, Any]]:
        length = int(self.length_combo.currentText())
        octave = int(self.octave_combo.currentText())
        division = self.division_combo.currentText()
        midi_ch = int(self.channel_combo.currentText())

        steps: List[Union[str, int]] = []
        velocity: List[int] = []
        vrand: List[int] = []
        gate: List[Union[int, str]] = []

        # Extract from grid widgets row by row
        # Steps row (row 1)
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(1, col + 1).widget()  # type: ignore
            steps.append(box.currentText())
        # Velocity row (row 2)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(2, col + 1).widget()  # type: ignore
            velocity.append(spin.value())
        # v-random row (row 3)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(3, col + 1).widget()  # type: ignore
            vrand.append(spin.value())
        # Gate row (row 4)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(4, col + 1).widget()  # type: ignore
            text = spin.text().strip().upper()
            if text == "T":
                gate.append("T")
            else:
                try:
                    gate.append(int(text))
                except ValueError:
                    gate.append(100)

        data = {
            "length": length,
            "steps": steps,
            "velocity": velocity,
            "v-random": vrand,
            "gate": gate,
            "oktawa": octave,
            "division": division,
        }
        return midi_ch, data


class ConfigEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TR Performer Config Editor")
        self.resize(1000, 600)

        self.pattern_widgets: Dict[str, PatternWidget] = {}
        self._load_config(CONFIG_PATH)
        self._build_menu()

    # ------------------------------- UI build ------------------------------

    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        save_act = QAction("Save", self)
        save_act.triggered.connect(self.save_current)
        save_as_act = QAction("Save As…", self)
        save_as_act.triggered.connect(self.save_as)
        open_act = QAction("Open Preset…", self)
        open_act.triggered.connect(self.open_preset)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.close)

        file_menu.addAction(open_act)
        file_menu.addAction(save_act)
        file_menu.addAction(save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

    # --------------------------- config handling ---------------------------

    def _load_config(self, path: Path):
        if not path.exists():
            QMessageBox.critical(self, "Error", f"File not found:\n{path}")
            return
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = fh.read()
            # Strip // comments (same logic as midi_router)
            clean_lines = []
            for line in raw.splitlines():
                if "//" in line and line.split("//")[0].count("\"") % 2 == 0:
                    line = line.split("//", 1)[0]
                clean_lines.append(line)
            data = json.loads("\n".join(clean_lines))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load JSON:\n{e}")
            return

        self._input_channel = int(data.get("input_channel", 1))
        self._output_channels: Dict[str, int] = data.get("output_channels", {})
        patterns: Dict[str, Dict[str, Any]] = data.get("patterns", {})

        # Build scrollable area for patterns
        central = QWidget()
        vbox = QVBoxLayout()
        central.setLayout(vbox)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout()
        inner.setLayout(inner_layout)
        scroll.setWidget(inner)
        vbox.addWidget(scroll)
        self.setCentralWidget(central)

        # Create pattern widgets
        for pname, midi_ch in self._output_channels.items():
            pcfg = patterns.get(pname, {})
            pw = PatternWidget(pname, pcfg, midi_ch)
            inner_layout.addWidget(pw)
            self.pattern_widgets[pname] = pw
        inner_layout.addStretch()

    # ------------------------------ actions --------------------------------

    def _collect_config(self) -> Dict[str, Any]:
        out_channels: Dict[str, int] = {}
        patterns: Dict[str, Dict[str, Any]] = {}
        for pname, pw in self.pattern_widgets.items():
            midi_ch, cfg = pw.export_data()
            out_channels[pname] = midi_ch
            patterns[pname] = cfg
        return {
            "input_channel": self._input_channel,
            "output_channels": out_channels,
            "patterns": patterns,
        }

    def save_current(self):
        data = self._collect_config()
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2))
            QMessageBox.information(self, "Saved", f"Saved to {CONFIG_PATH}")
            self._notify_router()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save preset", str(CONFIG_PATH.parent), "JSON (*.json)")
        if not path:
            return
        data = self._collect_config()
        try:
            Path(path).write_text(json.dumps(data, indent=2))
            self._notify_router()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open preset", str(CONFIG_PATH.parent), "JSON (*.json)")
        if not path:
            return
        # Clear current UI and reload
        self.pattern_widgets.clear()
        self.centralWidget().deleteLater()
        self._load_config(Path(path))

    # --------------------------- router reload -----------------------------

    def _notify_router(self):
        """Send signal to running midi_router process prompting config reload."""
        try:
            if LOCK_PATH.exists():
                pid = int(LOCK_PATH.read_text())
                os.kill(pid, RELOAD_SIGNAL)
        except Exception:
            # Non-fatal – router may not be running
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication([])
    editor = ConfigEditor()
    editor.show()
    app.exec()


if __name__ == "__main__":
    main() 