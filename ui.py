"""Tkinter interface: main window, review table, recovery dialog."""
import tkinter as tk
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import storage
from models import NAMES, OFF, ON, fmt, move_boundary, split
from tracker import Tracker

APP = "Timetrack"
TEAL, AMBER, GREY = "#0f766e", "#b45309", "#475569"
COLORS = {ON: TEAL, OFF: AMBER, None: GREY}
TS = "%Y-%m-%d %H:%M:%S"


def parse_time(text, ref):
    """HH:MM:SS (next occurrence at/after ref) or YYYY-MM-DD HH:MM:SS, in ref's UTC offset."""
    text = text.strip()
    try:
        if " " in text:
            return datetime.strptime(text, TS).replace(tzinfo=ref.tzinfo)
        t = datetime.strptime(text, "%H:%M:%S").time()
    except ValueError:
        raise ValueError("Use HH:MM:SS or YYYY-MM-DD HH:MM:SS.") from None
    d = datetime.combine(ref.date(), t, ref.tzinfo)
    return d if d >= ref else d + timedelta(days=1)


class Review:
    """Modal editable segment table. Edits stay in memory until CSV export."""

    def __init__(self, parent, segs, path, lock, gaps):
        self.segs = [replace(s) for s in segs]
        self.path, self.lock, self.gaps = path, lock, gaps
        self.dirty = self.acked = False
        self.exported = None
        w = self.win = tk.Toplevel(parent)
        w.title("Review session")
        w.geometry("780x420")
        w.minsize(640, 320)
        w.transient(parent)
        w.protocol("WM_DELETE_WINDOW", self.close)

        if gaps:
            ids = ", ".join(str(g[0]) for g in gaps)
            ttk.Label(w, foreground=AMBER, wraplength=740, padding=(8, 6), text=(
                f"Clock discrepancy (sleep or clock change) in segment(s) {ids}. Durations use "
                "elapsed time; check the boundaries before exporting.")).pack(fill="x")

        mid = ttk.Frame(w)
        mid.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("n", "start", "end", "dur", "state", "label")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, title, width in zip(cols, ("#", "Start", "End", "Duration", "State", "Label"),
                                   (40, 150, 150, 80, 80, 220)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=width, anchor="w", stretch=(c == "label"))
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        bar = ttk.Frame(w, padding=8)
        bar.pack(fill="x")
        self.entry = ttk.Entry(bar, width=22)
        self.entry.pack(side="left")
        for text, cmd in (("Set label", self.set_label), ("Toggle state", self.flip),
                          ("Split…", self.do_split), ("Move boundary…", self.do_move)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        ttk.Button(bar, text="Export CSV…", command=self.export).pack(side="right", padx=2)
        self.fill()

    def run(self):
        self.win.wait_visibility()
        self.win.grab_set()
        self.win.wait_window()
        return self.exported

    def fill(self, select=()):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.segs):
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, s.start.strftime(TS), s.end.strftime(TS), fmt(s.duration), NAMES[s.state], s.label))
        self.tree.selection_set([str(i) for i in select if i < len(self.segs)])

    def sel(self):
        return sorted(int(i) for i in self.tree.selection())

    def edited(self, sel):
        self.dirty = True
        self.fill(sel)

    def set_label(self):
        sel = self.sel()
        for i in sel:
            self.segs[i].label = self.entry.get()
        self.edited(sel)

    def flip(self):
        sel = self.sel()
        for i in sel:
            self.segs[i].state = OFF if self.segs[i].state == ON else ON
        self.edited(sel)

    def _one(self):
        sel = self.sel()
        if len(sel) != 1:
            messagebox.showinfo(APP, "Select exactly one segment.", parent=self.win)
            return None
        return sel[0]

    def _ask(self, prompt, ref, initial):
        text = simpledialog.askstring(APP, prompt, initialvalue=initial.strftime("%H:%M:%S"), parent=self.win)
        if text is None:
            return None
        try:
            return parse_time(text, ref)
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self.win)

    def _apply(self, fn, i, at):
        try:
            fn(self.segs, i, at)
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self.win)
            return
        self.edited([i])

    def do_split(self):
        i = self._one()
        if i is None:
            return
        s = self.segs[i]
        at = self._ask("Split at (HH:MM:SS or YYYY-MM-DD HH:MM:SS):", s.start, s.start + (s.end - s.start) / 2)
        if at:
            self._apply(split, i, at)

    def do_move(self):
        i = self._one()
        if i is None:
            return
        s = self.segs[i]
        at = self._ask(f"New boundary between segment {i + 1} and {i + 2}:", s.start, s.end)
        if at:
            self._apply(move_boundary, i, at)

    def export(self):
        if self.gaps and not self.acked:
            if not messagebox.askokcancel(APP, "Clock discrepancies were flagged. Export with the "
                                          "current boundaries?", parent=self.win):
                return
            self.acked = True
        name = f"Timetrack_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
        dest = filedialog.asksaveasfilename(parent=self.win, initialfile=name, defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])  # asks before replacing
        if not dest:
            return
        try:
            storage.export_csv(self.segs, Path(dest))
        except OSError as e:
            messagebox.showerror(APP, f"Export failed; your data is kept.\n\n{e}", parent=self.win)
            return
        self.exported = Path(dest)
        self.lock.release()
        try:
            storage.remove_session(self.path)
        except OSError as e:
            messagebox.showinfo(APP, f"Export succeeded, but the capture file could not be removed:\n"
                                     f"{self.path}\n\n{e}", parent=self.win)
        self.win.destroy()

    def close(self):
        if self.dirty and not messagebox.askokcancel(
                APP, "Unexported review edits will be lost. The captured segments stay in TEMP.\n\n"
                     "Close anyway?", parent=self.win):
            return
        self.win.destroy()


