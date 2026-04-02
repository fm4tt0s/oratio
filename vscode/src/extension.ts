import * as vscode from "vscode";
import * as http from "http";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Issue {
  severity: "FAIL" | "WARN";
  rule: string;
  found: string;
  fix: string;
  page?: number;
}

interface ReviewResponse {
  verdict: "PASS" | "PASS WITH SUGGESTIONS" | "NEEDS REVISION";
  issues: Issue[];
  summary: string;
}

// ── State ─────────────────────────────────────────────────────────────────────

let diagnosticCollection: vscode.DiagnosticCollection;
let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;

// ── Activation ────────────────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection("style-guide");
  context.subscriptions.push(diagnosticCollection);

  outputChannel = vscode.window.createOutputChannel("Oratio");
  context.subscriptions.push(outputChannel);

  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = "styleGuide.checkDocument";
  statusBarItem.text = "$(book) Oratio";
  statusBarItem.tooltip = "Oratio — click to check document against the style guide";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // ── Commands ───────────────────────────────────────────────────────────────

  // Check selected text (right-click → Check Selection)
  context.subscriptions.push(
    vscode.commands.registerCommand("styleGuide.checkSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Style Guide: no active editor.");
        return;
      }
      const selection = editor.selection;
      const text = editor.document.getText(selection).trim();
      if (!text) {
        vscode.window.showWarningMessage("Style Guide: select some text first, then right-click → Check Selection.");
        return;
      }
      await runCheck(text, editor.document, selection);
    })
  );

  // Check full document (status bar click or command palette)
  context.subscriptions.push(
    vscode.commands.registerCommand("styleGuide.checkDocument", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Style Guide: no active editor.");
        return;
      }
      const text = editor.document.getText().trim();
      if (!text) {
        vscode.window.showWarningMessage("Style Guide: document is empty.");
        return;
      }
      await runCheck(text, editor.document, null);
    })
  );

  // Clear all diagnostics
  context.subscriptions.push(
    vscode.commands.registerCommand("styleGuide.clearDiagnostics", () => {
      diagnosticCollection.clear();
      outputChannel.clear();
      setStatus("idle");
    })
  );

  // Ask a question about the style guide
  context.subscriptions.push(
    vscode.commands.registerCommand("styleGuide.askQuestion", async () => {
      const question = await vscode.window.showInputBox({
        prompt: "Ask a question about the style guide",
        placeHolder: "e.g. Can I use contractions? How should I format IP addresses?",
      });
      if (!question) { return; }
      await runAsk(question);
    })
  );
}

export function deactivate() {
  diagnosticCollection.clear();
}

// ── Config ────────────────────────────────────────────────────────────────────

function getConfig() {
  const cfg = vscode.workspace.getConfiguration("styleGuide");
  return {
    serverUrl: cfg.get<string>("serverUrl", "http://localhost:5123"),
    timeoutMs: cfg.get<number>("timeoutMs", 180000),
  };
}

// ── Status bar ────────────────────────────────────────────────────────────────

type StatusKind = "idle" | "checking" | "pass" | "suggestions" | "fail" | "error" | "offline";

function setStatus(kind: StatusKind, detail?: string) {
  const map: Record<StatusKind, { icon: string; text: string; color?: string }> = {
    idle:        { icon: "book",       text: "Style Guide"                     },
    checking:    { icon: "sync~spin",  text: "Checking..."                     },
    pass:        { icon: "check",      text: "Style OK",     color: "#4CAF50"  },
    suggestions: { icon: "info",       text: "Suggestions",  color: "#FF9800"  },
    fail:        { icon: "warning",    text: "Style Issues", color: "#F44336"  },
    error:       { icon: "alert",      text: "Check Error",  color: "#F44336"  },
    offline:     { icon: "plug",       text: "Server Offline"                  },
  };
  const s = map[kind];
  statusBarItem.text = `$(${s.icon}) ${detail ?? s.text}`;
  statusBarItem.color = s.color;
  statusBarItem.tooltip = "Oratio\nClick to check full document";
}

// ── HTTP ──────────────────────────────────────────────────────────────────────

function postToServer(path: string, payload: object, timeoutMs: number): Promise<object> {
  return new Promise((resolve, reject) => {
    const { serverUrl } = getConfig();
    const body = JSON.stringify(payload);
    const url = new URL(path, serverUrl);

    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: Number(url.port) || 80,
      path: url.pathname,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };

    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try { resolve(JSON.parse(data)); }
        catch { reject(new Error(`Invalid JSON: ${data.slice(0, 300)}`)); }
      });
    });

    req.on("error", reject);
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      const secs = Math.round(timeoutMs / 1000);
      reject(new Error(
        `Timed out after ${secs}s. If using Ollama, try a smaller model or increase styleGuide.timeoutMs.`
      ));
    });

    req.write(body);
    req.end();
  });
}

// ── Check logic ───────────────────────────────────────────────────────────────

