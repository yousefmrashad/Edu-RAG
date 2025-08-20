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