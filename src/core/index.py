from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    MatchText,
    MatchValue,
    PointStruct,
    Range,
)

from src.core.bm25 import BM25Index, STOP_WORDS
from src.core.chunker import RecursiveChunker
from src.core.embedder import get_embedder
from src.core.staleness import _hash_file
from src.parsers.registry import get_parser, has_file_parser
from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

# Namespace for deterministic chunk point ids — re-indexing the same file
# overwrites its points in place instead of duplicating them.
_INDEX_NS = uuid.UUID("6a7f2c1e-8b3d-4e5a-9c0f-1d2e3f4a5b6c")

# The index has its own chunk size — StoreService's 600-char re-chunk (and its
# prompt-injection sanitizer) is for prose memories, not verbatim code.
INDEX_CHUNK_SIZE = 1600      # chars ≈ 400 tokens
INDEX_CHUNK_OVERLAP = 200
EMBED_BATCH = 64
MAX_FILE_BYTES = 200_000
MAX_SNIPPET_CHARS = 2000     # cap on content returned per search hit

DEFAULT_EXCLUDES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", "target", ".next", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".idea", ".vscode",
}
EXCLUDED_FILES = {"uv.lock", "package-lock.json", "yarn.lock", "poetry.lock"}

# Limits on one push/remove call (context_index_push / context_index_remove and
# their REST routes). A pusher splits larger sets into batches; the byte cap
# keeps one request well under the reverse proxy's body limit.
PUSH_MAX_FILES = 50
PUSH_MAX_BYTES = 4 * 1024 * 1024
REMOVE_MAX_PATHS = 1000


def validate_push_files(files: Any) -> list[dict[str, str]]:
    """Check a push batch and return it as [{path, content}].

    Raises ValueError naming what is wrong, so a transport can answer 400
    rather than index half a batch."""
    if not isinstance(files, list) or not files:
        raise ValueError("files must be a non-empty list of {path, content}")
    if len(files) > PUSH_MAX_FILES:
        raise ValueError(
            f"too many files in one push: {len(files)} > {PUSH_MAX_FILES}; split into batches"
        )
    out: list[dict[str, str]] = []
    total = 0
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise ValueError(f"files[{i}] must be an object with path and content")
        path, content = f.get("path"), f.get("content")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"files[{i}].path must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError(f"files[{i}].content must be a string")
        if ".." in Path(path).parts:
            raise ValueError(f"files[{i}].path must not contain '..'")
        total += len(content.encode("utf-8"))
        out.append({"path": path, "content": content})
    if total > PUSH_MAX_BYTES:
        raise ValueError(
            f"push batch is {total} bytes > {PUSH_MAX_BYTES}; split into batches"
        )
    return out


def validate_remove_paths(paths: Any) -> list[str]:
    """Check a remove batch and return the paths, stripped of a leading '/'."""
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of strings")
    if len(paths) > REMOVE_MAX_PATHS:
        raise ValueError(
            f"too many paths in one remove: {len(paths)} > {REMOVE_MAX_PATHS}; split into batches"
        )
    out: list[str] = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"paths[{i}] must be a non-empty string")
        out.append(p.strip().lstrip("/"))
    return out

# Payload schema for ctx_<slug>__index collections. content gets a full-text
# index so exact identifiers are findable even when dense search misses them.
INDEX_PAYLOAD_SCHEMA: dict[str, Any] = {
    "user_id": "keyword",
    "path": "keyword",
    "symbol": "keyword",
    "language": "keyword",
    "chunk_index": "integer",
    "content": {
        "type": "text",
        "tokenizer": "word",
        "lowercase": True,
        "min_token_len": 2,
    },
}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_RE = re.compile(r"[`\"']([^`\"']+)[`\"']")


def chunk_point_id(slug: str, rel_path: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_INDEX_NS, f"{slug}:{rel_path}:{chunk_index}"))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class IndexFileResult:
    path: str
    status: str  # "indexed" | "unchanged" | "failed" | "skipped"
    chunks_upserted: int = 0
    chunks_deleted: int = 0
    error: str | None = None


