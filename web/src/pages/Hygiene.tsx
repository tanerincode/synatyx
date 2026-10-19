import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Hygiene.tsx + src/pages/hygiene/).
export default function HygienePage() {
  return <PagePlaceholder title="Hygiene" lede="Garbage collection, duplicates and staleness." calls={["api.hygiene", "api.itemAction"]} />;
}
