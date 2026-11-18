import PropTypes from "prop-types";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import { SVG_TYPE as type, cut } from "./sequenceLayout";

// Each participant's line down the drawing; the followed one in the accent colour.
export function LaneLines({ lanes, layout, followed, c }) {
  const { X, headH, H } = layout;
  return lanes.map((ln) => (
    <line
      key={`l-${ln.id}`}
      x1={X[ln.id]}
      y1={10 + headH}
      x2={X[ln.id]}
      y2={H - 4}
      stroke={followed === ln.id ? c.selLine : c.line}
      strokeWidth={followed === ln.id ? 1.5 : 1.2}
    />
  ));
}

LaneLines.propTypes = {
  lanes: PropTypes.array.isRequired,
  layout: PropTypes.object.isRequired,
  followed: PropTypes.string,
  c: PropTypes.object.isRequired,
};

// Each participant's name and role on top; a click follows that participant.
export function LaneHeads({ lanes, layout, followed, onFollow, c }) {
  const { X, headW, headH, heads } = layout;
  return lanes.map((ln, i) => {
    const on = followed === ln.id;
    const toggle = () => onFollow(on ? null : ln.id);
    return (
      <g
        key={`h-${ln.id}`}
        role="button"
        tabIndex={0}
        aria-pressed={on}
        data-lane-head="true"
        aria-label={on ? C.stopFollowing(ln.name) : C.follow(ln.name)}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        style={{ cursor: "pointer", outline: "none" }}
      >
        <title>
          {on ? C.followingHint(ln.name) : C.followHint(ln.name, ln.role)}
        </title>
        <rect
          x={X[ln.id] - headW / 2}
          y={10}
          width={headW}
          height={headH}
          rx={6}
          fill={on ? c.sel : c.paper}
          stroke={on ? c.selLine : c.line}
          strokeWidth={on ? 1.5 : 1}
        />
        {heads[i].map((line, j) => (
          <text
            // Two lines of one name can read the same; their place tells them apart.
            key={j}
            x={X[ln.id]}
            y={27 + j * 14}
            textAnchor="middle"
            fontSize={type.md}
            fontWeight="600"
            fill={c.fg}
          >
            {line}
          </text>
        ))}
        <text
          x={X[ln.id]}
          y={10 + headH - 8}
          textAnchor="middle"
          fontSize={type.sm}
          fill={c.muted}
        >
          {cut(ln.role, Math.floor((headW - 12) / 5.8))}
        </text>
      </g>
    );
  });
}

LaneHeads.propTypes = {
  lanes: PropTypes.array.isRequired,
  layout: PropTypes.object.isRequired,
  followed: PropTypes.string,
  onFollow: PropTypes.func.isRequired,
  c: PropTypes.object.isRequired,
};
