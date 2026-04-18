import os
import sys
import threading
import queue
import time
import random
import math
import psutil
import customtkinter as ctk

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import JarvisUltimate


class JarvisHUD(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("J.A.R.V.I.S. HUD - System Core")
        ctk.set_appearance_mode("dark")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = min(780, sw - 100), min(800, sh - 100)
        self.geometry(f"{w}x{h}")
        self.minsize(600, 500)

        self.font_main  = ("Consolas", 13)
        self.font_bold  = ("Consolas", 13, "bold")
        self.font_small = ("Consolas", 11)
        self.accent_color = "#00ffcc"

        self.jarvis      = None
        self.ui_queue    = queue.Queue()
        self._ready      = threading.Event()
        self._msg_count  = 0
        self._start_time = time.time()

        # --- Анімація waveform ---
        self.phase  = 0.0
        self.bars   = []
        self.glow_bars = []

        # --- Typewriter: одна активна задача ---
        self._tw_buffer   = []   # список (char, tag) для друку
        self._tw_running  = False

        # --- Прогрес-бари: цільові значення (без рекурсивних after) ---
        self._metric_targets = {"cpu": 0.0, "ram": 0.0, "bat": 0.0}
        self._metric_current = {"cpu": 0.0, "ram": 0.0, "bat": 0.0}

        self._build_ui()
        self._configure_tags()
        self.after(300, self._rebuild_bars)

        threading.Thread(target=self._start_jarvis, daemon=True).start()

        self._tick()          # ✅ ОДИН головний тік замість 4 окремих after-циклів

    # -----------------------------------------------------------------------
    # ГОЛОВНИЙ ТІК — замінює draw_wave / update_metrics / check_queue окремо
    # -----------------------------------------------------------------------
    def _tick(self) -> None:
        """
        ✅ Один after(50) = 20 FPS.
        Всі анімації та черги обробляються тут — жодних паралельних after-циклів.
        """
        now = time.time()

        # 1. Черга UI
        self._process_ui_queue()

        # 2. Typewriter — один символ за тік
        self._typewriter_step()

        # 3. Waveform
        self._draw_wave_step()

        # 4. Метрики (раз на секунду)
        if not hasattr(self, '_last_metrics') or now - self._last_metrics >= 1.0:
            self._update_metrics_targets()
            self._last_metrics = now

        # 5. Плавна анімація прогрес-барів
        self._animate_metrics_step()

        # 6. Uptime
        elapsed = int(now - self._start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self.uptime_label.configure(text=f"UPTIME: {h:02d}:{m:02d}:{s:02d}")

        self.after(50, self._tick)

    # -----------------------------------------------------------------------
    # ПОБУДОВА UI
    # -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=0)
        self.grid_rowconfigure(4, weight=0)

        self._build_header()
        self._build_visualizer()
        self._build_chat()
        self._build_input()
        self._build_sidebar()
        self._build_statusbar()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="#111111", height=55, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_propagate(False)

        ctk.CTkLabel(
            header,
            text="⚡ J.A.R.V.I.S. SYSTEM INTERFACE",
            font=("Consolas", 18, "bold"),
            text_color=self.accent_color
        ).pack(side="left", padx=20, pady=15)

        self.uptime_label = ctk.CTkLabel(
            header, text="UPTIME: 00:00:00",
            font=self.font_small, text_color="#333333"
        )
        self.uptime_label.pack(side="right", padx=20)

    def _build_visualizer(self) -> None:
        viz = ctk.CTkFrame(
            self, fg_color="#080808", height=75,
            border_width=1, border_color="#1a1a1a"
        )
        viz.grid(row=1, column=0, columnspan=2, padx=15, pady=(8, 0), sticky="nsew")
        viz.grid_propagate(False)

        self.canvas = ctk.CTkCanvas(viz, bg="#080808", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

    def _build_chat(self) -> None:
        self.chat_log = ctk.CTkTextbox(
            self,
            state="disabled",
            font=self.font_main,
            fg_color="#070707",
            border_width=1,
            border_color="#1a1a1a",
            wrap="word"
        )
        self.chat_log.grid(row=2, column=0, padx=(15, 5), pady=(10, 5), sticky="nsew")

    def _build_input(self) -> None:
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=3, column=0, padx=(15, 5), pady=(0, 12), sticky="nsew")

        self.command_entry = ctk.CTkEntry(
            f,
            placeholder_text="Введіть команду...",
            font=self.font_main,
            fg_color="#0d0d0d",
            border_color="#222222",
            text_color=self.accent_color,
            height=40
        )
        self.command_entry.pack(fill="x", side="left", expand=True)
        self.command_entry.bind("<Return>", self.process_manual_input)

        ctk.CTkButton(
            f, text="▶", width=42, height=40,
            fg_color="#111111", hover_color="#003d33",
            border_width=1, border_color="#222222",
            text_color=self.accent_color, font=("Consolas", 16),
            command=self.process_manual_input
        ).pack(side="left", padx=(6, 0))

    def _build_sidebar(self) -> None:
        sf = ctk.CTkFrame(
            self, fg_color="#0d0d0d",
            border_width=1, border_color="#1a1a1a"
        )
        sf.grid(row=1, column=1, rowspan=3, padx=(5, 15), pady=(8, 12), sticky="nsew")
        self.stats_frame = sf

        self._create_metric("CPU",     "cpu", "#ff4444")
        self._create_metric("RAM",     "ram", "#4499ff")
        self._create_metric("BATTERY", "bat", "#ffee44")

        ctk.CTkFrame(sf, fg_color="#1a1a1a", height=1).pack(fill="x", padx=10, pady=10)

        self.mic_label = ctk.CTkLabel(
            sf, text="🎤  IDLE",
            font=self.font_bold, text_color="#333333"
        )
        self.mic_label.pack(pady=(5, 0))

        self.ready_label = ctk.CTkLabel(
            sf, text="⏳ LOADING",
            font=self.font_small, text_color="#ffaa00"
        )
        self.ready_label.pack(pady=(4, 0))

        ctk.CTkFrame(sf, fg_color="#1a1a1a", height=1).pack(fill="x", padx=10, pady=10)

        self.msg_count_label = ctk.CTkLabel(
            sf, text="MSG: 0",
            font=self.font_small, text_color="#333333"
        )
        self.msg_count_label.pack(pady=(0, 4))

        ctk.CTkButton(
            sf, text="🗑  CLEAR", width=110, height=30,
            fg_color="#0d0d0d", hover_color="#1a0505",
            border_width=1, border_color="#2a1010",
            text_color="#ff4444", font=self.font_small,
            command=self._clear_chat
        ).pack(pady=(4, 0))

    def _build_statusbar(self) -> None:
        b = ctk.CTkFrame(
            self, fg_color="#080808", height=26,
            corner_radius=0, border_width=1, border_color="#1a1a1a"
        )
        b.grid(row=4, column=0, columnspan=2, sticky="nsew")
        b.grid_propagate(False)

        self.status_text = ctk.CTkLabel(
            b, text="● SYSTEM LOADING",
            font=self.font_small, text_color="#ffaa00"
        )
        self.status_text.pack(side="left", padx=15)

        self.provider_label = ctk.CTkLabel(
            b, text="AI: —",
            font=self.font_small, text_color="#333333"
        )
        self.provider_label.pack(side="right", padx=15)

    # -----------------------------------------------------------------------
    # ТЕГИ
    # -----------------------------------------------------------------------
    def _configure_tags(self) -> None:
        tb = self.chat_log._textbox
        tb.tag_configure("jarvis", foreground=self.accent_color)
        tb.tag_configure("user",   foreground="#cccccc")
        tb.tag_configure("time",   foreground="#2a2a2a")

    # -----------------------------------------------------------------------
    # МЕТРИКИ
    # -----------------------------------------------------------------------
    def _create_metric(self, label: str, key: str, color: str) -> None:
        ctk.CTkLabel(
            self.stats_frame, text=label,
            font=("Consolas", 10, "bold"), text_color="#444444"
        ).pack(pady=(12, 0))

        bar = ctk.CTkProgressBar(
            self.stats_frame, width=115, height=8,
            progress_color=color, fg_color="#1a1a1a"
        )
        bar.set(0)
        bar.pack(pady=3)

        lbl = ctk.CTkLabel(
            self.stats_frame, text="0%",
            font=self.font_small, text_color="#666666"
        )
        lbl.pack(pady=(0, 2))

        setattr(self, f"_{key}_bar", bar)
        setattr(self, f"_{key}_lbl", lbl)

    def _update_metrics_targets(self) -> None:
        """Зчитуємо реальні значення — викликається раз на секунду"""
        self._metric_targets["cpu"] = psutil.cpu_percent(interval=None) / 100
        self._metric_targets["ram"] = psutil.virtual_memory().percent / 100

        battery = psutil.sensors_battery()
        if battery:
            self._metric_targets["bat"] = battery.percent / 100
            plug = "⚡" if battery.power_plugged else "🔋"
            self._bat_lbl.configure(text=f"{plug}{battery.percent:.0f}%")
        else:
            self._metric_targets["bat"] = 0
            self._bat_lbl.configure(text="N/A")

        self._cpu_lbl.configure(text=f"{self._metric_targets['cpu']*100:.0f}%")
        self._ram_lbl.configure(text=f"{self._metric_targets['ram']*100:.0f}%")

    def _animate_metrics_step(self) -> None:
        """
        ✅ Easing за один крок тіку — без рекурсивних after().
        Викликається кожні 50мс з _tick().
        """
        for key in ("cpu", "ram", "bat"):
            cur = self._metric_current[key]
            tgt = self._metric_targets[key]
            nxt = cur + (tgt - cur) * 0.12
            self._metric_current[key] = nxt
            getattr(self, f"_{key}_bar").set(nxt)

    # -----------------------------------------------------------------------
    # WAVEFORM
    # -----------------------------------------------------------------------
    def _rebuild_bars(self) -> None:
        self.canvas.delete("all")
        self.bars      = []
        self.glow_bars = []
        self.canvas.update_idletasks()

        width  = self.canvas.winfo_width()  or 500
        height = self.canvas.winfo_height() or 75
        n      = 28
        step   = width // (n + 2)
        mid_y  = height // 2

        for i in range(n):
            x    = step + i * step
            glow = self.canvas.create_line(
                x, mid_y, x, mid_y,
                fill="#080808", width=10, capstyle="round"
            )
            bar  = self.canvas.create_line(
                x, mid_y, x, mid_y,
                fill="#0a0a0a", width=3, capstyle="round"
            )
            self.glow_bars.append(glow)
            self.bars.append(bar)

    def _draw_wave_step(self) -> None:
        """Викликається з _tick() — без власного after()"""
        if not self.bars:
            return
        try:
            vol    = getattr(self.jarvis.audio, "current_volume", 0) if self.jarvis else 0
            height = self.canvas.winfo_height() or 75
            mid_y  = height // 2
            scaled = min(vol / 60, 1.0)

            # ✅ Синус + шум
            self.phase = (self.phase + 0.2) % (2 * math.pi)

            t  = max(0.0, min(scaled, 1.0))
            mc = f"#{0:02x}{int(255*t):02x}{int(200*t):02x}"
            gc = f"#{0:02x}{int(80*t):02x}{int(60*t):02x}"

            for i, (bar, glow) in enumerate(zip(self.bars, self.glow_bars)):
                wave  = math.sin(self.phase + i * 0.35)
                noise = random.uniform(0.85, 1.15)
                h     = (mid_y - 5) * scaled * wave * noise

                coords = self.canvas.coords(bar)
                if not coords:
                    continue
                x = coords[0]
                self.canvas.coords(bar,  x, mid_y - h, x, mid_y + h)
                self.canvas.coords(glow, x, mid_y - h, x, mid_y + h)
                self.canvas.itemconfig(bar,  fill=mc)
                self.canvas.itemconfig(glow, fill=gc)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # TYPEWRITER — один символ за тік
    # -----------------------------------------------------------------------
    def _typewriter_step(self) -> None:
        """
        ✅ Один символ за виклик (50мс) — не перевантажує UI.
        При великих текстах — без лагів.
        """
        if not self._tw_buffer:
            return
        char, tag = self._tw_buffer.pop(0)
        tb = self.chat_log._textbox
        tb.configure(state="normal")
        tb.insert("end", char, tag)
        tb.configure(state="disabled")
        tb.yview("end")

    def _enqueue_typewriter(self, text: str, tag: str) -> None:
        for ch in text:
            self._tw_buffer.append((ch, tag))
        self._tw_buffer.append(("\n", tag))

    def _insert_message(self, prefix: str, text: str, tag: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        tb = self.chat_log._textbox
        tb.configure(state="normal")
        tb.insert("end", f"[{timestamp}] ", "time")
        tb.insert("end", f"{prefix} ",       tag)
        tb.configure(state="disabled")
        self._enqueue_typewriter(text, tag)

    # -----------------------------------------------------------------------
    # ЧЕРГА UI
    # -----------------------------------------------------------------------
    def _process_ui_queue(self) -> None:
        try:
            while True:
                msg    = self.ui_queue.get_nowait()
                sender = msg["sender"]
                text   = msg["text"]

                if sender == "STATUS":
                    is_online = "ONLINE" in text.upper()
                    is_error  = "ERROR"  in text.upper()

                    self.status_text.configure(
                        text=f"● {text.upper()}",
                        text_color=(
                            self.accent_color if is_online
                            else "#ff4444" if is_error
                            else "#ffaa00"
                        )
                    )
                    if "слухаю" in text.lower():
                        self.mic_label.configure(text="🎤  ACTIVE", text_color=self.accent_color)
                    else:
                        self.mic_label.configure(text="🎤  IDLE",   text_color="#333333")

                    if is_online:
                        self.ready_label.configure(text="✅ ONLINE", text_color="#00ff00")
                    elif is_error:
                        self.ready_label.configure(text="❌ ERROR",  text_color="#ff4444")

                else:
                    is_jarvis = sender != "Степан"
                    prefix    = "JARVIS ›" if is_jarvis else "USER   ›"
                    tag       = "jarvis"   if is_jarvis else "user"
                    self._insert_message(prefix, text, tag)
                    self._msg_count += 1
                    self.msg_count_label.configure(text=f"MSG: {self._msg_count}")

        except queue.Empty:
            pass

    def update_ui(self, sender: str, text: str) -> None:
        self.ui_queue.put({"sender": sender, "text": text})

    # -----------------------------------------------------------------------
    # ЗАПУСК ДЖАРВІСА
    # -----------------------------------------------------------------------
    def _start_jarvis(self) -> None:
        try:
            self.jarvis = JarvisUltimate()
            self.jarvis.ui_callback = self.update_ui
            self.jarvis.start_ears()
            self.jarvis.start_mouth()
            self.jarvis.audio.play_fx("startup")
            self.jarvis.enqueue_speech(
                f"Всі системи в нормі. Поточний інтелект: {self.jarvis.llm.active_provider}."
            )
            self.update_ui("STATUS", "SYSTEM ONLINE")
        except Exception as e:
            self.update_ui("STATUS", f"INIT ERROR: {e}")
        finally:
            self._ready.set()

        if self.jarvis:
            self.after(0, lambda: self.provider_label.configure(
                text=f"AI: {self.jarvis.llm.active_provider.upper()}",
                text_color=self.accent_color
            ))
            while self.jarvis.is_running:
                try:
                    cmd = self.jarvis.input_queue.get(timeout=1)
                    if cmd:
                        threading.Thread(
                            target=self.jarvis._process_command,
                            args=(cmd,),
                            daemon=True,
                            name="BrainWorker"
                        ).start()
                except queue.Empty:
                    pass

    # -----------------------------------------------------------------------
    # ВВЕДЕННЯ КОМАНДИ
    # -----------------------------------------------------------------------
    def process_manual_input(self, event=None) -> None:
        cmd = self.command_entry.get().strip()
        if not cmd:
            return
        self.update_ui("Степан", cmd)
        self.command_entry.delete(0, "end")

        if not self._ready.is_set():
            self.update_ui("STATUS", "Система ще ініціалізується...")
            threading.Thread(target=self._wait_and_send, args=(cmd,), daemon=True).start()
        else:
            self.jarvis.input_queue.put(cmd)

    def _wait_and_send(self, cmd: str) -> None:
        self._ready.wait(timeout=60)
        if self.jarvis:
            self.jarvis.input_queue.put(cmd)

    # -----------------------------------------------------------------------
    # ОЧИЩЕННЯ ЧАТУ
    # -----------------------------------------------------------------------
    def _clear_chat(self) -> None:
        self._tw_buffer.clear()
        tb = self.chat_log._textbox
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.configure(state="disabled")
        self._msg_count = 0
        self.msg_count_label.configure(text="MSG: 0")


# ---------------------------------------------------------------------------
# ЗАПУСК
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = JarvisHUD()
    app.mainloop()