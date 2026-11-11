package contracttest

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestAssertSaneWithGaps(t *testing.T) {
	sane := Completion{
		Content:      "hi",
		FinishReason: "stop",
		Usage:        &models.Usage{PromptTokens: 3, CompletionTokens: 1, TotalTokens: 4},
	}
	noUsage := sane
	noUsage.Usage = nil
	empty := Completion{}

	tests := []struct {
		name   string
		live   bool
		c      Completion
		gaps   []string
		output bool
		want   []string // substrings of the reported failures, in order
	}{
		{"no gaps is AssertSane", false, noUsage, nil, true, []string{"usage is missing"}},
		{"known gap is absorbed", false, noUsage, []string{"usage is missing"}, true, nil},
		{"other failures still fail", false, empty, []string{"usage is missing"}, true,
			[]string{"is not canonical", "no content and no tool calls"}},
		{"gap that no longer occurs fails", false, sane, []string{"usage is missing"}, true,
			[]string{`known sanity gap "usage is missing" no longer occurs`}},
		{"live: gap that does not occur is fine", true, sane, []string{"usage is missing"}, true, nil},
		{"live: known gap is absorbed", true, noUsage, []string{"usage is missing"}, true, nil},
		{"live: other failures still fail", true, empty, []string{"usage is missing"}, true,
			[]string{"is not canonical", "no content and no tool calls"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c := Case{Live: tc.live}
			ft := runFake(func(tb *fakeTB) { AssertSaneWithGaps(tb, c, tc.c, tc.output, tc.gaps...) })
			if len(ft.errors) != len(tc.want) {
				t.Fatalf("got failures %q, want ones matching %q", ft.errors, tc.want)
			}
			for i, w := range tc.want {
				if !strings.Contains(ft.errors[i], w) {
					t.Errorf("failure %d = %q, want it to mention %q", i, ft.errors[i], w)
				}
			}
		})
	}
}
