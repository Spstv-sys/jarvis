import os
import re
import time
import asyncio
import threading
import logging
import tempfile
import numpy as np
from colorama import Fore

import edge_tts
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
import speech_recognition as sr
import openwakeword
from openwakeword.model import Model
import pyaudio

logger = logging.getLogger(__name__)


class AudioManager:
    _SOUND_MAP = {
        "startup": "assets/sounds/startup.wav",
        "wake":    "assets/sounds/wake.wav",
        "success": "assets/sounds/success.wav",
        "error":   "assets/sounds/error.wav",
    }
    _SECONDS_PER_CHAR = 0.08

    def __init__(self, stop_event: threading.Event):
        self.stop_event     = stop_event
        self.current_volume = 0
        self.whisper_model  = None
        self._speak_lock    = threading.Lock()
        # ✅ Калібруємо шум тільки один раз
        self._noise_calibrated = False

        print(f"{Fore.CYAN}⚙️ Ініціалізація нейронного слуху...")
        try:
            pygame.mixer.init()
            self.recognizer = sr.Recognizer()

            openwakeword.utils.download_models()
            self.owwModel        = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            self.audio_interface = pyaudio.PyAudio()

            if self.audio_interface.get_device_count() == 0:
                logger.warning("Аудіопристрої вводу не знайдені.")

            self.whisper_model = self._load_whisper()

        except Exception as e:
            logger.error(f"Помилка ініціалізації аудіо: {e}")
            if self.whisper_model is None:
                print(f"{Fore.RED}⚠️ Запуск Whisper на CPU...")
                self.whisper_model = self._load_whisper(force_cpu=True)

    # -------------------------------------------------------------------------
    # ХЕЛПЕРИ
    # -------------------------------------------------------------------------
    @staticmethod
    def _load_whisper(force_cpu: bool = False):
        from faster_whisper import WhisperModel
        try:
            if not force_cpu:
                model = WhisperModel("turbo", device="cuda", compute_type="float16")
                logger.info("Whisper завантажено на CUDA.")
                return model
        except Exception as e:
            logger.warning(f"CUDA недоступна: {e}")
        try:
            model = WhisperModel("turbo", device="cpu", compute_type="int8")
            logger.info("Whisper завантажено на CPU.")
            return model
        except Exception as e:
            logger.error(f"❌ Критична помилка завантаження Whisper: {e}")
            return None

    @staticmethod
    def _run_async(coro):
        """✅ Безпечний запуск async з будь-якого потоку"""
        try:
            loop = asyncio.get_running_loop()
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)
        except RuntimeError:
            return asyncio.run(coro)

    # -------------------------------------------------------------------------
    # ЗВУКОВІ ЕФЕКТИ
    # -------------------------------------------------------------------------
    def play_fx(self, sound_type: str) -> None:
        path = self._SOUND_MAP.get(sound_type)
        if not path or not os.path.exists(path):
            return
        try:
            fx = pygame.mixer.Sound(path)
            fx.set_volume(0.5)
            fx.play()
        except Exception as e:
            logger.error(f"Помилка програвання FX '{sound_type}': {e}")

    # -------------------------------------------------------------------------
    # TTS
    # -------------------------------------------------------------------------
    def speak(self, text: str) -> None:
        clean_text = re.sub(r'[*#_]', '', text).strip()
        if not clean_text:
            return

        print(f"{Fore.GREEN}🤖 [ДЖАРВІС]: {clean_text}")
        self.stop_event.clear()

        tmp      = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()

        async def generate_audio():
            communicate  = edge_tts.Communicate(clean_text, "uk-UA-OstapNeural", rate="+30%")
            # ✅ Стримінг — збираємо чанки одразу без зайвих проміжних записів
            audio_buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.extend(chunk["data"])
            with open(tmp_path, "wb") as f:
                f.write(audio_buffer)

        with self._speak_lock:
            try:
                self._run_async(generate_audio())
                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.play()

                max_duration = max(10, len(clean_text) * self._SECONDS_PER_CHAR)
                start_time   = time.time()

                while pygame.mixer.music.get_busy():
                    if self.stop_event.is_set():
                        pygame.mixer.music.stop()
                        break
                    if time.time() - start_time > max_duration:
                        logger.warning("TTS: перевищено максимальний час відтворення.")
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.1)

                pygame.mixer.music.unload()
            except Exception as e:
                logger.error(f"Помилка TTS: {e}")
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass

    # -------------------------------------------------------------------------
    # WAKE WORD
    # -------------------------------------------------------------------------
    def listen_for_wake_word(self) -> bool:
        print(f"\n{Fore.CYAN}🎧 Очікування команди...")
        self.current_volume = 0
        stream = None

        try:
            stream = self.audio_interface.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1280
            )

            while True:
                if self.stop_event.is_set():
                    return False

                data_raw = stream.read(1280, exception_on_overflow=False)
                data     = np.frombuffer(data_raw, dtype=np.int16)

                if len(data) > 0:
                    rms = np.sqrt(np.mean(data.astype(np.float64) ** 2))
                    self.current_volume = rms

                prediction = self.owwModel.predict(data)
                score = max(
                    prediction.get('hey_jarvis',      0),
                    prediction.get('hey_jarvis_v0.1', 0)
                )
                if score > 0.35:
                    self.play_fx("wake")
                    self.current_volume = 0
                    self.speak("Слухаю.")
                    return True

        except Exception as e:
            logger.error(f"Помилка прослуховування WakeWord: {e}")
            self.current_volume = 0
            return False
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # КОМАНДА
    # -------------------------------------------------------------------------
    def listen_for_command(self, dictation_mode: bool = False) -> str | None:
        if self.whisper_model is None:
            logger.error("Whisper не завантажено.")
            return None

        tmp      = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            with sr.Microphone() as source:
                # ✅ Калібруємо шум тільки один раз — економить 1 сек кожної команди
                if not self._noise_calibrated:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                    self._noise_calibrated = True

                self.recognizer.pause_threshold       = 2.0 if dictation_mode else 1.2
                self.recognizer.non_speaking_duration = 0.5

                print(f"{Fore.CYAN}🎤 Слухаю вас...")
                limit = 180 if dictation_mode else 15
                audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=limit)

            with open(tmp_path, "wb") as f:
                f.write(audio.get_wav_data())

            segments, _ = self.whisper_model.transcribe(
                tmp_path,
                beam_size=1,
                best_of=1,         # ✅ не перебирає варіанти
                language="uk",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
                condition_on_previous_text=False
            )

            text = "".join(s.text for s in segments).strip().lower()

            if text:
                print(f"{Fore.YELLOW}💬 Ви: {text}")
                return text
            return None

        except sr.WaitTimeoutError:
            return None
        except Exception as e:
            logger.error(f"Помилка розпізнавання: {e}")
            return None
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # -------------------------------------------------------------------------
    # CLEANUP
    # -------------------------------------------------------------------------
    def close(self) -> None:
        try:
            if hasattr(self, 'audio_interface') and self.audio_interface:
                self.audio_interface.terminate()
                logger.info("PyAudio завершено.")
        except Exception as e:
            logger.error(f"Помилка закриття PyAudio: {e}")

    def __del__(self) -> None:
        self.close()