import { useMemo, useState } from "react";
import { api } from "../api/client";
import { EmptyState, ErrorState, Icon, ProjTag, SkeletonBlock } from "../components/ui";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { pickSkill, searchSkills, skillKey } from "./skills/logic";
import { SkillDetail } from "./skills/SkillDetail";

export default function SkillsPage() {
  const { project, user, param, setParams } = useScope();
  const list = useApi(`skills:${project}:${user}`, () => api.skills({ user: user || undefined, project: project || undefined }));
  const [query, setQuery] = useState("");

  const shown = useMemo(() => searchSkills(list.data?.skills ?? [], query), [list.data, query]);
  const selected = pickSkill(shown, param("skill"), param("owner"));
  const total = list.data?.skills.length ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Skills</h1>
          <p>
            {list.data ? `${total} skill${total === 1 ? "" : "s"} · ` : ""}matched to tasks by <span className="mono">context_skill_find</span>
          </p>
        </div>
      </div>

      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} what="skills" />
      ) : list.loading && !list.data ? (
        <div className="two-col">
          <div className="card">
            <SkeletonBlock lines={6} />
          </div>
          <div className="card">
            <SkeletonBlock lines={8} />
          </div>
        </div>
      ) : total === 0 ? (
        <EmptyState icon="skills" title="No skills yet">
          Agents save reusable skills with context_skill_store; they appear here.
        </EmptyState>
      ) : (
        <div className="two-col">
          <div className="card" style={{ padding: 10 }}>
            <label className="input-icon" style={{ display: "flex", marginBottom: 8 }}>
              <Icon name="search" />
              <input className="input" style={{ width: "100%" }} placeholder="Search skills…" aria-label="Search skills" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <nav aria-label="Skills">
              {shown.length === 0 && <div className="muted small" style={{ padding: 8 }}>No skill matches “{query}”.</div>}
              {shown.map((s) => (
                <a
                  key={skillKey(s)}
                  className={`list-i ${selected && skillKey(selected) === skillKey(s) ? "on" : ""}`}
                  style={{ display: "block" }}
                  href="#"
                  aria-current={selected && skillKey(selected) === skillKey(s) ? "true" : undefined}
                  data-testid="skill-item"
                  onClick={(e) => {
                    e.preventDefault();
                    setParams({ skill: s.slug, owner: s.user_id });
                  }}
                >
                  <div className="row">
                    <span className="t">{s.name}</span>
                    <span style={{ marginLeft: "auto" }}>
                      <ProjTag project={s.project ?? "users"} />
                    </span>
                  </div>
                  <div className="d">{s.description}</div>
                </a>
              ))}
            </nav>
          </div>
          {selected ? <SkillDetail skill={selected} /> : <div className="card muted">Select a skill.</div>}
        </div>
      )}
    </>
  );
}
