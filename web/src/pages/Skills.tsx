import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Skills.tsx + src/pages/skills/).
export default function SkillsPage() {
  return <PagePlaceholder title="Skills" lede="Reusable skills agents find with context_skill_find." calls={["api.skills", "api.skill"]} />;
}
