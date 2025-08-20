# Edu-RAG: Educational Retrieval-Augmented Generation System

A RAG system for training and education that processes authoritative documents and generates structured educational content with proper citations.

## Overview

Edu-RAG transforms PDF documents into a queryable knowledge base and generates comprehensive educational lessons.

## Key Features

- **Document Processing**: Automated PDF loading, chunking, and embedding generation
- **Vector Storage**: Weaviate database for efficient similarity search
- **Hybrid Retrieval**: Combines vector similarity and keyword search with re-ranking
- **Educational Content Generation**: Structured lessons with proper citations using Google Gemini
- **Streaming Output**: Real-time content generation with concurrent file writing

## Quick Start

1. **Setup Database Connection**
```python
from utils.db_config import DB
from preprocessing.embedding import Embedding

client = DB().connect()
embed = Embedding()
```

2. **Process Documents**
```python
from preprocessing.document import DocumentProcessor

doc = DocumentProcessor(doc_url, doc_title)
doc.process_document(embed, client)
```

3. **Create RAG Chain**
```python
from rag.weaviate_retriever import Retriever
from langchain_google_genai import GoogleGenerativeAI

retriever = Retriever(client, embed).as_retriever()
llm = GoogleGenerativeAI(model=LLM_MODEL_NAME)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)
```

4. **Generate Educational Content**
```python
async for chunk in rag_chain.astream({"input": query}):
if "answer" in chunk:
print(chunk["answer"], end="", flush=True)
```

## System Architecture

The system consists of three main packages:

- **`preprocessing`**: Document loading, chunking, and embedding generation
- **`retrieval`**: Vector similarity search and context retrieval
- **`utils`**: Database management and system configuration

## Configuration

Key configuration parameters are defined in `utils/config.py`:

- `EMBEDDING_MODEL_NAME`: Qwen3-Embedding-0.6B for text vectorization
- `LLM_MODEL_NAME`: Google Gemini 2.5 Pro for content generation
- `CHUNK_SIZE`: 256 tokens per document chunk
- `DB_NAME`: "Edu-RAG" Weaviate collection name

## Example Output

The system generates structured educational lessons with proper citations, automatically saved to markdown files like `phishing_lesson.md`.

**Notes**

The complete pipeline is demonstrated in `src/pipeline.ipynb` which serves as both documentation and a runnable example.The system includes duplicate detection to skip already processed documents and uses hybrid chunking with metadata preservation for optimal retrieval performance.