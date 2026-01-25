
import os
from typing import List, Optional
from pathlib import Path

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    load_index_from_storage,
    Document,
    Settings
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai_like import OpenAILike
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError

from edmcp_core.utils import retry_with_backoff

# Define common AI exceptions for retries
AI_RETRIABLE_EXCEPTIONS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

class KnowledgeBaseManager:
    """
    Manages local RAG operations using LlamaIndex, ChromaDB, and xAI.
    """

    def __init__(self, storage_dir: str = "data/vector_store", ocr_tool_class=None):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._ocr_tool_class = ocr_tool_class  # Optional OCR tool for PDF processing

        # Configure global settings
        self._setup_settings()

        # Initialize ChromaDB
        self.db = chromadb.PersistentClient(path=str(self.storage_dir))

    def _setup_settings(self):
        """Configure LLM and Embedding models in LlamaIndex Settings."""
        # 1. LLM (xAI / Grok)
        api_key = os.environ.get("XAI_API_KEY")
        base_url = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
        model = os.environ.get("XAI_API_MODEL", "grok-3")

        if not api_key:
            raise ValueError("XAI_API_KEY not found in environment.")

        Settings.llm = OpenAILike(
            model=model,
            api_key=api_key,
            api_base=base_url,
            is_chat_model=True,
            temperature=0,
            context_window=131072
        )

        # 2. Embeddings (Local)
        Settings.embed_model = HuggingFaceEmbedding(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )

        # 3. Node Parser (Chunking)
        Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)

    def _sanitize_topic(self, topic: str) -> str:
        """Sanitizes the topic name to be ChromaDB compliant."""
        import re
        # Replace non-alphanumeric chars (excluding ._-) with underscores
        safe_topic = re.sub(r'[^a-zA-Z0-9._-]', '_', topic)
        # Ensure it meets length constraints (3-63 is standard, we'll strip edges)
        return safe_topic.strip('_')

    def _get_index(self, topic: str) -> VectorStoreIndex:
        """Get or create a VectorStoreIndex for a specific topic (Chroma Collection)."""
        safe_topic = self._sanitize_topic(topic)
        chroma_collection = self.db.get_or_create_collection(safe_topic)
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

        # Using StorageContext is more reliable for persistence
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        # If collection has items, load it. Otherwise create empty.
        if chroma_collection.count() > 0:
            return VectorStoreIndex.from_vector_store(
                vector_store,
                storage_context=storage_context
            )
        else:
            return VectorStoreIndex.from_documents(
                [],
                storage_context=storage_context
            )

    @retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
    def ingest_documents(self, file_paths: List[str], topic: str) -> int:
        """
        Loads files, chunks them, and adds them to the vector store.
        Returns the number of documents processed.
        """
        import sys
        import glob
        from llama_index.core import SimpleDirectoryReader

        expanded_paths = []

        # Expand directories into file paths
        for p in file_paths:
            path_obj = Path(p)
            if not path_obj.exists():
                print(f"[RAG] Warning: Path not found: {p}", file=sys.stderr)
                continue

            if path_obj.is_file():
                expanded_paths.append(str(path_obj))
            elif path_obj.is_dir():
                # Recursively find all files in the directory
                # Common extensions to look for
                extensions = ["*.pdf", "*.txt", "*.docx", "*.md"]
                for ext in extensions:
                    # Using glob to find files recursively
                    found = list(path_obj.rglob(ext))
                    expanded_paths.extend([str(f) for f in found])

        if not expanded_paths:
            print(f"[RAG] No valid files found in provided paths.", file=sys.stderr)
            return 0

        print(f"[RAG] Loading {len(expanded_paths)} files into topic '{topic}'...", file=sys.stderr)

        documents = []
        pdf_paths = [p for p in expanded_paths if p.lower().endswith(".pdf")]
        other_paths = [p for p in expanded_paths if not p.lower().endswith(".pdf")]

        # 1. Process PDFs (Smart Detection: Native first, then OCR)
        if pdf_paths and self._ocr_tool_class:
            print(f"[RAG] Processing {len(pdf_paths)} PDF(s) with Smart Detection...", file=sys.stderr)
            try:
                ocr_tool = self._ocr_tool_class() # No job context needed for generic ingestion
                for pdf_path in pdf_paths:
                    try:
                        # Try native extraction first
                        extracted_pages = self._ocr_tool_class.extract_text_from_pdf(pdf_path)

                        if extracted_pages:
                            text = "\n\n".join(extracted_pages)
                        else:
                            # Fallback to OCR if native fails or is scanned
                            print(f"[RAG] Fallback to OCR for: {pdf_path}", file=sys.stderr)
                            text = ocr_tool.extract_text_via_ocr(pdf_path)

                        if text.strip():
                            doc = Document(text=text, metadata={"file_path": pdf_path, "filename": Path(pdf_path).name})
                            documents.append(doc)
                        else:
                            print(f"[RAG] Warning: No text extracted from {pdf_path}", file=sys.stderr)
                    except Exception as e:
                        print(f"[RAG] Error processing {pdf_path}: {e}", file=sys.stderr)
            except Exception as e:
                 print(f"[RAG] Failed to initialize OCRTool: {e}", file=sys.stderr)
        elif pdf_paths:
            # No OCR tool provided, try basic text extraction
            print(f"[RAG] Processing {len(pdf_paths)} PDF(s) without OCR capability...", file=sys.stderr)
            for pdf_path in pdf_paths:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(pdf_path)
                    text_parts = []
                    for page in reader.pages:
                        text_parts.append(page.extract_text() or "")
                    text = "\n\n".join(text_parts)
                    if text.strip():
                        doc = Document(text=text, metadata={"file_path": pdf_path, "filename": Path(pdf_path).name})
                        documents.append(doc)
                except Exception as e:
                    print(f"[RAG] Error processing {pdf_path}: {e}", file=sys.stderr)

        # 2. Process other files with SimpleDirectoryReader
        if other_paths:
            try:
                reader = SimpleDirectoryReader(input_files=other_paths)
                other_docs = reader.load_data()
                documents.extend(other_docs)
            except Exception as e:
                print(f"[RAG] Error loading non-PDF files: {e}", file=sys.stderr)

        if not documents:
            print(f"[RAG] No content extracted from files.", file=sys.stderr)
            return 0

        # We need to recreate the storage context to ensure it's connected to the specific topic
        safe_topic = self._sanitize_topic(topic)
        chroma_collection = self.db.get_or_create_collection(safe_topic)
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        # Create the index from documents directly
        index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context
        )

        final_count = chroma_collection.count()
        print(f"[RAG] Ingestion complete. Topic '{topic}' now has {final_count} nodes.", file=sys.stderr)
        return len(documents)

    @retry_with_backoff(retries=3, exceptions=AI_RETRIABLE_EXCEPTIONS)
    def query_knowledge(self, query: str, topic: str, similarity_top_k: int = 3) -> str:
        """
        Queries the knowledge base for a specific topic.
        Returns a synthesized answer based on retrieved context.
        """
        index = self._get_index(topic)
        query_engine = index.as_query_engine(similarity_top_k=similarity_top_k)
        response = query_engine.query(query)
        return str(response)

    def retrieve_context_chunks(self, query: str, topic: str, similarity_top_k: int = 5) -> List[str]:
        """
        Retrieves raw text chunks (context) without LLM synthesis.
        Useful for passing directly to the Evaluator tool.
        """
        index = self._get_index(topic)
        retriever = index.as_retriever(similarity_top_k=similarity_top_k)
        nodes = retriever.retrieve(query)
        return [node.get_content() for node in nodes]

    def delete_topic(self, topic: str) -> bool:
        """
        Deletes the entire collection (topic) from the vector store.
        Returns True if deleted, False if it didn't exist.
        """
        safe_topic = self._sanitize_topic(topic)
        try:
            self.db.delete_collection(safe_topic)
            return True
        except ValueError:
            # ChromaDB raises ValueError if collection doesn't exist
            return False
