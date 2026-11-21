export const SKILL_NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
export const LABEL_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
export const TAG_PATTERN = /^[a-z0-9][a-z0-9._-]{0,39}$/;
export const SYSTEM_LABELS = ['production', 'staging'];
export const LIMITS = Object.freeze({
  MAX_NAME: 64,
  MAX_DESCRIPTION: 1024,
  MAX_TAGS: 20,
  MAX_LABELS: 20,
  MAX_FILES: 200,
  MAX_PATH: 255,
  MAX_PATH_SEGMENTS: 8,
  MAX_FILE_BYTES: 512 * 1024,
  MAX_VERSION_BYTES: 2 * 1024 * 1024,
  MAX_FRONTMATTER_BYTES: 16 * 1024,
  MAX_NOTE: 2000,
  MAX_ZIP_BYTES: 10 * 1024 * 1024,
  MAX_ZIP_ENTRIES: 1000,
  MAX_UNZIPPED_BYTES: 20 * 1024 * 1024,
  MAX_ZIP_RATIO: 100,
  MAX_IMPORT_SKILLS: 50,
  MAX_LOOSE_FILES: 200,
  MAX_DOWNLOAD_SKILLS: 50,
  DEFAULT_PAGE_SIZE: 25,
  MAX_PAGE_SIZE: 100,
  DEFAULT_CHUNK_BYTES: 32768,
  MAX_CHUNK_BYTES: 49152,
  MCP_CHUNK_BYTES: 24000,
});
export const INSTALL_CLIENTS = [
  { id: 'claude-code', label: 'Claude Code', projectDir: '.claude/skills', personalDir: '~/.claude/skills' },
  { id: 'codex', label: 'Codex', projectDir: '.agents/skills', personalDir: '~/.agents/skills' },
  { id: 'cursor', label: 'Cursor', projectDir: '.cursor/skills', personalDir: '~/.cursor/skills' },
  { id: 'gemini', label: 'Gemini CLI', projectDir: '.gemini/skills', personalDir: '~/.gemini/skills' },
  { id: 'vscode', label: 'VS Code', projectDir: '.github/skills', personalDir: '~/.copilot/skills' },
];
