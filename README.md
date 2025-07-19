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