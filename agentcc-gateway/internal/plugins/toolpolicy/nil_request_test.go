package toolpolicy

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Embeddings, images, audio, ocr, rerank, search and responses run the pipeline
// without setting rc.Request. Dereferencing it panicked into a 500 on all of
// them whenever tool policy was enabled.
func TestProcessRequestSurvivesNilRequest(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true}, nil)
	rc := &models.RequestContext{Metadata: map[string]string{}}

	result := p.ProcessRequest(context.Background(), rc)

	if result.Error != nil {
		t.Errorf("Error = %v, want none for a request that cannot carry tools", result.Error)
	}
}
