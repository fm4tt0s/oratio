"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const http = require("http");
// ── State ─────────────────────────────────────────────────────────────────────
let diagnosticCollection;
let statusBarItem;
let debounceTimer;
// ── Activation ────────────────────────────────────────────────────────────────
function activate(context) {
    diagnosticCollection = vscode.languages.createDiagnosticCollection("style-guide");
    context.subscriptions.push(diagnosticCollection);
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = "styleGuide.checkNow";
    statusBarItem.text = "$(book) Style";
    statusBarItem.tooltip = "Oratio Style Guide Checker — click to check now";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    // Check on change (debounced)
    context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((event) => {
        if (!isEnabledDocument(event.document)) {
            return;
        }
        scheduleCheck(event.document);
    }));
    // Always check on save
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument((doc) => {
        if (!isEnabledDocument(doc)) {
            return;
        }
        const cfg = getConfig();
        if (cfg.checkOnSave) {
            if (debounceTimer) {
                clearTimeout(debounceTimer);
            }
            runCheck(doc);
        }
    }));
    // Check when switching to an already-open file
    context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor && isEnabledDocument(editor.document)) {
            scheduleCheck(editor.document);
        }
    }));
    // Commands
    context.subscriptions.push(vscode.commands.registerCommand("styleGuide.checkNow", () => {
        const editor = vscode.window.activeTextEditor;
        if (editor && isEnabledDocument(editor.document)) {
            runCheck(editor.document);
        }
        else {
            vscode.window.showInformationMessage("Style Guide: open a Markdown or plain text file to check.");
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand("styleGuide.clearDiagnostics", () => {
        diagnosticCollection.clear();
        setStatus("idle");
    }));
    // Check active document on startup
    if (vscode.window.activeTextEditor) {
        const doc = vscode.window.activeTextEditor.document;
        if (isEnabledDocument(doc)) {
            scheduleCheck(doc);
        }
    }
}
function deactivate() {
    diagnosticCollection.clear();
}
// ── Helpers ───────────────────────────────────────────────────────────────────
function getConfig() {
    const cfg = vscode.workspace.getConfiguration("styleGuide");
    return {
        serverUrl: cfg.get("serverUrl", "http://localhost:5123"),
        debounceMs: cfg.get("debounceMs", 2000),
        checkOnSave: cfg.get("checkOnSave", true),
        enabledLanguages: cfg.get("enabledLanguages", ["markdown", "plaintext"]),
        timeoutMs: cfg.get("timeoutMs", 180000),
    };
}
function isEnabledDocument(doc) {
    const { enabledLanguages } = getConfig();
    return enabledLanguages.includes(doc.languageId);
}
function scheduleCheck(doc) {
    const { debounceMs } = getConfig();
    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => runCheck(doc), debounceMs);
}
function setStatus(kind, detail) {
    const map = {
        idle: { icon: "book", text: "Style" },
        checking: { icon: "sync~spin", text: "Checking…" },
        pass: { icon: "check", text: "Style OK", color: "#4CAF50" },
        suggestions: { icon: "info", text: "Suggestions", color: "#FF9800" },
        fail: { icon: "warning", text: "Style Issues", color: "#F44336" },
        error: { icon: "alert", text: "Check Error", color: "#F44336" },
        offline: { icon: "plug", text: "Server Offline" },
    };
    const s = map[kind];
    statusBarItem.text = `$(${s.icon}) ${detail ?? s.text}`;
    statusBarItem.color = s.color;
    statusBarItem.tooltip = `Oratio Style Guide Checker — ${detail ?? s.text}\nClick to check now`;
}
// ── HTTP call to RAG server ───────────────────────────────────────────────────
function callServer(text, serverUrl, timeoutMs = 180000) {
    return new Promise((resolve, reject) => {
        const body = JSON.stringify({ text });
        const url = new URL("/check", serverUrl);
        const options = {
            hostname: url.hostname,
            port: url.port || 80,
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
                try {
                    resolve(JSON.parse(data));
                }
                catch {
                    reject(new Error(`Invalid JSON from server: ${data.slice(0, 200)}`));
                }
            });
        });
        req.on("error", reject);
        req.setTimeout(timeoutMs, () => {
            req.destroy();
            const secs = Math.round(timeoutMs / 1000);
            reject(new Error(`Request timed out after ${secs}s — if using Ollama, try a smaller model or increase the styleGuide.timeoutMs setting.`));
        });
        req.write(body);
        req.end();
    });
}
// ── Core check logic ──────────────────────────────────────────────────────────
async function runCheck(doc) {
    const text = doc.getText().trim();
    if (!text) {
        diagnosticCollection.delete(doc.uri);
        setStatus("idle");
        return;
    }
    const { serverUrl } = getConfig();
    const isLikelyOllama = serverUrl.includes("localhost") || serverUrl.includes("127.0.0.1");
    setStatus("checking", isLikelyOllama ? "Checking (local model)…" : "Checking…");
    let response;
    try {
        const { timeoutMs } = getConfig();
        response = await callServer(text, serverUrl, timeoutMs);
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const isOffline = msg.includes("ECONNREFUSED") || msg.includes("connect");
        setStatus(isOffline ? "offline" : "error");
        if (isOffline) {
            vscode.window.setStatusBarMessage("$(plug) Style Guide server offline — run `make server` in your project", 8000);
        }
        else {
            vscode.window.showWarningMessage(`Oratio Style Guide check failed: ${msg}`);
        }
        return;
    }
    // ── Map issues to VS Code diagnostics ────────────────────────────────────────
    const diagnostics = [];
    for (const issue of response.issues) {
        // Find the offending text in the document
        const found = issue.found.replace(/^["']|["']$/g, ""); // strip surrounding quotes
        const docText = doc.getText();
        const idx = docText.toLowerCase().indexOf(found.toLowerCase());
        let range;
        if (idx !== -1) {
            range = new vscode.Range(doc.positionAt(idx), doc.positionAt(idx + found.length));
        }
        else {
            // Fallback: mark the first line
            range = doc.lineAt(0).range;
        }
        const severity = issue.severity === "FAIL"
            ? vscode.DiagnosticSeverity.Error
            : vscode.DiagnosticSeverity.Warning;
        const pageNote = issue.page ? ` (p.${issue.page})` : "";
        const message = `[Style] ${issue.rule}${pageNote}\n→ Fix: ${issue.fix}`;
        const diag = new vscode.Diagnostic(range, message, severity);
        diag.source = "Style Guide";
        diag.code = issue.rule;
        diagnostics.push(diag);
    }
    diagnosticCollection.set(doc.uri, diagnostics);
    // ── Update status bar ─────────────────────────────────────────────────────
    const failCount = response.issues.filter((i) => i.severity === "FAIL").length;
    const warnCount = response.issues.filter((i) => i.severity === "WARN").length;
    if (response.verdict === "PASS") {
        setStatus("pass");
    }
    else if (response.verdict === "PASS WITH SUGGESTIONS") {
        setStatus("suggestions", `${warnCount} suggestion${warnCount !== 1 ? "s" : ""}`);
    }
    else {
        setStatus("fail", `${failCount} fail, ${warnCount} warn`);
    }
}
//# sourceMappingURL=extension.js.map