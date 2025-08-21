# Using PyTorch
import os
os.environ["USE_TORCH"] = "1"
# --------------------------------------------------------------------- #

from weaviate.collections.classes.grpc import Sort

# Secret Keys
from dotenv import load_dotenv
if not os.getenv("GOOGLE_API_KEY") and not os.getenv("OPEN_AI_KEY"):
    load_dotenv() 
# --------------------------------------------------------------------- #

# -- Constants -- #
HOST = os.getenv('DB_HOST', 'localhost')
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPEN_AI_KEY = os.getenv("OPEN_AI_KEY")
LLM_MODEL_NAME = "models/gemini-2.5-pro"

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
DB_NAME = "Edu_RAG"

# RAG
FETCHING_LIMIT = 1024
DOCUMENT_SEPERATOR = "\n\n---\n\n"
# --------------------------------------------------------------------- #