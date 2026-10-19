export type DiffLine = { op: "ctx" | "add" | "del"; text: string };

/** Line diff via LCS. Fine for brief bodies (hundreds of lines). */
export function lineDiff(a: string, b: string): DiffLine[] {
  const A = a.split("\n");
  const B = b.split("\n");
  const n = A.length;
  const m = B.length;
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  const at = (i: number, j: number): number => L[i]?.[j] ?? 0;
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      (L[i] as number[])[j] = A[i] === B[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1));
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && A[i] === B[j]) {
      out.push({ op: "ctx", text: A[i] as string });
      i++;
      j++;
    } else if (j < m && (i >= n || at(i, j + 1) >= at(i + 1, j))) {
      out.push({ op: "add", text: B[j] as string });
      j++;
    } else {
      out.push({ op: "del", text: A[i] as string });
      i++;
    }
  }
  return out;
}
