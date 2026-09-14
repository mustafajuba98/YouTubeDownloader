"""Main application window."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import customtkinter as ctk
import requests
from PIL import Image

from app.downloader import (
    DownloadManager,
    MediaInfo,
    fetch_info,
    format_bytes,
    format_duration,
    format_views,
    has_ffmpeg,
    is_mix_url,
    is_youtube_url,
    prefer_single_video,
    progress_fraction,
)
from app.theme import COLORS, FONTS


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("dark-blue")

        self.title("YouTube Downloader")
        self.geometry("1020x760")
        self.minsize(900, 680)
        self.configure(fg_color=COLORS["bg"])

        self.manager = DownloadManager()
        self.media: Optional[MediaInfo] = None
        self._thumb_image: Optional[ctk.CTkImage] = None
        self._fetch_token = 0
        self._progress_ui_ts = 0.0
        self._progress_peak = 0.0
        self._progress_phase = 0
        self._load_playlist = False
        self._item_selected: list[bool] = []

        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.output_dir = tk.StringVar(value=os.path.normpath(os.path.join(base, "downloads")))
        self.quality_var = tk.StringVar(value="Best available")
        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Paste a YouTube link to begin.")
        self.progress_text = tk.StringVar(value="")
        self.items_meta_var = tk.StringVar(value="0 selected")

        icon_path = os.path.join(base, "assets", "app.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_ffmpeg_hint()

    def _update_ffmpeg_hint(self) -> None:
        if has_ffmpeg():
            self.status_var.set("Paste a YouTube link to begin.")
            return
        self.status_var.set(
            "Warning: ffmpeg missing — video+audio merge may fail. Re-run run.bat to install deps."
        )

    # ── layout ──────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        root.pack(fill="both", expand=True, padx=24, pady=20)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header,
            text="YouTube Downloader",
            font=FONTS["title"],
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Fast fetches, clear preview, playlists without freezing the UI.",
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(2, 0))

        # URL
        url_card = self._card(root)
        url_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        url_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_card, text="Link", font=FONTS["heading"], text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(14, 6))

        self.url_entry = ctk.CTkEntry(
            url_card,
            textvariable=self.url_var,
            height=40,
            font=FONTS["body"],
            placeholder_text="YouTube video or playlist URL",
            fg_color=COLORS["surface"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            placeholder_text_color=COLORS["text_muted"],
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(0, 14))
        self.url_entry.bind("<Return>", lambda _e: self.fetch_media(False))

        self.fetch_btn = ctk.CTkButton(
            url_card,
            text="Fetch",
            width=100,
            height=40,
            font=FONTS["body"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#FFFFFF",
            command=lambda: self.fetch_media(False),
        )
        self.fetch_btn.grid(row=1, column=1, padx=(0, 6), pady=(0, 14))

        self.fetch_list_btn = ctk.CTkButton(
            url_card,
            text="Fetch playlist",
            width=120,
            height=40,
            font=FONTS["small"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=lambda: self.fetch_media(True),
        )
        self.fetch_list_btn.grid(row=1, column=2, padx=(0, 6), pady=(0, 14))

        self.clear_btn = ctk.CTkButton(
            url_card,
            text="Clear",
            width=80,
            height=40,
            font=FONTS["body"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.clear_all,
        )
        self.clear_btn.grid(row=1, column=3, padx=(0, 16), pady=(0, 14))

        # Preview + options
        mid = ctk.CTkFrame(root, fg_color="transparent")
        mid.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        mid.grid_columnconfigure(0, weight=3)
        mid.grid_columnconfigure(1, weight=2)
        mid.grid_rowconfigure(0, weight=1)

        preview = self._card(mid)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview.grid_columnconfigure(1, weight=1)

        self.thumb_label = ctk.CTkLabel(
            preview,
            text="Preview",
            width=200,
            height=112,
            fg_color=COLORS["surface_soft"],
            corner_radius=10,
            text_color=COLORS["text_muted"],
        )
        self.thumb_label.grid(row=0, column=0, rowspan=3, padx=14, pady=14, sticky="nw")

        self.title_label = ctk.CTkLabel(
            preview,
            text="No media loaded",
            font=FONTS["heading"],
            text_color=COLORS["text"],
            wraplength=400,
            justify="left",
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(14, 2))

        self.meta_label = ctk.CTkLabel(
            preview,
            text="Title, channel, and duration will appear here.",
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
            wraplength=400,
            justify="left",
            anchor="w",
        )
        self.meta_label.grid(row=1, column=1, sticky="ew", padx=(0, 14))

        self.kind_badge = ctk.CTkLabel(
            preview,
            text="",
            font=FONTS["small"],
            text_color=COLORS["accent"],
            fg_color=COLORS["accent_soft"],
            corner_radius=6,
            padx=10,
            pady=3,
        )
        self.kind_badge.grid(row=2, column=1, sticky="w", padx=(0, 14), pady=(8, 14))

        opt = self._card(mid)
        opt.grid(row=0, column=1, sticky="nsew")
        opt.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            opt, text="Download options", font=FONTS["heading"], text_color=COLORS["text"]
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))

        ctk.CTkLabel(
            opt, text="Quality", font=FONTS["small"], text_color=COLORS["text_muted"]
        ).grid(row=1, column=0, sticky="w", padx=14)
        self.quality_menu = ctk.CTkOptionMenu(
            opt,
            variable=self.quality_var,
            values=["Best available", "Audio only (MP3)"],
            height=34,
            font=FONTS["body"],
            fg_color=COLORS["surface_soft"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            text_color=COLORS["text"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_text_color=COLORS["text"],
            dropdown_hover_color=COLORS["accent_soft"],
        )
        self.quality_menu.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 10))

        ctk.CTkLabel(
            opt, text="Save to", font=FONTS["small"], text_color=COLORS["text_muted"]
        ).grid(row=3, column=0, sticky="w", padx=14)

        folder_row = ctk.CTkFrame(opt, fg_color="transparent")
        folder_row.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 14))
        folder_row.grid_columnconfigure(0, weight=1)
        self.folder_entry = ctk.CTkEntry(
            folder_row,
            textvariable=self.output_dir,
            height=34,
            font=FONTS["small"],
            fg_color=COLORS["surface"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        self.folder_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            folder_row,
            text="Browse",
            width=78,
            height=34,
            font=FONTS["small"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.browse_folder,
        ).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(
            folder_row,
            text="Open",
            width=70,
            height=34,
            font=FONTS["small"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.open_folder,
        ).grid(row=0, column=2)

        # Items list (lightweight native listbox — handles hundreds of rows)
        list_card = self._card(root)
        list_card.grid(row=3, column=0, sticky="nsew", pady=(0, 12))
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(1, weight=1)

        list_header = ctk.CTkFrame(list_card, fg_color="transparent")
        list_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        list_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            list_header, text="Items", font=FONTS["heading"], text_color=COLORS["text"]
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            list_header,
            textvariable=self.items_meta_var,
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        btns = ctk.CTkFrame(list_header, fg_color="transparent")
        btns.grid(row=0, column=2, sticky="e")
        for text, cmd in (
            ("Select all", lambda: self._set_all_items(True)),
            ("Select none", lambda: self._set_all_items(False)),
            ("Invert", self._invert_selection),
        ):
            ctk.CTkButton(
                btns,
                text=text,
                width=88,
                height=28,
                font=FONTS["small"],
                fg_color=COLORS["surface_soft"],
                hover_color=COLORS["border"],
                text_color=COLORS["text"],
                command=cmd,
            ).pack(side="left", padx=(0, 6))

        list_wrap = tk.Frame(list_card, bg=COLORS["surface"], highlightthickness=0)
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        list_wrap.grid_columnconfigure(0, weight=1)
        list_wrap.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "YT.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface_soft"],
            foreground=COLORS["text"],
            rowheight=30,
            borderwidth=0,
            font=FONTS["small"],
        )
        style.configure(
            "YT.Treeview.Heading",
            background=COLORS["surface_soft"],
            foreground=COLORS["text_muted"],
            relief="flat",
            font=FONTS["small"],
        )
        style.map(
            "YT.Treeview",
            background=[("selected", COLORS["accent_soft"])],
            foreground=[("selected", COLORS["text"])],
        )

        cols = ("sel", "num", "title", "duration")
        self.tree = ttk.Treeview(
            list_wrap,
            columns=cols,
            show="headings",
            style="YT.Treeview",
            selectmode="extended",
        )
        self.tree.heading("sel", text="✓")
        self.tree.heading("num", text="#")
        self.tree.heading("title", text="Title")
        self.tree.heading("duration", text="Length")
        self.tree.column("sel", width=44, anchor="center", stretch=False)
        self.tree.column("num", width=50, anchor="center", stretch=False)
        self.tree.column("title", width=560, anchor="w", stretch=True)
        self.tree.column("duration", width=80, anchor="center", stretch=False)

        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)

        hint = ctk.CTkLabel(
            list_card,
            text="Tip: click the ✓ column to toggle. Mix links fetch the current video only — use “Fetch playlist” for the list (max 100).",
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
        )
        hint.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        # Progress
        bottom = self._card(root)
        bottom.grid(row=4, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(
            bottom,
            height=14,
            progress_color=COLORS["progress"],
            fg_color=COLORS["surface_soft"],
            corner_radius=8,
        )
        self.progress.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.progress.set(0)

        prog_row = ctk.CTkFrame(bottom, fg_color="transparent")
        prog_row.grid(row=1, column=0, sticky="ew", padx=16)
        prog_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            prog_row,
            textvariable=self.status_var,
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            prog_row,
            textvariable=self.progress_text,
            font=FONTS["small"],
            text_color=COLORS["text_muted"],
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

        actions = ctk.CTkFrame(bottom, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="e", padx=16, pady=(10, 14))

        self.pause_btn = ctk.CTkButton(
            actions,
            text="Pause",
            width=96,
            height=38,
            font=FONTS["body"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            state="disabled",
            command=self.toggle_pause,
        )
        self.pause_btn.pack(side="left", padx=(0, 8))

        self.cancel_btn = ctk.CTkButton(
            actions,
            text="Stop",
            width=96,
            height=38,
            font=FONTS["body"],
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border"],
            text_color=COLORS["danger"],
            state="disabled",
            command=self.cancel_download,
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))

        self.download_btn = ctk.CTkButton(
            actions,
            text="Download",
            width=140,
            height=38,
            font=FONTS["body"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#FFFFFF",
            command=self.start_download,
        )
        self.download_btn.pack(side="left")

    def _card(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=COLORS["surface"],
            corner_radius=14,
            border_width=1,
            border_color=COLORS["border"],
        )

    # ── list helpers ────────────────────────────────────────
    def _render_items(self, items) -> None:
        self.tree.delete(*self.tree.get_children())
        self._item_selected = []
        if not items:
            self.items_meta_var.set("0 selected")
            return

        for idx, item in enumerate(items, start=1):
            selected = bool(item.selected)
            self._item_selected.append(selected)
            self.tree.insert(
                "",
                "end",
                iid=str(idx - 1),
                values=(
                    "☑" if selected else "☐",
                    str(idx),
                    item.title,
                    format_duration(item.duration),
                ),
            )
        self._refresh_selection_meta()

    def _refresh_selection_meta(self) -> None:
        total = len(self._item_selected)
        selected = sum(1 for x in self._item_selected if x)
        self.items_meta_var.set(f"{selected} / {total} selected")

    def _toggle_index(self, index: int) -> None:
        if index < 0 or index >= len(self._item_selected):
            return
        self._item_selected[index] = not self._item_selected[index]
        vals = list(self.tree.item(str(index), "values"))
        vals[0] = "☑" if self._item_selected[index] else "☐"
        self.tree.item(str(index), values=vals)
        self._refresh_selection_meta()

    def _on_tree_click(self, event) -> Optional[str]:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return None
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return None
        if col == "#1":  # sel column
            self._toggle_index(int(row))
            return "break"
        return None

    def _on_tree_space(self, _event) -> str:
        for iid in self.tree.selection():
            self._toggle_index(int(iid))
        return "break"

    def _set_all_items(self, value: bool) -> None:
        for i in range(len(self._item_selected)):
            self._item_selected[i] = value
            vals = list(self.tree.item(str(i), "values"))
            if not vals:
                continue
            vals[0] = "☑" if value else "☐"
            self.tree.item(str(i), values=vals)
        self._refresh_selection_meta()

    def _invert_selection(self) -> None:
        for i in range(len(self._item_selected)):
            self._toggle_index(i)

    def _selected_urls(self) -> list[str]:
        if not self.media:
            return []
        urls = []
        for item, selected in zip(self.media.entries, self._item_selected):
            if selected:
                urls.append(item.url)
        return urls

    # ── actions ─────────────────────────────────────────────
    def browse_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(os.path.normpath(path))

    def open_folder(self) -> None:
        path = os.path.normpath(self.output_dir.get().strip() or ".")
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def clear_all(self) -> None:
        if self.manager.busy():
            messagebox.showinfo("Busy", "Stop the current download first.")
            return
        self.url_var.set("")
        self.media = None
        self._thumb_image = None
        self.thumb_label.configure(image=None, text="Preview")
        self.title_label.configure(text="No media loaded")
        self.meta_label.configure(text="Title, channel, and duration will appear here.")
        self.kind_badge.configure(text="")
        self.quality_menu.configure(values=["Best available", "Audio only (MP3)"])
        self.quality_var.set("Best available")
        self._render_items([])
        self.progress.set(0)
        self.progress_text.set("")
        self._update_ffmpeg_hint()
        if has_ffmpeg():
            self.status_var.set("Paste a YouTube link to begin.")

    def fetch_media(self, load_playlist: bool) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing link", "Paste a YouTube URL first.")
            return
        if not is_youtube_url(url):
            messagebox.showwarning(
                "Invalid link",
                "This doesn't look like a YouTube / YouTube Music URL.",
            )
            return

        if load_playlist and is_mix_url(url):
            if not messagebox.askyesno(
                "YouTube Mix",
                "This looks like a Mix / Radio list (can be huge and slow).\n\n"
                "Load up to 100 items?",
            ):
                return

        self._fetch_token += 1
        token = self._fetch_token
        self.fetch_btn.configure(state="disabled", text="…")
        self.fetch_list_btn.configure(state="disabled")
        self.status_var.set(
            "Fetching playlist…" if load_playlist else "Fetching video info…"
        )

        def work() -> None:
            err: Optional[str] = None
            info: Optional[MediaInfo] = None
            try:
                # Auto: watch+mix → single video unless load_playlist
                force_list = load_playlist or (
                    "/playlist" in url and not prefer_single_video(url)
                )
                info = fetch_info(url, load_playlist=force_list, playlist_limit=100)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)

            def apply() -> None:
                if token != self._fetch_token:
                    return
                self.fetch_btn.configure(state="normal", text="Fetch")
                self.fetch_list_btn.configure(state="normal")
                if err or info is None:
                    self.status_var.set("Could not fetch media.")
                    messagebox.showerror("Fetch failed", err or "Unknown error")
                    return
                self.media = info
                self._apply_media(info)
                note = ""
                if info.is_mix and info.kind == "playlist":
                    note = " (Mix truncated to 100)"
                elif not load_playlist and is_mix_url(self.url_var.get()):
                    note = " — Mix detected, loaded current video only"
                self.status_var.set(
                    f"Ready — {len(info.entries)} item(s){note}."
                )

            self.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _apply_media(self, info: MediaInfo) -> None:
        self.title_label.configure(text=info.title)
        if info.kind == "playlist":
            meta = f"{info.channel or 'Playlist'}  ·  {len(info.entries)} videos"
            self.kind_badge.configure(text="Mix" if info.is_mix else "Playlist")
        else:
            meta = "  ·  ".join(
                [
                    info.channel or "Unknown channel",
                    format_duration(info.duration),
                    format_views(info.view_count),
                ]
            )
            self.kind_badge.configure(text="Video")
        self.meta_label.configure(text=meta)

        labels = [f["label"] for f in info.formats] or ["Best available"]
        self.quality_menu.configure(values=labels)
        self.quality_var.set(labels[0])
        self._render_items(info.entries)
        self._load_thumbnail(info.thumbnail)

    def _load_thumbnail(self, url: str) -> None:
        if not url:
            self.thumb_label.configure(image=None, text="No thumbnail")
            return

        def work() -> None:
            try:
                resp = requests.get(url, timeout=12)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img.thumbnail((200, 112))
                ctk_img = ctk.CTkImage(light_image=img, size=img.size)

                def apply() -> None:
                    self._thumb_image = ctk_img
                    self.thumb_label.configure(image=ctk_img, text="")

                self.after(0, apply)
            except Exception:
                self.after(
                    0,
                    lambda: self.thumb_label.configure(image=None, text="No thumbnail"),
                )

        threading.Thread(target=work, daemon=True).start()

    def _selected_quality_value(self) -> str:
        label = self.quality_var.get()
        if self.media:
            for fmt in self.media.formats:
                if fmt["label"] == label:
                    return fmt["value"]
        if "Audio" in label:
            return "audio"
        if label.endswith("p") and label[:-1].isdigit():
            return label[:-1]
        return "best"

    def _apply_progress(self, pct: float, text: str) -> None:
        self.progress.set(max(0.0, min(1.0, pct)))
        self.progress_text.set(text)

    def start_download(self) -> None:
        if self.manager.busy():
            messagebox.showinfo("Busy", "A download is already running.")
            return
        if not self.media:
            messagebox.showinfo("Fetch first", "Fetch the link so we can show info and qualities.")
            return

        urls = self._selected_urls()
        if not urls:
            messagebox.showwarning("Nothing selected", "Select at least one item.")
            return

        out_dir = os.path.normpath(self.output_dir.get().strip())
        if not out_dir:
            messagebox.showwarning("Folder", "Choose a download folder.")
            return

        quality = self._selected_quality_value()
        if quality == "audio" and "MP3" in self.quality_var.get() and not has_ffmpeg():
            messagebox.showwarning(
                "ffmpeg required",
                "MP3 needs ffmpeg.\nRe-run run.bat to install imageio-ffmpeg.",
            )
            return

        self.progress.set(0)
        self.progress_text.set("Starting…")
        self._set_running_ui(True)
        self._progress_ui_ts = 0.0
        self._progress_peak = 0.0
        self._progress_phase = 0

        def on_progress(d: dict) -> None:
            status = d.get("status")
            now = time.time()
            if status == "downloading" and (now - self._progress_ui_ts) < 0.1:
                return
            self._progress_ui_ts = now

            if status == "downloading":
                pct = progress_fraction(d)
                # Video then audio: avoid the bar jumping back to 0
                if pct + 0.15 < self._progress_peak and self._progress_peak > 0.2:
                    self._progress_phase = 1
                if self._progress_phase == 0:
                    display = pct * 0.55
                else:
                    display = 0.55 + pct * 0.4
                self._progress_peak = max(self._progress_peak, display)

                speed = d.get("speed") or 0
                eta = d.get("eta")
                speed_txt = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "—"
                eta_txt = f"{int(eta)}s" if isinstance(eta, (int, float)) else "—"
                done = format_bytes(d.get("downloaded_bytes"))
                total = format_bytes(
                    d.get("total_bytes") or d.get("total_bytes_estimate")
                )
                stage = "video" if self._progress_phase == 0 else "audio"
                text = (
                    f"{self._progress_peak * 100:.1f}%  ·  {stage}  ·  "
                    f"{done}/{total}  ·  {speed_txt}  ·  ETA {eta_txt}"
                )
                peak = self._progress_peak
                self.after(0, lambda p=peak, t=text: self._apply_progress(p, t))
            elif status == "finished":
                self._progress_peak = max(self._progress_peak, 0.95)
                self.after(
                    0,
                    lambda: self._apply_progress(0.97, "Merging / saving…"),
                )

        def on_status(msg: str) -> None:
            self.after(0, lambda m=msg: self.status_var.set(m))

        def on_finished(ok: int, fail: int, files: list[str]) -> None:
            def done() -> None:
                self._set_running_ui(False)
                if ok and not fail:
                    self.progress.set(1)
                    self.progress_text.set(f"Saved {ok} file(s)")
                    if messagebox.askyesno(
                        "Download complete",
                        f"Saved {ok} file(s) to:\n{out_dir}\n\nOpen folder?",
                    ):
                        self.open_folder()
                elif ok:
                    self.progress_text.set(f"{ok} ok, {fail} failed")
                    messagebox.showwarning(
                        "Partial success",
                        f"{ok} downloaded, {fail} failed.\nFolder:\n{out_dir}",
                    )
                else:
                    self.progress.set(0)
                    self.progress_text.set("Failed")
                    messagebox.showerror(
                        "Download failed",
                        "No files were saved.\n\n"
                        "Common fix: install ffmpeg, or try another quality.\n"
                        f"Folder:\n{out_dir}",
                    )

            self.after(0, done)

        try:
            self.manager.start(
                urls=urls,
                out_dir=out_dir,
                quality=quality,
                on_progress=on_progress,
                on_status=on_status,
                on_finished=on_finished,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_running_ui(False)
            messagebox.showerror("Download", str(exc))

    def toggle_pause(self) -> None:
        if not self.manager.busy():
            return
        if self.manager.is_paused:
            self.manager.resume()
            self.pause_btn.configure(text="Pause")
            self.status_var.set("Resumed.")
        else:
            self.manager.pause()
            self.pause_btn.configure(text="Resume")
            self.status_var.set("Paused.")

    def cancel_download(self) -> None:
        if self.manager.busy():
            self.manager.cancel()
            self.status_var.set("Stopping…")

    def _set_running_ui(self, running: bool) -> None:
        state_on = "normal"
        state_off = "disabled"
        if running:
            self.download_btn.configure(state=state_off)
            self.fetch_btn.configure(state=state_off)
            self.fetch_list_btn.configure(state=state_off)
            self.pause_btn.configure(state=state_on, text="Pause")
            self.cancel_btn.configure(state=state_on)
        else:
            self.download_btn.configure(state=state_on)
            self.fetch_btn.configure(state=state_on)
            self.fetch_list_btn.configure(state=state_on)
            self.pause_btn.configure(state=state_off, text="Pause")
            self.cancel_btn.configure(state=state_off)

    def _on_close(self) -> None:
        if self.manager.busy():
            if not messagebox.askyesno("Quit", "A download is still running. Stop and quit?"):
                return
            self.manager.cancel()
        self.destroy()


def run() -> None:
    app = App()
    app.mainloop()
