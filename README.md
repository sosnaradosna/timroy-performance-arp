# TR-performer-arp-DESKTOP-APP

Minimal MIDI router for macOS.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Możesz uruchomić router na dwa sposoby:

1. Bezpośrednio przez Pythona:

```bash
python midi_router.py
```

2. Albo przez wygodne polecenie NPM (nie wymaga dodatkowych zależności Node):

```bash
npm start    # wywoła python3 midi_router.py
```

Skrypt tworzy wirtualne wejście "TR Router In" nasłuchujące na kanale 1 oraz dwa wirtualne wyjścia:
1. "Pattern 1" – kanał 2
2. "Pattern 2" – kanał 3

Podłącz w DAW lub innym programie źródło MIDI do wejścia „TR Router In”. Nuty odebrane na kanale 1 zostaną przekierowane jednocześnie na kanały 2 i 3 na odpowiednich wyjściach.

---

### Konfiguracja

Domyślne ustawienia (wejście: kanał 1, wyjścia: kanały 2 i 3) są zapisane w pliku `config.json`. Możesz je tam zmienić – skrypt wczytuje konfigurację przy starcie. 

## Budowanie instalatora / paczki aplikacji

Poniższe kroki pozwolą zbudować samodzielną aplikację (jeden plik lub `*.app`) do przesłania znajomym.

### 1. Zainstaluj zależności deweloperskie

```bash
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

### 2. Uruchom PyInstaller

```bash
# macOS (tworzy TR_Performer.app oraz TR_Performer.dmg)
pyinstaller \
  --name "TR_Performer" \
  --windowed \
  --onefile \
  --add-data "config.json:." \
  --add-data "presets:presets" \
  --add-data "icons:icons" \
  main_app.py

# Windows (uruchom w PowerShell albo cmd)
pyinstaller ^
  --name "TR_Performer" ^
  --windowed ^
  --onefile ^
  --add-data "config.json;." ^
  --add-data "presets;presets" ^
  --add-data "icons;icons" ^
  main_app.py
```

Po zakończeniu w katalogu `dist/` znajdziesz:

* **macOS** → `TR_Performer` (`.app`) oraz plik `.dmg`, który można rozesłać.
* **Windows** → `TR_Performer.exe` – pojedynczy plik wykonywalny do wysłania znajomym.

Opcjonalnie na Windows możesz użyć Inno Setup do stworzenia instalatora `.exe`, a na macOS `create-dmg` do wygenerowania ładnego obrazu DMG.

### 3. Test lokalnie

```bash
# macOS
open dist/TR_Performer.app

# Windows
start dist/TR_Performer.exe
```

---

> PyInstaller buduje binaria **tylko na aktualnym systemie**. Aby uzyskać wersję na Windows musisz zbudować ją na Windowsie, a wersję na macOS – na macOS. 