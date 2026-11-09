package anthropic

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestStreamToolStateBounds(t *testing.T) {
	ctx := context.Background()
	events := make(chan []byte, 16)
	state := &streamState{currentBlockIdx: -1}
	if err := toolDelta(ctx, state, models.ToolCallDelta{Index: 0, ID: strings.Repeat("x", maxPendingToolBytes)}, events); err != nil {
		t.Fatal(err)
	}
	if err := toolDelta(ctx, state, models.ToolCallDelta{Index: 0, Function: &models.FunctionCall{Name: "a"}}, events); err == nil {
		t.Fatal("unbounded incomplete metadata")
	}
	state = &streamState{currentBlockIdx: -1}
	for i := 0; i < maxStreamTools; i++ {
		if err := toolDelta(ctx, state, models.ToolCallDelta{Index: i}, events); err != nil {
			t.Fatal(err)
		}
	}
	if err := toolDelta(ctx, state, models.ToolCallDelta{Index: maxStreamTools}, events); err == nil {
		t.Fatal("unbounded tool indices")
	}
	if err := finishTools(ctx, state, events); err == nil {
		t.Fatal("incomplete identity silently emitted")
	}
}
