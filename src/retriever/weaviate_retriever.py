from utils.config import *
from utils.helpers import ids_filter, id_filter
import numpy as np
from collections import Counter
from sentence_transformers import CrossEncoder
from weaviate import WeaviateClient
import weaviate.classes as wvc
from weaviate.collections.classes.internal import Object

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore, VectorStoreRetriever
from langchain_community.vectorstores.utils import maximal_marginal_relevance
# ================================================== #

class Retriever(VectorStore):
    """
    Manages document retrieval from a Weaviate vector store.

    The `Retriever` class is designed to find relevant documents from a Weaviate vector store based on a given query. It supports various search methods, including a hybrid similarity search, and includes functionality for re-ranking and data management.

    Attributes:
        client (WeaviateClient): The Weaviate client instance for interacting with the vector store.
        embedder (Any): The embedding model used for generating query and document embeddings.
        collection: The collection object retrieved from the Weaviate client.

    Methods:
        similarity_search(query: str, source_ids: List[str], auto_merge: bool = False, k: int = 16, top_k: int = 5, alpha: float = 0.5) -> List[LcDocument]:
            Executes a hybrid search combining both keyword and vector similarity. It returns a ranked list of relevant documents.
        max_marginal_relevance_search(query: str, source_ids: List[str], k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.5) -> List[Tuple[LcDocument, float]]:
            Performs a search to return a diverse set of documents by balancing relevance with novelty to the query.
        rerank_docs(query: str, docs: List[LcDocument], top_k: int) -> List[LcDocument]:
            Re-ranks a list of retrieved documents based on their relevance to the query using a cross-encoder model for improved accuracy.
        delete(source_id: str):
            Removes all documents associated with a specific source ID from the vector store.
    """
    def __init__(self, client: WeaviateClient, embedder: Embeddings) -> None:
        self.client = client
        self.embedder = embedder
        self.collection = self.client.collections.get(DB_NAME)
    # -------------------------------------------------- #
  
    # -- Main Methods -- #

    # Response to documents
    def objects_to_docs(self, objects: list[Object]) -> list[Document]:
        docs = []
        for obj in objects:
            text = obj.properties["text"]
            metadata = {"source_id": obj.properties["source_id"], "page_no": obj.properties["page_no"]}
            docs.append(Document(page_content=text, metadata=metadata))
        return docs
    # -------------------------------------------------- #

    def similarity_search(self, query: str, source_ids: list, auto_merge =False, k = 16, top_k = 5, alpha=0.5) -> list[Document]:
        query_emb = self.embedder.embed_query(query)

        objects = self.collection.query.hybrid(query=query, vector=query_emb,
                                                filters=ids_filter(source_ids) if (source_ids) else None,
                                                limit=k, alpha=alpha).objects
        objects = sorted(objects, key=lambda obj: obj.properties["index"])
        
        if (auto_merge):
            merged_objects = []
            for source_id in source_ids:
                merged_objects.extend(self.auto_merge(objects, source_id))
            docs = merged_objects
        
        docs = self.objects_to_docs(objects)
    
        # Re-rank results
        docs = self.rerank_docs(query, docs, top_k)
        return docs
    # -------------------------------------------------- #

    def similarity_search_with_relevance_scores(self, query: str, source_ids: list, k=5, alpha=0.5) -> list[tuple[Document, float]]:
        query_emb = self.embedder.embed_query(query)

        objects = self.collection.query.hybrid(query=query, vector=query_emb,
                                                filters=ids_filter(source_ids),
                                                limit=k, alpha=alpha,
                                                return_metadata=wvc.query.MetadataQuery(score=True)).objects
        objects = sorted(objects, key=lambda obj: obj.properties["index"])

        scores = [obj.metadata.score for obj in objects]
        docs = self.objects_to_docs(objects)
        docs = zip(docs, scores)

        return list(docs)
    # -------------------------------------------------- #

    def max_marginal_relevance_search(self, query: str, source_ids: list, k=5, fetch_k=20, lambda_mult=0.5) -> list[tuple[Document, float]]:
        query_emb = self.embedder.embed_query(query)

        objects = self.collection.query.near_vector(near_vector=query_emb,
                                                filters=ids_filter(source_ids),
                                                limit=fetch_k,
                                                return_metadata=wvc.query.MetadataQuery(distance=True),
                                                include_vector= True).objects
        objects = sorted(objects, key=lambda obj: obj.properties["index"])
        
        embeddings = [obj.vector["default"] for obj in objects]
        mmr_selected = maximal_marginal_relevance(np.array(query_emb), embeddings, k=k, lambda_mult=lambda_mult)

        objects = [objects[i] for i in mmr_selected]
        docs = self.objects_to_docs(objects)

        return docs
    # -------------------------------------------------- #
    
    def delete(self, source_id: str):
        self.collection.data.delete_many(where=id_filter(source_id))
    # -------------------------------------------------- #
    
    # -- Advanced Methods -- #
        
    # Re-rank Results
    def rerank_docs(self, query: str, docs: list[Document], top_k :int) -> list[Document]:
        model = CrossEncoder(RERANKER_MODEL_NAME, trust_remote_code=True)

        # Prepare the query-document pairs for the model
        documents = [doc.page_content for doc in docs]
        
        # Rank docs against query
        results = model.rank(query, documents, return_documents= False, top_k = top_k)
        indices = [res['corpus_id'] for res in results]
        docs = [docs[i] for i in indices]
        return docs
    # -------------------------------------------------- #

    # Auto-Merge
    def auto_merge(self, objects: list[Object], source_id: str) -> list[Object]:
        source_id_filter = id_filter(source_id)

        # Count retrieved level 1 & level 2 number
        l1_count = Counter(obj.properties["l1"] for obj in objects)
        l2_count = Counter(obj.properties["l2"] for obj in objects)

        # Get level 0 & level 1 & level 2 chunks number to merge
        l0_chunks_keys = [key for key, value in l1_count.items() if (value <= (L1 // 2))]
        l1_chunks_keys = [key for key, value in l1_count.items() if (value >= (L1 // 2) + 1)]
        l2_chunks_keys = [key for key, value in l2_count.items() if (value >= (L2 // 2) + 1)]

        # Exclude l1 chunks from l2 merged chunks
        l_ratio = L2 // L1 
        l1_chunks_keys = [c for c in l1_chunks_keys if all(c not in range(p*l_ratio, (p+1)*l_ratio) for p in l2_chunks_keys)]

        # Get level 0 chunks
        l0_chunks = [obj for obj in objects if (obj.properties["l1"] in l0_chunks_keys)]

        # Get level 1 chunks
        if (l1_chunks_keys):
            l1_filters = wvc.query.Filter.by_property("l1").contains_any(l1_chunks_keys) & source_id_filter
            l1_chunks = self.collection.query.fetch_objects(filters=l1_filters, limit=FETCHING_LIMIT, sort=SORT).objects
            l1_chunks = self.merge_chunks(l1_chunks, level="l1")
        else:
            l1_chunks = []

        # Get level 2 chunks
        if (l2_chunks_keys):
            l2_filters = wvc.query.Filter.by_property("l2").contains_any(l2_chunks_keys) & source_id_filter
            l2_chunks = self.collection.query.fetch_objects(filters=l2_filters, limit=FETCHING_LIMIT, sort=SORT).objects
            l2_chunks = self.merge_chunks(l2_chunks, level="l2")
        else:
            l2_chunks = []

        # Return all levels chunks
        return l0_chunks + l1_chunks + l2_chunks
    # -------------------------------------------------- #

    # -- Auto-Merge [Help Functions] -- # 
    def merge_chunks(self, objects: list[Object], level: str) -> list[Object]:
        object_dict = {}
        for obj in objects:
            key = obj.properties[level]
            if (key in object_dict):
                object_dict[key]["text"] += (" " + obj.properties["text"])
            else:
                object_props = {
                    "text": obj.properties["text"],
                    "source_id": obj.properties["source_id"],
                    "page_no": obj.properties["page_no"],
                }
                object_dict[key] = object_props
        
        # Convert object dictionary to weaviate object
        chunks_objects = []
        for obj_props in object_dict.values():
            obj = Object(properties=obj_props, uuid=None, metadata=None, references=None, vector=None, collection=None)
            chunks_objects.append(obj)
        return chunks_objects
    # -------------------------------------------------- #

    # -- Retriever Methods -- #
    def _get_retriever_tags(self) -> list[str]:
        tags = [self.__class__.__name__]
        if self.embeddings:
            tags.append(self.embeddings.__class__.__name__)
        return tags
    # -------------------------------------------------- #

    def as_retriever(self, **kwargs) -> VectorStoreRetriever:
        tags = kwargs.pop("tags", None) or []
        tags.extend(self._get_retriever_tags())
        return VectorStoreRetriever(vectorstore=self, **kwargs, tags=tags)
    # -------------------------------------------------- #
    
    # -- Abstract Methods -- #
    def add_texts(self, texts, metadatas=None, **kwargs): return
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs): return
    # -------------------------------------------------- #