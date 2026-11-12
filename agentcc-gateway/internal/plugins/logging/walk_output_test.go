package logging

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

const walkOutputBody = `{"choices":[{"index":0,"message":{"role":"assistant","content":null,"audio":{"id":"audio_fixture","data":"U0VDUkVU","transcript":"SECRET spoken","expires_at":1900000000},"refusal":"SECRET refusal","tool_calls":[{"id":"call_fixture","type":"function","function":{"name":"weather","arguments":"{\"city\":\"SECRET\"}"}}]},"finish_reason":"tool_calls"}]}`

func TestWalkOutputLogPrivacy(t *testing.T) {
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(walkOutputBody), &resp); err != nil {
		t.Fatal(err)
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = &resp
	rc.Request = &models.ChatCompletionRequest{Messages: []models.Message{resp.Choices[0].Message}}
	rec := buildRecord(rc, config.RequestLoggingConfig{IncludeBodies: true})
	before, err := json.Marshal(rec)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{`"audio"`, `"refusal"`, `"tool_calls"`, `U0VDUkVU`, `SECRET spoken`} {
		if !strings.Contains(string(before), s) {
			t.Errorf("full response log lost %s: %s", s, before)
		}
	}
	redactor := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "fixture", Pattern: "SECRET"}})
	for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull} {
		t.Run(mode, func(t *testing.T) {
			redacted := redactRecord(rec, redactor, mode)
			raw, err := json.Marshal(redacted)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(raw), "SECRET") || strings.Contains(string(raw), "U0VDUkVU") || strings.Contains(string(raw), "audio_fixture") {
				t.Fatalf("newly retained output escaped log privacy: %s", raw)
			}
			for _, field := range []string{`"audio"`, `"refusal"`, `"tool_calls"`} {
				if !strings.Contains(string(raw), field) {
					t.Errorf("redaction removed supported field %s", field)
				}
			}
		})
	}
	after, _ := json.Marshal(rec)
	if string(after) != string(before) {
		t.Fatal("log privacy mutated caller response or request")
	}
	noBodies := buildRecord(rc, config.RequestLoggingConfig{})
	if noBodies.RequestBody != nil || noBodies.ResponseBody != nil {
		t.Fatal("include_bodies=false retained output")
	}
}
