import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Usage.tsx + src/pages/usage/).
export default function UsagePage() {
  return <PagePlaceholder title="Usage" lede="What each tool injects into agent context, and what embeddings cost." calls={["api.usage"]} />;
}