class App:
    def __init__(self, base):
        self.base, self.t = Path(base), Tracker(base)
        self._cache, self._fonts = {}, []
        r = self.root = tk.Tk()
        r.title(APP)
        r.geometry("420x360")
        r.minsize(380, 340)
        self._build()
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        self.tick()
        r.after(150, self.startup)

    def run(self):
        self.root.mainloop()

    # ----- layout -----
    def _font(self, scale, base="TkDefaultFont", **kw):
        f = tkfont.nametofont(base).copy()
        f.configure(size=round(f.cget("size") * scale), **kw)
        self._fonts.append(f)  # keep a reference so Tk doesn't drop it
        return f

    def _build(self):
        f = ttk.Frame(self.root, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=APP, font=self._font(1.5, weight="bold")).pack(anchor="w")
        self.status = tk.Label(f, font=self._font(1.2, weight="bold"), fg="white", pady=4)
        self.status.pack(fill="x", pady=(6, 0))
        self.timer = ttk.Label(f, anchor="center", font=self._font(2.4, "TkFixedFont", weight="bold"))
        self.timer.pack(fill="x", pady=6)
        ttk.Style().configure("Big.TButton", padding=8)
        self.btn = ttk.Button(f, style="Big.TButton", command=self.primary)
        self.btn.pack(fill="x")

        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=8)
        grid.columnconfigure(1, weight=1)
        self.vals = {}
        for r, name in enumerate(("On task", "Off task", "Total tracked", "On task %")):
            ttk.Label(grid, text=name).grid(row=r, column=0, sticky="w")
            self.vals[name] = ttk.Label(grid)
            self.vals[name].grid(row=r, column=1, sticky="e")

        ttk.Label(f, text="Label (optional)").pack(anchor="w")
        self.lbl = tk.StringVar()
        self.lbl.trace_add("write", lambda *_: setattr(self.t, "label", self.lbl.get()))
        self.entry = ttk.Entry(f, textvariable=self.lbl)
        self.entry.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(f)
        row.pack(fill="x")
        self.end_btn = ttk.Button(row, text="End session", command=self.on_end)
        self.end_btn.pack(side="left")
        ttk.Button(row, text="Recover/export sessions", command=self.recover).pack(side="right")
        self.note = ttk.Label(f, wraplength=380, foreground=GREY)
        self.note.pack(anchor="w", pady=(6, 0))
        self.btn.focus_set()

    def _set(self, w, text):
        if self._cache.get(w) != text:
            self._cache[w] = text
            w.config(text=text)

    # ----- display -----
    def refresh(self):
        t, st = self.t, self.t.state
        self._set(self.status, NAMES.get(st, "Stopped"))
        self.status.config(bg=COLORS[st])
        self.timer.config(foreground=COLORS[st])
        self._set(self.btn, "Retry save" if t.pending else
                  "Start session" if st is None else f"Switch to {NAMES[OFF if st == ON else ON]}")
        self.end_btn.config(state="normal" if st and not t.pending else "disabled")
        self.entry.config(state="normal" if st and not t.pending else "disabled")
        self.paint()

    def paint(self):
        t = self.t
        on, off = t.totals()
        total = on + off
        self._set(self.timer, fmt(t.elapsed()))
        self._set(self.vals["On task"], fmt(on))
        self._set(self.vals["Off task"], fmt(off))
        self._set(self.vals["Total tracked"], fmt(total))
        self._set(self.vals["On task %"], f"{on / total * 100:.1f}%" if total else "0%")

    def tick(self):  # display only; never touches disk
        self.paint()
        self.root.after(250, self.tick)

    # ----- actions -----
    def save_error(self, e):
        if isinstance(e, storage.PartialTail) and messagebox.askyesno(
                APP, f"{e}\n\nRemove the damaged partial line and retry?"):
            try:
                storage.repair_tail(self.t.path)
                self.t.retry()
                return
            except storage.StorageError as e2:
                e = e2
        messagebox.showerror(APP, f"{e}\n\nNothing is lost: use Retry save when the problem is fixed."
                             if self.t.pending else str(e))

    def primary(self):
        t = self.t
        final = bool(t.pending and t.pending[0].session_end)
        try:
            if t.pending:
                t.retry()
            elif t.state is None:
                t.start()
            else:
                t.toggle()
        except storage.StorageError as e:
            self.save_error(e)
        self.lbl.set(t.label)
        self.note.config(text="")
        self.refresh()
        if final and not t.pending and t.state is None:
            self.finish()

    def end_session(self):
        try:
            self.t.end()
        except storage.StorageError as e:
            self.save_error(e)
        self.lbl.set(self.t.label)
        self.refresh()
        return self.t.state is None and not self.t.pending

    def on_end(self):
        if self.end_session():
            self.finish()

    def finish(self):
        t = self.t
        dest = Review(self.root, t.segments, t.path, t.lock, t.gaps).run()
        self.note.config(text=f"Exported {dest.name}." if dest else
                         "Session kept in TEMP. Use Recover/export sessions to export it later.")
        t.reset()
        self.refresh()

    def ask3(self, text, buttons):
        win = tk.Toplevel(self.root)
        win.title(APP)
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, text=text, padding=12, wraplength=340, justify="left").pack()
        res, row = [], ttk.Frame(win, padding=(12, 0, 12, 12))
        row.pack()
        for i, b in enumerate(buttons):
            ttk.Button(row, text=b, command=lambda i=i: (res.append(i), win.destroy())).pack(side="left", padx=3)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.wait_visibility()
        win.grab_set()
        win.wait_window()
        return res[0] if res else None

    def on_close(self):
        t = self.t
        if t.pending and not messagebox.askyesno(
                APP, "The latest segment could not be saved yet and will be lost if you quit.\n\nQuit anyway?"):
            return
        if t.state and not t.pending:
            c = self.ask3("A session is still running.",
                          ("End session and export", "End session and keep for later", "Cancel"))
            if c in (None, 2) or not self.end_session():
                return
            if c == 0:
                self.finish()
        self.root.destroy()

    # ----- recovery -----
    def startup(self):
        temp = self.base / "TEMP"
        if storage.list_sessions(temp) and messagebox.askyesno(
                APP, f"Unexported sessions were found in:\n{temp}\n\nReview them now?"):
            self.recover()

    def recover(self):
        temp, paths = self.base / "TEMP", []
        win = tk.Toplevel(self.root)
        win.title("Recover/export sessions")
        win.transient(self.root)
        lb = tk.Listbox(win, width=70, height=8, exportselection=False)
        lb.pack(fill="both", expand=True, padx=8, pady=8)

        def load():
            paths[:] = storage.list_sessions(temp)
            lb.delete(0, "end")
            for p in paths:
                lb.insert("end", storage.describe(p))
            if not paths:
                lb.insert("end", "No unexported sessions.")

        def picked():
            s = lb.curselection()
            return paths[s[0]] if s and paths else None

        def open_it():
            p = picked()
            if not p:
                return
            lock = storage.SessionLock(p)
            if not lock.acquire():
                messagebox.showinfo(APP, "That session is in use by another instance.", parent=win)
                return
            try:
                segs, damaged = storage.load_segments(p)
                if not segs:
                    messagebox.showinfo(APP, "Nothing recoverable in that file. You can discard it.", parent=win)
                    lock.release()
                    return
                if damaged:
                    messagebox.showinfo(APP, "The last line was damaged. Only intact records are shown; "
                                             "the original file is kept until export succeeds.", parent=win)
            except storage.StorageError as e:
                messagebox.showerror(APP, str(e), parent=win)
                lock.release()
                return
            Review(win, segs, p, lock, []).run()
            lock.release()
            load()

        def discard():
            p = picked()
            if not p or not messagebox.askyesno(APP, f"Permanently delete {p.name}?", parent=win):
                return
            lock = storage.SessionLock(p)
            if not lock.acquire():
                messagebox.showinfo(APP, "That session is in use by another instance.", parent=win)
                return
            lock.release()
            try:
                storage.remove_session(p)
            except OSError as e:
                messagebox.showerror(APP, str(e), parent=win)
            load()

        bar = ttk.Frame(win, padding=(8, 0, 8, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Review/export", command=open_it).pack(side="left")
        ttk.Button(bar, text="Discard", command=discard).pack(side="left", padx=4)
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        load()
        win.wait_visibility()
        win.grab_set()
        win.wait_window()
