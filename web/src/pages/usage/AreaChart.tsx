import { useState } from "react";
import type { Usage } from "../../api/types";
import { fmt } from "../../lib/format";
import { dayLabel, dayX, SERIES, stackAreas } from "./chart";

const W = 680;
const H = 190;

/** Tokens per day as a stacked area chart; hovering a day reads its numbers out above the chart. */
export function AreaChart({ days }: { days: Usage["by_day"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const { layers, max } = stackAreas(days, W, H);
  if (layers.length === 0) return <p className="muted small">No tokens in this window.</p>;

  const at = hover === null ? null : days[hover];
  return (
    <div data-testid="area-chart">
      <div className="small" style={{ minHeight: 20, marginBottom: 6 }} aria-live="polite" data-testid="area-readout">
        {at ? (
          <>
            <b>{dayLabel(at.day)}</b>
            <span className="muted">
              {" · "}
              {SERIES.slice().reverse().map((s) => `${fmt(at[s.key])} ${s.label}`).join(" · ")}
            </span>
          </>
        ) : (
          <span className="muted">Hover a day for its numbers · peak {fmt(max)} tokens</span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} role="img" aria-label="Tokens per day, stacked: context out, args in, embeddings">
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={0} x2={W} y1={H * f} y2={H * f} stroke="var(--line)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
        {layers.map((l) => (
          <path key={l.key} d={l.path} fill={l.color} fillOpacity={0.55} stroke={l.color} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
        ))}
        {hover !== null && (
          <line x1={dayX(hover, days.length, W)} x2={dayX(hover, days.length, W)} y1={0} y2={H} stroke="var(--ink-2)" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        )}
        {days.map((d, i) => {
          const step = days.length < 2 ? W : W / (days.length - 1);
          return (
            <rect
              key={d.day}
              data-testid="day-hit"
              x={dayX(i, days.length, W) - step / 2}
              y={0}
              width={step}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover((h) => (h === i ? null : h))}
            />
          );
        })}
      </svg>
      <div className="row small faint" style={{ justifyContent: "space-between", marginTop: 4 }}>
        <span className="mono">{days[0]?.day}</span>
        <span className="mono">{days[Math.floor((days.length - 1) / 2)]?.day}</span>
        <span className="mono">{days[days.length - 1]?.day}</span>
      </div>
    </div>
  );
}
