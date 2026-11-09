// Messages for mascot-themed empty and failed states. Loaders do not use
// them: the app loader says "Loading…" only (see loading-screen.jsx).
// Keep the voice consistent with the 404 / error fallback pages:
// - Short
// - Space/AI/dev humor
// - Friendly, never blaming the user

export const EMPTY_MESSAGES = [
  // Space flavored "nothing here yet"
  "Nothing on the radar yet.",
  "The sector is quiet. Suspiciously quiet.",
  "We scanned this region. Only stars.",
  "Open space. Room for something new.",
  "This orbit is wide open.",
  "No signals detected. Yet.",
  "The void awaits your first creation.",
  "A blank canvas, ready for launch.",
  "Mission control is standing by.",
  "No data pinging back. Yet.",

  // Dev / AI flavor
  "Nothing to see here. Be the first.",
  "The dataset is taking a nap. Wake it up.",
  "Your inbox, but for rockets. Currently empty.",
  "The model hasn't found anything worth reporting.",
  "Empty table. Infinite possibilities.",
  "The query returned zero rows and a shrug.",
];

export const FAILED_MESSAGES = [
  // Mission-critical failures
  "The rocket hit a patch of turbulence.",
  "Our ship is rebooting its onboard systems.",
  "Navigation is recalculating the route.",
  "A solar flare scrambled the signal.",
  "The auto-pilot just flipped a coin and lost.",
  "Command center is re-running the handshake.",
  "We lost the uplink for a moment.",
  "The engine sputtered. Trying again.",
  "One of the thrusters gave up. Just one.",
  "We hit an asteroid. Just a small one.",

  // Dev / AI flavor
  "The request timed out somewhere in orbit.",
  "The server blinked. We blinked back.",
  "Something upstream is having a moment.",
  "A semicolon went rogue. We're tracking it.",
  "The backend is negotiating with the frontend.",
  "A cosmic ray flipped a bit. Happens.",
];

export const pickRandom = (list) =>
  list[Math.floor(Math.random() * list.length)];
