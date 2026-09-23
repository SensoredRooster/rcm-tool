"""Compatibility entry point for the RCM Tool repository.

The modern application is RcmTool. If the Qt dependency is not installed,
the original Tk bench remains available as a compatibility fallback.
"""


def main() -> int | None:
    try:
        from signal_lab.ui import main as signal_lab_main
        return signal_lab_main()
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            from controller_integrity import main as legacy_main
            print("PySide6 is not installed; launching the legacy RCM capture bench.")
            print("Install the current requirements to launch RcmTool.")
            legacy_main()
            return None
        raise


if __name__ == "__main__":
    raise SystemExit(main() or 0)
