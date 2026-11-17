package main

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// The README states what each route family does on this release. The
// realtime, search and scheduled-job routes exist and answer 501 here
// (release note C4): no provider adapter that ships with this release serves
// realtime or search, and the config refuses routing.scheduled.enabled.
// So wherever the README names one of those routes or capabilities, the same
// line (or the same row of an HTML table) says so, and nothing lists them as
// a working capability. Video answers 501 until an operator turns it on and
// configures a video provider, and then needs an organization API key: one
// README paragraph says so.

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

// r7fUnavailableRoutes are the routes that answer 501 on this release.
var r7fUnavailableRoutes = regexp.MustCompile(`/v1/(realtime|search|scheduled)\b`)

// r7fUnavailableWords name the same capabilities in prose ("grounded search"
// for search: vector store search works).
var r7fUnavailableWords = regexp.MustCompile(`(?i)realtime|websocket|grounded search|\bscheduled\b`)

// r7fSpeechRelay names the speech vendors whose own WebSocket APIs the gateway
// relays (GET /deepgram/..., /cartesia/...). Those WebSocket routes answer on
// this release (server: TestCartesiaSynthesisEnforcesTheKeysModelRestrictions,
// TestSpeechRelaySessionOutlivesTheRequestCeiling), so "WebSocket" on a line
// about them is not the unavailable realtime API.
var r7fSpeechRelay = regexp.MustCompile(`(?i)deepgram|cartesia`)

// r7fUnavailableWord is the first unavailable capability u names, "" when
// none: "websocket" on a line about the speech relays does not count.
func r7fUnavailableWord(text string) string {
	for _, w := range r7fUnavailableWords.FindAllString(text, -1) {
		if strings.EqualFold(w, "websocket") && r7fSpeechRelay.MatchString(text) {
			continue
		}
		return w
	}
	return ""
}

// r7fSpeechGuardrails is the heading of the README section on guardrails for
// the speech relays. Its error codes and close reasons are named for what
// they guard (realtime_guardrail_unsupported, realtime_guardrail_blocked, a
// WebSocket 1008 close): there "realtime" and "WebSocket" are not the
// unavailable /v1/realtime API, so the capability-word rule skips those two
// words inside it. The route rule holds there as everywhere: a line naming
// /v1/realtime must say 501.
const r7fSpeechGuardrails = "### Streaming speech guardrail contract"

// r7fHeading matches a Markdown heading line.
var r7fHeading = regexp.MustCompile(`^#{1,6} `)

func TestR7fReadmeSaysWhichRoutesAnswer501(t *testing.T) {
	units := readmeUnits(t, "../../README.md")
	inSpeechGuardrails := false
	for _, u := range units {
		if r7fHeading.MatchString(u.text) {
			inSpeechGuardrails = strings.TrimSpace(u.text) == r7fSpeechGuardrails
		}
		says501 := strings.Contains(u.text, "501")
		if r7fUnavailableRoutes.MatchString(u.text) && !says501 {
			t.Errorf("README.md:%d names %s without saying it answers 501 on this release:\n%s",
				u.line, r7fUnavailableRoutes.FindString(u.text), u.text)
			continue
		}
		w := r7fUnavailableWord(u.text)
		if inSpeechGuardrails {
			// Any other unavailable capability named there still counts.
			w = ""
			for _, c := range r7fUnavailableWords.FindAllString(u.text, -1) {
				if !strings.EqualFold(c, "realtime") && !strings.EqualFold(c, "websocket") {
					w = c
					break
				}
			}
		}
		if w != "" && !says501 && !strings.Contains(u.text, "not available on this release") {
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

	// Video: one paragraph says when it answers 501 and what it needs then.
	videoRule := []string{
		"answer 501",
		"Video generation is not configured",
		"unless an operator turns video on and configures a video provider",
		"organization API key",
	}
	raw, err := os.ReadFile("../../README.md")
	if err != nil {
		t.Fatal(err)
	}
	said := false
	for _, para := range strings.Split(string(raw), "\n\n") {
		text := strings.Join(strings.Fields(para), " ")
		all := true
		for _, part := range videoRule {
			all = all && strings.Contains(text, part)
		}
		said = said || all
	}
	if !said {
		t.Errorf("README.md has no paragraph saying that video answers 501 %q unless an operator turns video on and configures a video provider, and then needs an organization API key", "Video generation is not configured")
	}
}
