"""MemoryMixin — запамʼятовування фактів, контактів і оновлення UserProfile."""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


class MemoryMixin:
    # ==================================================================
    # MEMORY
    # ==================================================================
    @staticmethod
    def _normalize_phone(raw_phone: str) -> str:
        digits = re.sub(r"\D", "", raw_phone)
        if raw_phone.strip().startswith("+") and digits:
            return f"+{digits}"
        if len(digits) == 10 and digits.startswith("0"):
            return f"+38{digits}"
        if len(digits) == 12 and digits.startswith("380"):
            return f"+{digits}"
        return raw_phone.strip()

    @staticmethod
    def _extract_phone_fact_text(fact: str) -> str | None:
        phone_match = re.search(r"(\+?\d[\d\s\-\(\)]{7,}\d)", fact)
        if not phone_match:
            return None

        raw_phone = phone_match.group(1)
        normalized_phone = MemoryMixin._normalize_phone(raw_phone)

        owner_part = fact.replace(raw_phone, " ")
        owner_part = re.sub(
            r"\b(номер|номером|телефон|телефону|контакт|це|є|мій|моєї|моє|запам['’]?ятай|запамятай)\b",
            " ",
            owner_part,
            flags=re.IGNORECASE,
        )
        owner_part = re.sub(r"\s+", " ", owner_part).strip(" .,:;!?")

        if owner_part:
            return f"Контакт: {owner_part}. Номер телефону: {normalized_phone}."
        return f"Номер телефону: {normalized_phone}."

    @staticmethod
    def _extract_contact_phone_pair(canonical_fact: str) -> tuple[str, str] | None:
        match = re.search(
            r"Контакт:\s*(.+?)\.\s*Номер телефону:\s*(\+?\d+)\.",
            canonical_fact,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        name = match.group(1).strip()
        phone = match.group(2).strip()
        if not name or not phone:
            return None
        return name, phone

    def cmd_memorize(self, cmd: str) -> str:
        logger.info(f"cmd_memorize: {cmd!r}")
        fact = re.sub(r"\b(запам['’]?ятай|запамятай|що)\b", "", cmd, flags=re.IGNORECASE).strip(" .,:;!?")
        if not fact:
            self.audio.speak("Уточніть, що саме запам'ятати.")
            return "CONTINUE"

        # 1) Спершу — спроба оновити UserProfile (ім'я / форма звертання / інтерес).
        if self._maybe_update_user_profile(fact):
            return "CONTINUE"

        canonical_fact = self._extract_phone_fact_text(fact) or fact

        self.db.add_fact(canonical_fact)
        contact_pair = self._extract_contact_phone_pair(canonical_fact)
        if contact_pair:
            self.db.upsert_contact(contact_pair[0], contact_pair[1])
        self.brain.memorize(canonical_fact, source="user_fact")

        if canonical_fact != fact:
            self.audio.speak("Зберіг контакт і номер телефону у пам'ять.")
        else:
            self.audio.speak("Інформацію завантажено у бази даних.")
        return "CONTINUE"

    # ------------------------------------------------------------------
    # USER PROFILE — лайт-евристики на природній мові
    # ------------------------------------------------------------------
    _RE_PROFILE_NAME = re.compile(
        r"\b(?:мене\s+звати|моє\s+ім['’]?я|я\s+(?:це|—|-)?\s*)\s*([А-ЯІЇЄҐA-Z][а-яіїєґa-zʼ'\-]{1,30})\b",
        re.IGNORECASE,
    )
    _RE_PROFILE_INTEREST = re.compile(
        r"\b(?:я\s+люблю|мені\s+подобається|обожнюю|мій\s+інтерес\s+це)\s+(.{2,60})",
        re.IGNORECASE,
    )

    def _maybe_update_user_profile(self, fact: str) -> bool:
        """Якщо фраза описує самого користувача — оновити UserProfile.

        Повертає True, якщо профіль було оновлено (тоді стандартний шлях
        запису у facts/RAG пропускається — це не «факт про світ», це «факт
        про господаря»).
        """
        profile = getattr(self.jarvis, "user_profile", None)
        if profile is None:
            return False

        low = fact.lower()
        updated = False

        # Форма звертання.
        if "звертайся на ви" in low or "звертайтеся на ви" in low or "перейди на ви" in low:
            profile.update(address_form="ви")
            self.audio.speak("Добре, звертатимусь на «ви».")
            return True
        if "звертайся на ти" in low or "перейди на ти" in low or "можна на ти" in low:
            profile.update(address_form="ти")
            self.audio.speak("Добре, звертатимусь на «ти».")
            return True

        # Ім'я.
        m = self._RE_PROFILE_NAME.search(fact)
        if m:
            name = m.group(1).strip()
            if name:
                profile.update(name=name)
                self.audio.speak(f"Запам'ятав, тебе звати {name}.")
                updated = True

        # Інтерес.
        m2 = self._RE_PROFILE_INTEREST.search(fact)
        if m2:
            topic = m2.group(1).strip(" .,:;!?")
            if topic and len(topic) <= 60:
                profile.add_interest(topic)
                if not updated:
                    self.audio.speak(f"Додав «{topic}» до твоїх інтересів.")
                updated = True

        return updated
