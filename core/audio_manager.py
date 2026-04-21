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
try:
    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    pygame = None
    _PYGAME_AVAILABLE = False
import speech_recognition as sr
try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    _OWW_AVAILABLE = True
except ImportError:
    openwakeword = None  # type: ignore[assignment]
    OWWModel = None  # type: ignore[assignment,misc]
    _OWW_AVAILABLE = False
import pyaudio

import config

logger = logging.getLogger(__name__)


class AudioManager:
    _SOUND_MAP = {
        "startup": "assets/sounds/startup.wav",
        "wake":    "assets/sounds/wake.wav",
        "success": "assets/sounds/success.wav",
        "error":   "assets/sounds/error.wav",
    }
    _SECONDS_PER_CHAR = 0.08

    def __init__(self, stop_event: threading.Event, output_callback=None):
        """Initialise audio subsystem.

        Args:
            stop_event:       Shared ``threading.Event`` used to interrupt TTS playback.
            output_callback:  Optional ``Callable[[str], None]``.  When provided,
                              :meth:`speak` routes text through this callback instead
                              of performing TTS directly.  The caller is responsible
                              for consuming the callback and later calling
                              :meth:`speak_tts` from the appropriate thread.
                              This removes the need for monkey-patching ``speak``.
        """
        self.stop_event     = stop_event
        self._output_cb     = output_callback
        self.current_volume = 0
        self.whisper_model  = None
        self._speak_lock    = threading.Lock()
        self._noise_calibrated = False
        self._oww_ready     = threading.Event()
        self.mic_available  = False

        print(f"{Fore.CYAN}⚙️ Ініціалізація нейронного слуху...")
        try:
            if _PYGAME_AVAILABLE:
                pygame.mixer.init()
            self.recognizer = sr.Recognizer()
            self.audio_interface = pyaudio.PyAudio()

            count = self.audio_interface.get_device_count()
            logger.info("Доступні пристрої вводу:")
            for i in range(count):
                info = self.audio_interface.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    logger.info(f"  [{i}] {info.get('name', '?')}")

            self.mic_available = self.check_microphone()

            self.whisper_model = self._load_whisper()

        except Exception as e:
            logger.error(f"Помилка ініціалізації аудіо: {e}")
            if self.whisper_model is None:
                print(f"{Fore.RED}⚠️ Запуск Whisper на CPU...")
                self.whisper_model = self._load_whisper(force_cpu=True)

        threading.Thread(target=self._load_oww_model, daemon=True, name="OWWLoader").start()

    def _load_oww_model(self):
        """Завантаження openwakeword у фоні — не блокує старт"""
        if not _OWW_AVAILABLE:
            logger.warning("OWW: бібліотека openwakeword недоступна. Буде використано Whisper-fallback.")
            self.owwModel = None
            self._oww_ready.set()
            return
        try:
            model_name    = getattr(config, "OWW_MODEL_NAME",    "hey_jarvis")
            vad_threshold = getattr(config, "OWW_VAD_THRESHOLD", 0.5)

            # Use the OWW API to locate bundled models — avoids hard-coded paths
            # that differ between platforms / install methods.
            pretrained_paths = openwakeword.get_pretrained_model_paths("onnx")
            model_stem = model_name.replace(" ", "_")
            model_exists = any(
                os.path.splitext(os.path.basename(p))[0] == model_stem
                and os.path.exists(p)
                for p in pretrained_paths
            )

            if not model_exists:
                logger.info(f"OWW: модель '{model_name}' не знайдена, завантажую з мережі...")
                # download_models() does not accept a model filter; it fetches all
                # pre-trained ONNX models (a one-time ~50 MB download).
                openwakeword.utils.download_models()
            else:
                logger.info(f"OWW: модель '{model_name}' вже є, завантаження пропущено.")
            self.owwModel = OWWModel(
                wakeword_models=[model_name],
                inference_framework="onnx",
                vad_threshold=vad_threshold,
            )
            logger.info(f"OWW: модель '{model_name}' готова (VAD threshold={vad_threshold}).")
        except Exception as e:
            logger.error(f"OWW: помилка завантаження моделі: {e}")
            self.owwModel = None
        finally:
            self._oww_ready.set()

    def check_microphone(self) -> bool:
        """Check whether at least one input (microphone) device is available.

        Returns:
            ``True`` if an input device is found, ``False`` otherwise.
        """
        try:
            count = self.audio_interface.get_device_count()
            for i in range(count):
                info = self.audio_interface.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    logger.info(f"Мікрофон знайдено: {info.get('name', 'невідомий')}")
                    return True
            logger.warning("Мікрофон не знайдено. Голосовий ввід недоступний.")
            return False
        except Exception as e:
            logger.error(f"Помилка перевірки мікрофону: {e}")
            return False

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
        """Безпечний запуск async з будь-якого потоку"""
        try:
            loop = asyncio.get_running_loop()
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)
        except RuntimeError:
            return asyncio.run(coro)

    def play_fx(self, sound_type: str) -> None:
        if not _PYGAME_AVAILABLE:
            return
        path = self._SOUND_MAP.get(sound_type)
        if not path or not os.path.exists(path):
            return
        try:
            fx = pygame.mixer.Sound(path)
            fx.set_volume(0.5)
            fx.play()
        except Exception as e:
            logger.error(f"Помилка програвання FX '{sound_type}': {e}")

    def speak(self, text: str) -> None:
        """Route *text* through the output callback when one is configured,
        otherwise fall back to direct TTS via :meth:`speak_tts`."""
        if self._output_cb is not None:
            self._output_cb(text)
        else:
            self.speak_tts(text)

    def speak_tts(self, text: str) -> None:
        """Perform TTS synthesis and blocking audio playback directly."""
        clean_text = re.sub(r'[*#_]', '', text).strip()
        if not clean_text:
            return

        print(f"{Fore.GREEN}🤖 [ДЖАРВІС]: {clean_text}")

        tmp      = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        tmp_path = tmp.name
        tmp.close()

        async def generate_audio():
            communicate  = edge_tts.Communicate(clean_text, "uk-UA-OstapNeural", rate="+30%")
            audio_buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.extend(chunk["data"])
            with open(tmp_path, "wb") as f:
                f.write(audio_buffer)

        with self._speak_lock:
            try:
                self._run_async(generate_audio())
                if not _PYGAME_AVAILABLE:
                    logger.warning("TTS: pygame недоступний, відтворення пропущено.")
                    return
                pygame.mixer.music.load(tmp_path)
                self.stop_event.clear()
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

    def listen_for_wake_word(self) -> bool:
        if not self.mic_available:
            logger.warning("listen_for_wake_word: мікрофон недоступний.")
            return False
        # Чекаємо поки модель завантажиться у фоні (максимум 60 сек)
        if not self._oww_ready.wait(timeout=60):
            logger.error("OWW: модель не завантажилась за 60 секунд.")
            if config.OWW_FALLBACK_TO_WHISPER:
                return self._whisper_wake_word_fallback()
            return False
        if self.owwModel is None:
            logger.warning("OWW: модель недоступна, перехід на Whisper-fallback.")
            if config.OWW_FALLBACK_TO_WHISPER:
                return self._whisper_wake_word_fallback()
            return False

        print(f"\n{Fore.CYAN}🎧 Очікування команди...")
        self.current_volume = 0
        stream = None
        mic_index = getattr(config, "MIC_INDEX", None)

        try:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    stream = self.audio_interface.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=16000,
                        input=True,
                        frames_per_buffer=1280,
                        input_device_index=mic_index,
                    )
                    break
                except OSError as e:
                    logger.warning(f"OWW: мікрофон недоступний (спроба {attempt+1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    else:
                        logger.error("OWW: не вдалося відкрити мікрофон після всіх спроб.")
                        if config.OWW_FALLBACK_TO_WHISPER:
                            return self._whisper_wake_word_fallback()
                        return False

            while True:
                if self.stop_event.is_set():
                    return False

                data_raw = stream.read(1280, exception_on_overflow=False)
                data     = np.frombuffer(data_raw, dtype=np.int16)

                if len(data) > 0:
                    rms = np.sqrt(np.mean(data.astype(np.float64) ** 2))
                    self.current_volume = rms

                prediction = self.owwModel.predict(
                    data,
                    debounce_time=config.OWW_DEBOUNCE_TIME,
                    # threshold tells OWW when to reset the debounce timer; the
                    # external check below still guards the application trigger.
                    threshold={config.OWW_MODEL_NAME: config.OWW_WAKE_THRESHOLD},
                )
                score = max(prediction.values(), default=0)
                if score > 0.1:
                    bar = '█' * int(score * 20)
                    print(f"\r🔊 OWW score: {score:.3f} [{bar:<20}]", end="", flush=True)
                if score > config.OWW_WAKE_THRESHOLD:
                    detected_model = max(prediction, key=prediction.get) if prediction else "unknown"
                    logger.info(f"OWW: wake word виявлено! модель='{detected_model}', score={score:.3f}")
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

    def _whisper_wake_word_fallback(self) -> bool:
        """Fallback: записує ~3 секунди через speech_recognition і перевіряє
        чи є слово 'джарвіс' у Whisper-транскрипції."""
        if self.whisper_model is None:
            logger.error("Whisper-fallback: Whisper не завантажено, wake word неможливо детектувати.")
            return False

        print(f"\n{Fore.YELLOW}🎧 [Whisper-fallback] Очікування команди 'джарвіс'...")
        tmp      = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            with sr.Microphone() as source:
                if not self._noise_calibrated:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                    self._noise_calibrated = True
                self.recognizer.pause_threshold       = 1.0
                self.recognizer.non_speaking_duration = 0.3
                audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=3)

            with open(tmp_path, "wb") as f:
                f.write(audio.get_wav_data())

            segments, _ = self.whisper_model.transcribe(
                tmp_path,
                beam_size=1,
                best_of=1,
                language="uk",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
            )
            text = "".join(s.text for s in segments).strip().lower()
            logger.debug(f"Whisper-fallback: розпізнано '{text}'")

            if "джарвіс" in text or "jarvis" in text:
                logger.info(f"Whisper-fallback: wake word виявлено в тексті '{text}'")
                self.play_fx("wake")
                self.current_volume = 0
                self.speak("Слухаю.")
                return True
            return False

        except sr.WaitTimeoutError:
            return False
        except OSError as e:
            logger.error(f"Whisper-fallback: не вдалося відкрити мікрофон: {e}")
            return False
        except Exception as e:
            logger.error(f"Whisper-fallback: помилка: {e}")
            return False
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def listen_for_command(self, dictation_mode: bool = False) -> str | None:
        if not self.mic_available:
            logger.warning("listen_for_command: мікрофон недоступний.")
            return None
        if self.whisper_model is None:
            logger.error("Whisper не завантажено.")
            return None

        tmp      = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            with sr.Microphone() as source:
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
                best_of=1,
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

    def close(self) -> None:
        try:
            if hasattr(self, 'audio_interface') and self.audio_interface:
                self.audio_interface.terminate()
                logger.info("PyAudio завершено.")
        except Exception as e:
            logger.error(f"Помилка закриття PyAudio: {e}")

    def __del__(self) -> None:
        self.close()