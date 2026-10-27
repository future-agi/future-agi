// Fun messages for mascot-themed states — loading, empty, failed.
// Keep the voice consistent with the 404 / error fallback pages:
// - Short
// - Space/AI/dev humor
// - Friendly, never blaming the user

export const LOADING_MESSAGES = [
  // Space-themed
  "Fueling the rocket...",
  "Running pre-flight checks...",
  "Plotting a course through the data...",
  "Warming up the warp drive...",
  "Igniting the boosters...",
  "Clearing the launch pad...",
  "Calibrating the star charts...",
  "Handshaking with mission control...",
  "Aligning the thrusters...",
  "Checking oxygen levels... all good.",

  // AI / dev flavor
  "Summoning the neurons...",
  "Tokenizing your request...",
  "Loading the training wheels...",
  "Teaching the AI manners...",
  "Compiling good vibes...",
  "Thinking at the speed of light. Almost.",
  "Crunching numbers the old-fashioned way... kidding, it's GPUs.",
  "Waking up the hamsters...",
  "Polishing the pixels...",
  "Fetching data from the cloud... the actual clouds.",
];

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

export const pickRandom = (list) => list[Math.floor(Math.random() * list.length)];
