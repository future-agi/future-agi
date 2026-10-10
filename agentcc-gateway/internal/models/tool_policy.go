package models

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// AllowedToolsChoice is OpenAI's restricted tool-choice form.
type AllowedToolsChoice struct {
	Mode  string
	Names []string
}

// ParseAllowedToolsChoice returns nil for other tool-choice forms. Invalid
// allowed_tools choices fail closed instead of making every tool available.
func ParseAllowedToolsChoice(raw json.RawMessage) (*AllowedToolsChoice, error) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '{' {
		return nil, nil
	}
	var header struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(raw, &header); err != nil {
		return nil, fmt.Errorf("tool_choice: %w", err)
	}
	if header.Type != "allowed_tools" {
		return nil, nil
	}
	var value struct {
		AllowedTools struct {
			Mode  string `json:"mode"`
			Tools []struct {
				Type     string `json:"type"`
				Name     string `json:"name"`
				Function struct {
					Name string `json:"name"`
				} `json:"function"`
			} `json:"tools"`
		} `json:"allowed_tools"`
	}
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, fmt.Errorf("tool_choice.allowed_tools: %w", err)
	}
	if value.AllowedTools.Mode != "auto" && value.AllowedTools.Mode != "required" {
		return nil, fmt.Errorf("tool_choice.allowed_tools.mode must be auto or required")
	}
	if len(value.AllowedTools.Tools) == 0 {
		return nil, fmt.Errorf("tool_choice.allowed_tools.tools must not be empty")
	}
	choice := &AllowedToolsChoice{Mode: value.AllowedTools.Mode}
	seen := make(map[string]bool)
	for _, tool := range value.AllowedTools.Tools {
		name := tool.Function.Name
		if name == "" {
			name = tool.Name
		}
		if tool.Type != "function" || name == "" || seen[name] {
			return nil, fmt.Errorf("tool_choice.allowed_tools.tools must name distinct function tools")
		}
		seen[name] = true
		choice.Names = append(choice.Names, name)
	}
	return choice, nil
}

// Filter selects only the declared functions named by an allowed_tools choice.
func (c *AllowedToolsChoice) Filter(tools []Tool) ([]Tool, error) {
	wanted := make(map[string]bool, len(c.Names))
	for _, name := range c.Names {
		wanted[name] = true
	}
	selected := make([]Tool, 0, len(c.Names))
	for _, tool := range tools {
		if tool.Type == "function" && wanted[tool.Function.Name] {
			selected = append(selected, tool)
			delete(wanted, tool.Function.Name)
		}
	}
	if len(wanted) != 0 {
		return nil, fmt.Errorf("tool_choice.allowed_tools names a tool not declared in tools")
	}
	return selected, nil
}
