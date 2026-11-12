package modeldb

import (
	"errors"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ComputerUseSupport describes exact gateway attestations, not library support.
type ComputerUseSupport uint8

const (
	ComputerUseUnknown ComputerUseSupport = iota
	ComputerUseSupported
	ComputerUseUnsupported
)

type ComputerUseAttestation struct {
	Binding models.NativeRouteBinding
	Support ComputerUseSupport
}
type ComputerUseDecision struct {
	binding models.NativeRouteBinding
	support ComputerUseSupport
}

func (d ComputerUseDecision) Binding() models.NativeRouteBinding { return d.binding }
func (d ComputerUseDecision) Support() ComputerUseSupport        { return d.support }

// ComputerUseCapabilities owns an immutable exact-key registry. Empty and nil
// registries never infer support from model names, generic flags or interfaces.
type ComputerUseCapabilities struct {
	revision string
	records  map[models.NativeRouteBinding]ComputerUseSupport
}

func NewComputerUseCapabilities(revision string, records []ComputerUseAttestation) (*ComputerUseCapabilities, error) {
	invalid := errors.New("invalid computer use capability snapshot")
	if revision == "" {
		return nil, invalid
	}
	s := &ComputerUseCapabilities{revision: revision, records: make(map[models.NativeRouteBinding]ComputerUseSupport, len(records))}
	for _, r := range records {
		if r.Support > ComputerUseUnsupported || !validComputerUseBinding(r.Binding) || r.Binding.CapabilityRevision != revision {
			return nil, invalid
		}
		if previous, ok := s.records[r.Binding]; ok && previous != r.Support {
			return nil, invalid
		}
		s.records[r.Binding] = r.Support
	}
	return s, nil
}
func (s *ComputerUseCapabilities) Revision() string {
	if s == nil {
		return ""
	}
	return s.revision
}
func (s *ComputerUseCapabilities) Lookup(binding models.NativeRouteBinding) ComputerUseDecision {
	d := ComputerUseDecision{binding: binding}
	if s != nil && s.revision != "" && binding.CapabilityRevision == s.revision {
		d.support = s.records[binding]
	}
	return d
}
func validComputerUseBinding(b models.NativeRouteBinding) bool {
	return b.CanonicalModel != "" && b.SentModel != "" && b.ModelRevision != "" && b.ProviderID != "" && b.ProviderRevision != "" && b.ProviderRouteID != "" && b.RouteRevision != "" && b.EndpointType != "" && b.EndpointID != "" && b.UpstreamProvider != "" && (b.CredentialType == models.CredentialBYOK || b.CredentialType == models.CredentialManaged) && b.Protocol >= models.NativeMessages && b.Protocol <= models.NativeGenAI && b.Action != "" && b.ToolProtocol != "" && b.VendorVersion != "" && b.CapabilityRevision != ""
}

// MatchesComputerUse compares restrictions only. It never grants support;
// callers must also look up the full binding in a trusted snapshot.
func MatchesComputerUse(b models.NativeRouteBinding, r models.ComputerUseRequirement) bool {
	return r.Required && r.Protocol != models.NativeProtocolUnknown && r.Action != "" && r.ToolProtocol != "" && r.VendorVersion != "" && b.Protocol == r.Protocol && b.Action == r.Action && b.Stream == r.Stream && b.ToolProtocol == r.ToolProtocol && b.VendorVersion == r.VendorVersion
}
