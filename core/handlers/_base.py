"""HandlerBase — спільна основа для доменних міксинів CommandHandlers.

Тримає посилання на інстанс ``JarvisUltimate`` (``jarvis``) і надає
властивості-аксесори до підсистем. Усі доменні міксини покладаються на ці
властивості (``self.audio``, ``self.llm`` тощо), а фінальний клас
``CommandHandlers`` успадковує цю базу останньою у MRO.
"""
from __future__ import annotations


class HandlerBase:
    def __init__(self, jarvis) -> None:
        self.jarvis = jarvis

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------
    @property
    def audio(self):
        return self.jarvis.audio

    @property
    def db(self):
        return self.jarvis.db

    @property
    def brain(self):
        return self.jarvis.brain

    @property
    def llm(self):
        return self.jarvis.llm

    @property
    def scraper(self):
        return self.jarvis.scraper

    @property
    def youtube(self):
        return self.jarvis.youtube
