package gemini

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Route annotation can run independently of dispatch. Merely computing the
// notes must not emit a second mixed-tool warning containing a raw field name.
func TestComputingGeminiRouteDropsDoesNotLog(t *testing.T) {
	var buf bytes.Buffer
	previous := slog.Default()
	t.Cleanup(func() { slog.SetDefault(previous) })
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))
	req := &models.ChatCompletionRequest{Tools: []models.Tool{
		{Type: "function", Function: models.ToolFunction{Name: "lookup"}, AllowedCallers: []string{"synthetic"}},
		{Raw: json.RawMessage(`{"private-field":"fixture","another-field":true}`)},
	}}
	route, drops := (&Provider{}).ChatRouteDrops(req)
	if route != models.RouteGemini || !reflect.DeepEqual(drops, []string{"allowed_callers"}) {
		t.Fatalf("route, drops = %q, %v", route, drops)
	}
	if buf.Len() != 0 {
		t.Errorf("computing notes wrote a log record: %s", &buf)
	}
}

func TestToolPolicyLogOnlyNamesAndSettingKeys(t *testing.T) {
	var buf bytes.Buffer
	previous := slog.Default()
	t.Cleanup(func() { slog.SetDefault(previous) })
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))

	req := &models.ChatCompletionRequest{
		Model:      "private-model",
		ToolChoice: json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","name":"lookup"}]}}`),
		Tools: []models.Tool{
			{Type: "function", Function: models.ToolFunction{
				Name: "lookup", Description: "private-description",
				Parameters: json.RawMessage(`{"type":"object","properties":{"private-key":{"enum":["private-value"]}}}`),
			}, AllowedCallers: []string{"private-caller"}},
			{Type: "function", Function: models.ToolFunction{Name: "unselected"}, AllowedCallers: []string{"private-caller"}},
		},
	}
	logToolPolicyDrops(req)

	var record map[string]any
	if err := json.Unmarshal(buf.Bytes(), &record); err != nil {
		t.Fatalf("expected one structured warning: %v", err)
	}
	delete(record, "time")
	want := map[string]any{
		"level": "WARN", "msg": "gemini drops unenforceable tool settings",
		"parameters": []any{"allowed_callers"}, "tool_names": []any{"lookup"},
	}
	if !reflect.DeepEqual(record, want) {
		t.Errorf("warning = %#v, want %#v", record, want)
	}
}
