"""Exact token counts from the embedder's tokenizer.

Only the ingest worker needs these, to size embed batches, so the dependency
lives in the ``ingest`` extra and this module is imported only from the ingest
task -- the API image never imports it.

The model is loaded from the Hub by the same id the TEI container runs, not
from the container itself: no extra round trip, at the cost of counts that can
differ from the server's by a token or two. For a batch budget that is noise.
"""

from collections.abc import Sequence
from functools import lru_cache

from tokenizers import Tokenizer

from edu_rag.config import settings


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    return Tokenizer.from_pretrained(settings.embedder_model)


def count_tokens(texts: Sequence[str]) -> list[int]:
    """Token counts for ``texts``, without special tokens.

    Batching only cares about the text the model will read; the separators the
    tokenizer would add are a couple of tokens per input, well inside the slack
    a 16k budget leaves.
    """
    encodings = _tokenizer().encode_batch(list(texts), add_special_tokens=False)
    return [len(encoding.ids) for encoding in encodings]
