import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Graph.tsx + src/pages/graph/).
export default function GraphPage() {
  return <PagePlaceholder title="Graph" lede="Memories and their relations as a 2D force graph." calls={["api.graph", "api.item"]} />;
}
