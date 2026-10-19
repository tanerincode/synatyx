import type { IndexChunk } from "../../api/types";
import { ago } from "../../lib/format";
import { chunkLines, lineRange } from "./tree";
import "./codeindex.css";

export function ChunkCard({ chunk }: { chunk: IndexChunk }) {
  const range = lineRange(chunk);
  const kind = [chunk.kind, chunk.symbol].filter(Boolean).join(" · ");
  return (
    <div className="ci-chunk" data-testid="chunk">
      <div className="ci-chunk-h">
        <span className="path">{chunk.path}</span>
        {kind && <span className="ci-kind">{kind}</span>}
        <span className="faint">
          {range && <>{range} · </>}chunk {chunk.chunk_index + 1}/{chunk.chunk_total}
        </span>
        <span className="faint" style={{ marginLeft: "auto" }}>{ago(chunk.indexed_at)}</span>
      </div>
      <div className="code" role="region" aria-label={`${chunk.path} chunk ${chunk.chunk_index + 1}`}>
        {chunkLines(chunk).map((l, i) => (
          <div className="ln" key={i}>
            <span>{l.no ?? ""}</span>
            <span>{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
