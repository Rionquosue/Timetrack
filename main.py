"""Timetrack entry point. TEMP is created beneath the launch working directory."""
import sys
from pathlib import Path


def main():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("Timetrack needs Tkinter (on Debian/Ubuntu: sudo apt install python3-tk).")
    from ui import App
    if "--smoke-test" in sys.argv:
        # Exercises the real bundled UI in CI without a running session.
        app = App(Path.cwd())
        app.root.after(500, app.root.destroy)
        app.run()
        return
    App(Path.cwd()).run()


if __name__ == "__main__":
    main()
