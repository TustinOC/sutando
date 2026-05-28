/**
 * HTTP server for the Sutando desktop + remote browser conversation page.
 *
 * Endpoints:
 *   GET  /, /v2[/*]         — Vite-built React bundle (client/dist). The two
 *                             roots serve the same SPA so existing bookmarks
 *                             pointing at /v2 keep working. A fresh checkout
 *                             without `pnpm build:client` returns 503 with a
 *                             pointer to the build command.
 *   GET  /sse               — Server-Sent Events: `agent-state`, `toggle-voice`,
 *                             `toggle-mute` — consumed by the page.
 *   GET  /sse-status        — JSON snapshot { muted, voiceConnected, state, label, clients }.
 *   GET  /voice-mode        — JSON { mode: 'active' | 'meeting' }.
 *   GET  /presenter         — JSON { active, expiresAt } from state/presenter-mode.sentinel.
 *   GET  /mute-state        — Read/write the browser + tool agent-state tracks.
 *   GET  /toggle, /mute     — Broadcast SSE events (driven by menu-bar hotkeys).
 *   POST /note-viewing      — Write /tmp/sutando-note-viewing.json (consumed by the agent).
 *   *    /vision/{state,start,stop,frame} — proxy to the voice-agent vision server.
 *   GET  /chat              — Standalone clean-chat HTML (CHAT_HTML).
 *   GET  /overlays          — Overlay manager HTML.
 *   *    /api/overlays[/*]  — Proxy to the desktop overlay app's control server.
 *   GET  /paidsubscriptions[, /data]
 *   POST /paidsubscriptions/scan — Subscription-scanner skill UI + data + scan trigger.
 *
 * Lifecycle: started by voice-agent.ts in the same process — replaces the old
 * standalone web-client.ts service. One Node process owns both the WebSocket
 * (PORT) and the HTTP server (CLIENT_PORT).
 *
 * Architecture rule (CLAUDE.md § Where does new code belong?): this is a voice-
 * session concern (web client wiring), step 3 of the decision guide — lives in
 * core (`src/`), not under skills.
 */

import { createServer, request as httpRequest } from 'node:http';
import { writeFileSync, readFileSync, existsSync, statSync } from 'node:fs';
import { dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readTmuxStatus } from './tmux-status.js';
import { CHAT_HTML } from './chat-ui.js';
import { OVERLAY_MANAGER_HTML } from './overlay-manager-ui.js';
import { resolveWorkspace, statusReadPath } from './workspace_default.js';

// Dist directory for the React bundle (`client/`). Resolved once at module
// load — web-server.ts lives in `src/`, so `../client/dist` lands at the
// repo root. `pnpm build:client` must run for `/` to render anything; before
// that, GET / returns 503 with the build hint.
const CLIENT_DIST_DIR = fileURLToPath(new URL('../client/dist/', import.meta.url));

const STATIC_MIME_TYPES: Record<string, string> = {
	'.html': 'text/html; charset=utf-8',
	'.js': 'application/javascript; charset=utf-8',
	'.mjs': 'application/javascript; charset=utf-8',
	'.css': 'text/css; charset=utf-8',
	'.json': 'application/json; charset=utf-8',
	'.map': 'application/json; charset=utf-8',
	'.svg': 'image/svg+xml',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.gif': 'image/gif',
	'.webp': 'image/webp',
	'.ico': 'image/x-icon',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
};

/**
 * Resolve a request path under `/` or `/v2/` to a file inside `client/dist/`.
 * Returns null when the path escapes the dist root (defense against
 * `/../etc/passwd` style traversal) or when the file is missing — either
 * case falls through to a 404 in the caller.
 */
function resolveDistFile(relPathRaw: string): string | null {
	const relPath = relPathRaw.replace(/^\/+/, '') || 'index.html';
	const normalized = normalize(relPath);
	if (normalized.startsWith('..') || normalized.includes(`..${sep}`)) return null;
	const abs = CLIENT_DIST_DIR + normalized;
	try {
		const stat = statSync(abs);
		if (!stat.isFile()) return null;
		return abs;
	} catch {
		return null;
	}
}

/**
 * Serve a file from `client/dist/`. Returns true when the response was
 * written, false when the file wasn't found (caller decides the fallback).
 *
 * `spaFallback` (default true) controls the "file missing → index.html"
 * behavior. Disable it for asset-shaped requests (e.g. /assets/*.js) where
 * silently substituting index.html would hand the browser HTML for what it
 * thinks is JS.
 */
function serveDistFile(
	rel: string,
	res: import('node:http').ServerResponse,
	opts: { spaFallback?: boolean } = {}
): boolean {
	const { spaFallback = true } = opts;
	const file = spaFallback
		? (resolveDistFile(rel) ?? resolveDistFile('index.html'))
		: resolveDistFile(rel);
	if (!file) return false;
	const mime = STATIC_MIME_TYPES[extname(file).toLowerCase()] ?? 'application/octet-stream';
	try {
		const body = readFileSync(file);
		res.writeHead(200, {
			'Content-Type': mime,
			// Hashed asset filenames from Vite are safe to cache long-term;
			// index.html must always be fresh so SPA route changes ship.
			'Cache-Control': file.endsWith('index.html')
				? 'no-cache, no-store, must-revalidate'
				: 'public, max-age=31536000, immutable',
		});
		res.end(body);
		return true;
	} catch {
		res.writeHead(500);
		res.end('Failed to read static asset');
		return true;
	}
}

