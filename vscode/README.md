# Oratio

Check text against your corporate Writing Style Guide PDF — right inside VS Code.

**Oratio** is a style guide enforcement tool powered by RAG and your choice of LLM backend.

Select any text, right-click, and get instant feedback with rule violations highlighted
directly in the editor and a full report in the Output panel.

---

## Usage

**Right-click menu:**

| Action | When available |
|---|---|
| **Style Guide: Check Selection** | Text is selected |
| **Style Guide: Check Full Document** | Always |
| **Style Guide: Ask a Question** | Always |

Results appear in the **Style Guide** Output panel (opens automatically) and as
squiggly underlines on the offending text. The status bar shows the verdict at a glance.

**Command palette** (`Cmd+Shift+P` / `Ctrl+Shift+P`): search for `Style Guide`.

---

## Requirements

This extension requires the **Style Guide HTTP server** to be running locally:

```bash
# In your mcp-styleguide project:
make server-http
```

The server must stay running while you use the extension.

---

## Settings

| Setting | Default | Description |
|---|---|---|
| `styleGuide.serverUrl` | `http://localhost:5123` | HTTP server URL |
| `styleGuide.timeoutMs` | `180000` | Request timeout in ms — increase for slow local models |

---

## More information

See the full setup guide in the [mcp-styleguide repository](https://github.com/your-repo).
