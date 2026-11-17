package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// replayGenAIStreamStart puts the observed startup chunks back in order for
// the usage tee and Gemini translator. The handler cancels ctx on every exit,
// so abandoning the downstream writer cannot strand the forwarding goroutine.
func replayGenAIStreamStart(ctx context.Context, chunks <-chan models.StreamChunk, prefixes []models.StreamChunk, first models.StreamChunk) <-chan models.StreamChunk {
	started := make(chan models.StreamChunk)
	go func() {
		defer close(started)
		for _, chunk := range append(prefixes, first) {
			select {
			case started <- chunk:
			case <-ctx.Done():
				return
			}
		}
		for {
			select {
			case chunk, ok := <-chunks:
				if !ok {
					return
				}
				select {
				case started <- chunk:
				case <-ctx.Done():
					return
				}
			case <-ctx.Done():
				return
			}
		}
	}()
	return started
}
