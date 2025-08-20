# Using PyTorch
import os
os.environ["USE_TORCH"] = "1"
# --------------------------------------------------------------------- #

from weaviate.collections.classes.grpc import Sort

# Secret Keys
from dotenv import load_dotenv
if not os.getenv("GOOGLE_API_KEY") and not os.getenv("OPEN_AI_KEY"):
    load_dotenv('/home/yousef/cyber-rag/src/utils/.env') 
# --------------------------------------------------------------------- #

# -- Constants -- #
HOST = os.getenv('DB_HOST', 'localhost')
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPEN_AI_KEY = os.getenv("OPEN_AI_KEY")
LLM_MODEL_NAME = "models/gemini-2.5-flash"

# Document Load
CHUNK_SIZE = 256

# Auto Merging
L1 = 4
L2 = 16

# Retrieving Filters
SORT = Sort.by_property(name="index", ascending=True)

# Embedding Model
EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
REPRESENTATION_PROMPT = "Represent this sentence for searching relevant passages: " 

# Re-ranker Model
RERANKER_MODEL_NAME = "jinaai/jina-reranker-v2-base-multilingual"

# Database Name
DB_NAME = "cyberrag"

# RAG
FETCHING_LIMIT = 1024

# Retrieving Filters
import weaviate.classes as wvc

def id_filter(source_id: str):
    return wvc.query.Filter.by_property("source_id").equal(source_id)

def ids_filter(source_ids: list[str]):
    return wvc.query.Filter.by_property("source_id").contains_any(source_ids)

def page_filter(page_no: int):
    return wvc.query.Filter.by_property("page_no").equal(page_no)

# -- Document Metadata -- #
from langchain_core.documents import Document
def get_page_nos(chunk: Document) -> str:
    pages = []
    for item in chunk.metadata['dl_meta']['doc_items']:
        for prov in item['prov']:
            pages.append(prov['page_no'])
    return str(set(pages))

def get_headings(chunk: Document) -> str:
    return ' - '.join(chunk.metadata['dl_meta']['headings'])
# --------------------------------------------------------------------- #