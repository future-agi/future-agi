import { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import useAttachmentGrants from "./useAttachmentGrants";

export default function InlineClipPlayer({
  projectId,
  parent,
  item,
  label,
  active,
  onActivate,
}) {
  const id = item.reference.occurrence_id;
  const node = useRef(null),
    intent = useRef(false),
    recovered = useRef(false),
    position = useRef(0);
  const [playing, setPlaying] = useState(false),
    [failed, setFailed] = useState(false);
  const enabled = active && item.status === "ready" && item.capture_complete;
  const { results, status, retry } = useAttachmentGrants(
    projectId,
    [{ occurrence_id: id, parent, representation: "playable" }],
    { enabled, renew: true },
  );
  const grant = results[0],
    path =
      enabled && grant?.status === "ready" && !failed
        ? grant.gateway_path
        : null;
  const attach = useCallback((el) => {
    const prev = node.current;
    if (prev && prev !== el) {
      prev.pause();
      prev.removeAttribute("src");
      prev.load();
    }
    node.current = el;
  }, []);
  useEffect(() => {
    if (!enabled) {
      intent.current = false;
      recovered.current = false;
      position.current = 0;
      setPlaying(false);
      setFailed(false);
    }
  }, [enabled]);
  useEffect(() => {
    const el = node.current;
    if (!el || !path) return undefined;
    let alive = true;
    const restore = () => {
      if (position.current) el.currentTime = position.current;
      if (intent.current)
        void el.play()?.catch(() => {
          if (alive) {
            intent.current = false;
            setPlaying(false);
          }
        });
    };
    el.addEventListener("loadedmetadata", restore, { once: true });
    el.src = path;
    el.load();
    return () => {
      alive = false;
      position.current = el.currentTime || 0;
      el.removeEventListener("loadedmetadata", restore);
      el.pause();
      el.removeAttribute("src");
      el.load();
    };
  }, [path]);
  const incomplete =
    item.status === "partial" ||
    (item.status === "ready" && !item.capture_complete);
  if (item.status !== "ready" || incomplete)
    return (
      <Typography variant="caption" role="status">
        {incomplete
          ? "Clip incomplete"
          : item.status === "failed"
            ? "Clip unavailable"
            : "Clip processing"}
      </Typography>
    );
  const denied = enabled && status === "ready" && grant?.status !== "ready";
  if (denied)
    return (
      <Typography variant="caption" role="status">
        Clip unavailable
      </Typography>
    );
  const error = failed || (enabled && status === "unavailable");
  const busy = enabled && !error && !path;
  const action = error
    ? "Retry clip"
    : busy
      ? "Preparing clip…"
      : playing
        ? "Pause clip"
        : "Play clip";
  return (
    <>
      <Button
        size="small"
        color="inherit"
        aria-label={`${action}: ${label}`}
        aria-disabled={busy}
        startIcon={
          <Iconify
            width={14}
            icon={playing ? "solar:pause-linear" : "solar:play-linear"}
          />
        }
        onClick={(e) => {
          e.stopPropagation();
          if (busy) return;
          if (playing) {
            intent.current = false;
            node.current?.pause();
            return;
          }
          intent.current = true;
          if (error) {
            recovered.current = false;
            setFailed(false);
            retry();
          } else if (!active) onActivate(id);
          else if (path)
            void node.current?.play()?.catch(() => {
              intent.current = false;
              setPlaying(false);
            });
        }}
      >
        {action}
      </Button>
      {enabled && (
        <audio
          hidden
          ref={attach}
          aria-label={`Clip: ${label}`}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            intent.current = false;
            position.current = 0;
            setPlaying(false);
          }}
          onError={() => {
            if (!node.current?.getAttribute("src")) return;
            if (!recovered.current) {
              recovered.current = true;
              retry();
            } else {
              intent.current = false;
              setFailed(true);
            }
          }}
        />
      )}
    </>
  );
}
InlineClipPlayer.propTypes = {
  projectId: PropTypes.string.isRequired,
  parent: PropTypes.object.isRequired,
  item: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
  active: PropTypes.bool,
  onActivate: PropTypes.func.isRequired,
};
