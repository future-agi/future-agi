package main

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// The README states what each route family does on this release. The
// realtime, video, search and scheduled-job routes exist and answer 501 here:
// no provider adapter that ships with this release serves realtime, video or
// search, and the config refuses routing.scheduled.enabled.
// So wherever the README names one of those routes or capabilities, the same
// line (or the same row of an HTML table) says so, and nothing lists them as
// a working capability.

// readmeUnits returns the README as units: each <tr>...</tr> block of an HTML
// table is one unit (its cells sit on separate lines), every other line is
// one unit. Each unit carries the line number it starts on.
func readmeUnits(t *testing.T, path string) []struct {
	line int
	text string
} {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var units []struct {
		line int
		text string
	}
	lines := strings.Split(string(raw), "\n")
	for i := 0; i < len(lines); i++ {
		start, text := i+1, lines[i]
		if strings.TrimSpace(text) == "<tr>" {
			for i+1 < len(lines) && strings.TrimSpace(lines[i]) != "</tr>" {
				i++
				text += "\n" + lines[i]
			}
		}
		units = append(units, struct {
			line int
			text string
		}{start, text})
	}
	return units
}

// unavailableRoutes are the routes that answer 501 on this release.
var unavailableRoutes = regexp.MustCompile(`/v1/(realtime|videos|search|scheduled)\b`)

// unavailableWords name the same capabilities in prose ("grounded search"
// for search: vector store search works).
var unavailableWords = regexp.MustCompile(`(?i)realtime|websocket|video|grounded search|\bscheduled\b`)

// speechRelay names the speech vendors whose own WebSocket APIs the gateway
// relays (GET /deepgram/..., /cartesia/...). Those WebSocket routes answer on
// this release (server: TestCartesiaSynthesisEnforcesTheKeysModelRestrictions,
// TestSpeechRelaySessionOutlivesTheRequestCeiling), so "WebSocket" on a line
// about them is not the unavailable realtime API.
var speechRelay = regexp.MustCompile(`(?i)deepgram|cartesia`)

// unavailableWord is the first unavailable capability u names, "" when
// none: "websocket" on a line about the speech relays does not count.
func unavailableWord(text string) string {
	for _, w := range unavailableWords.FindAllString(text, -1) {
		if strings.EqualFold(w, "websocket") && speechRelay.MatchString(text) {
			continue
		}
		return w
	}
	return ""
}

func TestReadmeSaysWhichRoutesAnswer501(t *testing.T) {
	units := readmeUnits(t, "../../README.md")
	for _, u := range units {
		says501 := strings.Contains(u.text, "501")
		if unavailableRoutes.MatchString(u.text) && !says501 {
			t.Errorf("README.md:%d names %s without saying it answers 501 on this release:\n%s",
				u.line, unavailableRoutes.FindString(u.text), u.text)
			continue
		}
		if w := unavailableWord(u.text); w != "" && !says501 && !strings.Contains(u.text, "not available on this release") {
			t.Errorf("README.md:%d lists %q as a capability without saying it is not available on this release (501):\n%s",
				u.line, w, u.text)
		}
		if strings.Contains(u.text, "shipped, not roadmap") {
			t.Errorf("README.md:%d says \"shipped, not roadmap\"", u.line)
		}
	}

	// The comparison table: Agent Command Center's cell of the realtime and
	// video rows is not "✅" (first-class).
	for _, u := range units {
		for _, row := range []string{"| **Realtime / WebSocket** |", "| **Video API** |"} {
			if !strings.HasPrefix(u.text, row) {
				continue
			}
			cells := strings.Split(u.text, "|")
			if len(cells) < 3 || strings.Contains(cells[2], "✅") {
				t.Errorf("README.md:%d comparison row %s rates this gateway first-class: %s", u.line, row, u.text)
			}
		}
	}
}