function escapeHtml(s: string): string {
	return String(s).replace(/[<>&"']/g, c => (({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'} as Record<string, string>)[c] || c));
}

// /paidsubscriptions page — full HTML, server-side rendered from
// skills/subscription-scanner/state/subscriptions.json. Sortable table,
// diff highlights from last scan, "Scan now" button.
function renderSubscriptionsHtml(rawJson: string): string {
	let data: any;
	try { data = JSON.parse(rawJson); } catch (e: any) { data = { last_scan: null, subscriptions: [], scan_history: [], _parse_error: e?.message }; }
	const lastScan = data.last_scan ? new Date(data.last_scan).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : '— never scanned —';
	const dataJson = JSON.stringify(data).replace(/</g, '\\u003c');

	return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paid Subscriptions — Sutando</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; background: #0e0e14; color: #e8e8ee; padding: 24px; min-height: 100vh; }
  .wrap { max-width: 1200px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 700; }
  .subtitle { color: #707080; font-size: 13px; }
  .meta { display: flex; gap: 20px; font-size: 13px; color: #888; margin: 12px 0 20px; flex-wrap: wrap; align-items: center; }
  .meta strong { color: #c0c0d0; font-weight: 600; }
  .scan-btn { background: #1e4028; color: #4ecca3; border: 1px solid #2a4a36; padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }
  .scan-btn:hover:not(:disabled) { background: #2a503a; }
  .scan-btn:disabled { background: #1a1a2a; color: #444; border-color: #2a2a3e; cursor: wait; }
  .scan-status { font-size: 12px; color: #4ecca3; margin-left: 8px; }
  .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .stat { background: #14141e; border: 1px solid #1e1e2a; border-radius: 10px; padding: 14px 16px; }
  .stat .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: #707080; margin-bottom: 6px; }
  .stat .value { font-size: 24px; font-weight: 700; color: #e8e8ee; }
  .stat .sub { font-size: 11px; color: #888; margin-top: 4px; }
  .stat.added .value { color: #4ecca3; }
  .stat.removed .value { color: #e94560; }
  .stat.uncertain .value { color: #f0ad4e; }

  table { width: 100%; border-collapse: collapse; background: #14141e; border-radius: 10px; overflow: hidden; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #1e1e2a; font-size: 13px; }
  th { background: #1a1a26; color: #a0a0b0; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; cursor: pointer; user-select: none; position: relative; }
  th:hover { color: #e8e8ee; }
  th.sort-asc::after { content: ' ▲'; color: #4ecca3; }
  th.sort-desc::after { content: ' ▼'; color: #4ecca3; }
  tbody tr:hover { background: #181826; }
  td.amount { text-align: right; font-variant-numeric: tabular-nums; }
  td.amount .currency { color: #707080; font-size: 11px; margin-left: 2px; }

  .status { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .status.active { background: #1e4028; color: #4ecca3; }
  .status.cancelled { background: #2a1a20; color: #e94560; }
  .status.uncertain { background: #2a2418; color: #f0ad4e; }

  .vendor { color: #e8e8ee; font-weight: 600; }
  .account { color: #888; font-size: 12px; }
  .notes { color: #707080; font-size: 11px; font-style: italic; max-width: 320px; }
  .freq { color: #a0a0b0; font-size: 12px; }

  .row-added { background: rgba(78, 204, 163, 0.08); }
  .row-cancelled { opacity: 0.55; }
  .row-cancelled td { text-decoration: line-through; text-decoration-color: #e94560; }
  .row-cancelled .vendor { color: #e94560; text-decoration-color: #e94560; }

  .empty { text-align: center; padding: 40px; color: #555; }
  footer { margin-top: 32px; color: #555; font-size: 11px; text-align: center; }
  footer a { color: #888; text-decoration: none; }
  footer a:hover { color: #4ecca3; }

  details { margin-top: 24px; }
  details summary { cursor: pointer; color: #707080; font-size: 12px; padding: 8px 0; }
  details summary:hover { color: #a0a0b0; }
  pre { background: #0a0a12; padding: 14px; border-radius: 8px; overflow-x: auto; font-size: 11px; color: #a0a0b0; margin-top: 8px; max-height: 300px; }
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>💳 Paid Subscriptions</h1>
      <div class="subtitle">Scanned from Gmail receipts</div>
      <div style="margin-left:auto"><a href="/" style="color:#707080;font-size:12px;text-decoration:none;border:1px solid #2a2a3e;padding:5px 12px;border-radius:6px;">← Dashboard</a></div>
    </header>

    <div class="meta">
      <span><strong>Last scan:</strong> ${escapeHtml(lastScan)}</span>
      <button class="scan-btn" id="scanBtn" onclick="triggerScan()">⟳ Scan now</button>
      <span class="scan-status" id="scanStatus"></span>
    </div>

    <div id="summary" class="summary"></div>

    <div id="diff-banner"></div>

    <table id="subs-table">
      <thead>
        <tr>
          <th data-key="vendor">Vendor</th>
          <th data-key="amount" class="amount">Amount</th>
          <th data-key="frequency">Frequency</th>
          <th data-key="account">Account</th>
          <th data-key="last_charged">Last charged</th>
          <th data-key="next_charge">Next charge</th>
          <th data-key="status">Status</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody id="subs-tbody"></tbody>
    </table>

    <details>
      <summary>Raw JSON</summary>
      <pre id="raw-json"></pre>
    </details>

    <footer>
      Subscription data lives at <code>skills/subscription-scanner/state/subscriptions.json</code> (gitignored).<br>
      Auto-scan runs monthly via the <code>subscription-scan</code> cron. Source: Gmail receipts via Claude MCP.
    </footer>
  </div>

<script>
  const data = ${dataJson};
  const tbody = document.getElementById('subs-tbody');
  const summary = document.getElementById('summary');
  const diffBanner = document.getElementById('diff-banner');
  const rawJson = document.getElementById('raw-json');
  const lastDiff = (data.scan_history && data.scan_history.length) ? data.scan_history[data.scan_history.length - 1] : { added: [], removed: [], amount_changed: [] };

  let sortKey = 'amount';
  let sortDir = 'desc';

  function fmtMoney(amount, currency) {
    if (amount === null || amount === undefined) return '<span style="color:#555">—</span>';
    const sym = currency === 'EUR' ? '€' : currency === 'GBP' ? '£' : '$';
    return sym + amount.toFixed(2) + (currency && currency !== 'USD' ? ' <span class="currency">' + currency + '</span>' : '');
  }

  function fmtDate(d) {
    if (!d) return '<span style="color:#555">—</span>';
    return d;
  }

  function escapeHtmlClient(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function monthlyEquivalent(sub) {
    if (sub.amount === null || sub.amount === undefined) return null;
    if (sub.frequency === 'monthly') return sub.amount;
    if (sub.frequency === 'annual') return sub.amount / 12;
    return sub.amount;
  }

  function renderSummary() {
    const subs = data.subscriptions || [];
    const active = subs.filter(s => s.status === 'active');
    const uncertain = subs.filter(s => s.status === 'uncertain');
    const cancelled = subs.filter(s => s.status === 'cancelled');

    let monthlyTotal = 0, monthlyKnown = 0, monthlyUnknown = 0;
    for (const s of active) {
      const me = monthlyEquivalent(s);
      if (me !== null) {
        const usdRate = s.currency === 'EUR' ? 1.08 : (s.currency === 'GBP' ? 1.27 : 1.0);
        monthlyTotal += me * usdRate;
        monthlyKnown++;
      } else {
        monthlyUnknown++;
      }
    }

    summary.innerHTML = \`
      <div class="stat"><div class="label">Active</div><div class="value">\${active.length}</div><div class="sub">\${monthlyUnknown ? monthlyUnknown + ' missing price' : 'all priced'}</div></div>
      <div class="stat"><div class="label">Monthly burn (~)</div><div class="value">$\${monthlyTotal.toFixed(0)}</div><div class="sub">\${monthlyKnown}/\${active.length} priced • \$\${(monthlyTotal*12).toFixed(0)}/yr</div></div>
      <div class="stat uncertain"><div class="label">Uncertain</div><div class="value">\${uncertain.length}</div><div class="sub">verify these</div></div>
      <div class="stat removed"><div class="label">Cancelled</div><div class="value">\${cancelled.length}</div><div class="sub">recent cancellations</div></div>
    \`;
  }

  function renderDiffBanner() {
    const a = lastDiff.added || [];
    const r = lastDiff.removed || [];
    const c = lastDiff.amount_changed || [];
    if (a.length === 0 && r.length === 0 && c.length === 0) {
      diffBanner.innerHTML = '<div style="font-size:12px;color:#555;margin-bottom:14px;">No changes since previous scan.</div>';
      return;
    }
    const parts = [];
    if (a.length) parts.push('<span style="color:#4ecca3">+' + a.length + ' added: ' + a.map(escapeHtmlClient).join(', ') + '</span>');
    if (r.length) parts.push('<span style="color:#e94560">−' + r.length + ' removed: ' + r.map(escapeHtmlClient).join(', ') + '</span>');
    if (c.length) parts.push('<span style="color:#f0ad4e">' + c.length + ' price changed</span>');
    diffBanner.innerHTML = '<div style="font-size:13px;margin-bottom:14px;padding:10px 14px;background:#181826;border-radius:8px;border-left:3px solid #4ecca3;">Since last scan: ' + parts.join(' • ') + '</div>';
  }

  function renderTable() {
    const subs = (data.subscriptions || []).slice();
    const addedSet = new Set(lastDiff.added || []);

    subs.sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (sortKey === 'amount') { av = monthlyEquivalent(a) ?? -1; bv = monthlyEquivalent(b) ?? -1; }
      if (av === null || av === undefined) av = '';
      if (bv === null || bv === undefined) bv = '';
      if (typeof av === 'string') av = av.toLowerCase();
      if (typeof bv === 'string') bv = bv.toLowerCase();
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    tbody.innerHTML = '';
    if (subs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No subscriptions found yet. Click "Scan now" to populate.</td></tr>';
      return;
    }
    for (const s of subs) {
      const tr = document.createElement('tr');
      const isAdded = addedSet.has(s.vendor);
      if (s.status === 'cancelled') tr.className = 'row-cancelled';
      else if (isAdded) tr.className = 'row-added';
      tr.innerHTML = \`
        <td><div class="vendor">\${escapeHtmlClient(s.vendor)}</div><div class="account">\${escapeHtmlClient(s.category || '')}</div></td>
        <td class="amount">\${fmtMoney(s.amount, s.currency)}</td>
        <td><span class="freq">\${escapeHtmlClient(s.frequency || '')}</span></td>
        <td><span class="account">\${escapeHtmlClient(s.account || '')}</span></td>
        <td>\${fmtDate(s.last_charged)}</td>
        <td>\${fmtDate(s.next_charge)}</td>
        <td><span class="status \${escapeHtmlClient(s.status || '')}">\${escapeHtmlClient(s.status || '')}</span></td>
        <td><span class="notes">\${escapeHtmlClient(s.notes || '')}</span></td>
      \`;
      tbody.appendChild(tr);
    }
    document.querySelectorAll('th[data-key]').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (th.dataset.key === sortKey) th.classList.add(sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    });
  }

  document.querySelectorAll('th[data-key]').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.key;
      if (k === sortKey) sortDir = (sortDir === 'asc' ? 'desc' : 'asc');
      else { sortKey = k; sortDir = (k === 'vendor' || k === 'frequency' || k === 'status' || k === 'account') ? 'asc' : 'desc'; }
      renderTable();
    });
  });

  async function triggerScan() {
    const btn = document.getElementById('scanBtn');
    const status = document.getElementById('scanStatus');
    btn.disabled = true;
    status.textContent = '⏳ queueing...';
    try {
      const r = await fetch('/paidsubscriptions/scan', { method: 'POST' });
      const j = await r.json();
      if (j.ok) {
        status.textContent = '✓ ' + j.message;
        let elapsed = 0;
        const poll = setInterval(async () => {
          elapsed += 5;
          if (elapsed > 180) { clearInterval(poll); btn.disabled = false; status.textContent = '⚠ scan still running — refresh in a moment'; return; }
          const fresh = await fetch('/paidsubscriptions/data').then(r => r.json()).catch(() => null);
          if (fresh && fresh.last_scan && fresh.last_scan !== data.last_scan) {
            clearInterval(poll);
            status.textContent = '✓ scan complete — refreshing...';
            setTimeout(() => location.reload(), 800);
          }
        }, 5000);
      } else {
        status.textContent = '✗ ' + (j.error || 'failed');
        btn.disabled = false;
      }
    } catch (e) {
      status.textContent = '✗ ' + (e.message || 'network error');
      btn.disabled = false;
    }
  }

  rawJson.textContent = JSON.stringify(data, null, 2);
  renderSummary();
  renderDiffBanner();
  renderTable();
</script>
</body>
</html>`;
}

export interface WebServerOptions {
	/** Port for the HTTP server (default: 8080, matches the legacy CLIENT_PORT env). */
	port: number;
	/** Bind address (default: '0.0.0.0' — accept remote browser connections). */
	host: string;
	/** WebSocket port that the served page connects back to (default: voice-agent's PORT, 9900). */
	wsPort: number;
}

/**
 * Start the HTTP server on `opts.host:opts.port`. Returns the underlying
 * `http.Server` so callers can close() it during teardown.
 *
 * Side effect: spins up an SSE heartbeat interval (30s) and an internal
 * setTimeout per /mute-state?state=seeing call. Calling startWebServer more
 * than once in the same process is unsupported.
 */
export function startWebServer(opts: WebServerOptions): import('node:http').Server {
	const HTTP_PORT = opts.port;
	const HTTP_HOST = opts.host;
	const WS_PORT = opts.wsPort;

	// Workspace-relative paths use resolveWorkspace(). Skills paths
	// (non-runtime, code-adjacent) remain anchored to the repo root.
	const WORKSPACE_DIR = resolveWorkspace();
	const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
	const TASK_DIR = join(WORKSPACE_DIR, 'tasks');
	const STATE_DIR = join(WORKSPACE_DIR, 'state');
	const SUBSCRIPTIONS_PATH = join(REPO_ROOT, 'skills/subscription-scanner/state/subscriptions.json');
	const PRESENTER_SENTINEL = join(WORKSPACE_DIR, 'state', 'presenter-mode.sentinel');

	const sseClients: import('node:http').ServerResponse[] = [];
	let _muteState = false;
	let _voiceState = false;
	// Semantic agent state. Two independent tracks:
	//   - _browserState: what the browser derives from local signals
	//     (connected+unmuted → listening, audio RMS → speaking, disconnected → idle).
	//     Refreshed ~1x/second by reportAgentState in the page.
	//   - _toolState: set by server-side tool code (voice-agent onToolCall →
	//     'working', screen-capture → 'seeing'). Only tool code writes this.
	// Effective state (returned by /sse-status + broadcast via SSE) is the
	// tool track when non-idle, else the browser track. This prevents the
	// browser's 1s poll from overwriting a tool-originated 'working' back
	// to 'listening'.
	type AgentState = 'idle' | 'listening' | 'speaking' | 'working' | 'seeing';
	let _browserState: AgentState = 'idle';
	let _toolState: AgentState = 'idle';
	// Optional label for the tool track, e.g. the specific tool name
	// ('describe_screen') or core-status step. Surfaced by /sse-status so
	// the menu-bar tooltip can say "running describe_screen" instead of
	// the generic "running a tool".
	let _toolLabel: string = '';
	// Saves the tool-track state at the moment seeing is set, so after the
	// seeing TTL expires we revert to whatever tool was running BEFORE the
	// capture — most commonly 'working'. Previously seeing → idle, which
	// killed the working pulse mid-tool.
	let _preSeeingToolState: AgentState = 'idle';
	// Timestamp of the last transition INTO 'seeing'. Screen capture is transient
	// (sub-second), so the 'seeing' state flashes briefly then auto-reverts.
	let _seeingUntil = 0;

	// Read core-status.json and return { running, step, stale }.
	// - `running`: CLI is mid-pass (status == "running" and ts within grace).
	// - `step`: tooltip label when no tool label is set.
	// - `stale`: the file is unreliable — either older than 60s on disk, or
	//   status=="running" with ts older than 60s. When stale, consumers should fall
	//   back to the tmux pane scrape for a fresh signal.
	const CORE_STATUS_STALE_SECONDS = 60;
	function readCoreStatus(): { running: boolean; step: string; stale: boolean } {
		try {
			const statusFile = statusReadPath('core-status.json', WORKSPACE_DIR);
			const raw = readFileSync(statusFile, 'utf-8');
			const s = JSON.parse(raw) as { status?: string; ts?: number; step?: string };
			const nowSec = Date.now() / 1000;
			let stale = false;
			try {
				const mtimeSec = statSync(statusFile).mtimeMs / 1000;
				if (nowSec - mtimeSec > CORE_STATUS_STALE_SECONDS) stale = true;
			} catch { stale = true; }
			if (s.status === 'running' && typeof s.ts === 'number' && nowSec - s.ts > CORE_STATUS_STALE_SECONDS) {
				stale = true;
			}
			if (s.status !== 'running') return { running: false, step: '', stale };
			if (typeof s.ts === 'number' && nowSec - s.ts > 600) return { running: false, step: '', stale };
			return { running: true, step: typeof s.step === 'string' ? s.step : '', stale };
		} catch {
			return { running: false, step: '', stale: true };
		}
	}

	const VOICE_STATE_STALE_SECONDS = 120;
	function readVoiceState(): boolean | null {
		try {
			const statusFile = statusReadPath('voice-state.json', WORKSPACE_DIR);
			const raw = readFileSync(statusFile, 'utf-8');
			const s = JSON.parse(raw) as { connected?: boolean; ts?: number };
			const nowSec = Date.now() / 1000;
			if (typeof s.ts === 'number' && nowSec - s.ts > VOICE_STATE_STALE_SECONDS && s.connected) {
				return null;
			}
			return typeof s.connected === 'boolean' ? s.connected : null;
		} catch {
			return null;
		}
	}

	function effectiveAgentState(): AgentState {
		if (_toolState === 'seeing' && Date.now() > _seeingUntil) {
			_toolState = _preSeeingToolState;
			_preSeeingToolState = 'idle';
		}
		if (_toolState !== 'idle') return _toolState;
		// Core-agent (Claude Code proactive-loop / task pass) running beats the
		// browser track — if core is actively doing work, that's the truer state
		// than "user is currently speaking".
		const core = readCoreStatus();
		if (core.running) return 'working';
		if (core.stale) {
			const scrape = readTmuxStatus();
			if (scrape.state === 'working') return 'working';
		}
		if (_browserState !== 'idle') return _browserState;
		return 'idle';
	}

	// Heartbeat: ping every 30s, remove clients that fail to write (stale connections).
	// .unref() so a lone heartbeat doesn't block Node from exiting when the
	// caller has closed the server.
	const heartbeat = setInterval(() => {
		for (let i = sseClients.length - 1; i >= 0; i--) {
			try {
				sseClients[i].write(':\n\n');
			} catch {
				sseClients.splice(i, 1);
			}
		}
	}, 30_000);
	heartbeat.unref();

	const server = createServer((req, res) => {
		const url = new URL(req.url || '/', `http://${req.headers.host}`);

		if (url.pathname === '/sse') {
			res.writeHead(200, {
				'Content-Type': 'text/event-stream',
				'Cache-Control': 'no-cache',
				'Connection': 'keep-alive',
				'Access-Control-Allow-Origin': '*',
			});
			res.write(':\n\n');
			// Send current agent state immediately so freshly-connected clients
			// don't display stale DOM classes from the previous session.
			try {
				res.write(`event: agent-state\ndata: ${effectiveAgentState()}\n\n`);
			} catch {}
			sseClients.push(res);
			req.on('close', () => {
				const idx = sseClients.indexOf(res);
				if (idx >= 0) sseClients.splice(idx, 1);
			});
			return;
		}

		if (url.pathname === '/sse-status') {
			const eff = effectiveAgentState();
			let label = '';
			if (_toolState !== 'idle') {
				label = _toolLabel;
			} else if (eff === 'working') {
				const core = readCoreStatus();
				label = core.step;
				if (core.stale && !core.step) {
					const scrape = readTmuxStatus();
					if (scrape.state === 'working') label = scrape.label;
				}
			}
			const vs = readVoiceState();
			const voiceConnected = vs !== null ? vs : _voiceState;
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({
				clients: sseClients.length,
				muted: _muteState,
				voiceConnected,
				state: eff,
				label,
			}));
			return;
		}

		// Presenter-mode sentinel — written by scripts/presenter-mode.sh and
		// read by Discord/Slack/Telegram bridges to silence proactive pings.
		// React badge polls this for visual parity with the legacy UI. The
		// sentinel body is an ISO-8601 expiry; we treat any unparseable or
		// past timestamp as inactive (matches the bridges' is_active checks).
		if (url.pathname === '/presenter') {
			let active = false;
			let expiresAt: string | null = null;
			try {
				const raw = readFileSync(PRESENTER_SENTINEL, 'utf-8').trim();
				if (raw) {
					const expiry = Date.parse(raw);
					if (!Number.isNaN(expiry) && expiry > Date.now()) {
						active = true;
						expiresAt = raw;
					}
				}
			} catch { /* sentinel missing → inactive */ }
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ active, expiresAt }));
			return;
		}

		// Voice-agent mode sentinel (state/voice-mode.txt written by voice-agent
		// on switch_mode / zoom-auto-flip). Returns "active" or "meeting".
		if (url.pathname === '/voice-mode') {
			let mode = 'active';
			try {
				const raw = readFileSync(join(STATE_DIR, 'voice-mode.txt'), 'utf-8').trim();
				if (raw === 'meeting' || raw === 'active') mode = raw;
			} catch {}
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ mode }));
			return;
		}

		// Mute + voice + agent state report. `source=tool` writes the tool
		// track (working/seeing) and takes precedence; everything else
		// writes the browser track (idle/listening/speaking).
		if (url.pathname === '/mute-state') {
			const mState = url.searchParams.get('muted');
			const vState = url.searchParams.get('voice');
			const aState = url.searchParams.get('state');
			const source = url.searchParams.get('source');
			if (mState !== null) _muteState = mState === 'true';
			if (vState !== null) _voiceState = vState === 'true';
			if (aState === 'idle' || aState === 'listening' || aState === 'speaking' || aState === 'working' || aState === 'seeing') {
				const prevEffective = effectiveAgentState();
				if (source === 'tool') {
					const labelParam = url.searchParams.get('label');
					if (aState === 'seeing') {
						if (_toolState !== 'seeing') _preSeeingToolState = _toolState;
						_toolState = 'seeing';
						if (labelParam) _toolLabel = labelParam;
						const ttlParam = url.searchParams.get('ttl_ms');
						const ttl = ttlParam ? parseInt(ttlParam, 10) : 3000;
						// Upper-bound via RelationalComparison so CodeQL recognizes
						// the sanitizer guard. `Math.min(ttl, MAX)` is treated as a
						// numeric passthrough and does NOT close the alert.
						const MAX_TTL_MS = 60000;
						const ttlMs = (isFinite(ttl) && ttl > 0)
							? (ttl <= MAX_TTL_MS ? ttl : MAX_TTL_MS)
							: 3000;
						_seeingUntil = Date.now() + ttlMs;
						setTimeout(() => {
							if (_toolState === 'seeing' && Date.now() >= _seeingUntil) {
								_toolState = _preSeeingToolState;
								_preSeeingToolState = 'idle';
								const eff = effectiveAgentState();
								for (const client of sseClients) {
									try { client.write(`event: agent-state\ndata: ${eff}\n\n`); } catch {}
								}
							}
						}, ttlMs + 50);
					} else {
						_toolState = aState === 'working' ? 'working' : 'idle';
						_preSeeingToolState = 'idle';
						if (aState === 'working' && labelParam) _toolLabel = labelParam;
						else if (aState !== 'working') _toolLabel = '';
					}
				} else {
					// Browser can't legitimately know working/seeing — those
					// originate server-side. Clamp to listening if mislabeled.
					_browserState = (aState === 'working' || aState === 'seeing') ? 'listening' : aState;
				}
				const nextEffective = effectiveAgentState();
				if (prevEffective !== nextEffective) {
					for (const client of sseClients) {
						try { client.write(`event: agent-state\ndata: ${nextEffective}\n\n`); } catch {}
					}
				}
			}
			res.writeHead(200, { 'Content-Type': 'application/json' });
			const vs2 = readVoiceState();
			res.end(JSON.stringify({ muted: _muteState, voiceConnected: vs2 !== null ? vs2 : _voiceState, state: effectiveAgentState() }));
			return;
		}

		if (url.pathname === '/toggle' || url.pathname === '/mute') {
			const event = url.pathname === '/toggle' ? 'toggle-voice' : 'toggle-mute';
			for (const client of sseClients) {
				client.write(`event: ${event}\ndata: 1\n\n`);
			}
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: true, event, clients: sseClients.length }));
			return;
		}

		// Note view event from the in-page note reader. Writes the current slug +
		// content to /tmp/sutando-note-viewing.json; the voice-agent's
		// startNoteViewingWatcher picks it up and injects into Gemini so the
		// assistant can answer questions about whatever the user is looking at.
		if (url.pathname === '/note-viewing' && req.method === 'POST') {
			const chunks: Buffer[] = [];
			req.on('data', (c: Buffer) => chunks.push(c));
			req.on('end', () => {
				try {
					const body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
					if (!body.slug || typeof body.content !== 'string') {
						res.writeHead(400, { 'Content-Type': 'application/json' });
						res.end(JSON.stringify({ error: 'slug and content required' }));
						return;
					}
					const event = { slug: body.slug, content: body.content, ts: new Date().toISOString() };
					writeFileSync('/tmp/sutando-note-viewing.json', JSON.stringify(event));
					res.writeHead(200, { 'Content-Type': 'application/json' });
					res.end(JSON.stringify({ ok: true }));
				} catch (e) {
					res.writeHead(400, { 'Content-Type': 'application/json' });
					res.end(JSON.stringify({ error: e instanceof Error ? e.message : 'parse failed' }));
				}
			});
			return;
		}

		// Vision control proxy. The voice-agent process exposes
		// /vision/{state,start,stop,frame} on 127.0.0.1:VISION_CONTROL_PORT
		// (default 7848); the browser hits us same-origin to avoid CORS.
		// /vision/frame carries a binary JPEG body — preserve the content-type
		// and pass the buffer straight through.
		if (
			url.pathname === '/vision/state' ||
			url.pathname === '/vision/start' ||
			url.pathname === '/vision/stop' ||
			url.pathname === '/vision/frame'
		) {
			const visionPort = Number(process.env.VISION_CONTROL_PORT) || 7848;
			const method = req.method === 'POST' ? 'POST' : 'GET';
			const isFrame = url.pathname === '/vision/frame';
			const visionPath = url.pathname;
			const chunks: Buffer[] = [];
			req.on('data', (c: Buffer) => chunks.push(c));
			req.on('end', async () => {
				try {
					const incomingType =
						(req.headers['content-type'] as string | undefined) ||
						(isFrame ? 'image/jpeg' : 'application/json');
					const upstream = await fetch(`http://127.0.0.1:${visionPort}${visionPath}`, {
						method,
						headers: method === 'POST' ? { 'Content-Type': incomingType } : undefined,
						body:
							method === 'POST'
								? chunks.length
									? Buffer.concat(chunks)
									: isFrame
										? Buffer.alloc(0)
										: '{}'
								: undefined,
					});
					const text = await upstream.text();
					res.writeHead(upstream.status, { 'Content-Type': 'application/json' });
					res.end(text);
				} catch {
					const fallback =
						visionPath === '/vision/state'
							? { streaming: false, source: null, fps: 0, frames: 0, durationMs: 0, sessionReady: false }
							: { status: 'failed', error: 'voice-agent not reachable' };
					res.writeHead(visionPath === '/vision/state' ? 200 : 503, {
						'Content-Type': 'application/json',
					});
					res.end(JSON.stringify(fallback));
				}
			});
			return;
		}

		// Clean chat-first UI — Gemini/Claude-app style. Same task-bridge
		// backend as the dashboard textbox; markdown rendering + full-viewport
		// chat + persistent history. Lives at /chat to leave / untouched.
		if (url.pathname === '/chat') {
			res.writeHead(200, {
				'Content-Type': 'text/html; charset=utf-8',
				'Cache-Control': 'no-cache, no-store, must-revalidate',
			});
			res.end(CHAT_HTML);
			return;
		}

		// Overlay Manager — lists/controls the desktop overlay applications.
		// The page itself is static; it talks to /api/overlays/* below.
		if (url.pathname === '/overlays') {
			res.writeHead(200, {
				'Content-Type': 'text/html; charset=utf-8',
				'Cache-Control': 'no-cache, no-store, must-revalidate',
			});
			res.end(OVERLAY_MANAGER_HTML);
			return;
		}

		// Proxy to the overlay app's control server. The overlay app writes its
		// port to state/overlay-control.json; we forward same-origin requests
		// so the browser needs no CORS or port discovery.
		if (url.pathname === '/api/overlays' || url.pathname.startsWith('/api/overlays/')) {
			let disc: { host?: string; port?: number } | null = null;
			try {
				disc = JSON.parse(readFileSync(join(STATE_DIR, 'overlay-control.json'), 'utf-8'));
			} catch {
				disc = null;
			}
			if (!disc || !disc.port) {
				res.writeHead(503, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'overlay control server not running' }));
				return;
			}
			const subPath = url.pathname.replace('/api/overlays', '/overlays') + (url.search || '');
			const proxyReq = httpRequest(
				{
					host: disc.host || '127.0.0.1',
					port: disc.port,
					path: subPath,
					method: req.method,
					headers: { 'Content-Type': 'application/json' },
				},
				(proxyRes) => {
					res.writeHead(proxyRes.statusCode || 502, { 'Content-Type': 'application/json' });
					proxyRes.pipe(res);
				},
			);
			proxyReq.on('error', (e) => {
				console.error('[web-server] overlay control proxy failed:', e);
				res.writeHead(502, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'overlay control proxy failed' }));
			});
			req.pipe(proxyReq);
			return;
		}

		// Paid subscriptions dashboard. Reads skills/subscription-scanner/state/subscriptions.json
		// and renders a sortable table with diff highlights from the previous scan.
		// Trigger an out-of-cycle scan via POST to /paidsubscriptions/scan.
		if (url.pathname === '/paidsubscriptions') {
			try {
				const raw = existsSync(SUBSCRIPTIONS_PATH)
					? readFileSync(SUBSCRIPTIONS_PATH, 'utf-8')
					: '{"last_scan":null,"subscriptions":[],"scan_history":[]}';
				res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
				res.end(renderSubscriptionsHtml(raw));
			} catch (e: any) {
				console.error('[web-server] /paidsubscriptions render failed:', e);
				res.writeHead(500, { 'Content-Type': 'text/plain' });
				res.end('Error reading subscriptions');
			}
			return;
		}
		if (url.pathname === '/paidsubscriptions/data') {
			try {
				const raw = existsSync(SUBSCRIPTIONS_PATH)
					? readFileSync(SUBSCRIPTIONS_PATH, 'utf-8')
					: '{"last_scan":null,"subscriptions":[],"scan_history":[]}';
				res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' });
				res.end(raw);
			} catch (e: any) {
				console.error('[web-server] /paidsubscriptions/data failed:', e);
				res.writeHead(500, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ error: 'failed to read subscriptions' }));
			}
			return;
		}
		if (url.pathname === '/paidsubscriptions/scan' && req.method === 'POST') {
			// Localhost-only: this endpoint writes an owner-tier task file that
			// the watcher processes with full agent privileges. Reads
			// req.socket.remoteAddress directly rather than a header (X-Forwarded-For
			// is spoofable). IPv4-mapped IPv6 (::ffff:127.0.0.1) and IPv6
			// loopback (::1) are both localhost.
			const remote = req.socket?.remoteAddress || '';
			const isLocalhost = (
				remote === '127.0.0.1' ||
				remote === '::1' ||
				remote === '::ffff:127.0.0.1'
			);
			if (!isLocalhost) {
				res.writeHead(403, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'forbidden: /paidsubscriptions/scan accepts localhost connections only' }));
				return;
			}
			try {
				const taskId = `task-${Date.now()}`;
				// Pointer (not inline) — prevents prompt-injection via
				// header-shaped lines in scan-prompt.md (`source:`,
				// `access_tier:`, etc.) being parsed as real task headers.
				const taskContent = `id: ${taskId}\ntimestamp: ${new Date().toISOString()}\ntask: Run subscription scan (out-of-cycle, triggered from /paidsubscriptions UI). Read the full instructions in skills/subscription-scanner/scan-prompt.md and follow them verbatim.\nsource: web\nfrom: paidsubscriptions-ui\naccess_tier: owner\n`;
				writeFileSync(join(TASK_DIR, `${taskId}.txt`), taskContent);
				res.writeHead(200, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: true, task_id: taskId, message: 'Scan queued; the next proactive-loop pass will pick it up (~1 min). Refresh to see results.' }));
			} catch (e: any) {
				console.error('[web-server] /paidsubscriptions/scan failed:', e);
				res.writeHead(500, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'failed to enqueue scan' }));
			}
			return;
		}

		// Static asset + SPA fallback. Vite's index.html references hashed
		// assets via relative paths (./assets/index-XXXX.js). When the
		// browser resolves those against /, the request comes in as
		// /assets/index-XXXX.js — neither / nor /v2 nor any of the API
		// endpoints above. We:
		//   1. Serve the exact file from client/dist/ when it exists.
		//   2. Fall back to index.html only for extension-less paths —
		//      that's the SPA-routing case (deep links like /settings).
		//   3. 404 asset misses (paths with an extension). Returning
		//      index.html for a missing .js file produces "Unexpected
		//      token '<'" in the console and a blank page.
		//   4. Return a 503 with build hint when client/dist/index.html
		//      itself is missing (fresh checkout, no `pnpm build:client`).
		const isV2Path = url.pathname === '/v2' || url.pathname.startsWith('/v2/');
		let rel: string;
		if (isV2Path) {
			rel = url.pathname === '/v2' ? 'index.html' : url.pathname.slice('/v2/'.length);
		} else if (url.pathname === '/') {
			rel = 'index.html';
		} else {
			rel = url.pathname.replace(/^\/+/, '');
		}

		if (serveDistFile(rel, res, { spaFallback: false })) return;

		const looksLikeAsset = !!extname(rel) && rel !== 'index.html';
		if (looksLikeAsset) {
			res.writeHead(404, { 'Content-Type': 'text/plain' });
			res.end('Not found');
			return;
		}

		if (serveDistFile('index.html', res, { spaFallback: false })) return;
		res.writeHead(503, {
			'Content-Type': 'text/html; charset=utf-8',
			'Cache-Control': 'no-cache',
		});
		res.end(
			`<!doctype html><meta charset="utf-8"><title>Sutando — build required</title>` +
				`<style>body{font-family:-apple-system,sans-serif;max-width:560px;margin:80px auto;padding:0 20px;color:#222}</style>` +
				`<h1>Sutando client not built</h1>` +
				`<p>Run <code>pnpm install && pnpm build:client</code> from the repo root, then refresh this page.</p>`
		);
	});

	server.listen(HTTP_PORT, HTTP_HOST, () => {
		const serverUrl = HTTP_HOST === '0.0.0.0'
			? `http://localhost:${HTTP_PORT} (or use your server's IP/DNS)`
			: `http://${HTTP_HOST}:${HTTP_PORT}`;
		console.log(`\n  Sutando — Web Client`);
		console.log(`  ────────────────────────────────`);
		console.log(`  Open in browser:  ${serverUrl}`);
		console.log(`  WebSocket URL:    Auto-detected from browser hostname`);
		console.log(`  WebSocket port:  ${WS_PORT}`);
		console.log(`\n  Press Ctrl+C to stop.\n`);
	});

	return server;
}
