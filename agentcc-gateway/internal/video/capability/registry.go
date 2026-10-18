package capability

import (
	"encoding/json"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"sort"
	"strings"
)

// Registry is immutable. Returned descriptors are copies, including maps/slices.
type Registry struct{ services map[string]video.Capabilities }

func NewRegistry() *Registry {
	return &Registry{services: map[string]video.Capabilities{"byteplus": BytePlus()}}
}
func (r *Registry) Service(service string) (video.Capabilities, bool) {
	c, ok := r.services[service]
	if !ok {
		return c, false
	}
	b, _ := json.Marshal(c)
	var out video.Capabilities
	_ = json.Unmarshal(b, &out)
	return out, true
}
func (r *Registry) Model(service, id string) (video.ModelCapability, error) {
	c, ok := r.Service(service)
	if ok {
		for _, m := range c.Models {
			if m.ModelID == id {
				return m, nil
			}
		}
	}
	reason := "unknown"
	if retired(id) {
		reason = "retired"
	}
	return video.ModelCapability{}, invalid("unsupported_model", "model", reason)
}
func (r *Registry) Models(service string) []string {
	c, _ := r.Service(service)
	out := make([]string, 0, len(c.Models))
	for _, m := range c.Models {
		out = append(out, m.ModelID)
	}
	sort.Strings(out)
	return out
}
func retired(id string) bool {
	if i := strings.LastIndexByte(id, '/'); i >= 0 {
		id = id[i+1:]
	}
	switch id {
	case "sora", "sora-2", "sora-2-pro":
		return true
	}
	return false
}
func (r *Registry) resolve(req Request, region string) (Resolved, error) {
	if retired(req.Model) {
		return Resolved{}, invalid("unsupported_model", "model", "retired")
	}
	service, id, ok := strings.Cut(req.Model, "/")
	if !ok || id == "" {
		return Resolved{}, invalid("unsupported_model", "model", "service-qualified model required")
	}
	m, err := r.Model(service, id)
	if err != nil {
		return Resolved{}, err
	}
	c, _ := r.Service(service)
	if region == "" && len(c.Regions) > 0 {
		region = c.Regions[0]
	}
	if !contains(c.Regions, region) {
		return Resolved{}, invalid("unsupported_model", "model", "unsupported region")
	}
	op := video.TextToVideo
	first, last, refs := false, false, false
	for _, in := range req.Inputs {
		switch in.Role {
		case video.FirstFrame:
			first = true
		case video.LastFrame:
			last = true
		default:
			refs = true
		}
	}
	if last && !first {
		return Resolved{}, invalid("unsupported_combination", "inputs", "last_frame requires first_frame")
	}
	if (first || last) && refs {
		return Resolved{}, invalid("unsupported_combination", "inputs", "frame and reference roles cannot be mixed")
	}
	if refs {
		op = video.Reference
	} else if last {
		op = video.ImageFirstLast
	} else if first {
		op = video.ImageFirstFrame
	}
	if _, ok := m.Operations[op]; !ok {
		return Resolved{}, invalid("unsupported_combination", "inputs", "operation unsupported")
	}
	version := id[strings.LastIndexByte(id, '-')+1:]
	return Resolved{Request: req, Service: service, ModelID: id, Version: version, Region: region, Operation: op, CapabilityRevision: c.Revision, Capability: m}, nil
}
func contains[T comparable](values []T, v T) bool {
	for _, x := range values {
		if x == v {
			return true
		}
	}
	return false
}
