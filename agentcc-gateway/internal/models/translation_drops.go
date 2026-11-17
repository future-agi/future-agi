package models

import "strings"

// SetResponsesRouteDrops records route as the serving attempt's Responses-route
// notes and rebuilds the request's translation_drops metadata from the
// translator's notes followed by these, deduplicated and without empty names.
// It returns the joined notes, or "" when there are none. Notes are rebuilt
// from these trusted lists only, never from headers or caller metadata.
func (rc *RequestContext) SetResponsesRouteDrops(route []string) string {
	var routeNotes []string
	seenRoute := make(map[string]bool)
	for _, drop := range route {
		if drop != "" && !seenRoute[drop] {
			seenRoute[drop] = true
			routeNotes = append(routeNotes, drop)
		}
	}
	rc.ResponsesRouteDrops = routeNotes

	var drops []string
	seen := make(map[string]bool)
	for _, group := range [][]string{rc.TranslationDrops, routeNotes} {
		for _, drop := range group {
			if drop != "" && !seen[drop] {
				seen[drop] = true
				drops = append(drops, drop)
			}
		}
	}
	if len(drops) == 0 {
		delete(rc.Metadata, "translation_drops")
		return ""
	}
	if rc.Metadata == nil {
		rc.Metadata = make(map[string]string)
	}
	joined := strings.Join(drops, ",")
	rc.Metadata["translation_drops"] = joined
	return joined
}
