# Utils
from utils.config import *
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer
# ===================================================================== #

# HFEmbedding Model
class Embedding(Embeddings):
    def __init__(self, model_name=EMBEDDING_MODEL_NAME,
                prompt=REPRESENTATION_PROMPT):
        self.model = SentenceTransformer(model_name, trust_remote_code=True).cuda()
        self.prompt = prompt

    def embed_documents(self, texts: list[str]):
        return self.model.encode(texts).tolist()

    def embed_query(self, text: str):
        text = self.prompt + text if (self.prompt) else text
        return self.model.encode(text).squeeze().tolist()
# --------------------------------------------------------------------- #