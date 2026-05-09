"""Запити до Knowledge Graph: персонажі, місця, події з прочитаних книг.

Тригери: "хто такий", "хто така", "що ти знаєш про", "розкажи про персонажа",
         "знайди персонажа", "у яких книгах згадується".

Приклади:
  "Хто такий Кайдаш?"             → персонаж + книги + кількість згадок + контексти
  "Розкажи про персонажа Маруся"  → опис + пов'язані сутності
  "У яких книгах згадується Київ" → список джерел

Якщо персонаж не знайдений → fallback до RAG (jarvis.brain.search).
"""
from __future__ import annotations

import re
import logging
from typing import List

try:
    from core.knowledge_graph import get_kg
    _KG_AVAILABLE = True
except Exception:
    _KG_AVAILABLE = False

logger = logging.getLogger(__name__)

TRIGGERS = [
    # ВАЖЛИВО: тільки специфічні фрази, без перекриття з web_search ("хто такий", "розкажи про").
    "що ти знаєш про персонажа",
    "розкажи про персонажа",
    "знайди персонажа",
    "знайди в графі",
    "у яких книгах згадується",
    "в яких книгах згадується",
    "де згадується персонаж",
    "хто з персонажів",
]

_KIND_LABEL = {
    "person":       "персонаж",
    "location":     "місце",
    "organization": "організація",
    "event":        "подія",
    "work":         "твір",
    "misc":         "сутність",
}


def _extract_query(cmd: str) -> str:
    text = cmd.strip()
    for t in sorted(TRIGGERS, key=len, reverse=True):
        text = re.sub(re.escape(t), "", text, flags=re.IGNORECASE)
    text = text.strip(" ?.!:,'\"«»")
    # Прибираємо "джарвіс" якщо лишилось
    text = re.sub(r"\bджарвіс\b", "", text, flags=re.IGNORECASE).strip()
    return text


def _format_entity_card(kg, ent: dict, neighbor_limit: int = 6, mention_limit: int = 3) -> str:
    parts: List[str] = []
    kind_label = _KIND_LABEL.get(ent["kind"], ent["kind"])
    header = f"📖 **{ent['name']}** — {kind_label} (згадок: {ent['mentions']})"
    if ent.get("first_seen_book"):
        header += f", джерело: {ent['first_seen_book']}"
    parts.append(header)

    neighbors = kg.get_neighbors(ent["id"], limit=neighbor_limit)
    if neighbors:
        names = ", ".join(
            f"{n['name']} ({n['weight']})" for n in neighbors
        )
        parts.append(f"🔗 Пов'язані: {names}")

    mentions = kg.get_mentions(ent["id"], limit=mention_limit)
    if mentions:
        parts.append("📜 Контексти:")
        for m in mentions:
            ctx = (m.get("context") or "").strip()
            book = m.get("source_book") or "?"
            parts.append(f"  • [{book}] {ctx}")

    return "\n".join(parts)


def execute(cmd, jarvis):
    if not _KG_AVAILABLE:
        return "Knowledge Graph недоступний (відсутній модуль knowledge_graph)."

    query = _extract_query(cmd)
    if not query:
        return "Кого або що шукати? Скажи, наприклад: 'хто такий Кайдаш'."

    try:
        kg = get_kg()
    except Exception as e:
        return f"Не можу відкрити граф знань: {e}"

    stats = kg.stats()
    if stats["entities"] == 0:
        return (
            "Граф знань поки порожній. Запусти 'вивчи бібліотеку' з KG_ENABLED=1, "
            "щоб витягти персонажів і місця з книжок."
        )

    matches = kg.find_entity(query, limit=5)
    if not matches:
        # Fallback до RAG-пошуку
        rag = jarvis.brain.search(query, k=3) if hasattr(jarvis, "brain") else ""
        if rag:
            return f"У графі немає '{query}'. Ось що знайшов у пам'яті:\n\n{rag}"
        return f"Не знайшов '{query}' ні в графі, ні в пам'яті."

    # Якщо одне точне попадання — детальна картка
    if len(matches) == 1 or matches[0]["mentions"] >= 3 * (matches[1]["mentions"] if len(matches) > 1 else 1):
        winner = matches[0]
        # Реєструємо в Working Memory як активну сутність розмови.
        try:
            wm = getattr(jarvis, "working_memory", None)
            if wm is not None:
                wm.register_entity(
                    winner["name"], kind=winner["kind"],
                    source=winner.get("first_seen_book"),
                )
                wm.set_topic(f"персонаж/сутність {winner['name']}")
        except Exception as e:
            logger.warning(f"[skill_who_is] WM update failed: {e}")
        return _format_entity_card(kg, winner)

    # Інакше — список варіантів
    lines = [f"Знайшов {len(matches)} варіантів для '{query}':"]
    for m in matches:
        kind_label = _KIND_LABEL.get(m["kind"], m["kind"])
        lines.append(f"  • {m['name']} ({kind_label}, згадок: {m['mentions']})")
    lines.append("\nДеталі по найбільш згадуваному:")
    lines.append(_format_entity_card(kg, matches[0]))
    return "\n".join(lines)
