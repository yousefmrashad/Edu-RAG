from utils.config import *
from utils.helpers import get_page_nos, id_filter

from langchain_core.embeddings import Embeddings

from weaviate import WeaviateClient
from weaviate.collections import Collection
import weaviate.classes as wvc

from docling.chunking import HybridChunker
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from langchain_docling import DoclingLoader
from langchain_docling.loader import ExportType

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = False
pipeline_options.do_table_structure = True
pipeline_options.table_structure_options.do_cell_matching = True
doc_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
    }
)
# --------------------------------------------------------------------- #

# -- Document Class -- #
class DocumentProcessor:
    """
    Processes and embeds documents for use in a RAG system.

    This class handles the entire workflow of preparing a document for a retrieval-augmented generation (RAG) system. It loads a document, breaks it down into manageable chunks, generates vector embeddings for each chunk, and stores the data in a database.
    
    Attributes:
        doc_path (str): The file path of the document to be processed.
        doc_id (str): A unique identifier for the document.
        chunks (List[str]): Stores the document chunks.
        embeddings (List[List[float]]): Stores the generated embeddings.

    Methods:
        load_and_split(): Loads the document and splits it into chunks using a hybrid chunking strategy. The resulting chunks are stored in `self.chunks`.

        generate_embeddings(embedder: Embeddings): Creates embeddings for each document chunk using a specified embedding model. The embeddings are stored in `self.embeddings`.

        store_in_db(collection: Collection): Stores the chunks and their corresponding embeddings as data objects in the specified database collection.
        
        process_document(embedder: Embeddings, client: WeaviateClient): Orchestrates the document processing workflow. Checks if the document already exists in the database. If not, it runs the full workflow: loading, splitting, embedding, and storing the data.
    """
    def __init__(self, doc_path: str, doc_id: str):
        self.doc_path = doc_path
        self.doc_id = doc_id

    # ---------------------------------------------- #
    def load_and_split(self):
        loader = DoclingLoader(
        converter=doc_converter,
        file_path=self.doc_path,
        export_type=ExportType.DOC_CHUNKS,
        chunker=HybridChunker(tokenizer=EMBEDDING_MODEL_NAME, max_tokens= CHUNK_SIZE, merge_peers= True),
        )
        self.chunks = loader.load()
    # ---------------------------------------------- #

    def generate_embeddings(self, embedder: Embeddings):
        self.embeddings = embedder.embed_documents([chunk.page_content for chunk in self.chunks])
    # ---------------------------------------------- #
    
    def store_in_db(self, collection: Collection):        
        objs = []
        l1, l2 = (0, 0)
        for i, chunk in enumerate(self.chunks):
            if (i != 0) and (i % L1 == 0): l1 += 1
            if (i != 0) and (i % L2 == 0): l2 += 1
        
            properties = {
                "index": i,
                "source_id": self.doc_id,
                "page_no": get_page_nos(chunk),
                "text": chunk.page_content,
                "l1": l1,
                "l2": l2
            }
            obj = wvc.data.DataObject(properties=properties, vector=self.embeddings[i])
            objs.append(obj)

        collection.data.insert_many(objs)
    # ---------------------------------------------- #
    
    def process_document(self, embedder: Embeddings, client: WeaviateClient):
        collection = client.collections.get(DB_NAME)   
        exist_filter = id_filter(self.doc_id)
        not_exist = (len((collection.query.fetch_objects(filters=exist_filter, limit=1).objects)) == 0)
        if not_exist:
            print(f"Processing document: {self.doc_path}")
            self.load_and_split()
            self.generate_embeddings(embedder)
            self.store_in_db(collection)
        else:
            print(f"Document {self.doc_path} already exists in the database. Skipping processing.")
        return
# --------------------------------------------------------------------- #