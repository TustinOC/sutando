/**
 * Skill web mounts — let any skill expose HTTP routes under the sutando hostname.
 *
 * A skill's manifest.json may declare:
 *   "web": [
 *     { "mount": "/slides", "port": 7877, "methods": ["GET"] },
 *     ...
 *   ]
 *
 * - mount    — URL path prefix (must start with "/"; longer paths matched first).
 * - port     — local port the skill's server listens on (proxied as 127.0.0.1).
 * - methods  — HTTP methods allowed through the proxy. Anything else → 405.
 *              Defaults to ["GET","HEAD"]. Mutation methods stay localhost-only
 *              by NOT being declared here.
 *
 * Mount conflicts (two skills mounting the same path) are fail-fast at startup.
 *
 * Auth: not declared per-mount. Authorization happens at the Cloudflare Access
 * edge — each mount path can have its own CF Access app+policy in the dashboard
 * (e.g. /slides/public-* Bypass, /slides/team-* Allow @ag2.ai). A future origin-
 * side tier model will validate the CF Access JWT here, but until that exists
 * the field would be advisory-only and we'd rather not promise enforcement we
 * don't deliver.
 */
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
import { join } from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';

export interface SkillMount {
	skill: string;
	mount: string;
	port: number;
	methods: string[];
}

interface RawMount {
	mount?: unknown;
	port?: unknown;
	methods?: unknown;
}

export function loadSkillMounts(): SkillMount[] {
	const dirsToScan: string[] = [join(process.cwd(), 'skills')];
	const privateRoot = process.env.SUTANDO_PRIVATE_DIR;
	if (privateRoot) {
		const expanded = privateRoot.replace(/^~/, process.env.HOME || '');
		dirsToScan.push(join(expanded, 'skills'));
	}

	const mounts: SkillMount[] = [];
	const seen = new Map<string, string>();

	for (const skillsDir of dirsToScan) {
		if (!existsSync(skillsDir)) continue;
		let dirs: string[];
		try {
			dirs = readdirSync(skillsDir).filter(n => {
				try { return statSync(join(skillsDir, n)).isDirectory(); } catch { return false; }
			});
		} catch { continue; }

		for (const dirName of dirs) {
			const manifestPath = join(skillsDir, dirName, 'manifest.json');
			if (!existsSync(manifestPath)) continue;
			let manifest: { enabled?: boolean; web?: RawMount[]; name?: string };
			try {
				manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
			} catch (err) {
				console.warn(`[skill-mounts] bad manifest ${dirName} in ${skillsDir}:`, err instanceof Error ? err.message : err);
				continue;
			}
			if (!manifest.enabled) continue;
			if (!Array.isArray(manifest.web)) continue;

			const skillName = manifest.name || dirName;
			for (const raw of manifest.web) {
				const mount = typeof raw.mount === 'string' ? raw.mount : '';
				if (!mount.startsWith('/')) {
					throw new Error(`[skill-mounts] ${skillName}: mount "${mount}" must start with "/"`);
				}
				const port = Number(raw.port);
				if (!Number.isInteger(port) || port < 1 || port > 65535) {
					throw new Error(`[skill-mounts] ${skillName} mount ${mount}: invalid port ${raw.port}`);
				}
				const methods = Array.isArray(raw.methods) && raw.methods.length > 0
					? (raw.methods as string[]).map(s => String(s).toUpperCase())
					: ['GET', 'HEAD'];

				if (seen.has(mount)) {
					throw new Error(`[skill-mounts] mount conflict: "${mount}" claimed by both ${seen.get(mount)} and ${skillName}`);
				}
				seen.set(mount, skillName);
				mounts.push({ skill: skillName, mount, port, methods });
			}
		}
	}

	// Longest mount first — so /talk/highlight matches before /talk.
	mounts.sort((a, b) => b.mount.length - a.mount.length);
	return mounts;
}

export function matchMount(mounts: SkillMount[], pathname: string): SkillMount | null {
	for (const m of mounts) {
		if (pathname === m.mount || pathname.startsWith(m.mount + '/')) return m;
	}
	return null;
}

export function proxyToSkill(mount: SkillMount, req: IncomingMessage, res: ServerResponse): void {
	const method = (req.method || 'GET').toUpperCase();
	if (!mount.methods.includes(method)) {
		res.writeHead(405, { 'Allow': mount.methods.join(', '), 'Content-Type': 'text/plain' });
		res.end(`405 Method Not Allowed: ${method} on ${mount.mount}\n`);
		return;
	}

	const proxyReq = httpRequest({
		host: '127.0.0.1',
		port: mount.port,
		path: req.url,
		method: req.method,
		headers: { ...req.headers, host: `127.0.0.1:${mount.port}` },
	}, (proxyRes) => {
		res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
		proxyRes.pipe(res);
	});

	proxyReq.on('error', (err) => {
		if (!res.headersSent) {
			res.writeHead(502, { 'Content-Type': 'text/plain' });
		}
		res.end(`502 Skill backend not reachable: ${mount.skill} on :${mount.port}\n${err.message}\n`);
	});

	req.pipe(proxyReq);
}
