import os
import logging
import threading
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

logger = logging.getLogger(__name__)


class JarvisSmartMemory:
    def __init__(self, db_path="jarvis_vector_db", docs_dir="knowledge_base"):
        self.db_path  = os.path.abspath(db_path)
        self.docs_dir = os.path.abspath(docs_dir)

        os.makedirs(self.docs_dir, exist_ok=True)
        os.makedirs(self.db_path,  exist_ok=True)

        self.embeddings = None
        self.vector_db  = None
        self._ready     = threading.Event()  # ✅ Сигнал готовності

        # ✅ Завантаження у фоні — не блокує старт застосунку
        threading.Thread(target=self._init_embeddings, daemon=True).start()

    def _init_embeddings(self) -> None:
        try:
            print("⚙️ Завантаження моделі ембедингів (фоново)...")
            self.embeddings = HuggingFaceEmbeddings(
                model_name="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.vector_db = Chroma(
                persist_directory=self.db_path,
                embedding_function=self.embeddings,
                collection_name="smart_memory"
            )
            logger.info(f"Chroma DB ініціалізовано: {self.db_path}")
            print("✅ Модель ембедингів завантажено.")
        except Exception as e:
            logger.error(f"Помилка ініціалізації Chroma: {e}")
        finally:
            self._ready.set()  # ✅ Завжди знімаємо блок — навіть при помилці

    def _wait_ready(self, timeout: float = 30.0) -> bool:
        """✅ Чекає готовності БД з таймаутом"""
        if not self._ready.wait(timeout=timeout):
            logger.warning("JarvisSmartMemory: таймаут очікування ініціалізації.")
            return False
        return self.vector_db is not None

    def ingest_documents(self) -> None:
        """Сканує папку з документами та індексує нові у векторну базу."""
        if not self._wait_ready():
            logger.error("ingest_documents: векторна БД недоступна.")
            return

        print(f"📂 Сканування папки: {self.docs_dir}...")
        LOADERS = {
            ".pdf": PyPDFLoader,
            ".txt": TextLoader,
        }

        documents = []
        for filename in os.listdir(self.docs_dir):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in LOADERS:
                continue
            filepath = os.path.join(self.docs_dir, filename)
            try:
                loader = LOADERS[ext](filepath)
                documents.extend(loader.load())
            except Exception as e:
                logger.error(f"Помилка завантаження файлу {filename}: {e}")

        if not documents:
            print("⚠️ Папка зі знаннями порожня або файли не вдалося прочитати.")
            return

        splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
        chunks   = splitter.split_documents(documents)

        print(f"🧠 Додавання {len(chunks)} фрагментів у векторну пам'ять...")
        self.vector_db.add_documents(chunks)
        print("✅ Індексація завершена.")

    def search(self, query: str, k: int = 3) -> str:
        """Повертає найрелевантніші фрагменти зі векторної бази."""
        if not self._wait_ready(timeout=10.0):
            return ""
        try:
            results = self.vector_db.similarity_search(query, k=k)
            return "\n\n".join(res.page_content for res in results)
        except Exception as e:
            logger.error(f"Помилка пошуку у векторній базі: {e}")
            return ""

    def memorize(self, text: str, source: str = "dialogue") -> None:
        """Зберігає факт або репліку у векторну пам'ять (з дедуплікацією)."""
        if not text or not text.strip():
            return
        if not self._wait_ready(timeout=10.0):
            logger.warning("memorize: векторна БД недоступна.")
            return
        try:
            # Deduplicate: skip storing if a very similar entry already exists.
            # Uses a character-level Jaccard similarity on word sets as a fast proxy.
            existing = self.vector_db.similarity_search(text, k=1)
            if existing:
                stored = existing[0].page_content.strip()
                words_new    = set(text.strip().lower().split())
                words_stored = set(stored.lower().split())
                union = words_new | words_stored
                if union:
                    jaccard = len(words_new & words_stored) / len(union)
                    if jaccard >= 0.85:
                        logger.info("memorize: пропускаємо дублікат (Jaccard=%.2f).", jaccard)
                        return
            self.vector_db.add_texts(
                texts=[text],
                metadatas=[{"source": source}]
            )
            logger.info(f"RAG пам'ять оновлена (джерело: {source})")
        except Exception as e:
            logger.error(f"Помилка збереження у векторну пам'ять: {e}")