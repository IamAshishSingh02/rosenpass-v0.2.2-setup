"""Entry point: python -m rosenpass_gui [profile-name]"""

import sys
import tkinter as tk

from .app import App


def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    App(root, initial_profile=initial)
    root.mainloop()


if __name__ == "__main__":
    main()
