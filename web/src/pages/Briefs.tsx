import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Briefs.tsx + src/pages/briefs/).
export default function BriefsPage() {
  return <PagePlaceholder title="Briefs" lede="Handoffs an orchestrator writes for an agent: board, list and detail with the report thread." calls={["api.briefs", "api.brief", "api.createBrief", "api.patchBrief"]} />;
}
