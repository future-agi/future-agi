# Standard library imports

import concurrent.futures
import os
import re
import tempfile
import traceback
from dataclasses import dataclass
from typing import Any

import docx
import structlog
from django.db import close_old_connections
from langchain.text_splitter import RecursiveCharacterTextSplitter

# LangChain imports
from langchain_community.document_loaders import PyPDFLoader
from striprtf.striprtf import rtf_to_text

from agentic_eval.core.embeddings.embedding_manager import (
    EmbeddingManager,
    log_performance,
)
from tfc.telemetry import wrap_for_thread

logger = structlog.get_logger(__name__)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

KB_TABLE_NAME = "syn"
KB_INDEX_COL_TYPE = "text"
KB_INDEX_COL_NAME = "chunk_text"


@dataclass
class Chunk:
    """Represents a chunk of text with metadata"""

    text: str
    file_id: str
    organization_id: str
    page_num: int
    chunk_id: str
    metadata: dict[str, Any] | None = None
    embedding: list[float] | None = None


class KBIndexer:
    """Handles loading, chunking, and indexing of PDF documents"""

    def __init__(
        self,
        doc_id: str | None = None,
    ):
        """Initialize the indexer."""
        self.embedding_manager = EmbeddingManager()
        self.chunks: list[Chunk] = []

        self.bucket_name = UPLOAD_BUCKET_NAME

        self.minio_client = get_storage_client()

    def load_pdf(self, pdf_path: str) -> str:
        """Load and extract text from a PDF file."""
        try:
            loader = PyPDFLoader(pdf_path)
            pages = loader.load()
            text = "\n\n".join(page.page_content for page in pages)

            cleaned_text = self._clean_text(text)

            return cleaned_text

        except ImportError as e:
            logger.error(f"Error: PDF loader import error: {str(e)}")
            raise
        except PermissionError as e:
            logger.error(f"Error: Permission denied accessing PDF {pdf_path}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error: Unexpected error loading PDF {pdf_path}: {str(e)}")
            raise

    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text."""
        if not text:
            return ""

        # Basic text cleaning
        text = text.replace("\x00", "")  # Remove null bytes
        text = " ".join(text.split())  # Normalize whitespace
        text = text.strip()

        # Remove common PDF artifacts
        artifacts = [
            r"\(cid:\d+\)",  # CID artifacts
            r"^\s*\d+\s*$",  # Standalone page numbers
            r"^\s*Page \d+\s*$",  # Page headers
        ]
        for artifact in artifacts:
            text = re.sub(artifact, "", text, flags=re.MULTILINE)

        return text

    def process_pdf(self, pdf_path: str) -> str | None:
        """Process a single PDF file.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            str: knowledge base ID if successful, None otherwise
        """
        text = self.load_pdf(pdf_path)
        if not text:
            return None
        else:
            return text

    def process_text(self, text_path: str) -> str | None:
        """Process a single text file.

        Args:
            text_path: Path to the text file
        """
        with open(text_path) as file:
            text = file.read()
        if not text:
            return None
        else:
            return text

    def process_docx(self, docx_path: str) -> str | None:
        """Process a single docx file.

        Args:
            docx_path: Path to the docx file
        """
        with open(docx_path, "rb") as file:
            doc = docx.Document(file)
            text = "\n\n".join([paragraph.text for paragraph in doc.paragraphs])
        if not text:
            return None
        else:
            return text

    def process_rtf(self, rtf_path: str) -> str | None:
        """Process a single rtf file.

        Args:
            rtf_path: Path to the rtf file
        """
        with open(rtf_path, encoding="utf-8") as file:
            rtf_content = file.read()
            text = rtf_to_text(rtf_content)
        if not text:
            return None
        else:
            return text

    @log_performance
    def process_content(
        self, text: str, file_id: str, kb_id: str, organization_id: str
    ):
        # Optimize chunk size based on text length
        chunk_size = 800
        chunk_overlap = 150

        # Update chunker with optimized parameters
        self.chunker = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ".", "!", "?", ";", ":", " ", ""],
            keep_separator=True,
        )

        # Create documents in batches
        documents = self.chunker.create_documents([text])

        # Process chunks in batches of 50
        batch_size = 50
        all_chunks = []

        def process_batch(batch_idx):
            try:
                close_old_connections()
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(documents))
                batch_docs = documents[start_idx:end_idx]

                new_chunks = [
                    Chunk(
                        text=doc.page_content,
                        file_id=file_id,
                        organization_id=organization_id,
                        page_num=0,
                        chunk_id=f"{file_id}_{start_idx}_{j}",
                        metadata={"chunk_type": "semantic", "section_idx": start_idx},
                    )
                    for j, doc in enumerate(batch_docs)
                ]

                metadatas = [
                    {
                        "file_id": chunk.file_id,
                        "chunk_id": chunk.chunk_id,
                        KB_INDEX_COL_NAME: chunk.text,
                        "organization_id": chunk.organization_id,
                    }
                    for chunk in new_chunks
                ]

                # Process batch in parallel
                try:
                    self.embedding_manager.parallel_process_metadata(
                        eval_id=kb_id,
                        metadatas=metadatas,
                        inputs_formater=[KB_INDEX_COL_NAME],
                        table_name=KB_TABLE_NAME,
                    )
                    logger.info(
                        f"Processed batch {batch_idx + 1} of {(len(documents) + batch_size - 1) // batch_size}"
                    )
                    return new_chunks
                except Exception as e:
                    error_msg = (
                        f"Error in parallel processing for batch {batch_idx}: {str(e)}"
                    )
                    logger.error(error_msg)
                    raise RuntimeError(error_msg) from e
            except Exception as e:
                error_msg = f"Error processing batch {batch_idx}: {str(e)}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
            finally:
                close_old_connections()

        # Use ThreadPoolExecutor to process batches concurrently
        errors = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_batch = wrap_for_thread(process_batch)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            # Submit all batch processing tasks
            future_to_batch = {
                executor.submit(wrapped_process_batch, i): i
                for i in range((len(documents) + batch_size - 1) // batch_size)
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    batch_chunks = future.result()
                    all_chunks.extend(batch_chunks)
                except Exception as e:
                    error_msg = f"Error in batch {batch_idx}: {str(e)}"
                    logger.exception(error_msg)
                    errors.append(error_msg)

        # After all batches are processed, check for errors
        if errors:
            error_summary = "\n".join(errors)
            raise RuntimeError(
                f"Errors occurred during batch processing:\n{error_summary}"
            )

        # Update the chunks list with all processed chunks
        self.chunks.extend(all_chunks)

    def get_subset_kb_id(self, query: str, kb_id: str) -> str:
        """Get a new kb_id for the relevant chunks

        Args:
            query: Query to find relevant chunks
            kb_id: Original document ID

        Returns:
            str: New document ID for the subset of chunks
        """
        # Call get_relevant_chunks, which returns only the new doc_id.
        new_kb_id = self.embedding_manager.get_relevant_chunks(
            query=query,
            table_name=KB_TABLE_NAME,
            eval_id=kb_id,
            index_col_type=[KB_INDEX_COL_TYPE],
            input_cols=KB_INDEX_COL_NAME,
        )

        return new_kb_id

    def get_data_subset_kb_id(
        self, query: list[str], kb_id: str, top_k: int = 4
    ) -> str:
        """

        Args:
            query: Query to find relevant chunks
            kb_id: Original document ID
        """
        input_cols = [KB_INDEX_COL_NAME] * len(query)
        metadata = self.embedding_manager.retrieve_avg_rag_based_examples(
            inputs=query,
            table_name=KB_TABLE_NAME,
            eval_id=kb_id,
            input_cols=input_cols,
            top_k=top_k,
            threshold=0.35,
        )

        return metadata

    def search(
        self, kb_id, query: str, top_k: int = 5, min_similarity: float = 0.0
    ) -> list[dict[str, Any]]:
        """Search for relevant chunks."""

        res = self.embedding_manager.retrieve_avg_rag_based_examples(
            eval_id=kb_id,
            inputs=query,
            input_cols=[KB_INDEX_COL_NAME],
            table_name=KB_TABLE_NAME,
        )
        return res

    def download_s3_file(
        self,
        base_prefix: str,
        organization_id: str,
        kb_id: str,
        file_id: str,
        temp_dir: str,
        file_extension: str,
    ) -> None:
        """Download a single file from an S3 folder to a temporary directory."""
        # Construct the file path
        # file_path = f"{base_prefix}/{organization_id}/{kb_id}/{file_id}"
        file_name = f"{file_id}.{file_extension}"
        try:
            object_key = f"knowledge-base/{kb_id}/{file_name}"

            # Create local file path
            local_path = os.path.join(temp_dir, file_name)

            # Download the file
            self.minio_client.fget_object(self.bucket_name, object_key, local_path)

            logger.info(f"Successfully downloaded {file_name} to {temp_dir}")
            return local_path

        except Exception as e:
            logger.exception(
                f"Error downloading from S3: {str(e)}. "
                f"Bucket: {self.bucket_name}, Object Key: {object_key}"
            )
            raise

    def process_file(
        self, file_path: str, file_id: str, kb_id: str, organization_id: str
    ) -> None:
        """Process a single file from a temporary directory."""
        # Get the file extension
        file_extension = os.path.splitext(file_path)[1]

        # Process the file based on its extension
        if file_extension == ".pdf":
            text = self.process_pdf(file_path)
        elif file_extension == ".txt":
            text = self.process_text(file_path)
        elif file_extension == ".docx":
            text = self.process_docx(file_path)
        elif file_extension == ".rtf":
            text = self.process_rtf(file_path)
        if text:
            self.process_content(text, file_id, kb_id, organization_id)
        else:
            raise ValueError(f"No content extracted from file with ID: {file_id}")

    def process_documents_from_memory(
        self,
        documents: list[tuple[str, dict]],
        kb_id: str,
        organization_id: str,
    ) -> list[dict]:
        """
        Index a list of (text, metadata) documents into the KB without touching the filesystem.

        Args:
            documents: List of (text, metadata) tuples.
            kb_id: Knowledge Base ID used as namespace in the vector store.
            organization_id: Organization ID.

        Returns:
            List of dicts with doc_index and vector count per document.
        """
        chunk_size = 800
        chunk_overlap = 150

        self.chunker = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ".", "!", "?", ";", ":", " ", ""],
            keep_separator=True,
        )

        results = []
        batch_size = 50

        for doc_index, (text, doc_metadata) in enumerate(documents):
            if not text.strip():
                continue

            documents_chunked = self.chunker.create_documents([text])
            file_id = f"dataset-row-{doc_index}"

            def process_batch(batch_idx):
                close_old_connections()
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(documents_chunked))
                batch_docs = documents_chunked[start_idx:end_idx]

                metadatas = [
                    {
                        "file_id": file_id,
                        "chunk_id": f"{file_id}_{start_idx}_{j}",
                        KB_INDEX_COL_NAME: doc.page_content,
                        "organization_id": organization_id,
                        **doc_metadata,
                    }
                    for j, doc in enumerate(batch_docs)
                ]

                try:
                    self.embedding_manager.parallel_process_metadata(
                        eval_id=kb_id,
                        metadatas=metadatas,
                        inputs_formater=[KB_INDEX_COL_NAME],
                        table_name=KB_TABLE_NAME,
                    )
                    return len(batch_docs)
                except Exception as e:
                    logger.error(f"Error processing document batch {batch_idx}: {e}")
                    raise

            num_batches = (len(documents_chunked) + batch_size - 1) // batch_size
            vector_count = 0

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(process_batch, i): i for i in range(num_batches)
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        vector_count += future.result()
                    except Exception as e:
                        logger.error(f"Batch failed for document {doc_index}: {e}")
                        raise

            results.append({
                "doc_index": doc_index,
                "vectors": vector_count,
                "metadata": doc_metadata,
            })

        return results

    def process_s3_file(
        self, filepath: str, file_id: str, kb_id: str, organization_id: str
    ) -> str | None:
        """Process a single file from an S3 folder using folder_name, folder_id, and file_name.

        Args:
            folder_name: The name of the folder (e.g., 'uploads')
            folder_id: The folder ID in S3
            file_name: The name of the file to process

        Returns:
            str: Document ID if successful, None otherwise
        """
        try:
            # Download the file
            # upload_file_to_s3(filepath, kb_id, file_id, self.bucket_name)
            tempdir = tempfile.mkdtemp()
            localpath = self.download_s3_file(
                self.bucket_name,
                organization_id,
                kb_id,
                file_id,
                tempdir,
                filepath.split(".")[-1],
            )

            # Process the file
            self.process_file(localpath, file_id, kb_id, organization_id)
            return {"file_id": file_id, "kb_id": kb_id}
        except Exception as e:
            logger.exception(f"Error processing file: {str(e)}")
            return {"file_id": file_id, "kb_id": kb_id, "error": str(e)}
        finally:
            try:
                # Delete the file
                if os.path.isfile(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Error removing file {filepath}: {str(e)}")

    def remove_chunks_from_kb(
        self, file_id: str, kb_id: str, organization_id: str
    ) -> None:
        """
        Remove chunks from the knowledge base.

        Args:
            eval_id (str): The evaluation ID associated with the chunks
            source (str): The source identifier of the chunks to delete
        """
        try:
            self.embedding_manager.delete_chunks(
                file_id, kb_id, KB_TABLE_NAME, organization_id
            )
            return {"file_id": file_id, "kb_id": kb_id}
        except Exception as e:
            logger.exception(f"Error deleting chunks from knowledge base: {str(e)}")
            return {"file_id": file_id, "kb_id": kb_id, "error": str(e)}


def main():
    # Example usage
    indexer = KBIndexer()

    # Process PDFs
    kb_id = indexer.process_directory("/app/backend/documents", "123", "456")

    # Search
    results = indexer.search(kb_id, ["Explain the concept of MapReduce?"])

    for result in results:
        print(result)


def ingest_kb_from_memory(
    documents: list[tuple[str, dict]],
    kb_id: str,
    organization_id: str,
) -> list[dict]:
    """
    Standalone convenience function: index in-memory documents into a Knowledge Base.

    Args:
        documents: List of (text, metadata) tuples.
        kb_id: Knowledge Base ID.
        organization_id: Organization ID.

    Returns:
        List of result dicts from KBIndexer.process_documents_from_memory.
    """
    indexer = KBIndexer()
    return indexer.process_documents_from_memory(
        documents=documents,
        kb_id=kb_id,
        organization_id=organization_id,
    )


if __name__ == "__main__":
    main()
