"""Application settings, read from the environment (and .env locally)."""

from pydantic_settings import BaseSettings, SettingsConfigDict

# microsoft/harrier-oss-v1-0.6b is instruction-conditioned on the query side
# only; documents are encoded bare. Its own eval set uses a hand-written
# instruction per task, so keep these domain-neutral and pick the one that
# matches how the text was produced.
#
# Changing one requires no re-embedding: only queries see it.
DEFAULT_QUERY_INSTRUCTION = "Given a question, retrieve passages that answer it"

# A bare topic ("phishing", "supply and demand") wants coverage, not an answer.
TOPIC_QUERY_INSTRUCTION = (
    "Given a topic, retrieve passages that explain and expand on it"
)

# HyDE feeds a generated hypothetical passage rather than a question, so the
# instruction describes that input instead of asking for an answer to it.
HYDE_QUERY_INSTRUCTION = (
    "Given a hypothetical passage, retrieve passages that contain the same information"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    database_url: str
    celery_broker_url: str = "amqp://edu:edu@localhost:5672//"

    # Model services (one TEI container each)
    embedder_url: str = "http://localhost:8081"
    reranker_url: str = "http://localhost:8082"

    # The embedder's model id, used only to load a matching tokenizer locally
    # so the ingest worker can size batches in real tokens.
    embedder_model: str = "microsoft/harrier-oss-v1-0.6b"

    # Generation
    openrouter_api_key: str = ""
    generation_model: str = ""

    # Embeddings. 1024 is harrier-oss-v1-0.6b's width and also the pgvector
    # column width, so changing models changes both places at once.
    embedding_dim: int = 1024
    max_input_tokens: int = 32_768
    query_instruction: str = DEFAULT_QUERY_INSTRUCTION

    # Retrieval
    chunk_size: int = 512
    retrieve_candidates: int = 50
    context_chunks: int = 6

    http_timeout: float = 30.0


# One object, imported wherever config is needed. Building it at import time
# means a missing DATABASE_URL fails at boot instead of on the first request;
# the type checker can't see that pydantic fills required fields at runtime.
settings = Settings()  # pyright: ignore[reportCallIssue]
