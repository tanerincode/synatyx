import { useMemo, useState } from "react";
import { Icon } from "../../components/ui";
import { fmt } from "../../lib/format";
import { buildTree, visibleRows, type TreeNode } from "./tree";
import type { IndexGraph } from "../../api/types";
import "./codeindex.css";

/** Collapsible file tree of one index; selecting a node reports its path. */
export function FileTree({ graph, selected, onSelect }: { graph: IndexGraph; selected: string; onSelect: (path: string) => void }) {
  const roots = useMemo(() => buildTree(graph), [graph]);
  const [overrides, setOverrides] = useState<ReadonlyMap<string, boolean>>(new Map());
  const rows = visibleRows(roots, overrides);

  const toggle = (node: TreeNode, open: boolean): void =>
    setOverrides((prev) => new Map(prev).set(node.path, !open));

  if (roots.length === 0) return <p className="muted small">No files in this index yet.</p>;
  return (
    <div className="tree ci-tree" role="tree" aria-label="Indexed files">
      {rows.map(({ node, depth, open }) => {
        const dir = node.kind === "dir";
        return (
          <button
            key={node.path}
            type="button"
            role="treeitem"
            aria-level={depth + 1}
            aria-expanded={dir ? open : undefined}
            aria-selected={selected === node.path}
            className={`ci-tn${selected === node.path ? " on" : ""}`}
            style={{ paddingLeft: 6 + depth * 16 }}
            title={node.path}
            onClick={() => {
              if (dir) toggle(node, open);
              onSelect(node.path);
            }}
          >
            <span className="tw" aria-hidden="true">{dir ? (open ? "▾" : "▸") : ""}</span>
            <Icon name={dir ? "folder" : "file"} size={14} />
            <span>{node.name}</span>
            <span className="c faint">{fmt(node.chunks)}</span>
          </button>
        );
      })}
    </div>
  );
}
