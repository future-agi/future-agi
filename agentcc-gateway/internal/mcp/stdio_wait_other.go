//go:build !linux

package mcp

func reapStdioGroup(int) error { return nil }
