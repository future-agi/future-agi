package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// Price predictable mutations in the FIRST work lease. Growing after decoding
// can otherwise queue behind another request that needs the bytes this request
// already holds. The final pdata check remains a safety fence, not the usual
// allocation path for auth/header fields.
type resourceStampBudget struct {
	values [6]valueInfo
	routed bool
}

const (
	stampName       = 1
	stampProject    = 2
	stampOrg        = 3
	stampSimulation = 4
	stampType       = 5
)

var stampKeys = [6]string{"", "project_name", "fi.project_id", "fi.org_id", "fi.simulation.origin", "project_type"}

func resourceStampKey(key []byte) int {
	switch string(key) {
	case "project_name":
		return stampName
	case "fi.project_id":
		return stampProject
	case "fi.org_id":
		return stampOrg
	case "fi.simulation.origin":
		return stampSimulation
	case "project_type":
		return stampType
	}
	return 0
}
func stampString(value string) valueInfo {
	p, h := jsonEscapedLen([]byte(value))
	return valueInfo{json: p + 2, htmlJSON: h + 2, raw: int64(len(value)), scalar: true, isString: true, lastStringJSON: p + 2}
}
func resourceStamps(ctx context.Context, headers auth.ProjectNameValues) resourceStampBudget {
	var out resourceStampBudget
	// Invalid headers still get their existing project error during preparation,
	// and remain ignored when every resource has its own usable project name.
	if name, err := auth.ProjectNameHeaderValue(headers); err == nil && name != "" {
		out.values[stampName] = stampString(name)
	}
	if result := auth.FromContext(ctx); result != nil {
		out.values[stampOrg] = stampString(result.OrgID)
		// A newly resolved/created Observe project may add this hint. Reserve
		// it before resolving names; the final scan still checks the exact shape.
		out.values[stampType] = stampString(auth.ProjectTypeObserve)
		// PostgreSQL tracer_project.id is a UUID, serialized as 36 ASCII bytes.
		// A nonstandard resolver or a later generation is still checked exactly
		// before conversion; it cannot bypass the final reservation/size fence.
		out.values[stampProject] = valueInfo{json: 38, htmlJSON: 38, raw: 36, scalar: true, isString: true, lastStringJSON: 38}
		if result.KeyType == auth.KeyTypeIngest {
			out.routed = true
			// Routing replaces project_name. Stamp validates <=255 Unicode scalars;
			// each is <=4 UTF-8 bytes or <=6 JSON bytes (including U+2028/U+2029).
			out.values[stampName] = valueInfo{json: 255*6 + 2, htmlJSON: 255*6 + 2, raw: 255 * 4, scalar: true, isString: true, lastStringJSON: 255*6 + 2}
			out.values[stampSimulation] = stampString("simulation")
		}
	}
	return out
}

func (s *otlpShape) reserveResourceStamps(rs *rsAccum) {
	for k := stampName; k <= stampType; k++ {
		next := s.stamps.values[k]
		if next.json == 0 {
			continue
		}
		before, seen := rs.stampValues[k], rs.stampSeen[k]
		if k == stampName && !s.stamps.routed && seen && before.lastStringJSON > 2 {
			continue
		}
		if k == stampType && seen && (!before.isString || before.lastStringJSON > 2) {
			continue
		}
		if k == stampSimulation && seen {
			continue
		}
		keyBytes := int64(len(stampKeys[k]))
		oldJSON, oldRaw := int64(0), int64(0)
		if seen {
			oldJSON = 2 * (keyBytes + 4 + before.asResourceColumn())
			oldRaw = before.raw
		} else {
			s.keyValues++
			s.stringBytes += keyBytes
			s.strings++
		}
		// The original scan already paid for overwritten/duplicate fields. Add
		// only a positive delta for the first field that pdata.PutStr replaces.
		rs.resourceJSON += max(0, 2*(keyBytes+4+next.json)-oldJSON)
		s.stringBytes += max(0, next.raw-oldRaw)
		if !seen || !before.isString || next.raw > oldRaw {
			s.strings++
		}
	}
}
