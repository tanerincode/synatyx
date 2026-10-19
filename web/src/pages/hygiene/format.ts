export const similarityLabel = (s: number | null): string => (s === null ? "—" : `${Math.round(s * 100)}%`);
