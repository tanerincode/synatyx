from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from src.core.retention import RetentionPolicy

# Always resolve .env relative to the project root regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class RunMode(str, Enum):
    MCP = "mcp"           # stdio MCP server only (default)
    MCP_HTTP = "mcp-http" # FastMCP over HTTP/SSE — for server deployment
    GC = "gc"             # Garbage collection daemon


class QdrantSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "ctx_default"
    vector_size: int = 1536

    model_config = SettingsConfigDict(env_prefix="QDRANT_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379"
    l1_max_messages: int = 20

    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class PostgresSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    db: str = "context_engine"
    user: str = "context_engine"
    password: str = "context_engine"

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")



class EmbeddingSettings(BaseSettings):
    provider: str = "openai"  # "openai" | "sentence-transformers"
    # The embedding model name — works for any provider:
    #   openai:                 text-embedding-3-small, text-embedding-3-large, text-embedding-ada-002
    #   sentence-transformers:  all-MiniLM-L6-v2, all-mpnet-base-v2, …
    model: str = "text-embedding-3-small"
    openai_api_key: str = ""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class AuthSettings(BaseSettings):
    # Admin API key for the HTTP/SSE transport. When empty, auth is disabled
    # (every request is allowed) so local stdio/dev keeps working unchanged.
    admin_key: str = ""
    # Header clients must send the key in. `Authorization: Bearer <key>` is
    # always accepted as a fallback regardless of this value.
    header_name: str = "X-Auth-Key"

    @property
    def enabled(self) -> bool:
        return bool(self.admin_key)

    model_config = SettingsConfigDict(env_prefix="AUTH_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class OAuthSettings(BaseSettings):
    """Built-in OAuth 2.1 authorization server (src/core/oauth.py).

    For clients that cannot attach a static header — above all claude.ai
    custom connectors. Only active when AUTH_ADMIN_KEY is set: without an
    owner secret there would be nothing to authenticate the authorize page
    against, and an open authorization server hands memories to anyone.
    """

    enabled: bool = True
    # Secret the owner types into the authorize page. Empty → AUTH_ADMIN_KEY is
    # the only accepted value; set this to keep the MCP key and the browser
    # password separate.
    owner_password: str = ""
    # Scope issued to (and required of) every client — single-principal server,
    # so one scope is enough.
    scope: str = "synatyx"
    code_ttl_seconds: int = 300            # RFC 6749 §4.1.2: short-lived codes
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_days: int = 30       # rotated on every refresh
    # RFC 7591: dynamically issued client secrets expire; the client re-registers.
    client_secret_ttl_days: int = 90
    # /register is anonymous (claude.ai needs it), so bound the client table:
    # registrations that never obtained a token are pruned after this long,
    # and registration is refused once the table holds max_clients rows.
    unused_client_ttl_hours: int = 24
    max_clients: int = 200
    # Owner login throttling: a parked /authorize request is discarded after
    # this many wrong secrets, and each client IP gets this many attempts/min.
    login_max_failures: int = 5
    login_max_per_minute: int = 10

    @property
    def refresh_token_ttl_seconds(self) -> int:
        return self.refresh_token_ttl_days * 24 * 3600

    @property
    def client_secret_ttl_seconds(self) -> int:
        return self.client_secret_ttl_days * 24 * 3600

    @property
    def unused_client_ttl_seconds(self) -> int:
        return self.unused_client_ttl_hours * 3600

    model_config = SettingsConfigDict(
        env_prefix="OAUTH_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class GCSettings(BaseSettings):
    enabled: bool = True
    run_interval_hours: int = 24
    l2_base_ttl_days: int = 30
    l3_base_ttl_days: int = 90
    grace_period_days: int = 30       # days between soft deprecation and hard delete
    importance_multiplier: float = 3.0  # effective_ttl = base × (1 + importance × multiplier)
    # Type-aware decay: facts rot at different speeds. Items tagged with
    # metadata.fact_type get their effective TTL scaled by these multipliers
    # (file locations go stale in days; preferences barely rot at all).
    # Override via GC_FACT_TYPE_MULTIPLIERS='{"file-location": 0.3, ...}'.
    fact_type_multipliers: dict[str, float] = Field(
        default_factory=lambda: {
            "file-location": 0.3,
            "config": 0.7,
            "architecture": 1.5,
            "preference": 3.0,
        }
    )
    # Retention commitments per project prefix, as JSON:
    #   [{"prefix": "cx-", "layers": {"L1": 90, "L2": 90, "L3": "keep"}}]
    # A number is a hard ceiling in days measured from creation; "keep" means
    # age never expires that layer. Unlike the TTLs above these are promises
    # rather than heuristics, so nothing stretches them — see src/core/retention.py.
    retention_policies: str = ""

    @property
    def retention_policy_list(self) -> "list[RetentionPolicy]":
        """Parsed retention policies, longest prefix first."""
        from src.core.retention import parse_retention_policies

        return parse_retention_policies(self.retention_policies)

    model_config = SettingsConfigDict(env_prefix="GC_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class ConsolidationSettings(BaseSettings):
    """Background merge of overlapping episodic (L2) memories into L3 facts."""

    enabled: bool = True
    # cosine similarity two L2 items must reach to land in the same cluster
    similarity_threshold: float = 0.83
    # clusters smaller than this stay untouched
    min_cluster_size: int = 3
    # safety valve: max merges per run across all collections
    max_merges_per_run: int = 20

    model_config = SettingsConfigDict(
        env_prefix="CONSOLIDATION_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class RelationSettings(BaseSettings):
    """Automatic alternative-detection on store (see docs/alternatives.md)."""

    detect_enabled: bool = True
    # similarity >= autolink_threshold -> alternative_to edge created automatically
    autolink_threshold: float = 0.92
    # suggest_threshold <= similarity < autolink_threshold -> returned as suggestion
    suggest_threshold: float = 0.80
    detect_limit: int = 5

    model_config = SettingsConfigDict(
        env_prefix="RELATION_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class ObserverSettings(BaseSettings):
    """Background auto-relate pass: links similar same-user memories.

    Runs in the GC daemon on its own interval (default 6h = 4×/day). Only
    ever creates `related_to` edges — semantic types (depends_on, caused_by)
    need meaning that cosine similarity cannot infer. Every edge carries
    metadata {auto: true, origin: "observer", score} so inferred edges are
    distinguishable from deliberate ones and a bad run can be bulk-removed.
    """

    enabled: bool = True
    run_interval_hours: int = 6
    # cosine similarity two items must reach to be linked
    similarity_threshold: float = 0.80
    # cap on observer-created edges per item — keeps clusters from becoming
    # fully-connected hairballs (manual edges never count against this)
    max_edges_per_item: int = 3
    # safety valve: max new edges per run across all collections
    max_edges_per_run: int = 50
    # log would-be edges without writing them — for tuning the threshold
    dry_run: bool = False

    model_config = SettingsConfigDict(
        env_prefix="OBSERVER_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class IndexSettings(BaseSettings):
    """Background code/doc indexing (GC daemon). Set INDEX_WATCH_ROOTS to a
    comma-separated list of directories the daemon can read — a root that is
    a git repo is one project; otherwise each immediate subdirectory becomes
    a project named after the folder. Empty = disabled (use push indexing
    via scripts/index_project.py when the code isn't on the server)."""

    watch_roots: str = ""
    run_interval_hours: int = 6
    # user the auto-indexed chunks belong to; empty → DEFAULT_USER_ID
    watch_user_id: str = ""
    max_files_per_project: int = 2000

    @property
    def roots(self) -> list[str]:
        return [r.strip() for r in self.watch_roots.split(",") if r.strip()]

    model_config = SettingsConfigDict(
        env_prefix="INDEX_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class TrackingSettings(BaseSettings):
    """Server-side session tracking — implicit capture with zero client setup.

    Every MCP tool call leaves a compact trace event in Redis; a background
    loop compacts traces into L2 session memories once a session goes idle.
    Works for every MCP client (Claude Code, Cursor, Desktop, custom agents)
    because it observes traffic the server already receives.
    """

    enabled: bool = True
    # a session is considered over after this much inactivity
    idle_minutes: int = 30
    # traces with fewer events than this are dropped as noise, not stored
    min_events: int = 3
    # cap per-trace buffer so a runaway session can't grow unbounded
    max_events: int = 200
    # how often the compaction loop wakes up
    compact_interval_seconds: int = 600
    # Redis TTL safety net on trace buffers
    trace_ttl_hours: int = 48

    model_config = SettingsConfigDict(
        env_prefix="TRACKING_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


class UsageSettings(BaseSettings):
    """Token spend metering — one Postgres row per tool call.

    Records inbound (arguments), outbound (response) and embedding-API token
    counts per user/project/tool. Surfaced via the context_usage tool and the
    dashboard Usage tab.
    """

    enabled: bool = True
    # USD per 1M embedding tokens; default matches text-embedding-3-small.
    # Cost is computed at read time, so a price change applies retroactively.
    embedding_price_per_mtok: float = 0.02
    # usage rows older than this are pruned by the GC daemon
    retention_days: int = 90

    model_config = SettingsConfigDict(
        env_prefix="USAGE_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )


def _default_user_id() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return "default"


class Settings(BaseSettings):
    app_name: str = "Synatyx Context Engine"
    # Bumped alongside pyproject version. GIT_COMMIT is baked in at Docker
    # build time (see Dockerfile ARG); "local" when running outside Docker.
    app_version: str = "0.2.0"
    git_commit: str = "local"  # env GIT_COMMIT — baked in at Docker build time
    debug: bool = False
    log_level: str = "INFO"
    run_mode: RunMode = RunMode.MCP
    # Identity used by MCP resources/prompts, which have no user_id argument
    # channel in most clients. Env: DEFAULT_USER_ID; falls back to OS user.
    default_user_id: str = Field(default_factory=_default_user_id)
    # Externally reachable base URL of this server (env PUBLIC_URL). Used as the
    # OAuth issuer and resource identifier, so it must match what clients type —
    # behind a reverse proxy set it to the public https URL (e.g.
    # https://memory.example.com). Authoritative: forwarded headers are not
    # trusted for issuer construction.
    public_url: str = "http://localhost:9000"

    # Use Field(default_factory=...) so each sub-settings class is instantiated
    # independently and resolves its own env vars with its own env_prefix.
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    oauth: OAuthSettings = Field(default_factory=OAuthSettings)
    gc: GCSettings = Field(default_factory=GCSettings)
    relation: RelationSettings = Field(default_factory=RelationSettings)
    consolidation: ConsolidationSettings = Field(default_factory=ConsolidationSettings)
    observer: ObserverSettings = Field(default_factory=ObserverSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    index: IndexSettings = Field(default_factory=IndexSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

