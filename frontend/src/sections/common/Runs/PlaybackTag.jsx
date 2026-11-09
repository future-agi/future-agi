/**
 * L7c g1 (the capability contract c): HIDDEN. A created run plays back data the mock world
 * invents from the run's choices, not a recorded run, so no `Simulated` /
 * `Plays back 20×` tag sits beside its progress (run chip, leaderboard
 * progress, tray, run picker). Progress and ETA stay. The component renders
 * nothing; callers in other groups drop it at merge.
 */
export function playbackText() {
  return null;
}

export default function PlaybackTag() {
  return null;
}
