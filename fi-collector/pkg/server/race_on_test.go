//go:build race

package server

// raceEnabled: the race detector makes allocation-heavy tests many times
// slower; they shrink what they build (but keep its shape).
const raceEnabled = true
