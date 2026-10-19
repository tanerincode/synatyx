import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/CodeIndex.tsx + src/pages/codeindex/).
export default function CodeIndexPage() {
  return <PagePlaceholder title="Code index" lede="One code index per project: files, chunks, symbols." calls={["api.indexes", "api.indexGraph", "api.indexChunks"]} />;
}
