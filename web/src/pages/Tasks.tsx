import { PagePlaceholder } from "../components/ui";

// Placeholder: the page is built in its own PR (src/pages/Tasks.tsx + src/pages/tasks/).
export default function TasksPage() {
  return <PagePlaceholder title="Tasks" lede="Tasks across projects and users, as a table or a board." calls={["api.tasks", "api.patchTask"]} />;
}
