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

# Allow running this module as a script (python core/audio_manager.py) by
# ensuring the project root is on sys.path before importing top-level `config`.
if __name__ == "__main__" and __package__ in (None, ""):
    import sys
    _PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

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
        self._whisper_ready = threading.Event()
        self._speak_lock    = threading.Lock()
        self._is_speaking   = threading.Event()
        self._tts_cooldown_sec = 0.35
        self._noise_calibrated = False
        self._oww_ready     = threading.Event()
        self._oww_unavailable_logged = False
        self.mic_available  = False

        print(f"{Fore.CYAN}⚙️ Ініціалізація нейронного слуху...")
        try:
            if _PYGAME_AVAILABLE:
                pygame.mixer.init()
            self.recognizer = sr.Recognizer()
            self.audio_interface = pyaudio.PyAudio()

            self.mic_available = self.check_microphone()

        except Exception as e:
            logger.error(f"Помилка ініціалізації аудіо: {e}")

        # Whisper is large (~1GB) and slow to load (5-10s).
        # Defer to a background thread so server boot is not blocked.
        threading.Thread(
            target=self._load_whisper_async, daemon=True, name="WhisperLoader"
        ).start()
        threading.Thread(target=self._load_oww_model, daemon=True, name="OWWLoader").start()

    def _load_whisper_async(self) -> None:
        try:
            # Спробувати завантажити кастомну модель з whisper-final-model
            model = self._load_custom_whisper()
            if model is not None:
                self.whisper_model = model
                logger.info("Whisper: кастомна модель успішно завантажена з whisper-final-model.")
            else:
                # fallback: faster-whisper turbo
                model = self._load_whisper()
                if model is None:
                    print(f"{Fore.RED}⚠️ Запуск Whisper на CPU...")
                    model = self._load_whisper(force_cpu=True)
                self.whisper_model = model
        except Exception as e:
            logger.error(f"Whisper: помилка фонового завантаження: {e}")
        finally:
            self._whisper_ready.set()

    @staticmethod
    def _load_custom_whisper():
        """
        Завантаження Whisper з базової моделі та LoRA-адаптера через PEFT.
        Повертає pipeline для розпізнавання аудіо або None, якщо не вдалося.
        """
        try:
            from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor
            from peft import PeftModel
            import torch
            import os
            lora_adapter_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'whisper-final-model'))
            base_model_id = "openai/whisper-small"
            if not os.path.exists(lora_adapter_path):
                logger.error(f"Whisper: не знайдено LoRA-адаптер: {lora_adapter_path}")
                return None
            processor = AutoProcessor.from_pretrained(base_model_id)
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                base_model_id,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32
            )
            model = PeftModel.from_pretrained(model, lora_adapter_path)
            device = 0 if torch.cuda.is_available() else 'cpu'
            pipe = pipeline(
                "automatic-speech-recognition",
                model=model,
                tokenizer=processor.tokenizer,
                feature_extractor=processor.feature_extractor,
                device=device,
                return_timestamps=True,
                chunk_length_s=30,
                stride_length_s=5,
            )
            return pipe
        except Exception as e:
            logger.error(f"Whisper: не вдалося завантажити LoRA-адаптер: {e}")
            return None

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

            model_path = self._resolve_oww_model_path(model_name)
            if model_path is None:
                logger.info(f"OWW: модель '{model_name}' не знайдена, завантажую з мережі...")
                # download_models() does not accept a model filter; it fetches all
                # pre-trained ONNX models (a one-time ~50 MB download).
                openwakeword.utils.download_models()

                model_path = self._resolve_oww_model_path(model_name)
                if model_path is None:
                    raise FileNotFoundError(f"OWW model '{model_name}' not found after download")
            else:
                logger.info(f"OWW: модель '{model_name}' вже є, завантаження пропущено.")
            self.owwModel = OWWModel(
                wakeword_models=[model_path],
                inference_framework="onnx",
                vad_threshold=vad_threshold,
            )
            self._oww_unavailable_logged = False
            logger.info(f"OWW: модель '{model_name}' готова (VAD threshold={vad_threshold}).")
        except Exception as e:
            logger.error(f"OWW: помилка завантаження моделі: {e}")
            self.owwModel = None
        finally:
            self._oww_ready.set()

    @staticmethod
    def _resolve_oww_model_path(model_name: str):
        model_stem = model_name.replace(" ", "_")
        for path in openwakeword.get_pretrained_model_paths("onnx"):
            basename = os.path.basename(path)
            stem, ext = os.path.splitext(basename)
            if ext.lower() == ".onnx" and stem == model_stem:
                return path
        return None

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
            self._is_speaking.set()
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
                # Дати тиші стихнути в колонках, щоб мікрофон не вловив луну
                time.sleep(self._tts_cooldown_sec)
                self._is_speaking.clear()
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
            if not self._oww_unavailable_logged:
                logger.warning("OWW: модель недоступна, перехід на Whisper-fallback.")
                self._oww_unavailable_logged = True
            if config.OWW_FALLBACK_TO_WHISPER:
                return self._whisper_wake_word_fallback()
            return False

        # Не починати слухати поки TTS грає у колонках
        while self._is_speaking.is_set():
            if self.stop_event.is_set():
                return False
            time.sleep(0.05)

        print(f"\n{Fore.CYAN}🎧 Очікування команди...")
        self.current_volume = 0
        stream = None

        try:
            try:
                stream = self.audio_interface.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=1280
                )
            except OSError as e:
                logger.error(f"OWW: не вдалося відкрити мікрофон: {e}")
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
        # Wait briefly for Whisper to finish loading in the background.
        if self.whisper_model is None:
            self._whisper_ready.wait(timeout=30)
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
        # Wait briefly for Whisper to finish loading in the background.
        if self.whisper_model is None:
            self._whisper_ready.wait(timeout=30)
        if self.whisper_model is None:
            logger.error("Whisper не завантажено.")
            return None

        # Не починати запис поки TTS грає
        while self._is_speaking.is_set():
            if self.stop_event.is_set():
                return None
            time.sleep(0.05)

        tmp      = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            with sr.Microphone() as source:
                if not self._noise_calibrated:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                    self._noise_calibrated = True

                self.recognizer.pause_threshold       = (
                    config.DICTATION_PAUSE_THRESHOLD if dictation_mode
                    else config.LISTEN_PAUSE_THRESHOLD
                )
                self.recognizer.non_speaking_duration = config.LISTEN_NON_SPEAKING

                print(f"{Fore.CYAN}🎤 Слухаю вас...")
                limit = config.DICTATION_PHRASE_LIMIT if dictation_mode else config.LISTEN_PHRASE_LIMIT
                audio = self.recognizer.listen(
                    source,
                    timeout=config.LISTEN_TIMEOUT,
                    phrase_time_limit=limit,
                )

            # Записуємо WAV у 16 kHz / 16-bit моно — рідний формат Whisper.
            # Це усуває можливі артефакти ресемплінгу і робить вивід стабільним.
            wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)
            with open(tmp_path, "wb") as f:
                f.write(wav_bytes)

            # Діагностика: тип моделі, розмір аудіо
            try:
                from faster_whisper import WhisperModel as _FWModel
                is_faster_whisper = isinstance(self.whisper_model, _FWModel)
            except Exception:
                is_faster_whisper = False
            model_type = "faster-whisper" if is_faster_whisper else "hf-pipeline"
            audio_size = len(wav_bytes)
            # WAV header ~44 байти; ~32000 байт/сек для 16kHz/16-bit моно
            approx_seconds = max(0.0, (audio_size - 44) / 32000.0)
            logger.info(
                f"[DEBUG] listen_for_command: model_type={model_type}, "
                f"audio_path={tmp_path}, bytes={audio_size}, ~sec={approx_seconds:.2f}"
            )

            if approx_seconds < 0.3:
                logger.warning("[DEBUG] Аудіо занадто коротке — ймовірно тиша або обрив.")

            if is_faster_whisper:
                segments, info = self.whisper_model.transcribe(
                    tmp_path,
                    beam_size=1,
                    best_of=1,
                    language="uk",
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=400),
                    condition_on_previous_text=False,
                )
                segs_list = list(segments)
                logger.info(
                    f"[DEBUG] faster-whisper: lang={getattr(info, 'language', '?')}, "
                    f"prob={getattr(info, 'language_probability', 0):.2f}, "
                    f"segments={len(segs_list)}"
                )
                text = "".join(s.text for s in segs_list).strip().lower()
            else:
                # Hugging Face pipeline (LoRA-адаптер на whisper-small).
                # Для актуальних transformers потрібна повна назва мови та явний task,
                # інакше модель може повертати порожній текст.
                result = self.whisper_model(
                    tmp_path,
                    return_timestamps=True,
                    generate_kwargs={"language": "ukrainian", "task": "transcribe"},
                )
                logger.info(f"[DEBUG] pipeline result: {result}")
                if isinstance(result, dict) and "text" in result:
                    text = (result["text"] or "").strip().lower()
                else:
                    text = ""

            logger.info(f"[DEBUG] recognized text: '{text}'")
            if text:
                # Прибрати ведучі wake-word слова: "слухай", "слухаю", "джарвіс", "jarvis"
                # (користувач часто повторює їх перед командою)
                text = re.sub(
                    r'^[\s,\.\-]*(?:слухай(?:те)?|слухаю|джарв[іи]с|jarvis|hey\s+jarvis)[\s,\.\-:]+',
                    '',
                    text,
                    flags=re.IGNORECASE,
                ).strip()
                if not text:
                    logger.warning("[DEBUG] Після зрізання wake-префіксу команда порожня — None.")
                    return None
                print(f"{Fore.YELLOW}💬 Ви: {text}")
                return text
            logger.warning("[DEBUG] Розпізнавання повернуло порожній текст — повертаю None.")
            return None

        except sr.WaitTimeoutError:
            logger.warning("[DEBUG] WaitTimeoutError in listen_for_command")
            return None
        except Exception as e:
            logger.error(f"[DEBUG] Помилка розпізнавання: {e}")
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

    def transcribe_file(self, path: str) -> str:
        """Розпізнати довільний аудіо-файл (wav/webm/ogg/mp3) існуючою Whisper моделлю.

        Викликається з REST-ендпоїнту /transcribe (web-mic input).
        """
        if self.whisper_model is None:
            self._whisper_ready.wait(timeout=30)
        if self.whisper_model is None:
            raise RuntimeError("Whisper model is not loaded")

        try:
            from faster_whisper import WhisperModel as _FWModel
            is_faster_whisper = isinstance(self.whisper_model, _FWModel)
        except Exception:
            is_faster_whisper = False

        if is_faster_whisper:
            segments, _info = self.whisper_model.transcribe(
                path,
                beam_size=1,
                best_of=1,
                language="uk",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
                condition_on_previous_text=False,
            )
            text = "".join(s.text for s in segments).strip()
        else:
            result = self.whisper_model(
                path,
                return_timestamps=True,
                generate_kwargs={"language": "ukrainian", "task": "transcribe"},
            )
            if isinstance(result, dict) and "text" in result:
                text = (result["text"] or "").strip()
            else:
                text = ""

        if text:
            text = re.sub(
                r'^[\s,\.\-]*(?:слухай(?:те)?|слухаю|джарв[іи]с|jarvis|hey\s+jarvis)[\s,\.\-:]+',
                '',
                text,
                flags=re.IGNORECASE,
            ).strip()
        return text

    def __del__(self) -> None:
        self.close()