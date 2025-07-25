import json
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Union
import os, signal
from dataclasses import asdict

from PyQt6.QtCore import Qt, QPoint, QSize
from PyQt6.QtGui import QIntValidator, QAction, QIcon
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
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolBar,
    QToolButton,
    QCheckBox,
    QSlider,
    QVBoxLayout,
    QWidget,
    QStyle,
    QSizePolicy,
    QGraphicsOpacityEffect,
)
from dataclasses import dataclass, field

CONFIG_PATH = Path(__file__).with_name("config.json")
PRESET_DIR = (Path(__file__).resolve().parent / "presets")
PRESET_DIR.mkdir(exist_ok=True)
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

STEP_OPTIONS = ["X", "R"] + [str(i) for i in range(1, 9)]


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


# ---------------------------------------------------------------------------
# Gate input supporting numeric 0-100 plus special 'T'
# ---------------------------------------------------------------------------


class GateSpinBox(QLineEdit):
    """Input for Gate that supports 0-100 and special 'T', with drag behaviour."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dragging = False
        self._drag_start_pos: Optional[QPoint] = None
        self._drag_start_val: int = 0  # 0-101 where 101 represents 'T'
        self._px_per_step = 10

    # ----------------------- helpers -----------------------

    def _text_to_val(self) -> int:
        txt = self.text().strip().upper()
        if txt == "T":
            return 101
        try:
            return max(0, min(100, int(txt)))
        except ValueError:
            return 0

    def _val_to_text(self, val: int):
        if val > 100:
            self.setText("T")
        else:
            self.setText(str(max(0, min(100, val))))

    # -------------------- mouse events ---------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.globalPosition().toPoint()
            self._drag_start_val = self._text_to_val()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start_pos is not None:
            dy = self._drag_start_pos.y() - event.globalPosition().toPoint().y()
            steps = dy // self._px_per_step
            new_val = self._drag_start_val + steps
            # normalise range: val <=100 numeric; >100 treated as T cap
            if new_val > 100:
                new_val = 101
            elif new_val < 0:
                new_val = 0
            self._val_to_text(new_val)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_start_pos = None
            event.accept()
        super().mouseReleaseEvent(event)

    # ---------------------- API ----------------------------

    def value_text(self) -> str:
        """Return 'T' or numeric string"""
        t = self.text().strip().upper()
        if t == "T":
            return "T"
        try:
            v = int(t)
            return str(max(0, min(100, v)))
        except ValueError:
            return "100"

    # Provide numeric value (T mapped to 101) for compatibility
    def value(self) -> int:
        vt = self.value_text()
        if vt == "T":
            return 101
        return int(vt)


class PatternWidget(QGroupBox):
    """Widget representing a single pattern block."""

    def __init__(self, name: str, cfg: Dict[str, Any], midi_channel: int, parent=None):
        super().__init__("", parent)  # title handled manually via label
        self.setStyleSheet("QGroupBox { margin-top:20px; }")
        self._name = name
        self._data = cfg  # reference maintained
        self._output_channel = midi_channel
        # ------------------------------------------------------------------
        # Enabled toggle (power icon)
        # ------------------------------------------------------------------
        self._enabled: bool = bool(cfg.get("enabled", True))

        power_icon_path = Path(__file__).resolve().parent / "icons" / "ic_power.svg"
        self.enable_btn = QToolButton()
        self.enable_btn.setIcon(QIcon(str(power_icon_path)))
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(self._enabled)
        # Subtle visual cue: dim icon when unchecked (disabled)
        self.enable_btn.setStyleSheet(
            "QToolButton { border: none; padding:0px; }\n"
            "QToolButton:!checked { opacity: 0.3; }"
        )
        self.enable_btn.clicked.connect(self._on_enabled_toggled)  # type: ignore[arg-type]

        # Apply opacity effect to whole widget
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._update_opacity()  # type: ignore[attr-defined]

        self.top_layout = QHBoxLayout()

        # Pattern name label on the left
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight: bold;")

        # Add power button then name label
        self.top_layout.addWidget(self.enable_btn)
        self.top_layout.addWidget(name_lbl)

        # Center section
        self.top_layout.addStretch()

        self.length_combo = self._make_combo([str(i) for i in range(1, 17)], str(cfg.get("length", 1)))
        self.octave_combo = self._make_combo([str(i) for i in range(-2, 3)], str(cfg.get("oktawa", 0)))
        self.division_combo = self._make_combo(DIVISION_OPTIONS, cfg.get("division", "1/16"))

        self.top_layout.addWidget(QLabel("Length"))
        self.top_layout.addWidget(self.length_combo)
        self.top_layout.addWidget(QLabel("Octave"))
        self.top_layout.addWidget(self.octave_combo)
        self.top_layout.addWidget(QLabel("Division"))
        self.top_layout.addWidget(self.division_combo)

        # Right section
        self.top_layout.addStretch()

        self.channel_combo = self._make_combo([str(i) for i in range(1, 17)], str(midi_channel))
        self.top_layout.addWidget(QLabel("MIDI ch"))
        self.top_layout.addWidget(self.channel_combo)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        self.grid.setContentsMargins(8, 0, 0, 0)

        self.grid_container = QWidget()
        self.grid_container.setLayout(self.grid)
        self.grid_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        main_layout = QVBoxLayout()
        main_layout.addLayout(self.top_layout)
        main_layout.addWidget(self.grid_container)
        self.setLayout(main_layout)

        self._build_grid()
        self.length_combo.currentTextChanged.connect(self._build_grid)  # type: ignore[arg-type]

        # adjust container width after adding widgets
        if hasattr(self, "grid_container"):
            self.grid_container.setMinimumWidth(self.grid_container.sizeHint().width())

    # ------------------------------------------------------------------
    # Enabled helper & handler
    # ------------------------------------------------------------------

    def _on_enabled_toggled(self, checked: bool):
        self._enabled = checked
        # persist to backing data so export/save reflects change immediately
        self._data["enabled"] = self._enabled
        self._update_opacity()  # type: ignore[attr-defined]
        # Keep interactions possible even when disabled
        self.setEnabled(True)

    def is_enabled(self) -> bool:
        return self._enabled

    def _update_opacity(self) -> None:
        """Adjust QGraphicsOpacityEffect according to enabled flag."""
        if hasattr(self, "_opacity_effect"):
            self._opacity_effect.setOpacity(1.0 if self._enabled else 0.5)
        # also update button checked state to stay in sync (in case called externally)
        if hasattr(self, "enable_btn"):
            self.enable_btn.setChecked(self._enabled)
        self.update()

    # ------------------------------------------------------------------
    # External setter for enabled flag (used by master toggle)
    # ------------------------------------------------------------------

    def set_enabled(self, flag: bool) -> None:
        """Programmatically enable/disable pattern without altering controls."""
        self._enabled = bool(flag)
        # update underlying data so export_data reflects new state
        self._data["enabled"] = self._enabled
        self._update_opacity()

    def _make_combo(self, options: List[str], current: str) -> QComboBox:
        box = QComboBox()
        for opt in options:
            box.addItem(opt)
        idx = box.findText(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
        box.setEditable(False)
        return box

    def _capture_grid_state(self):
        """Read values from existing grid widgets and store them into self._data before grid is rebuilt.
        This preserves user edits when the pattern length changes."""
        if self.grid.count() == 0:
            # Grid hasn't been built yet
            return

        prev_length = len(self._data.get("steps", []))
        if prev_length == 0:
            return

        # Helper to safely fetch widget at given position
        def _widget(row: int, col: int):
            item = self.grid.itemAtPosition(row, col)
            return item.widget() if item else None

        # Steps row (row 1)
        new_steps = []
        for col in range(prev_length):
            box = _widget(1, col + 1)
            if isinstance(box, QComboBox):
                txt = box.currentText().strip()
                txt_up = txt.upper()
                if txt_up in ("R", "X"):
                    new_steps.append(txt_up)
                else:
                    try:
                        new_steps.append(int(txt))
                    except ValueError:
                        new_steps.append(txt)
        if new_steps:
            self._data["steps"] = new_steps

        # Velocity row (row 2)
        new_velocity = []
        for col in range(prev_length):
            spin = _widget(2, col + 1)
            if isinstance(spin, DragSpinBox):
                new_velocity.append(spin.value())
        if new_velocity:
            self._data["velocity"] = new_velocity

        # v-random row (row 3)
        new_vrand = []
        for col in range(prev_length):
            spin = _widget(3, col + 1)
            if isinstance(spin, DragSpinBox):
                new_vrand.append(spin.value())
        if new_vrand:
            self._data["v-random"] = new_vrand

        # s-prob row (row 4)
        new_sprob = []
        for col in range(prev_length):
            spin = _widget(4, col + 1)
            if isinstance(spin, DragSpinBox):
                new_sprob.append(spin.value())
        if new_sprob:
            self._data["s-prob"] = new_sprob

        # s-oct row (row 5)
        new_soct = []
        for col in range(prev_length):
            box = _widget(5, col + 1)
            if isinstance(box, QComboBox):
                try:
                    new_soct.append(int(box.currentText()))
                except ValueError:
                    new_soct.append(0)
        if new_soct:
            self._data["s-oct"] = new_soct

        # r-oct row (row 6)
        new_roct = []
        for col in range(prev_length):
            box = _widget(6, col + 1)
            if isinstance(box, QComboBox):
                new_roct.append(box.currentText())
        if new_roct:
            self._data["r-oct"] = new_roct

        # Gate row (row 7)
        new_gate = []
        for col in range(prev_length):
            spin = _widget(7, col + 1)
            if isinstance(spin, GateSpinBox):
                txt = spin.value_text()
                if txt == "T":
                    new_gate.append("T")
                else:
                    try:
                        new_gate.append(int(txt))
                    except ValueError:
                        new_gate.append(100)
        if new_gate:
            self._data["gate"] = new_gate

    # --------------------------------- grid ---------------------------------

    def _build_grid(self, _changed: Optional[str] = None) -> None:
        # Capture current values before rebuilding so user edits are preserved
        self._capture_grid_state()

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
            ("s-prob", 100),
            ("s-oct", 0),
            ("r-oct", "0"),
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
        self.grid.addWidget(QLabel("Step"), 1, 0)
        for col in range(length):
            box = QComboBox()
            box.setFixedWidth(70)
            for opt in STEP_OPTIONS:
                box.addItem(opt)
            current = str(self._data["steps"][col])
            idx = box.findText(current)
            if idx >= 0:
                box.setCurrentIndex(idx)
            self.grid.addWidget(box, 1, col + 1)
        # Velocity row
        self.grid.addWidget(QLabel("Velocity"), 2, 0)
        for col in range(length):
            spin = DragSpinBox(1, 127)
            spin.setFixedWidth(70)
            spin.setValue(int(self._data["velocity"][col]))
            self.grid.addWidget(spin, 2, col + 1)
        # v-random row
        self.grid.addWidget(QLabel("V-Random"), 3, 0)
        for col in range(length):
            spin = DragSpinBox(0, 100)
            spin.setFixedWidth(70)
            spin.setValue(int(self._data["v-random"][col]))
            self.grid.addWidget(spin, 3, col + 1)
        # s-prob row
        self.grid.addWidget(QLabel("S-Prob"), 4, 0)
        for col in range(length):
            spin = DragSpinBox(0, 100)
            spin.setFixedWidth(70)
            spin.setValue(int(self._data["s-prob"][col]))
            self.grid.addWidget(spin, 4, col + 1)
        # s-oct row
        self.grid.addWidget(QLabel("S-Oct"), 5, 0)
        for col in range(length):
            box = QComboBox()
            box.setFixedWidth(70)
            box.addItems([str(i) for i in range(-2,3)])
            cur = str(self._data.get("s-oct", [0]*length)[col])
            idx = box.findText(cur)
            if idx>=0:
                box.setCurrentIndex(idx)
            self.grid.addWidget(box,5,col+1)
        # r-oct row
        self.grid.addWidget(QLabel("R-Oct"), 6, 0)
        OCT_OPTIONS = ["0","+1","+2","-1","-2","+-1","+-2"]
        for col in range(length):
            box = QComboBox()
            box.setFixedWidth(70)
            box.addItems(OCT_OPTIONS)
            cur = str(self._data.get("r-oct", ["0"]*length)[col])
            idx = box.findText(cur)
            if idx>=0:
                box.setCurrentIndex(idx)
            self.grid.addWidget(box,6,col+1)
        # Gate row
        self.grid.addWidget(QLabel("Gate"), 7, 0)
        for col in range(length):
            spin = GateSpinBox()
            spin.setFixedWidth(70)
            val = self._data["gate"][col]
            if isinstance(val, str) and val.upper() == "T":
                spin.setText("T")
            else:
                spin.setText(str(int(val)))
            self.grid.addWidget(spin, 7, col + 1)

        # Dodaj elastyczną pustą kolumnę, aby wiersze kroków zawsze były
        # wyrównane do lewej, a wolne miejsce „rozpychało się” na prawo.
        self.grid.setColumnStretch(length + 1, 1)

    # --------------------------------- export --------------------------------

    def export_data(self) -> Tuple[int, Dict[str, Any]]:
        length = int(self.length_combo.currentText())
        octave = int(self.octave_combo.currentText())
        division = self.division_combo.currentText()
        midi_ch = int(self.channel_combo.currentText())

        steps: List[Union[str, int]] = []
        velocity: List[int] = []
        vrand: List[int] = []
        sprob: List[int] = []
        soct: List[int] = []
        roct: List[str] = []
        gate: List[Union[int, str]] = []

        # Extract from grid widgets row by row
        # Steps row (row 1)
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(1, col + 1).widget()  # type: ignore
            txt = box.currentText()
            if txt.upper() in ("R", "X"):
                steps.append(txt.upper())
            else:
                try:
                    steps.append(int(txt))
                except ValueError:
                    steps.append(txt)
        # Velocity row (row 2)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(2, col + 1).widget()  # type: ignore
            velocity.append(spin.value())
        # v-random row (row 3)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(3, col + 1).widget()  # type: ignore
            vrand.append(spin.value())
        # s-prob row (row 4)
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(4, col + 1).widget()  # type: ignore
            sprob.append(spin.value())
        # s-oct row (row 5)
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(5, col + 1).widget()  # type: ignore
            soct.append(int(box.currentText()))
        # r-oct row (row 6)
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(6, col + 1).widget()  # type: ignore
            roct.append(box.currentText())
        # Gate row (row 6)
        for col in range(length):
            spin: GateSpinBox = self.grid.itemAtPosition(7, col + 1).widget()  # type: ignore
            text = spin.value_text()
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
            "s-prob": sprob,
            "s-oct": soct,
            "r-oct": roct,
            "gate": gate,
            "oktawa": octave,
            "division": division,
            "enabled": self._enabled,
        }
        return midi_ch, data

    def randomize(self, settings: "RandomSettings"):
        import random
        # Randomize top-level params (except MIDI channel)
        if random.randint(1, 100) <= settings.length:
            self.length_combo.setCurrentText(str(random.randint(1, 16)))
        if random.randint(1, 100) <= settings.octave:
            self.octave_combo.setCurrentText(str(random.randint(-2, 2)))
        if random.randint(1, 100) <= settings.division:
            allowed_divs = [d for d in DIVISION_OPTIONS if (
                (settings.allow_div_d or not d.endswith("d")) and
                (settings.allow_div_t or not d.endswith("t")) and
                (settings.allow_div_q or not d.endswith("q"))
            )]
            self.division_combo.setCurrentText(random.choice(allowed_divs))
        length = int(self.length_combo.currentText())
        # Rebuild grid ensures correct length
        self._build_grid()
        # Steps row
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(1, col + 1).widget()  # type: ignore
            if random.randint(1, 100) <= settings.steps:
                box.setCurrentIndex(random.randrange(box.count()))
        # Velocity row
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(2, col + 1).widget()  # type: ignore[assignment]
            if random.randint(1, 100) <= settings.velocity:
                spin.setValue(random.randint(1, 127))  # type: ignore[attr-defined]
        # v-random row
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(3, col + 1).widget()  # type: ignore[assignment]
            if random.randint(1, 100) <= settings.vrandom:
                spin.setValue(random.randint(0, 100))  # type: ignore[attr-defined]
        # s-prob row
        for col in range(length):
            spin: DragSpinBox = self.grid.itemAtPosition(4, col + 1).widget()  # type: ignore[assignment]
            if random.randint(1, 100) <= settings.sprob:
                spin.setValue(random.randint(0, 100))  # type: ignore[attr-defined]
        # s-oct row
        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(5, col + 1).widget()  # type: ignore[assignment]
            if random.randint(1, 100) <= settings.sprob: # Assuming sprob controls s-oct randomization
                box.setCurrentIndex(random.randrange(box.count()))
        # r-oct row – custom logic
        OCT_OPTIONS = ["0", "+1", "+2", "-1", "-2", "+-1", "+-2"]

        def _small_variants(cur: str) -> List[str]:
            """Return a list of allowed small-change variants for given current value."""
            if cur == "0":
                return ["0", "+1", "+-1"]
            if cur == "+1":
                return ["0", "+1", "+2"]
            if cur == "-1":
                return ["0", "-1", "-2"]
            if cur == "+2":
                return ["+1", "+2"]
            if cur == "-2":
                return ["-1", "-2"]
            if cur == "+-1":
                return ["0", "+-1", "+1", "-1"]
            if cur == "+-2":
                return ["+-2", "+2", "-2", "+1", "-1"]
            # fallback – all options
            return OCT_OPTIONS

        for col in range(length):
            box: QComboBox = self.grid.itemAtPosition(6, col + 1).widget()  # type: ignore[assignment]
            roct_setting = settings.roct
            if roct_setting == 0:
                continue  # no changes

            # Determine probability (1-50%)
            if roct_setting <= 50:
                prob = roct_setting
            else:
                prob = roct_setting - 50

            if random.randint(1, 100) > prob:
                continue  # skip change

            current_txt = box.currentText()

            if roct_setting <= 50:
                # Small variant change
                choices = _small_variants(current_txt)
                box.setCurrentText(random.choice(choices))
            else:
                # Large variant change – pick any option
                box.setCurrentText(random.choice(OCT_OPTIONS))
        # Gate row
        for col in range(length):
            spin: GateSpinBox = self.grid.itemAtPosition(7, col + 1).widget()  # type: ignore
            if random.randint(1, 100) <= settings.gate:
                if settings.allow_gate_T and random.random() < 0.2:
                    spin.setText("T")
                else:
                    spin._val_to_text(random.randint(0, 100))


# ---------------------------------------------------------------------------
# Randomization settings dataclass
# ---------------------------------------------------------------------------


@dataclass
class RandomSettings:
    length: int = 100
    octave: int = 100
    division: int = 100
    steps: int = 100
    velocity: int = 100
    vrandom: int = 100
    sprob: int = 100
    roct: int = 100
    gate: int = 100
    patterns_enabled: Dict[str, bool] = field(
        default_factory=lambda: {
            "Pattern 1": True,
            "Pattern 2": True,
            "Pattern 3": True,
            "Pattern 4": True,
        }
    )
    allow_gate_T: bool = True
    allow_div_d: bool = True
    allow_div_t: bool = True
    allow_div_q: bool = True


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------


class RandomSettingsDialog(QDialog):
    def __init__(self, settings: "RandomSettings", pattern_names: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Global Random Settings")
        self._settings = settings
        layout = QVBoxLayout()

        # Sliders section
        sliders_cfg = [
            ("Length", "length"),
            ("Octave", "octave"),
            ("Division", "division"),
            ("Steps", "steps"),
            ("Velocity", "velocity"),
            ("V-Random", "vrandom"),
            ("S-Prob", "sprob"),
            ("R-Oct", "roct"),
            ("Gate", "gate"),
        ]
        for label, attr in sliders_cfg:
            hl = QHBoxLayout()
            hl.addWidget(QLabel(label))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(getattr(settings, attr))
            slider.setTickInterval(10)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            value_lbl = QLabel(str(slider.value()))

            def make_upd(lbl: QLabel, name: str):
                return lambda val: (lbl.setText(str(val)), setattr(settings, name, val))

            slider.valueChanged.connect(make_upd(value_lbl, attr))  # type: ignore[arg-type]
            hl.addWidget(slider)
            hl.addWidget(value_lbl)
            layout.addLayout(hl)

        # Patterns enable checkboxes
        layout.addWidget(QLabel("Affect patterns:"))
        pat_layout = QHBoxLayout()
        for pname in pattern_names:
            cb = QCheckBox(pname)
            cb.setChecked(settings.patterns_enabled.get(pname, True))

            def make_toggle(name: str):
                return lambda state: settings.patterns_enabled.__setitem__(name, state == Qt.CheckState.Checked)

            cb.stateChanged.connect(make_toggle(pname))  # type: ignore[arg-type]
            pat_layout.addWidget(cb)
        layout.addLayout(pat_layout)

        # Other options
        layout.addWidget(QLabel("Random value options:"))
        other_layout = QHBoxLayout()
        self.cb_gate_T = QCheckBox("Allow Gate T")
        self.cb_gate_T.setChecked(settings.allow_gate_T)
        self.cb_gate_T.stateChanged.connect(lambda s: setattr(settings, "allow_gate_T", s == Qt.CheckState.Checked))  # type: ignore[arg-type]
        other_layout.addWidget(self.cb_gate_T)

        for lbl, attr in [("Allow d", "allow_div_d"), ("Allow t", "allow_div_t"), ("Allow q", "allow_div_q")]:
            cb = QCheckBox(lbl)
            cb.setChecked(getattr(settings, attr))
            cb.stateChanged.connect(lambda s, a=attr: setattr(settings, a, s == Qt.CheckState.Checked))  # type: ignore[arg-type]
            other_layout.addWidget(cb)

        layout.addLayout(other_layout)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)


class ConfigEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TR Performer Config Editor")
        self.resize(1000, 600)

        self.pattern_widgets: Dict[str, PatternWidget] = {}
        self.rand_settings = RandomSettings()

        # Master arpeggiator enable state
        self._arp_enabled: bool = True
        self._prev_pattern_enabled: Dict[str, bool] = {}

        # Prefer loading a preset named "Default.json" from presets directory if it exists
        default_preset_path = (Path(__file__).resolve().parent / "presets" / "Default.json")
        if default_preset_path.exists():
            self._load_config(default_preset_path)
        else:
            # Fallback to generic config.json in app directory
            self._load_config(CONFIG_PATH)
        self._build_menu()
        self._build_toolbar()

        # Send current configuration to midi_router on startup
        try:
            self.save_current()
        except Exception:
            pass

    # ------------------------------- UI build ------------------------------

    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        save_act = QAction("Send", self)
        save_act.triggered.connect(self.save_current)
        save_as_act = QAction("Save As…", self)
        save_as_act.triggered.connect(self.save_as)
        open_act = QAction("Open Preset…", self)
        open_act.triggered.connect(self.open_preset)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.close)

        file_menu.addAction(open_act)
        # Keep "Send" in menu as well (optional)
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

        # Load saved random settings if present
        rs_data = data.get("random_settings")
        if rs_data and isinstance(rs_data, dict):
            # update existing RandomSettings instance
            for k, v in rs_data.items():
                if hasattr(self.rand_settings, k):
                    setattr(self.rand_settings, k, v)

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

        # Remember current path
        self._current_path = path

        # Create pattern widgets
        for pname, midi_ch in self._output_channels.items():
            pcfg = patterns.get(pname, {})
            pw = PatternWidget(pname, pcfg, midi_ch)
            inner_layout.addWidget(pw)
            self.pattern_widgets[pname] = pw
        inner_layout.addStretch()

        # Update preset name display if toolbar already built
        if hasattr(self, "preset_name_edit"):
            self._update_preset_name()

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
            "random_settings": asdict(self.rand_settings),
        }

    def save_current(self):
        data = self._collect_config()
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2))
            # Silent save – no confirmation dialog
            self._notify_router()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save preset", str(PRESET_DIR), "JSON (*.json)")
        if not path:
            return
        data = self._collect_config()
        try:
            Path(path).write_text(json.dumps(data, indent=2))
            self._notify_router()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open preset", str(PRESET_DIR), "JSON (*.json)")
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

    def _build_toolbar(self):
        toolbar = QToolBar("TopBar", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setFixedHeight(60)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        # Left padding
        left_pad = QWidget()
        left_pad.setFixedWidth(20)
        toolbar.addWidget(left_pad)

        # --- Master Power toggle ---
        power_icon_path = Path(__file__).resolve().parent / "icons" / "ic_power.svg"
        self.master_enable_btn = QToolButton()
        self.master_enable_btn.setIcon(QIcon(str(power_icon_path)))
        self.master_enable_btn.setCheckable(True)
        self.master_enable_btn.setChecked(True)
        self.master_enable_btn.setStyleSheet(
            "QToolButton { border:none; padding:0px; }\n"
            "QToolButton:!checked { opacity: 0.3; }"
        )
        self.master_enable_btn.clicked.connect(self._toggle_master_enabled)  # type: ignore[arg-type]
        toolbar.addWidget(self.master_enable_btn)

        # --- Global Random button (next) ---
        btn_random = QPushButton("Global Random")
        btn_random.setMinimumHeight(40)
        btn_random.clicked.connect(self.randomize_all)  # type: ignore[arg-type]
        toolbar.addWidget(btn_random)

        # Settings (gear) button next to random, uses custom SVG icon
        icon_path = (Path(__file__).resolve().parent / "icons" / "ic_settings.svg")
        btn_settings = QPushButton()
        btn_settings.setIcon(QIcon(str(icon_path)))
        btn_settings.setMinimumSize(QSize(40, 40))
        btn_settings.clicked.connect(self.open_random_settings)  # type: ignore[arg-type]
        toolbar.addWidget(btn_settings)

        # spacer expands pushing following widgets to right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Preset name field (read-only)
        self.preset_name_edit = QLineEdit("New preset")
        self.preset_name_edit.setReadOnly(True)
        self.preset_name_edit.setFixedWidth(200)
        self.preset_name_edit.setFixedHeight(40)
        self.preset_name_edit.setStyleSheet("QLineEdit { padding-left: 8px; }")
        toolbar.addWidget(self.preset_name_edit)

        # Open preset icon button
        open_icon = QIcon(str((Path(__file__).resolve().parent / "icons" / "ic_open.svg")))
        btn_open = QPushButton()
        btn_open.setIcon(open_icon)
        btn_open.setMinimumSize(QSize(40, 40))
        btn_open.clicked.connect(self.open_preset)  # type: ignore[arg-type]
        toolbar.addWidget(btn_open)

        # Save As icon button
        saveas_icon = QIcon(str((Path(__file__).resolve().parent / "icons" / "ic_save.svg")))
        btn_saveas = QPushButton()
        btn_saveas.setIcon(saveas_icon)
        btn_saveas.setMinimumSize(QSize(40, 40))
        btn_saveas.clicked.connect(self.save_as)  # type: ignore[arg-type]
        toolbar.addWidget(btn_saveas)

        # Spacer to push Send to far right (centering preset block)
        spacer_center_right = QWidget()
        spacer_center_right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer_center_right)

        # Send button on the far right
        btn_send = QPushButton("Send")
        btn_send.setMinimumHeight(40)
        btn_send.clicked.connect(self.save_current)  # type: ignore[arg-type]
        toolbar.addWidget(btn_send)

        # Right padding
        right_pad = QWidget()
        right_pad.setFixedWidth(20)
        toolbar.addWidget(right_pad)

        # initialise preset name
        self._update_preset_name()

    def randomize_all(self, checked: bool = False):  # noqa: F841
        if not self._arp_enabled:
            return
        for _name, pw in self.pattern_widgets.items():
            if pw.is_enabled():
                pw.randomize(self.rand_settings)

    # ----------------------- master enable handler -----------------------

    def _toggle_master_enabled(self, checked: bool):
        """Toggle entire arpeggiator on/off remembering individual states."""
        self._arp_enabled = bool(checked)
        if self._arp_enabled:
            # restore per-pattern previous states
            for name, pw in self.pattern_widgets.items():
                desired = self._prev_pattern_enabled.get(name, True)
                pw.set_enabled(desired)
        else:
            # save current states and disable all
            self._prev_pattern_enabled = {name: pw.is_enabled() for name, pw in self.pattern_widgets.items()}
            for pw in self.pattern_widgets.values():
                pw.set_enabled(False)
        # save & notify router
        self.save_current()

    def open_random_settings(self):
        dlg = RandomSettingsDialog(self.rand_settings, list(self.pattern_widgets.keys()), self)
        dlg.exec()

    # --------------------------- preset name helper ------------------------

    def _update_preset_name(self):
        """Update read-only line edit with current preset file name."""
        if hasattr(self, "preset_name_edit"):
            try:
                preset_path: Path = Path(getattr(self, "_current_path", CONFIG_PATH))
                if preset_path.resolve() == CONFIG_PATH.resolve():
                    self.preset_name_edit.setText("New preset")
                else:
                    self.preset_name_edit.setText(preset_path.stem)
            except Exception:
                self.preset_name_edit.setText("New preset")

    # ------------------------------------------------------------------
    # Graceful shutdown – ensure midi_router is terminated as well
    # ------------------------------------------------------------------

    def closeEvent(self, event):  # type: ignore[override]
        """On window close, terminate running midi_router process (if any)."""
        try:
            if LOCK_PATH.exists():
                pid = int(LOCK_PATH.read_text())
                # Avoid killing self
                if pid and pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
        except Exception:
            pass  # ignore errors
        super().closeEvent(event)


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