# A notes plugin

## What it does

A plugin is a folder with an `omega.plugin.json` that declares what it adds. This one adds an MCP server with two tools, `add_note` and `list_notes`, and asks for one setting: the file where the notes are kept. Install it and any agent in your project can save and read notes.

## Run it with no key

```bash
omega hub run notes-plugin
```

It starts the plugin's server as a child process the way an MCP client does, with the notes file set from the plugin config, saves two notes, lists them and shows the file. No model runs.

It prints:

```text
plugin acme.notes 1.2.0 asks for: NOTES_FILE (required)
server tools: add_note, list_notes
add_note -> saved note 1
add_note -> saved note 2
list_notes -> ["Buy oat milk","Call the plumber on Monday"]
the notes file holds 2 notes
```

## How it works

- `plugin/omega.plugin.json` is the manifest: id, version, the `NOTES_FILE` setting and the MCP server it contributes.
- `plugin/notes-server.mjs` is the server: JSON-RPC over standard input and output, no dependencies.
- `main.ts` reads the manifest, starts the server with the config applied and calls both tools.
- `main.test.ts` is the keyless test CI runs.

## Make it yours

- Add a tool in `plugin/notes-server.mjs` and list it in the manifest's `tools`.
- Install it: `omega hub add notes-plugin --install` copies the folder and runs `omega plugins add` for you, disabled until you enable it.

After a change, run the test and record the demo again:

```bash
omega hub check notes-plugin --record
```
