import os
import logging
import threading
from typing import Optional, List, Dict, Any
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

try:
    import config as _cfg
except Exception:
    _cfg = None

logger = logging.getLogger(__name__)

# Дефолтна модель — багатомовна (краща для української)
DEFAULT_EMBEDDING_MODEL = getattr(
    _cfg, "EMBEDDING_MODEL", "intfloat/multilingual-e5-base"
) if _cfg else "intfloat/multilingual-e5-base"


class JarvisSmartMemory:
    def __init__(
        self,
        db_path: str = "jarvis_vector_db",
        docs_dir: str = "knowledge_base",
        embedding_model: Optional[str] = None,
    ):
        self.db_path  = os.path.abspath(db_path)
        self.docs_dir = os.path.abspath(docs_dir)
        self.embedding_model = embedding_model or DEFAULT_EMBEDDING_MODEL

        os.makedirs(self.docs_dir, exist_ok=True)
        os.makedirs(self.db_path,  exist_ok=True)

        self.embeddings = None
        self.vector_db  = None
        self._ready     = threading.Event()  # ✅ Сигнал готовності

        # ✅ Завантаження у фоні — не блокує старт застосунку
        threading.Thread(target=self._init_embeddings, daemon=True).start()

    def _init_embeddings(self) -> None:
        try:
            print(f"⚙️ Завантаження моделі ембедингів: {self.embedding_model} (фоново)...")
            self.embeddings = HuggingFaceEmbeddings(
                model_name=self.embedding_model
            )
            self.vector_db = Chroma(
                persist_directory=self.db_path,
                embedding_function=self.embeddings,
                collection_name="smart_memory"
            )
            logger.info(f"Chroma DB ініціалізовано: {self.db_path} (model={self.embedding_model})")
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

    def memorize(self, text: str, source: str = "dialogue", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Зберігає факт або репліку у векторну пам'ять.

        Args:
            text: текст для збереження.
            source: коротке джерело (для зворотньої сумісності).
            metadata: повні метадані (author, title, chapter, ...). Якщо задано —
                      додається до базового {"source": source}.
        """
        if not text or not text.strip():
            return
        if not self._wait_ready(timeout=10.0):
            logger.warning("memorize: векторна БД недоступна.")
            return
        try:
            meta: Dict[str, Any] = {"source": source}
            if metadata:
                meta.update({k: v for k, v in metadata.items() if v is not None})
            self.vector_db.add_texts(
                texts=[text],
                metadatas=[meta]
            )
            logger.info(f"RAG пам'ять оновлена (джерело: {source})")
        except Exception as e:
            logger.error(f"Помилка збереження у векторну пам'ять: {e}")

    def memorize_chunks(self, chunks: List[str], metadata: Dict[str, Any]) -> int:
        """Масово зберігає список чанків з однаковими метаданими.

        Повертає кількість успішно доданих чанків.
        """
        if not chunks:
            return 0
        if not self._wait_ready(timeout=15.0):
            logger.warning("memorize_chunks: векторна БД недоступна.")
            return 0
        try:
            metadatas = [
                {k: v for k, v in metadata.items() if v is not None}
                for _ in chunks
            ]
            self.vector_db.add_texts(texts=chunks, metadatas=metadatas)
            return len(chunks)
        except Exception as e:
            logger.error(f"Помилка масового збереження чанків: {e}")
            return 0