@dataclass
class IndexResult:
    source: str
    collection: str
    files_indexed: int = 0
    files_unchanged: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    chunks_upserted: int = 0
    chunks_deleted: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "collection": self.collection,
            "files_indexed": self.files_indexed,
            "files_unchanged": self.files_unchanged,
            "files_failed": self.files_failed,
            "files_skipped": self.files_skipped,
            "chunks_upserted": self.chunks_upserted,
            "chunks_deleted": self.chunks_deleted,
            "details": self.details[:50],
        }


class IndexService:
    """Write path of the persistent code/doc index.

    Idempotent by construction: point ids are uuid5(project:path:chunk_index),
    unchanged files are skipped via whole-file hashes, changed files re-embed
    only the chunks whose content hash moved, and shrunken files get their
    tail chunks swept."""

    def __init__(
        self,
        index_storage: QdrantStorage,
        project_slug: str,
        chunker: RecursiveChunker | None = None,
        embedder: Any | None = None,
    ) -> None:
        self._storage = index_storage
        self._slug = project_slug
        self._chunker = chunker or RecursiveChunker(
            chunk_size=INDEX_CHUNK_SIZE, chunk_overlap=INDEX_CHUNK_OVERLAP
        )
        self._embedder = embedder or get_embedder()

    async def index(
        self,
        source: str,
        user_id: str,
        force: bool = False,
        max_files: int = 500,
    ) -> IndexResult:
        files, root, truncated = await self._resolve_files(source, max_files)
        result = IndexResult(source=source, collection=self._storage.collection_name)
        if truncated:
            result.details.append(
                {"note": f"file list capped at {max_files} — pass max_files to raise"}
            )

        indexed_paths = await self._indexed_file_hashes(user_id)

        for path in files:
            rel_path = self._rel_path(path, root)
            file_result = await self._index_file(
                path, rel_path, user_id, force, indexed_paths.get(rel_path)
            )
            if file_result.status == "indexed":
                result.files_indexed += 1
            elif file_result.status == "unchanged":
                result.files_unchanged += 1
            elif file_result.status == "skipped":
                result.files_skipped += 1
            else:
                result.files_failed += 1
            result.chunks_upserted += file_result.chunks_upserted
            result.chunks_deleted += file_result.chunks_deleted
            if file_result.status != "unchanged":
                detail: dict[str, Any] = {"path": rel_path, "status": file_result.status}
                if file_result.error:
                    detail["error"] = file_result.error
                result.details.append(detail)

        # Directory mode: sweep files that vanished from disk since last index
        if Path(source).is_dir():
            walked = {self._rel_path(p, root) for p in files}
            for rel_path in indexed_paths:
                if rel_path not in walked:
                    deleted = await self._delete_file_chunks(user_id, rel_path)
                    result.chunks_deleted += deleted
                    result.details.append({"path": rel_path, "status": "removed"})

        return result

    async def diff_files(
        self, user_id: str, client_hashes: dict[str, str]
    ) -> dict[str, Any]:
        """Compare a client's {rel_path: file_hash} manifest against the index.

        The push-indexing handshake: the client hashes everything locally,
        asks what changed, and uploads only those files. `removed` lists
        indexed paths absent from the client manifest."""
        server_hashes = await self._indexed_file_hashes(user_id)
        changed = [
            path for path, digest in client_hashes.items()
            if server_hashes.get(path) != digest
        ]
        removed = [path for path in server_hashes if path not in client_hashes]
        return {
            "changed": sorted(changed),
            "removed": sorted(removed),
            "unchanged": len(client_hashes) - len(changed),
        }

    async def index_content(
        self,
        user_id: str,
        files: list[dict[str, str]],
        force: bool = False,
    ) -> IndexResult:
        """Index files pushed as content (path + text) — no server filesystem
        access needed, so this works when the repo lives on another machine.
        Parsers read from disk, so each file is staged to a temp path with its
        real extension before parsing."""
        import tempfile

        result = IndexResult(source="<push>", collection=self._storage.collection_name)
        for f in files:
            rel_path = str(f.get("path") or "").strip().lstrip("/")
            content = f.get("content") or ""
            if not rel_path or not content:
                result.files_skipped += 1
                continue
            try:
                data = content.encode("utf-8")
                file_hash = hashlib.sha256(data).hexdigest()[:16]
                suffix = Path(rel_path).suffix or ".txt"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    parser = get_parser(tmp_path)
                    parsed = await parser.parse(tmp_path)
                finally:
                    os.unlink(tmp_path)
                # parser metadata carries the temp path — replace with the real one
                for pc in parsed:
                    if isinstance(pc.metadata, dict) and "file" in pc.metadata:
                        pc.metadata["file"] = rel_path
                file_result = await self._upsert_parsed(
                    parsed, rel_path, file_hash, user_id, force,
                    abs_path=None,
                    default_language=suffix.lstrip("."),
                )
            except Exception as exc:
                logger.warning("Push index failed for %s: %s", rel_path, exc)
                file_result = IndexFileResult(rel_path, "failed", error=str(exc)[:200])

            if file_result.status == "indexed":
                result.files_indexed += 1
            elif file_result.status == "unchanged":
                result.files_unchanged += 1
            elif file_result.status == "skipped":
                result.files_skipped += 1
            else:
                result.files_failed += 1
            result.chunks_upserted += file_result.chunks_upserted
            result.chunks_deleted += file_result.chunks_deleted
            if file_result.status != "unchanged":
                detail: dict[str, Any] = {"path": rel_path, "status": file_result.status}
                if file_result.error:
                    detail["error"] = file_result.error
                result.details.append(detail)
        return result

    async def remove_files(self, user_id: str, paths: list[str]) -> int:
        """Sweep all chunks of the given rel_paths (client-confirmed deletes)."""
        deleted = 0
        for rel_path in paths:
            deleted += await self._delete_file_chunks(user_id, rel_path)
        return deleted

    async def status(
        self, user_id: str, check_staleness: bool = True, stale_cap: int = 200
    ) -> dict[str, Any]:
        """One record per indexed file (via chunk_index == 0 points), plus
        aggregates and on-disk staleness."""
        records = await self._scroll_all(
            [
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="chunk_index", match=MatchValue(value=0)),
            ]
        )
        files: list[dict[str, Any]] = []
        by_language: dict[str, int] = {}
        total_chunks = 0
        last_indexed: str | None = None
        for r in records:
            p = r.payload or {}
            entry = {
                "path": p.get("path"),
                "language": p.get("language"),
                "chunks": p.get("chunk_total", 1),
                "indexed_at": p.get("indexed_at"),
                "abs_path": p.get("abs_path"),
                "file_hash": p.get("file_hash"),
            }
            files.append(entry)
            lang = p.get("language") or "unknown"
            by_language[lang] = by_language.get(lang, 0) + 1
            total_chunks += entry["chunks"] or 1
            ts = p.get("indexed_at")
            if ts and (last_indexed is None or ts > last_indexed):
                last_indexed = ts

        out: dict[str, Any] = {
            "collection": self._storage.collection_name,
            "files": len(files),
            "chunks": total_chunks,
            "by_language": by_language,
            "last_indexed_at": last_indexed,
        }
        if check_staleness:
            stale: list[str] = []
            missing: list[str] = []
            for entry in files[:stale_cap]:
                abs_path = entry.get("abs_path")
                if not abs_path:
                    continue
                current = _hash_file(abs_path)
                if current is None:
                    missing.append(entry["path"])
                elif current != entry.get("file_hash"):
                    stale.append(entry["path"])
            out["stale_files"] = stale
            out["missing_files"] = missing
            if len(files) > stale_cap:
                out["staleness_checked"] = stale_cap
        return out

    # ------------------------------------------------------------------

    async def _resolve_files(
        self, source: str, max_files: int
    ) -> tuple[list[Path], Path, bool]:
        src = Path(source).expanduser()

        if src.is_file():
            return [src], src.parent, False

        if src.is_dir():
            files = await self._walk_dir(src)
        else:
            # glob pattern — glob.glob handles absolute patterns and **
            import glob as _glob
            files = [Path(p) for p in _glob.glob(str(src), recursive=True) if Path(p).is_file()]
            if not files:
                raise FileNotFoundError(f"source not found or matched no files: {source}")
            src = Path(os.path.commonpath([str(f) for f in files]))

        candidates = []
        for f in files:
            if not has_file_parser(str(f)) or f.name in EXCLUDED_FILES:
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                with open(f, "rb") as fh:
                    if b"\x00" in fh.read(1024):
                        continue
            except OSError:
                continue
            candidates.append(f)

        candidates.sort()
        truncated = len(candidates) > max_files
        root = src if src.is_dir() else (src.parent if src.is_file() else src)
        return candidates[:max_files], root, truncated

    async def _walk_dir(self, root: Path) -> list[Path]:
        """List files under root. In a git repo, use git's own view (perfect
        .gitignore semantics, zero deps); otherwise os.walk with a static
        exclude list."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(root), "ls-files",
                "--cached", "--others", "--exclude-standard",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return [
                    root / line
                    for line in stdout.decode("utf-8", errors="replace").splitlines()
                    if line.strip() and (root / line).is_file()
                ]
        except (FileNotFoundError, OSError):
            pass

        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDES]
            for name in filenames:
                found.append(Path(dirpath) / name)
        return found

    def _rel_path(self, path: Path, root: Path) -> str:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(path)

    async def _indexed_file_hashes(self, user_id: str) -> dict[str, str]:
        """rel_path → file_hash for everything currently in the index."""
        records = await self._scroll_all(
            [
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="chunk_index", match=MatchValue(value=0)),
            ]
        )
        return {
            (r.payload or {}).get("path", ""): (r.payload or {}).get("file_hash", "")
            for r in records
            if (r.payload or {}).get("path")
        }

    async def _scroll_all(self, conditions: list[Any]) -> list[Any]:
        records: list[Any] = []
        offset = None
        while True:
            batch, offset = await self._storage.scroll_by_conditions(
                conditions, limit=1000, offset=offset
            )
            records.extend(batch)
            if offset is None:
                break
        return records

    async def _index_file(
        self,
        path: Path,
        rel_path: str,
        user_id: str,
        force: bool,
        prior_file_hash: str | None,
    ) -> IndexFileResult:
        try:
            file_hash = _hash_file(str(path))
            if file_hash is None:
                return IndexFileResult(rel_path, "skipped", error="unreadable")
            if not force and prior_file_hash == file_hash:
                return IndexFileResult(rel_path, "unchanged")

            parser = get_parser(str(path))
            parsed = await parser.parse(str(path))
            return await self._upsert_parsed(
                parsed, rel_path, file_hash, user_id, force,
                abs_path=str(path.resolve()),
                default_language=path.suffix.lstrip("."),
            )
        except Exception as exc:
            logger.warning("Index failed for %s: %s", rel_path, exc)
            return IndexFileResult(rel_path, "failed", error=str(exc)[:200])

    async def _upsert_parsed(
        self,
        parsed: list[Any],
        rel_path: str,
        file_hash: str,
        user_id: str,
        force: bool,
        abs_path: str | None,
        default_language: str,
    ) -> IndexFileResult:
        try:
            # Chunk ONCE — split only oversized parser chunks, inherit metadata
            chunks: list[dict[str, Any]] = []
            for pc in parsed:
                if pc.is_empty:
                    continue
                if len(pc.content) <= INDEX_CHUNK_SIZE:
                    chunks.append({"text": pc.content, "meta": pc.metadata, "title": pc.title})
                else:
                    for sub in self._chunker.chunk_text(pc.content):
                        chunks.append({"text": sub, "meta": pc.metadata, "title": pc.title})

            if not chunks:
                return IndexFileResult(rel_path, "skipped", error="no content")

            # Embed only new/changed chunks (compare against stored hashes)
            existing = await self._existing_chunk_hashes(user_id, rel_path)
            now = datetime.now(timezone.utc).isoformat()
            total = len(chunks)
            to_embed: list[int] = []
            for i, c in enumerate(chunks):
                c["hash"] = content_hash(c["text"])
                if force or existing.get(i) != c["hash"]:
                    to_embed.append(i)

            vectors: dict[int, list[float]] = {}
            for start in range(0, len(to_embed), EMBED_BATCH):
                batch_idx = to_embed[start:start + EMBED_BATCH]
                embedded = await self._embedder.embed_batch(
                    [chunks[i]["text"] for i in batch_idx]
                )
                vectors.update(dict(zip(batch_idx, embedded)))

            points = []
            for i in to_embed:
                c = chunks[i]
                meta = c["meta"] or {}
                points.append(PointStruct(
                    id=chunk_point_id(self._slug, rel_path, i),
                    vector=vectors[i],
                    payload={
                        "user_id": user_id,
                        "project": self._slug,
                        "path": rel_path,
                        # None for push-indexed content — the server has no
                        # local file to hash; the client's diff is the truth
                        "abs_path": abs_path,
                        "file_hash": file_hash,
                        "content_hash": c["hash"],
                        "content": c["text"],
                        "symbol": meta.get("name") or c["title"] or None,
                        "kind": meta.get("kind")
                            or ("section" if meta.get("language") is None else "chunk"),
                        "language": meta.get("language") or default_language,
                        "line_start": meta.get("line_start"),
                        "line_end": meta.get("line_end"),
                        "chunk_index": i,
                        "chunk_total": total,
                        "indexed_at": now,
                        "type": "code-index",
                    },
                ))
            await self._storage.upsert_points(points)

            # Refresh file-level payload on the untouched chunks too — without
            # this, a partially-changed file keeps stale file_hash on old
            # points, so the next index() re-runs it forever and status flags
            # it stale forever.
            untouched = [
                chunk_point_id(self._slug, rel_path, i)
                for i in range(total)
                if i not in vectors and i in existing
            ]
            if untouched:
                await self._storage.set_payload(
                    {"file_hash": file_hash, "chunk_total": total, "indexed_at": now},
                    untouched,
                )

            deleted = 0
            if existing and max(existing) >= total:
                await self._storage.delete_by_conditions([
                    FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                    FieldCondition(key="path", match=MatchValue(value=rel_path)),
                    FieldCondition(key="chunk_index", range=Range(gte=total)),
                ])
                deleted = sum(1 for i in existing if i >= total)

            return IndexFileResult(
                rel_path, "indexed",
                chunks_upserted=len(points), chunks_deleted=deleted,
            )
        except Exception as exc:
            logger.warning("Index failed for %s: %s", rel_path, exc)
            return IndexFileResult(rel_path, "failed", error=str(exc)[:200])

    async def _existing_chunk_hashes(self, user_id: str, rel_path: str) -> dict[int, str]:
        records = await self._scroll_all([
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="path", match=MatchValue(value=rel_path)),
        ])
        return {
            (r.payload or {}).get("chunk_index", -1): (r.payload or {}).get("content_hash", "")
            for r in records
        }

    async def _delete_file_chunks(self, user_id: str, rel_path: str) -> int:
        existing = await self._existing_chunk_hashes(user_id, rel_path)
        if existing:
            await self._storage.delete_by_conditions([
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="path", match=MatchValue(value=rel_path)),
            ])
        return len(existing)


def discover_projects(root: Path) -> list[tuple[str, Path]]:
    """Map a watch root to (project_slug, path) pairs for background indexing.

    A root that is itself a git repo is one project; otherwise each immediate
    non-hidden subdirectory is treated as a project named after the folder."""
    from src.core.project import slugify

    if not root.is_dir():
        return []
    if (root / ".git").exists():
        return [(slugify(root.name), root)]
    return [
        (slugify(d.name), d)
        for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith(".") and d.name not in DEFAULT_EXCLUDES
    ]


DENSE_K_MULT = 3
DENSE_WEIGHT = 0.65
BM25_WEIGHT = 0.35
SYMBOL_BOOST = 0.25
KEYWORD_BOOST = 0.10
_MAX_IDENTIFIERS = 3


def _extract_identifiers(query: str) -> list[str]:
    """Candidate exact-match identifiers: backticked/quoted terms first, then
    identifier-shaped tokens that aren't plain English."""
    quoted = [t.strip() for t in _BACKTICK_RE.findall(query) if t.strip()]
    if quoted:
        return quoted[:_MAX_IDENTIFIERS]
    idents = [
        t for t in _IDENT_RE.findall(query)
        if t.lower() not in STOP_WORDS and ("_" in t or any(c.isupper() for c in t[1:]) or len(t) > 6)
    ]
    idents.sort(key=len, reverse=True)
    return idents[:_MAX_IDENTIFIERS]


