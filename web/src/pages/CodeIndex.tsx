import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { IndexInfo } from "../api/types";
import { EmptyState, ErrorState, Icon, SkeletonBlock, SkeletonRows } from "../components/ui";
import { ago, fmt } from "../lib/format";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { ChunkCard } from "./codeindex/ChunkList";
import { FileTree } from "./codeindex/FileTree";
import { languageColor } from "./codeindex/tree";
import "./codeindex/codeindex.css";

const CHUNK_LIMIT = 100;

function Languages({ by }: { by: Record<string, number> }) {
  const entries = Object.entries(by).sort((a, b) => b[1] - a[1]);
  return (
    <div className="ci-langs">
      {entries.map(([lang, n]) => (
        <span key={lang} className="ci-lang">
          <i style={{ background: languageColor(lang) }} />
          {lang} {fmt(n)}
        </span>
      ))}
    </div>
  );
}

function IndexTable({ indexes, selected, onSelect }: { indexes: IndexInfo[]; selected: string; onSelect: (collection: string) => void }) {
  return (
    <div className="card flush table-wrap">
      <table aria-label="Indexed projects">
        <thead>
          <tr>
            <th>Project</th>
            <th className="right">Files</th>
            <th className="right">Chunks</th>
            <th>Languages</th>
            <th className="right">Last indexed</th>
          </tr>
        </thead>
        <tbody>
          {indexes.map((ix) => (
            <tr
              key={ix.collection}
              className={`ci-row${ix.collection === selected ? " on" : ""}`}
              tabIndex={0}
              aria-selected={ix.collection === selected}
              onClick={() => onSelect(ix.collection)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(ix.collection);
                }
              }}
            >
              <td><span className="tag">{ix.project}</span></td>
              <td className="right num">{fmt(ix.files)}</td>
              <td className="right num">{fmt(ix.chunks)}</td>
              <td><Languages by={ix.by_language} /></td>
              <td className="right num muted">{ago(ix.last_indexed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ index }: { index: IndexInfo }) {
  const { param, setParams } = useScope();
  const file = param("file");
  const [text, setText] = useState(param("q"));
  const q = param("q");

  // The search box writes to the URL after a pause, so typing does not push a history entry per key.
  useEffect(() => {
    if (text === q) return;
    const t = setTimeout(() => setParams({ q: text.trim() === "" ? null : text }), 250);
    return () => clearTimeout(t);
  }, [text, q, setParams]);

  const query = q || file;
  const graph = useApi(`ci:graph:${index.collection}`, () => api.indexGraph(index.collection));
  const chunks = useApi(`ci:chunks:${index.collection}:${query}`, () => api.indexChunks(index.collection, query, CHUNK_LIMIT));

  return (
    <div className="ci-split">
      <div className="card">
        <div className="card-h">
          <h2>Files · {index.project}</h2>
          <div className="grow" />
          {file && (
            <button className="btn sm" onClick={() => setParams({ file: null })}>
              <Icon name="x" /> Clear
            </button>
          )}
        </div>
        {graph.error ? (
          <ErrorState error={graph.error} onRetry={graph.reload} what="the file tree" />
        ) : !graph.data ? (
          <SkeletonBlock lines={8} />
        ) : (
          <>
            <FileTree graph={graph.data} selected={file} onSelect={(path) => setParams({ file: path })} />
            {graph.data.truncated && <p className="muted small" style={{ marginTop: 10 }}>Showing the first 400 files; search to reach the rest.</p>}
          </>
        )}
      </div>
      <div className="card">
        <div className="toolbar">
          <label className="input-icon">
            <Icon name="search" />
            <input className="input" style={{ width: 280 }} placeholder="Search path or symbol" aria-label="Search path or symbol" value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          {chunks.data && (
            <span className="muted small" data-testid="chunk-count">
              {chunks.data.total} {chunks.data.total === 1 ? "chunk" : "chunks"}
              {chunks.data.total > chunks.data.chunks.length && <> · showing {chunks.data.chunks.length}</>}
            </span>
          )}
          {file && !q && <span className="tag">{file}</span>}
        </div>
        {chunks.error ? (
          <ErrorState error={chunks.error} onRetry={chunks.reload} what="chunks" />
        ) : !chunks.data ? (
          <SkeletonBlock lines={6} />
        ) : chunks.data.chunks.length === 0 ? (
          <EmptyState icon="code" title="No chunks match">
            {query ? <>Nothing in this index has a path or symbol containing “{query}”.</> : "This index has no chunks yet."}
          </EmptyState>
        ) : (
          chunks.data.chunks.map((c) => <ChunkCard key={`${c.path}:${c.chunk_index}`} chunk={c} />)
        )}
      </div>
    </div>
  );
}

export default function CodeIndexPage() {
  const { project, param, setParams } = useScope();
  const indexes = useApi("ci:indexes", () => api.indexes());
  const all = indexes.data?.indexes ?? [];
  const shown = project ? all.filter((i) => i.project === project) : all;
  const selected = shown.find((i) => i.collection === param("index")) ?? shown[0];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Code index</h1>
          <p>One index per project: files, chunks, symbols. Indexes are filled by the local indexer or <span className="mono">context_index_push</span>.</p>
        </div>
      </div>
      {indexes.error ? (
        <div className="card"><ErrorState error={indexes.error} onRetry={indexes.reload} what="code indexes" /></div>
      ) : !indexes.data ? (
        <div className="card flush table-wrap"><table><SkeletonRows rows={3} cols={5} /></table></div>
      ) : shown.length === 0 ? (
        <div className="card">
          <EmptyState icon="code" title={project ? `No code index for ${project}` : "No code indexes yet"}>
            Run the local indexer in a repository (or push files with <span className="mono">context_index_push</span>) and its index appears here.
          </EmptyState>
        </div>
      ) : (
        <>
          <IndexTable indexes={shown} selected={selected?.collection ?? ""} onSelect={(c) => setParams({ index: c, file: null, q: null })} />
          {selected && <Detail key={selected.collection} index={selected} />}
        </>
      )}
    </>
  );
}
