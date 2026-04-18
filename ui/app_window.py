import os
import sys
import threading
import queue
import time
import random
import psutil
import customtkinter as ctk

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import JarvisUltimate


class JarvisHUD(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("J.A.R.V.I.S. HUD - System Core")
        ctk.set_appearance_mode("dark")

        # ✅ Адаптивний розмір під екран
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = min(700, sw - 100), min(750, sh - 100)
        self.geometry(f"{w}x{h}")

        self.font_main  = ("Consolas", 13)
        self.font_bold  = ("Consolas", 13, "bold")
        self.accent_color = "#00ffcc"

        self.jarvis     = None          # ✅ Явна ініціалізація
        self.ui_queue   = queue.Queue()
        self._ready     = threading.Event()  # ✅ Сигнал готовності Джарвіса

        self._build_ui()

        # Запускаємо Джарвіса у фоні
        threading.Thread(target=self._start_jarvis, daemon=True).start()

        self.check_queue()
        self.update_metrics()
        self.draw_wave()

    # -----------------------------------------------------------------------
    # ПОБУДОВА UI
    # -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        # --- Шапка ---
        self.header = ctk.CTkFrame(self, fg_color="#1a1a1a", height=60, corner_radius=0)
        self.header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ctk.CTkLabel(
            self.header,
            text="⚡ J.A.R.V.I.S. SYSTEM INTERFACE",
            font=("Consolas", 20, "bold"),
            text_color=self.accent_color
        ).pack(pady=15)

        # --- Візуалізатор ---
        viz_frame = ctk.CTkFrame(
            self, fg_color="#0d0d0d", height=80,
            border_width=1, border_color="#333333"
        )
        viz_frame.grid(row=0, column=0, columnspan=2, padx=15, pady=(70, 0), sticky="nsew")

        self.canvas = ctk.CTkCanvas(viz_frame, bg="#0d0d0d", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bars = []
        for i in range(20):
            x   = 100 + i * 20
            bar = self.canvas.create_line(x, 40, x, 40, fill=self.accent_color, width=3, capstyle="round")
            self.bars.append(bar)

        # --- Лог чату ---
        self.chat_log = ctk.CTkTextbox(
            self,
            state="disabled",
            font=self.font_main,
            fg_color="#0d0d0d",
            text_color="#00ff00",
            border_width=1,
            border_color="#333333"
        )
        self.chat_log.grid(row=1, column=0, padx=(15, 5), pady=(15, 5), sticky="nsew")

        # ✅ Теги ��ля кольорів різних відправників
        self.chat_log.tag_config("jarvis", foreground=self.accent_color)
        self.chat_log.tag_config("user",   foreground="#ffffff")
        self.chat_log.tag_config("time",   foreground="#555555")

        # --- Поле вводу ---
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=2, column=0, padx=(15, 5), pady=(0, 15), sticky="nsew")

        self.command_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="Введіть команду для системи...",
            font=self.font_main,
            fg_color="#0d0d0d",
            border_color="#333333",
            text_color=self.accent_color,
            height=40
        )
        self.command_entry.pack(fill="x", side="left", expand=True)
        self.command_entry.bind("<Return>", self.process_manual_input)

        # --- Панель метрик ---
        self.stats_frame = ctk.CTkFrame(self, fg_color="#1a1a1a", width=150)
        self.stats_frame.grid(row=1, column=1, rowspan=2, padx=(5, 15), pady=15, sticky="nsew")

        self._create_metric("CPU USAGE", "cpu_val", "#ff3333")
        self._create_metric("RAM USAGE", "ram_val", "#3399ff")
        self._create_metric("BATTERY",   "bat_val", "#ffff33")

        self.mic_label = ctk.CTkLabel(
            self.stats_frame, text="🎤 MIC: IDLE",
            font=self.font_bold, text_color="#666666"
        )
        self.mic_label.pack(pady=20)

        # ✅ Індикатор готовності Джарвіса
        self.ready_label = ctk.CTkLabel(
            self.stats_frame, text="⏳ LOADING...",
            font=("Consolas", 11), text_color="#ffaa00"
        )
        self.ready_label.pack(pady=5)

        # --- Статус-бар ---
        status_bar = ctk.CTkFrame(self, fg_color="#111111", height=30)
        status_bar.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.status_text = ctk.CTkLabel(
            status_bar, text="SYSTEM LOADING",
            font=self.font_bold, text_color="#ffaa00"
        )
        self.status_text.pack(side="left", padx=20)

    def _create_metric(self, label_text: str, attr_name: str, color: str) -> None:
        ctk.CTkLabel(self.stats_frame, text=label_text, font=("Consolas", 11, "bold")).pack(pady=(15, 0))
        bar = ctk.CTkProgressBar(self.stats_frame, width=120, height=10, progress_color=color)
        bar.set(0)
        bar.pack(pady=5)
        lbl = ctk.CTkLabel(self.stats_frame, text="0%", font=self.font_main)
        lbl.pack(pady=(0, 5))
        setattr(self, f"{attr_name}_bar", bar)
        setattr(self, f"{attr_name}_lbl", lbl)

    # -----------------------------------------------------------------------
    # ЗАПУСК ДЖАРВІСА
    # -----------------------------------------------------------------------
    def _start_jarvis(self) -> None:
        """✅ run() замінено на власний цикл — без sys.exit()"""
        self.jarvis = JarvisUltimate()
        self.jarvis.ui_callback = self.update_ui

        # Запускаємо потоки Джарвіса
        self.jarvis.start_ears()
        self.jarvis.start_mouth()
        self.jarvis.audio.play_fx("startup")
        self.jarvis.enqueue_speech(
            f"Всі системи в нормі. Поточний інтелект: {self.jarvis.llm.active_provider}."
        )

        # ✅ Сигналізуємо UI що Джарвіс готовий
        self._ready.set()
        self.update_ui("STATUS", "SYSTEM ONLINE")

        # ✅ Власний цикл замість run() — не викликає sys.exit()
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
        self.command_entry.delete(0, 'end')

        # ✅ Якщо Джарвіс ще не готовий — чекаємо у фоні
        if not self._ready.is_set():
            self.update_ui("STATUS", "Система ще ініціалізується...")
            threading.Thread(
                target=self._wait_and_send,
                args=(cmd,),
                daemon=True
            ).start()
        else:
            self.jarvis.input_queue.put(cmd)

    def _wait_and_send(self, cmd: str) -> None:
        """✅ Чекає готовності Джарвіса і відправляє команду"""
        self._ready.wait(timeout=60)
        if self.jarvis:
            self.jarvis.input_queue.put(cmd)

    # -----------------------------------------------------------------------
    # МЕТРИКИ
    # -----------------------------------------------------------------------
    def update_metrics(self) -> None:
        # ✅ interval=None — швидко, але перший виклик не 0.0
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        battery     = psutil.sensors_battery()
        bat_percent = battery.percent if battery else 100

        self.cpu_val_bar.set(cpu / 100)
        self.cpu_val_lbl.configure(text=f"{cpu:.0f}%")
        self.ram_val_bar.set(ram / 100)
        self.ram_val_lbl.configure(text=f"{ram:.0f}%")
        self.bat_val_bar.set(bat_percent / 100)
        self.bat_val_lbl.configure(text=f"{bat_percent:.0f}%")

        self.after(1000, self.update_metrics)

    # -----------------------------------------------------------------------
    # ВІЗУАЛІЗАЦІЯ
    # -----------------------------------------------------------------------
    def draw_wave(self) -> None:
        try:
            vol        = getattr(self.jarvis.audio, "current_volume", 0) if self.jarvis else 0
            scaled_vol = min(vol / 40, 35)
            color      = self.accent_color if vol > 50 else "#222222"

            for i, bar in enumerate(self.bars):
                offset = random.uniform(0.6, 1.3)  # ✅ random імпортовано на рівні модуля
                h      = scaled_vol * offset
                x      = 100 + i * 20
                self.canvas.coords(bar, x, 40 - h, x, 40 + h)
                self.canvas.itemconfig(bar, fill=color)
        except Exception:
            pass
        self.after(50, self.draw_wave)

    # -----------------------------------------------------------------------
    # ЧЕРГА UI
    # -----------------------------------------------------------------------
    def update_ui(self, sender: str, text: str) -> None:
        self.ui_queue.put({"sender": sender, "text": text})

    def check_queue(self) -> None:
        try:
            while True:
                msg    = self.ui_queue.get_nowait()
                sender = msg["sender"]
                text   = msg["text"]

                if sender == "STATUS":
                    self.status_text.configure(
                        text=text.upper(),
                        text_color=self.accent_color if "ONLINE" in text.upper() else "#ffaa00"
                    )
                    if "слухаю" in text.lower():
                        self.mic_label.configure(text="🎤 MIC: ACTIVE", text_color=self.accent_color)
                        self.ready_label.configure(text="✅ ONLINE", text_color="#00ff00")
                    elif "loading" not in text.lower():
                        self.mic_label.configure(text="🎤 MIC: IDLE", text_color="#666666")
                else:
                    # ✅ Різні кольори для Джарвіса і користувача
                    timestamp = time.strftime("%H:%M:%S")
                    is_jarvis = sender != "Степан"
                    prefix    = "JARVIS:" if is_jarvis else ">>"
                    tag       = "jarvis" if is_jarvis else "user"

                    self.chat_log.configure(state="normal")
                    self.chat_log.insert("end", f"[{timestamp}] ", "time")
                    self.chat_log.insert("end", f"{prefix} {text}\n", tag)
                    self.chat_log.configure(state="disabled")
                    self.chat_log.yview("end")

        except queue.Empty:
            pass
        self.after(100, self.check_queue)


# ---------------------------------------------------------------------------
# ЗАПУСК
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = JarvisHUD()
    app.mainloop()