class IndexSearchService:
    """Read path: dense semantic search fused with exact-symbol and full-text
    keyword passes, so `get_storage_for`-style identifier queries hit even
    when the embedding space misses them."""

    def __init__(self, index_storage: QdrantStorage, embedder: Any | None = None) -> None:
        self._storage = index_storage
        self._embedder = embedder or get_embedder()

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        language: str | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        base_conditions: list[Any] = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]
        if language:
            base_conditions.append(
                FieldCondition(key="language", match=MatchValue(value=language.lstrip(".")))
            )

        vector = await self._embedder.embed(query)
        dense_hits = await self._dense(vector, base_conditions, top_k * DENSE_K_MULT)

        identifiers = _extract_identifiers(query)
        symbol_ids: set[str] = set()
        keyword_ids: set[str] = set()
        candidates: dict[str, dict[str, Any]] = {}

        for hit_id, payload, score in dense_hits:
            candidates[hit_id] = {"payload": payload, "dense": score}

        for ident in identifiers:
            for r in await self._storage.text_search(
                base_conditions + [FieldCondition(key="symbol", match=MatchValue(value=ident))],
                limit=10,
            ):
                rid = str(r.id)
                symbol_ids.add(rid)
                candidates.setdefault(rid, {"payload": r.payload or {}, "dense": 0.0})
            for r in await self._storage.text_search(
                base_conditions + [FieldCondition(key="content", match=MatchText(text=ident))],
                limit=20,
            ):
                rid = str(r.id)
                keyword_ids.add(rid)
                candidates.setdefault(rid, {"payload": r.payload or {}, "dense": 0.0})

        if not candidates:
            return []

        if path_prefix:
            candidates = {
                cid: c for cid, c in candidates.items()
                if str((c["payload"] or {}).get("path", "")).startswith(path_prefix)
            }
            if not candidates:
                return []

        # BM25 over the union corpus for the lexical half of the fusion score
        ids = list(candidates)
        corpus = [str((candidates[c]["payload"] or {}).get("content", "")) for c in ids]
        bm25 = BM25Index(corpus)
        raw = bm25.score_all(query)
        max_raw = max(raw) if raw and max(raw) > 0 else 1.0

        hits = []
        for i, cid in enumerate(ids):
            payload = candidates[cid]["payload"] or {}
            score = DENSE_WEIGHT * candidates[cid]["dense"] + BM25_WEIGHT * (raw[i] / max_raw)
            if cid in symbol_ids:
                score += SYMBOL_BOOST
            if cid in keyword_ids:
                score += KEYWORD_BOOST
            content = str(payload.get("content", ""))[:MAX_SNIPPET_CHARS]
            hit = {
                "path": payload.get("path"),
                "symbol": payload.get("symbol"),
                "kind": payload.get("kind"),
                "language": payload.get("language"),
                "line_start": payload.get("line_start"),
                "line_end": payload.get("line_end"),
                "content": content,
                "score": round(score, 4),
                "match": {
                    "dense": candidates[cid]["dense"] > 0,
                    "exact_symbol": cid in symbol_ids,
                    "keyword": cid in keyword_ids,
                },
            }
            abs_path = payload.get("abs_path")
            if abs_path:
                current = _hash_file(abs_path)
                if current is not None and current != payload.get("file_hash"):
                    hit["possibly_stale"] = True
            hits.append(hit)

        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    async def _dense(
        self, vector: list[float], conditions: list[Any], limit: int
    ) -> list[tuple[str, dict[str, Any], float]]:
        from qdrant_client.models import Filter

        results = await self._storage.client.query_points(
            collection_name=self._storage.collection_name,
            query=vector,
            query_filter=Filter(must=list(conditions)),
            limit=limit,
            with_payload=True,
        )
        return [(str(r.id), r.payload or {}, r.score) for r in results.points]