async function runCheck(
  text: string,
  doc: vscode.TextDocument,
  selection: vscode.Selection | null
) {
  const { serverUrl, timeoutMs } = getConfig();
  const scope = selection ? "selection" : "document";
  setStatus("checking", `Checking ${scope}...`);
  outputChannel.clear();
  outputChannel.show(true);
  outputChannel.appendLine(`Style Guide check — ${scope}`);
  outputChannel.appendLine(`Server: ${serverUrl}`);
  outputChannel.appendLine("-".repeat(60));

  let response: ReviewResponse;
  try {
    response = await postToServer("/check", { text }, timeoutMs) as ReviewResponse;
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    const isOffline = msg.includes("ECONNREFUSED") || msg.includes("connect");
    setStatus(isOffline ? "offline" : "error");
    outputChannel.appendLine(isOffline
      ? "ERROR: Server is offline.\nRun `make server-http` in your project directory."
      : `ERROR: ${msg}`
    );
    vscode.window.showErrorMessage(
      isOffline
        ? "Style Guide server is offline — run `make server-http`"
        : `Style Guide check failed: ${msg}`
    );
    return;
  }

  // ── Output panel ──────────────────────────────────────────────────────────
  outputChannel.appendLine(`VERDICT: ${response.verdict}`);
  outputChannel.appendLine("");

  if (response.issues.length === 0) {
    outputChannel.appendLine("No violations found.");
  } else {
    outputChannel.appendLine(`${response.issues.length} issue(s) found:`);
    outputChannel.appendLine("");
    for (const issue of response.issues) {
      const icon = issue.severity === "FAIL" ? "FAIL" : "WARN";
      const page = issue.page ? ` (p.${issue.page})` : "";
      outputChannel.appendLine(`[${icon}] ${issue.rule}${page}`);
      if (issue.found) { outputChannel.appendLine(`   Found : "${issue.found}"`); }
      if (issue.fix)   { outputChannel.appendLine(`   Fix   : "${issue.fix}"`);   }
      outputChannel.appendLine("");
    }
  }

  if (response.summary) {
    outputChannel.appendLine("-".repeat(60));
    outputChannel.appendLine("Summary: " + response.summary);
  }

  // ── Diagnostics (squiggles) ───────────────────────────────────────────────
  const docText = doc.getText();

  // Clear only the relevant scope before re-adding
  if (!selection) {
    diagnosticCollection.delete(doc.uri);
  }

  const newDiags: vscode.Diagnostic[] = [];

  for (const issue of response.issues) {
    const found = issue.found.replace(/^["']|["']$/g, "").trim();
    if (!found) { continue; }

    const searchText = selection ? doc.getText(selection) : docText;
    const baseOffset = selection ? doc.offsetAt(selection.start) : 0;
    const idx = searchText.toLowerCase().indexOf(found.toLowerCase());

    let range: vscode.Range;
    if (idx !== -1) {
      range = new vscode.Range(
        doc.positionAt(baseOffset + idx),
        doc.positionAt(baseOffset + idx + found.length)
      );
    } else {
      range = selection
        ? new vscode.Range(selection.start, selection.end)
        : doc.lineAt(0).range;
    }

    const sev = issue.severity === "FAIL"
      ? vscode.DiagnosticSeverity.Error
      : vscode.DiagnosticSeverity.Warning;

    const pageNote = issue.page ? ` (p.${issue.page})` : "";
    const diag = new vscode.Diagnostic(
      range,
      `[Style] ${issue.rule}${pageNote} — Fix: ${issue.fix}`,
      sev
    );
    diag.source = "Style Guide";
    diag.code = issue.rule;
    newDiags.push(diag);
  }

  const existing = selection ? (diagnosticCollection.get(doc.uri) ?? []) : [];
  diagnosticCollection.set(doc.uri, [...existing, ...newDiags]);

  // ── Status bar ────────────────────────────────────────────────────────────
  const fails = response.issues.filter((i) => i.severity === "FAIL").length;
  const warns = response.issues.filter((i) => i.severity === "WARN").length;

  if (response.verdict === "PASS") {
    setStatus("pass", "Style OK");
  } else if (response.verdict === "PASS WITH SUGGESTIONS") {
    setStatus("suggestions", `${warns} suggestion${warns !== 1 ? "s" : ""}`);
  } else {
    setStatus("fail", `${fails} fail, ${warns} warn`);
  }
}

// ── Ask logic ─────────────────────────────────────────────────────────────────

async function runAsk(question: string) {
  const { serverUrl, timeoutMs } = getConfig();
  setStatus("checking", "Asking...");
  outputChannel.clear();
  outputChannel.show(true);
  outputChannel.appendLine("Style Guide — Q&A");
  outputChannel.appendLine(`Q: ${question}`);
  outputChannel.appendLine("-".repeat(60));

  try {
    const resp = await postToServer("/ask", { question }, timeoutMs) as { answer: string };
    outputChannel.appendLine(resp.answer ?? "No answer returned.");
    setStatus("idle");
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    const isOffline = msg.includes("ECONNREFUSED") || msg.includes("connect");
    setStatus(isOffline ? "offline" : "error");
    outputChannel.appendLine(isOffline
      ? "ERROR: Server offline. Run `make server-http`."
      : `ERROR: ${msg}`
    );
  }
}
