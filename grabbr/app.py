"""grabbr desktop window (CustomTkinter)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from grabbr import __version__, tools
from grabbr.downloader import (
    Download,
    DownloadOptions,
    Event,
    FileDone,
    Finished,
    Log,
    Progress,
    Stage,
    is_supported_url,
    looks_like_url,
)
from grabbr.paths import data_dir
from grabbr.settings import Settings

LOCKED_BUTTON_COLOR = ("gray78", "gray30")
LOCKED_TEXT_COLOR = ("gray45", "gray60")
DOWNLOAD_SHARE = 0.85  # portion of the bar for the download; the rest is conversion

FORMAT_LABELS = {"MP3": "mp3", "Original": "original"}
FORMAT_NAMES = {v: k for k, v in FORMAT_LABELS.items()}


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d} left"


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class GrabbrApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._download: Download | None = None
        self._busy: str | None = None  # None | "download" | "tools"
        self._last_clipboard = ""
        self._bar_busy = False

        self.title(f"grabbr {__version__}")
        self.geometry("680x520")
        self.minsize(560, 420)
        self._build()
        self._set_busy(None)

        self.after(80, self._drain_ui_queue)
        self.after(1000, self._poll_clipboard)
        self.after(200, lambda: self._spawn(self._check_tools_startup))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- layout -------------------------------------------------------------

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        pad = {"padx": 14, "pady": (10, 0)}

        # URL row
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 0))
        url_row.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(
            url_row, placeholder_text="Paste a YouTube or SoundCloud link", height=38
        )
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda _e: self._start_download())
        self.grab_btn = ctk.CTkButton(
            url_row, text="Grab", width=96, height=38, command=self._start_download
        )
        self.grab_btn.grid(row=0, column=1, padx=(8, 0))

        # Options row
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=1, column=0, sticky="ew", **pad)
        self.format_btn = ctk.CTkSegmentedButton(
            opts, values=list(FORMAT_LABELS), command=self._on_format
        )
        self.format_btn.set(FORMAT_NAMES.get(self.settings.audio_format, "MP3"))
        self.format_btn.pack(side="left")
        self.playlist_var = tk.BooleanVar(value=self.settings.playlist)
        self.playlist_box = ctk.CTkCheckBox(
            opts,
            text="Whole playlist",
            variable=self.playlist_var,
            command=self._on_playlist,
        )
        self.playlist_box.pack(side="left", padx=(14, 0))
        self.clip_var = tk.BooleanVar(value=self.settings.watch_clipboard)
        self.clip_box = ctk.CTkCheckBox(
            opts,
            text="Auto-paste links",
            variable=self.clip_var,
            command=self._on_clipboard_toggle,
        )
        self.clip_box.pack(side="left", padx=(14, 0))

        # Output folder row
        out = ctk.CTkFrame(self, fg_color="transparent")
        out.grid(row=2, column=0, sticky="ew", **pad)
        out.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out, text="Save to:").grid(row=0, column=0, padx=(0, 8))
        self.out_label = ctk.CTkLabel(out, text=self.settings.output_dir, anchor="w")
        self.out_label.grid(row=0, column=1, sticky="ew")
        self.change_btn = ctk.CTkButton(out, text="Change…", width=80, command=self._choose_folder)
        self.change_btn.grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(
            out,
            text="Open",
            width=64,
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "gray90"),
            command=self._open_output,
        ).grid(row=0, column=3, padx=(6, 0))

        # Progress
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=3, column=0, sticky="ew", padx=14, pady=(14, 0))
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        self.status = ctk.CTkLabel(prog, text="Ready.", anchor="w")
        self.status.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # Log
        self.log_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=12))
        self.log_box.grid(row=4, column=0, sticky="nsew", padx=14, pady=(8, 0))
        self.log_box.tag_config("error", foreground="#e5534b")
        self.log_box.tag_config("warning", foreground="#d4a72c")
        self.log_box.tag_config("ok", foreground="#57ab5a")
        self.log_box.configure(state="disabled")

        # Bottom bar
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", padx=14, pady=12)
        self.setup_btn = ctk.CTkButton(
            bottom, text="Set up tools", width=110, command=self._setup_tools
        )
        self.setup_btn.pack(side="left")
        self.update_btn = ctk.CTkButton(
            bottom, text="Update yt-dlp", width=110, command=self._update_ytdlp
        )
        self.update_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn = ctk.CTkButton(
            bottom,
            text="Cancel",
            width=90,
            fg_color="#8b2f2f",
            hover_color="#a33a3a",
            command=self._cancel,
        )
        self.cancel_btn.pack(side="right")

    # --- thread plumbing ----------------------------------------------------

    def _ui(self, fn: Callable[[], None]) -> None:
        """Run fn on the Tk thread (safe to call from any thread)."""
        self._ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                try:
                    fn = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    # One bad update must never freeze the window.
                    self._report_error()
        finally:
            self.after(80, self._drain_ui_queue)

    def report_callback_exception(self, exc, val, tb) -> None:  # Tk hook
        self._report_error((exc, val, tb))

    def _report_error(self, exc_info=None) -> None:
        text = "".join(traceback.format_exception(*(exc_info or sys.exc_info())))
        try:
            with open(data_dir() / "errors.log", "a", encoding="utf-8") as fh:
                fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} grabbr {__version__}\n")
                fh.write(text)
        except OSError:
            pass
        try:
            last = text.strip().splitlines()[-1]
            self.log(f"Internal error: {last} (details in errors.log)", "error")
        except Exception:
            pass

    def _spawn(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _bar(self, fraction: float = 0.0, busy: bool = False) -> None:
        """Set the progress bar, or animate it when there's no measurable progress."""
        if busy:
            if not self._bar_busy:
                self.progress.configure(mode="indeterminate")
                self.progress.start()
                self._bar_busy = True
            return
        if self._bar_busy:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self._bar_busy = False
        self.progress.set(max(0.0, min(1.0, fraction)))

    def log(self, text: str, tag: str | None = None) -> None:
        self.log_box.configure(state="normal")
        if tag:
            self.log_box.insert("end", text + "\n", tag)
        else:
            self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def log_bg(self, text: str, tag: str | None = None) -> None:
        self._ui(lambda: self.log(text, tag))

    def _set_busy(self, what: str | None) -> None:
        self._busy = what
        idle = "normal" if what is None else "disabled"
        # Lock every setting while a download or tool install is running.
        # ("Open" folder stays usable: it doesn't change anything.)
        for widget in (
            self.url_entry,
            self.format_btn,
            self.playlist_box,
            self.clip_box,
        ):
            widget.configure(state=idle)
        # CTkButton only dims its text when disabled; grey the whole button so
        # it's obvious it can't be clicked.
        normal_fg = ctk.ThemeManager.theme["CTkButton"]["fg_color"]
        for button in (self.grab_btn, self.change_btn, self.setup_btn, self.update_btn):
            button.configure(
                state=idle,
                fg_color=normal_fg if what is None else LOCKED_BUTTON_COLOR,
                text_color_disabled=LOCKED_TEXT_COLOR,
            )
        self.cancel_btn.configure(state="normal" if what == "download" else "disabled")

    # --- settings handlers --------------------------------------------------

    def _on_format(self, label: str) -> None:
        if self._busy:  # shouldn't happen (widget is disabled), but be safe
            self.format_btn.set(FORMAT_NAMES[self.settings.audio_format])
            return
        self.settings.audio_format = FORMAT_LABELS[label]
        self.settings.save()

    def _on_playlist(self) -> None:
        if self._busy:
            self.playlist_var.set(self.settings.playlist)
            return
        self.settings.playlist = bool(self.playlist_var.get())
        self.settings.save()

    def _on_clipboard_toggle(self) -> None:
        if self._busy:
            self.clip_var.set(self.settings.watch_clipboard)
            return
        self.settings.watch_clipboard = bool(self.clip_var.get())
        self.settings.save()

    def _choose_folder(self) -> None:
        if self._busy:
            return
        chosen = filedialog.askdirectory(initialdir=self.settings.output_dir, mustexist=False)
        if chosen:
            self.settings.output_dir = str(Path(chosen))
            self.out_label.configure(text=self.settings.output_dir)
            self.settings.save()

    def _open_output(self) -> None:
        path = Path(self.settings.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    def _poll_clipboard(self) -> None:
        try:
            if self.settings.watch_clipboard and self._busy is None:
                text = self.clipboard_get().strip()
                if text != self._last_clipboard:
                    self._last_clipboard = text
                    if is_supported_url(text) and self.url_entry.get().strip() != text:
                        self.url_entry.delete(0, "end")
                        self.url_entry.insert(0, text)
                        self.status.configure(text="Link pasted from clipboard. Hit Grab.")
        except tk.TclError:
            pass  # clipboard empty or not text
        self.after(1000, self._poll_clipboard)

    # --- downloads ----------------------------------------------------------

    def _start_download(self) -> None:
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not looks_like_url(url):
            self.status.configure(text="That doesn't look like a link.")
            return
        if tools.find("yt-dlp") is None:
            self.log("yt-dlp isn't installed yet. Click 'Set up tools' first.", "error")
            return
        if tools.find("ffmpeg") is None:
            self.log("ffmpeg isn't installed yet. Click 'Set up tools' first.", "error")
            return

        opts = DownloadOptions(
            output_dir=self.settings.output_dir,
            audio_format=self.settings.audio_format,
            playlist=self.settings.playlist,
        )
        self.log(f"\n▶ {url}")
        self._bar(0)
        self.status.configure(text="Starting…")
        self._set_busy("download")
        self._download = Download(url, opts, lambda e: self._ui(lambda: self._on_event(e)))
        self._download.start()

    def _on_event(self, event: Event) -> None:
        if isinstance(event, Progress):
            prefix = ""
            if event.index and event.count and event.count > 1:
                prefix = f"[{event.index}/{event.count}] "
            if event.fraction is not None:
                # Downloading only fills part of the bar; conversion and tagging
                # still have to happen, so never show "full" until Finished.
                item = event.fraction * DOWNLOAD_SHARE
                if event.index and event.count and event.count > 1:
                    item = (event.index - 1 + item) / event.count
                self._bar(item)
                pct = f"{event.fraction * 100:.0f}%"
            else:
                pct = human_bytes(event.downloaded)
            speed = f"{human_bytes(event.speed)}/s" if event.speed else ""
            bits = [b for b in (pct, speed, human_eta(event.eta)) if b]
            self.status.configure(text=f"{prefix}{event.title}  ·  " + "  ·  ".join(bits))
        elif isinstance(event, Stage):
            self._bar(busy=True)  # yt-dlp gives no % for ffmpeg work, so animate
            self.status.configure(text=f"{event.text}…  {event.title}")
        elif isinstance(event, FileDone):
            self.log(f"✓ Saved {event.path}", "ok")
        elif isinstance(event, Log):
            if event.level != "info" or not event.text.startswith("[download]"):
                self.log(event.text, event.level if event.level != "info" else None)
        elif isinstance(event, Finished):
            self._download = None
            self._set_busy(None)
            if event.ok:
                self._bar(1)
                n = len(event.files)
                self.status.configure(text=f"Done. {n} file{'s' if n != 1 else ''} saved.")
                self.log(f"✓ Done ({n} file{'s' if n != 1 else ''})", "ok")
                self.url_entry.delete(0, "end")
                self.bell()
            elif event.cancelled:
                self._bar(0)
                self.status.configure(text="Cancelled.")
                self.log("Cancelled.", "warning")
            else:
                self._bar(0)
                self.status.configure(text="Download failed. See log.")
                self.log(event.error or "Download failed.", "error")
                if event.error and ("Sign in" in event.error or "403" in event.error):
                    self.log("Tip: YouTube errors are usually fixed by 'Update yt-dlp'.", "warning")

    def _cancel(self) -> None:
        if self._download:
            self.status.configure(text="Cancelling…")
            self._download.cancel()

    # --- tools --------------------------------------------------------------

    def _check_tools_startup(self) -> None:
        for name, tool in tools.TOOLS.items():
            path = tools.find(name)
            if path:
                where = "grabbr" if tools.is_managed(path) else "PATH"
                self.log_bg(f"{name}: {tools.version(name)}  ({where})")
            else:
                level = "error" if tool.required else "warning"
                self.log_bg(f"{name}: not installed ({tool.why})", level)
        absent = tools.missing()
        if absent and tools.IS_WINDOWS:
            self._ui(lambda: self._offer_setup(absent))

    def _offer_setup(self, absent: list[str]) -> None:
        if messagebox.askyesno(
            "grabbr setup",
            "grabbr needs to download a few free tools before it can work:\n\n"
            + "\n".join(f"  • {n}: {tools.TOOLS[n].why}" for n in absent)
            + "\n\nThis is a one-time download of roughly 150–200 MB "
            "from GitHub. Download now?",
        ):
            self._setup_tools()

    def _setup_tools(self) -> None:
        if self._busy:
            return
        absent = tools.missing()
        if not absent:
            self.log("All tools are installed.", "ok")
            return
        self._set_busy("tools")
        self._spawn(lambda: self._install_tools(absent))

    def _install_tools(self, names: list[str]) -> None:
        for name in names:
            self._ui(lambda n=name: self.status.configure(text=f"Downloading {n}…"))

            def progress(frac: float | None, n: str = name) -> None:
                if frac is not None:
                    self._ui(lambda f=frac: self._bar(f))

            try:
                tools.install(name, self.log_bg, progress)
            except tools.ToolError as exc:
                self.log_bg(str(exc), "error")
        still = tools.missing()

        def done() -> None:
            self._set_busy(None)
            self._bar(0)
            if still:
                self.status.configure(text="Some tools are still missing. See log.")
            else:
                self.status.configure(text="Tools ready. Paste a link!")
                self.log("All tools installed.", "ok")

        self._ui(done)

    def _update_ytdlp(self) -> None:
        if self._busy:
            return
        self._set_busy("tools")
        self.status.configure(text="Updating yt-dlp…")

        def work() -> None:
            try:
                tools.update_ytdlp(self.log_bg)
                self.log_bg(f"yt-dlp is now {tools.version('yt-dlp')}", "ok")
            except tools.ToolError as exc:
                self.log_bg(str(exc), "error")
            self._ui(lambda: (self._set_busy(None), self.status.configure(text="Ready.")))

        self._spawn(work)

    def _on_close(self) -> None:
        if self._download:
            self._download.cancel()
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    GrabbrApp().mainloop()


if __name__ == "__main__":
    main()
