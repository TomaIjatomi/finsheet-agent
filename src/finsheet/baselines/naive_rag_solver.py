"""
Naive RAG solver — baseline #2 for M1.4.

Strategy:
  1. Serialize the xlsx to text once, cache.
  2. Chunk into row windows of `chunk_rows` rows each. Include the header
     block (title + units + header row) at the top of every chunk so the
     LLM always has column names.
  3. Embed all chunks with text-embedding-005 in one batch call. Cache
     embeddings per file — many questions hit the same file.
  4. For each query: embed the question, retrieve top-K chunks by cosine
     similarity.
  5. Prompt Gemini 2.5 Pro with question + retrieved chunks (NOT full file).

Expected to underperform full-context (M1.3) because:
  - Aggregation / sort questions need ALL rows; top-K can't supply them.
  - Fund dividers and average rows live in their own (sparse) chunks
    that may not co-retrieve with answer chunks.
  - Embedding similarity is weak for "what fund is X in" questions.
  - LLM has no signal that it's seeing a subset rather than the whole file.

This baseline's purpose is to establish that naive RAG — the obvious
budget-conscious or large-document alternative an FDE customer might
reach for first — is the wrong tool for spreadsheet QA. The agentic
architecture in M2 is the principled answer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .serializer import serialize_xlsx
from .solver import SolveResult, _estimate_cost

# text-embedding-005 input pricing as of May 2026 (per million tokens).
EMBEDDING_INPUT_PER_MTOK = 0.025


@dataclass
class _Chunk:
    text: str
    embedding: np.ndarray


class NaiveRagSolver:
    """Naive RAG baseline: chunk + embed + retrieve top-K, then prompt
    Gemini 2.5 Pro with the retrieved subset.

    Default config (chunk_rows=10, top_k=5) follows the most common "first
    pass at RAG" parameters in production code — deliberately not tuned to
    the spreadsheet domain. The point is to show what a generic RAG pipeline
    achieves here, not to optimize.
    """

    name = "naive_rag_gemini_2.5_pro"

    def __init__(
        self,
        client,
        chat_model: str = "gemini-2.5-pro",
        embedding_model: str = "text-embedding-005",
        top_k: int = 5,
        chunk_rows: int = 10,
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
    ):
        self._client = client
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._top_k = top_k
        self._chunk_rows = chunk_rows
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._chunk_cache: dict[Path, list[_Chunk]] = {}
        self.embedding_cost_total = 0.0  # tracked for the report

    def _split_header_and_body(self, lines: list[str]) -> tuple[list[str], list[str]]:
        """Find where the header row ends and data begins.

        Generator writes: title row, "$ in millions", blank, header row,
        then data. We locate the header row by scanning for one that contains
        the Company column.
        """
        for i, line in enumerate(lines[:10]):
            if not line.startswith("| "):
                continue
            cells = [c.strip() for c in line.split("|")]
            if any("Company" in c for c in cells):
                return lines[: i + 1], lines[i + 1 :]
        # Fallback: assume the first 4 lines are header block
        return lines[:4], lines[4:]

    def _chunk_xlsx(self, xlsx_path: Path) -> list[str]:
        """Split serialized xlsx into row-window chunks with headers in each."""
        text = serialize_xlsx(xlsx_path)
        lines = text.split("\n")
        header_block, body_lines = self._split_header_and_body(lines)
        chunks: list[str] = []
        for i in range(0, len(body_lines), self._chunk_rows):
            window = body_lines[i : i + self._chunk_rows]
            chunk_text = "\n".join(header_block + window)
            chunks.append(chunk_text)
        return chunks

    async def _embed_batch(
        self,
        texts: list[str],
        task_type: str,
    ) -> tuple[list[np.ndarray], int]:
        """Embed a batch of texts. Returns (embeddings, approx_token_count)."""
        from google.genai import types

        def _do_embed():
            return self._client.models.embed_content(
                model=self._embedding_model,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type),
            )

        # Vertex embed_content is a sync call; offload to thread pool so we
        # don't block the asyncio loop driving the LLM calls.
        response = await asyncio.to_thread(_do_embed)
        embeddings = [np.array(e.values, dtype=np.float32) for e in response.embeddings]
        tokens = sum(max(1, len(t) // 4) for t in texts)
        return embeddings, tokens

    async def _prepare_file(self, xlsx_path: Path) -> list[_Chunk]:
        if xlsx_path in self._chunk_cache:
            return self._chunk_cache[xlsx_path]
        chunk_texts = self._chunk_xlsx(xlsx_path)
        embeddings, tokens = await self._embed_batch(chunk_texts, "RETRIEVAL_DOCUMENT")
        self.embedding_cost_total += tokens * EMBEDDING_INPUT_PER_MTOK / 1_000_000
        chunks = [_Chunk(text=t, embedding=e) for t, e in zip(chunk_texts, embeddings, strict=True)]
        self._chunk_cache[xlsx_path] = chunks
        return chunks

    def _retrieve_top_k(self, chunks: list[_Chunk], query_emb: np.ndarray) -> list[_Chunk]:
        q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-9)
        chunk_matrix = np.stack([c.embedding for c in chunks])
        chunk_norms = chunk_matrix / (np.linalg.norm(chunk_matrix, axis=1, keepdims=True) + 1e-9)
        scores = chunk_norms @ q_norm
        top_idx = np.argsort(scores)[::-1][: self._top_k]
        return [chunks[i] for i in top_idx]

    async def solve(
        self,
        xlsx_path: Path,
        question: str,
        answer_type: str,
    ) -> SolveResult:
        from google.genai import types

        loop = asyncio.get_event_loop()
        start = loop.time()
        try:
            chunks = await self._prepare_file(xlsx_path)
            query_embs, q_tokens = await self._embed_batch([question], "RETRIEVAL_QUERY")
            self.embedding_cost_total += q_tokens * EMBEDDING_INPUT_PER_MTOK / 1_000_000
            top_chunks = self._retrieve_top_k(chunks, query_embs[0])
            retrieved_text = "\n\n---\n\n".join(c.text for c in top_chunks)
            user_prompt = build_user_prompt(retrieved_text, question, answer_type)
            response = await self._client.aio.models.generate_content(
                model=self._chat_model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=self._temperature,
                    max_output_tokens=self._max_output_tokens,
                ),
            )
        except Exception as e:
            latency_ms = int((loop.time() - start) * 1000)
            return SolveResult(
                answer_text="",
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency_ms,
                cost_usd=0.0,
                error=f"{type(e).__name__}: {e}",
            )

        latency_ms = int((loop.time() - start) * 1000)
        usage = response.usage_metadata
        tokens_in = (usage.prompt_token_count or 0) if usage else 0
        tokens_out = (usage.candidates_token_count or 0) if usage else 0
        text = (response.text or "").strip()
        return SolveResult(
            answer_text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=_estimate_cost(tokens_in, tokens_out),
            error=None,
        )
