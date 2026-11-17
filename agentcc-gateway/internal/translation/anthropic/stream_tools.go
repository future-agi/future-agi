package anthropic

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const maxPendingToolBytes = 64 * 1024
const maxStreamTools = 128

type streamTool struct {
	id, name, arguments string
	block               int
	started, stopped    bool
}

// Tool index, not the most recently emitted block, owns each argument delta.
// Hold only incomplete protocol state until id/name/arguments are available.
func toolDelta(ctx context.Context, state *streamState, tc models.ToolCallDelta, events chan<- []byte) error {
	if tc.Index < 0 {
		return fmt.Errorf("negative tool index")
	}
	if state.tools == nil {
		state.tools = make(map[int]*streamTool)
	}
	tool := state.tools[tc.Index]
	if tool == nil {
		if len(state.tools) >= maxStreamTools {
			return fmt.Errorf("too many streaming tools")
		}
		tool = &streamTool{}
		state.tools[tc.Index] = tool
		state.toolOrder = append(state.toolOrder, tc.Index)
	}
	if tool.stopped {
		return fmt.Errorf("tool delta after stop")
	}
	name, args := "", ""
	if tc.Function != nil {
		name, args = tc.Function.Name, tc.Function.Arguments
	}
	if tool.started {
		if (tc.ID != "" && tc.ID != tool.id) || name != "" {
			return fmt.Errorf("tool identity changed after start")
		}
		emitToolArguments(ctx, events, tool.block, args)
		return nil
	}
	added := len(tc.ID) + len(name) + len(args)
	if added > maxPendingToolBytes-state.toolBytes {
		return fmt.Errorf("incomplete tool state exceeds stream limit")
	}
	state.toolBytes += added
	tool.id += tc.ID
	tool.name += name
	tool.arguments += args
	if tool.id != "" && tool.name != "" && tool.arguments != "" {
		startTool(ctx, state, tool, events)
	}
	return nil
}

func startTool(ctx context.Context, state *streamState, tool *streamTool, events chan<- []byte) {
	if state.currentBlockType != "" {
		emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{Type: "content_block_stop", Index: state.currentBlockIdx}))
		state.currentBlockType = ""
	}
	state.currentBlockIdx++
	tool.block, tool.started = state.currentBlockIdx, true
	name := tool.name
	if original, ok := state.toolNameMapping[name]; ok {
		name = original
	}
	emit(ctx, events, sseFrame("content_block_start", ContentBlockStartEvent{
		Type: "content_block_start", Index: tool.block,
		ContentBlock: ContentBlock{Type: "tool_use", ID: tool.id, Name: name, Input: json.RawMessage("{}")},
	}))
	emitToolArguments(ctx, events, tool.block, tool.arguments)
	state.toolBytes -= len(tool.arguments)
	tool.arguments = ""
}

func emitToolArguments(ctx context.Context, events chan<- []byte, block int, args string) {
	if args == "" {
		return
	}
	emit(ctx, events, sseFrame("content_block_delta", ContentBlockDeltaEvent{
		Type: "content_block_delta", Index: block,
		Delta: ContentDelta{Type: "input_json_delta", PartialJSON: args},
	}))
}

func finishTools(ctx context.Context, state *streamState, events chan<- []byte) error {
	for _, index := range state.toolOrder {
		tool := state.tools[index]
		if tool.stopped {
			continue
		}
		if !tool.started {
			if tool.id == "" || tool.name == "" {
				return fmt.Errorf("stream ended with incomplete tool identity")
			}
			startTool(ctx, state, tool, events)
		}
		emit(ctx, events, sseFrame("content_block_stop", ContentBlockStopEvent{Type: "content_block_stop", Index: tool.block}))
		tool.stopped = true
	}
	return nil
}
