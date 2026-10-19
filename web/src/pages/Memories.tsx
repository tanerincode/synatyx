import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Memories.tsx + src/pages/memories/).
export default function MemoriesPage() {
  return <PagePlaceholder title="Memories" lede="Every memory in a collection: filter, sort, open the detail drawer." calls={["api.itemsPage", "api.item", "api.itemAction"]} />;
}
