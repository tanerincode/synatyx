// Small markdown renderer that builds React nodes (no innerHTML), so memory
// and brief content can never inject markup. Supports headings, lists,
// fenced code, blockquotes, **bold**, *em* and `code`.
import { Fragment, type ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(re)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    const id = `${key}-${k++}`;
    if (tok.startsWith("`")) out.push(<code key={id}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) out.push(<strong key={id}>{tok.slice(2, -2)}</strong>);
    else out.push(<em key={id}>{tok.slice(1, -1)}</em>);
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source, className = "md" }: { source: string; className?: string }) {
  const blocks: ReactNode[] = [];
  const lines = source.split("\n");
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;
  let code: string[] | null = null;
  const key = (): string => `b${blocks.length}`;
  const flushPara = (): void => {
    if (para.length) {
      const k = key();
      blocks.push(<p key={k}>{inline(para.join(" "), k)}</p>);
      para = [];
    }
  };
  const flushList = (): void => {
    if (list) {
      const k = key();
      const items = list.items.map((t, i) => <li key={i}>{inline(t, `${k}-${i}`)}</li>);
      blocks.push(list.kind === "ul" ? <ul key={k}>{items}</ul> : <ol key={k}>{items}</ol>);
      list = null;
    }
  };
  for (const ln of lines) {
    if (ln.startsWith("```")) {
      flushPara();
      flushList();
      if (code) {
        blocks.push(
          <pre key={key()}>
            <code>{code.join("\n")}</code>
          </pre>,
        );
        code = null;
      } else code = [];
      continue;
    }
    if (code) {
      code.push(ln);
      continue;
    }
    let m: RegExpMatchArray | null;
    if ((m = ln.match(/^(#{1,3}) (.*)/))) {
      flushPara();
      flushList();
      const k = key();
      const level = (m[1] ?? "#").length;
      const kids = inline(m[2] ?? "", k);
      blocks.push(level === 1 ? <h1 key={k}>{kids}</h1> : level === 2 ? <h2 key={k}>{kids}</h2> : <h3 key={k}>{kids}</h3>);
    } else if ((m = ln.match(/^\s*[-*] (.*)/))) {
      flushPara();
      if (list?.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(m[1] ?? "");
    } else if ((m = ln.match(/^\s*\d+\. (.*)/))) {
      flushPara();
      if (list?.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(m[1] ?? "");
    } else if ((m = ln.match(/^> ?(.*)/))) {
      flushPara();
      flushList();
      const k = key();
      blocks.push(<blockquote key={k}>{inline(m[1] ?? "", k)}</blockquote>);
    } else if (!ln.trim()) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(ln);
    }
  }
  if (code) blocks.push(<pre key={key()}><code>{(code as string[]).join("\n")}</code></pre>);
  flushPara();
  flushList();
  return <div className={className}>{blocks.length ? blocks : <Fragment />}</div>;
}
