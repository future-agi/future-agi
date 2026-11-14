package capability

import (
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

type Request = video.Request
type Input = video.Input

// Resolved pins model, region, operation and registry revision at acceptance.
type Resolved struct {
	Request            Request               `json:"request"`
	Service            string                `json:"service"`
	ModelID            string                `json:"model_id"`
	Version            string                `json:"version"`
	Region             string                `json:"region"`
	Operation          video.Operation       `json:"operation"`
	CapabilityRevision string                `json:"capability_revision"`
	Capability         video.ModelCapability `json:"-"`
}
type ValidationError struct{ Code, Param, Reason string }

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s: %s (%s)", e.Code, e.Param, e.Reason)
}
func (e *ValidationError) APIError() *models.APIError {
	return &models.APIError{Status: 400, Type: models.ErrTypeInvalidRequest, Code: e.Code, Param: &e.Param, Message: e.Reason}
}
func invalid(code, param, reason string) error {
	return &ValidationError{Code: code, Param: param, Reason: reason}
}
