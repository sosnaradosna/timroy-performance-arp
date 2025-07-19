import atexit
from multiprocessing import Process, set_start_method
import signal
from config_editor import main as editor_main
from config_editor import RELOAD_SIGNAL
from midi_router import main as router_main


# Use 'spawn' start method for compatibility with frozen executables.
try:
    set_start_method("spawn")
except RuntimeError:
    pass  # already set


def start_router_process() -> Process:
    proc = Process(target=router_main, name="MidiRouterProcess", daemon=True)
    proc.start()
    return proc


def main():
    router_proc = start_router_process()

    # Ensure router is terminated when GUI exits
    def _cleanup():
        if router_proc.is_alive():
            try:
                router_proc.terminate()
                router_proc.join(timeout=2)
            except Exception:
                pass

    atexit.register(_cleanup)

    # uruchamiamy GUI (blokuje do zamknięcia okna)
    # Główny proces GUI ignoruje sygnał RELOAD_SIGNAL, żeby nie kończył aplikacji
    try:
        signal.signal(RELOAD_SIGNAL, lambda signum, frame: None)
    except Exception:
        pass  # signal may not be available on platform
    editor_main()


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main() 