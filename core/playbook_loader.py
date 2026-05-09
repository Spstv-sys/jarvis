"""Playbook loader for the agent chain.

Reads YAML playbooks from /playbooks/ on disk. Each playbook describes a
domain (security, web_scraper, api_server, ...) with:
  - keywords: list[str] used by the keyword classifier
  - architect_hints: extra guidance injected into the Architect prompt
  - reviewer_checklist: domain-specific Reviewer checklist
  - examples_dont / examples_do: short snippets (optional, for future use)

Hot-reload: the loader caches by playbooks-directory mtime, so editing a
YAML file is picked up on next call without restart.

Graceful degradation: if /playbooks/ is missing or any YAML is malformed,
falls back to a hardcoded "generic" playbook so the chain never crashes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

PLAYBOOKS_DIR = Path(__file__).resolve().parent.parent / "playbooks"


@dataclass(frozen=True)
class Playbook:
    domain: str
    description: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
    architect_hints: str = ""
    reviewer_checklist: str = ""
    examples_dont: str = ""
    examples_do: str = ""


_FALLBACK_GENERIC = Playbook(
    domain="generic",
    description="Hardcoded fallback when /playbooks/ is unavailable.",
    keywords=(),
    architect_hints="- Чітко окресли публічний API модуля.\n- Логування через logging, не print.",
    reviewer_checklist=(
        "БАЗОВІ КРИТЕРІЇ:\n"
        "  • Resource leaks?\n"
        "  • Обробка винятків конкретна?\n"
        "  • Logging замість print?\n"
    ),
)

_lock = RLock()
_cache: dict[str, Playbook] = {}
_cache_mtime: float = 0.0


def _dir_mtime(path: Path) -> float:
    """Max mtime over all *.yaml files (so editing any one invalidates cache)."""
    try:
        return max(
            (p.stat().st_mtime for p in path.glob("*.yaml")),
            default=0.0,
        )
    except OSError:
        return 0.0


def _parse_yaml_file(path: Path) -> Playbook | None:
    try:
        import yaml  # lazy: only when actually loading
    except ImportError:
        logger.error("pyyaml not installed; playbooks unavailable")
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to parse %s: %s", path.name, exc)
        return None
    domain = (data.get("domain") or path.stem).strip()
    keywords_raw = data.get("keywords") or []
    if not isinstance(keywords_raw, list):
        keywords_raw = []
    return Playbook(
        domain=domain,
        description=str(data.get("description") or "").strip(),
        keywords=tuple(str(k).lower().strip() for k in keywords_raw if k),
        architect_hints=str(data.get("architect_hints") or "").strip(),
        reviewer_checklist=str(data.get("reviewer_checklist") or "").strip(),
        examples_dont=str(data.get("examples_dont") or "").strip(),
        examples_do=str(data.get("examples_do") or "").strip(),
    )


def load_playbooks(force: bool = False) -> dict[str, Playbook]:
    """Return {domain: Playbook}. Cached by directory mtime; thread-safe."""
    global _cache, _cache_mtime
    with _lock:
        if not PLAYBOOKS_DIR.is_dir():
            if not _cache:
                logger.warning("Playbooks dir not found: %s", PLAYBOOKS_DIR)
                _cache = {"generic": _FALLBACK_GENERIC}
            return _cache
        current_mtime = _dir_mtime(PLAYBOOKS_DIR)
        if not force and _cache and current_mtime <= _cache_mtime:
            return _cache
        loaded: dict[str, Playbook] = {}
        for yaml_path in sorted(PLAYBOOKS_DIR.glob("*.yaml")):
            pb = _parse_yaml_file(yaml_path)
            if pb is not None:
                loaded[pb.domain] = pb
        if "generic" not in loaded:
            loaded["generic"] = _FALLBACK_GENERIC
        _cache = loaded
        _cache_mtime = current_mtime
        logger.info("Loaded %d playbooks: %s", len(loaded), sorted(loaded.keys()))
        return _cache


def get_playbook(domain: str) -> Playbook:
    """Return playbook for domain, or 'generic' if unknown."""
    pbs = load_playbooks()
    return pbs.get(domain) or pbs.get("generic") or _FALLBACK_GENERIC


def classify_domain(request: str) -> str:
    """Keyword-based classifier. Returns domain name or 'generic'."""
    text = (request or "").lower()
    if not text:
        return "generic"
    pbs = load_playbooks()
    # Score by number of matched keywords (longest match wins ties).
    best_domain = "generic"
    best_score = 0
    for domain, pb in pbs.items():
        if domain == "generic":
            continue
        score = 0
        for kw in pb.keywords:
            if kw and kw in text:
                # Longer keywords carry more weight (more specific).
                score += len(kw)
        if score > best_score:
            best_score = score
            best_domain = domain
    return best_domain
