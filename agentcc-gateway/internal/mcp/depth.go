package mcp

import (
	"fmt"
	"net/http"
	"strconv"
)

const (
	// HeaderAgentDepth is the header used to track agent recursion depth.
	HeaderAgentDepth = "X-Agentcc-Agent-Depth"

	// DefaultMaxAgentDepth is the default maximum recursion depth.
	DefaultMaxAgentDepth = 10
)

// DepthTracker validates and manages agent recursion depth.
type DepthTracker struct {
	maxDepth int
}

// NewDepthTracker creates a tracker with the given maximum depth.
func NewDepthTracker(maxDepth int) *DepthTracker {
	if maxDepth <= 0 {
		maxDepth = DefaultMaxAgentDepth
	}
	return &DepthTracker{maxDepth: maxDepth}
}

// ExtractDepth reads the current agent depth from the request header.
func (dt *DepthTracker) ExtractDepth(r *http.Request) int {
	val := r.Header.Get(HeaderAgentDepth)
	if val == "" {
		return 0
	}
	depth, err := strconv.Atoi(val)
	if err != nil || depth < 0 {
		return 0
	}
	return depth
}

// CheckDepth returns an error if the depth exceeds the maximum.
func (dt *DepthTracker) CheckDepth(depth int) error {
	if depth >= dt.maxDepth {
		return fmt.Errorf(
			"maximum agent depth (%d) exceeded — this may indicate an infinite tool-call loop",
			dt.maxDepth,
		)
	}
	return nil
}

// IncrementHeader returns the depth+1 value as a string, suitable for setting
// on outgoing requests that may trigger further LLM calls.
func (dt *DepthTracker) IncrementHeader(depth int) string {
	return strconv.Itoa(depth + 1)
}

// MaxDepth returns the configured maximum depth.
func (dt *DepthTracker) MaxDepth() int {
	return dt.maxDepth
}
