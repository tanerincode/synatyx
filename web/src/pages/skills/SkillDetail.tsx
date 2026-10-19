import { api } from "../../api/client";
import type { Skill } from "../../api/types";
import { ErrorState, Icon, ProjTag, SkeletonBlock } from "../../components/ui";
import { ago } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import { useToast } from "../../lib/toast";
import { useApi } from "../../lib/useApi";
import { skillKey } from "./logic";

export function SkillDetail({ skill }: { skill: Skill }) {
  const toast = useToast();
  const detail = useApi(`skill:${skillKey(skill)}:${skill.project ?? ""}`, () => api.skill(skill.slug, skill.user_id, skill.project ?? undefined));

  const copy = (): void => {
    navigator.clipboard.writeText(skill.slug).then(
      () => toast(`Copied ${skill.slug}`),
      () => toast("Couldn't copy to the clipboard"),
    );
  };

  return (
    <article className="card" aria-label={skill.name}>
      <div className="row" style={{ marginBottom: 14 }}>
        <h2 style={{ fontSize: 18 }}>{skill.name}</h2>
        <ProjTag project={skill.project ?? "users"} />
        <div style={{ flex: 1 }} />
        <span className="mono small muted">{skill.slug}</span>
        <button className="btn sm" onClick={copy}>
          <Icon name="copy" />
          Copy slug
        </button>
      </div>
      {detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.reload} what="this skill" />
      ) : detail.data ? (
        <>
          <Markdown source={detail.data.content} />
          <div className="muted small" style={{ marginTop: 14 }}>
            Updated {ago(detail.data.updated_at)} · owner {detail.data.user_id}
          </div>
        </>
      ) : (
        <SkeletonBlock lines={6} />
      )}
    </article>
  );
}
