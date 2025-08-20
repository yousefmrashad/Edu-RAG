# Config
from utils.config import *
import weaviate.classes.config as wc
import weaviate
# ================================================== #

class DB:
    """
    Manages a local Weaviate database connection and collection.

    The `DB` class provides a streamlined interface for connecting to a local Weaviate instance, creating a new collection if it doesn't already exist, and managing the database client. This is essential for setting up the vector store backend for a retrieval-augmented generation (RAG) system.

    Attributes:
        client: The Weaviate client instance for database interaction.
        collection_name: The name of the collection to be managed.

    Methods:
        __init__(collection_name: str):
            Initializes the DB instance with the name of the collection.

        connect() -> weaviate.Client:
            Connects to the Weaviate database. If the collection specified in the constructor does not exist, it is created automatically with a predefined schema. The method returns the connected client instance.
            
        create():
            Creates a new collection in the database with a specific configuration tailored for storing document chunks. The collection includes properties for tracking document metadata such as "index", "source_id", "page_no", and the text content itself.
    """
    def __init__(self):
        self.client = weaviate.connect_to_local(host=HOST)

    def connect(self):
        if not (self.client.collections.exists(DB_NAME)):
            self.create()
        
        return self.client
    
    def create(self):
        self.client.collections.create(
        name=DB_NAME,
        vector_config=wc.Configure.Vectors.self_provided(
            vector_index_config=wc.Configure.VectorIndex.hnsw(
                distance_metric=wc.VectorDistances.COSINE
            )
        ),
        properties=[
            wc.Property(name="index", data_type=wc.DataType.INT, vectorize_property_name=False),
            wc.Property(name="source_id", data_type=wc.DataType.TEXT),
            wc.Property(name="page_no", data_type=wc.DataType.TEXT),
            wc.Property(
                name="text",
                data_type=wc.DataType.TEXT,
                tokenization=wc.Tokenization.LOWERCASE,
            ),
            wc.Property(name="l1", data_type=wc.DataType.INT, vectorize_property_name= False),
            wc.Property(name="l2", data_type=wc.DataType.INT, vectorize_property_name= False),
        ]
    )
# -------------------------------------------------- #