/**
 * Sutando — MentraOS Glasses Bridge
 *
 * Streams the glasses camera into the active voice session's vision pipeline,
 * so Gemini can see what the user sees through the glasses. Tap-to-capture
 * photos arrive as single high-quality frames + a follow-up text prompt to
 * the task bridge.
 *
 * Data flow:
 *   Glasses camera → managed video stream → thumbnail every 3s
 *     → POST /vision/frame (binary JPEG) → bodhi VoiceSession → Gemini Live
 *   Glasses tap → onPhotoTaken → POST /vision/frame (single hi-res frame)
 *     → write tasks/task-{ts}.txt "I just took a photo with my glasses..."
 *   Glasses HUD ← session.layouts.showTextWall (status text)
 *
 * Setup:
 *   npm i @mentra/sdk
 *   Register at https://portal.mentra.glass/
 *   Set MENTRA_API_KEY in .env, expose this server (ngrok http 7050),
 *   register the public URL in the Mentra portal, then:
 *     tsx src/mentra-bridge.ts
 *
 * Optional env:
 *   MENTRA_PORT             default 7050
 *   MENTRA_PACKAGE          default com.sutando.glasses
 *   MENTRA_FRAME_INTERVAL   default 3000 (ms between camera frames)
 *   VISION_CONTROL_PORT     default 7847 (must match voice-agent)
 *   SUTANDO_WORKSPACE       default cwd (where tasks/ lives)
 *
 * Why a separate process? The glasses session is event-driven via Mentra's
 * AppServer (its own HTTP listener on MENTRA_PORT). Keeping it out of
 * voice-agent.ts means voice-agent doesn't pay the SDK cost when glasses
 * aren't connected, and the bridge can be restarted independently.
 *
 * TODO(roadmap §4 bet "Adopt OC's device substrate; don't fork it" + §8
 * pitfall "Forking the OC substrate"): This bridge talks directly to
 * Sutando's vision pipeline, bypassing OpenCompanion's multi-device protocol
 * (device_register / context_push / hud_update). Deliberate shortcut for the
 * "land continuous vision" milestone — once Sutando subscribes to OC's
 * runtime as a backend, this should collapse into an OC adapter or vanish
 * entirely if OC's own mentra-bridge hosts the device.
 *
 * TODO(roadmap §4 bet "3-tier access control"): Any Mentra session paired
 * with the configured package is implicitly trusted as owner. Mirror the
 * Discord/Telegram pattern (allowlist + sandboxed task path for non-owners).
 *
 * TODO(roadmap §5 Next "Device-aware output routing"): showTextWall here is
 * static strings. Once DeviceSession + profile catalog land, route HUD
 * output via the device profile (Mentra Live = 400×240 green center, ≤80
 * chars/zone) instead of hard-coding.
 *
 * TODO(roadmap §5 Next "Skill manifests — omnia-agent.yaml"): No manifest
 * shipped for the vision capability. Author one once OC discovery is the
 * intended consumer.
 */

import 'dotenv/config';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const ts = () => new Date().toLocaleTimeString('en-US', { hour12: false });
const log = (kind: string, msg: string) => console.log(`${ts()} [Mentra/${kind}] ${msg}`);

// Lazy-load @mentra/sdk so this file imports cleanly even when the SDK isn't
// installed — only `start()` requires it. Keeps `tsc --noEmit` happy on every
// box and lets the optional bridge stay optional.
let MentraSdk: { AppServer: any; AppSession: any; LayoutType?: any } | null = null;
function loadSdk(): NonNullable<typeof MentraSdk> {
	if (MentraSdk) return MentraSdk;
	try {
		MentraSdk = require('@mentra/sdk');
	} catch (err) {
		console.error(`${ts()} [Mentra] @mentra/sdk not installed. Run: npm i @mentra/sdk`);
		throw err;
	}
	return MentraSdk!;
}

const MENTRA_API_KEY = process.env.MENTRA_API_KEY || '';
const MENTRA_PORT = Number(process.env.MENTRA_PORT) || 7050;
const MENTRA_PACKAGE = process.env.MENTRA_PACKAGE || 'com.sutando.glasses';
const FRAME_INTERVAL = Number(process.env.MENTRA_FRAME_INTERVAL) || 3000;
const VISION_PORT = Number(process.env.VISION_CONTROL_PORT) || 7847;
const VISION_BASE = `http://127.0.0.1:${VISION_PORT}`;
const WORKSPACE = process.env.SUTANDO_WORKSPACE || process.cwd();
const TASKS_DIR = join(WORKSPACE, 'tasks');

const SOURCE_LABEL = 'glasses';

// ── Vision pipeline helpers ────────────────────────────────────────────────

/** Register this glasses session with the DeviceSession registry (PR #739).
 *  Best-effort — if voice-agent predates the endpoint (404), we silently
 *  continue with the legacy push-mode path so the bridge keeps working on
 *  older deploys. */
async function registerDevice(deviceId: string, caps?: any): Promise<void> {
	try {
		const body = {
			deviceId,
			profileId: 'mentra-live',
			name: caps?.modelName || 'Mentra Live',
			category: 'glasses',
			mic: !!caps?.hasMicrophone,
			camera: { enabled: !!caps?.hasCamera, mode: 'stream', resolution: '720p' },
			hud: { enabled: !!caps?.hasDisplay, resolution: '400x240', color: 'green', position: 'center' },
			gestures: ['tap', 'scroll_up', 'scroll_down'],
			pushSourceLabel: SOURCE_LABEL,
			role: 'HUD + on-demand capture. "What is this?" with one tap.',
		};
		const r = await fetch(`${VISION_BASE}/vision/register`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (r.ok) {
			log('vision', `registered device ${deviceId} with voice-agent`);
		} else if (r.status === 404) {
			log('vision', `register endpoint unavailable (voice-agent predates DeviceSession); continuing in legacy mode`);
		} else {
			const errText = await r.text().catch(() => '');
			log('vision', `register failed ${r.status}: ${errText.slice(0, 120)}`);
		}
	} catch (err) {
		log('vision', `register error: ${(err as Error).message}`);
	}
}

async function unregisterDevice(deviceId: string): Promise<void> {
	try {
		await fetch(`${VISION_BASE}/vision/unregister`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ deviceId }),
		});
	} catch {}
}

async function startVisionPush(): Promise<boolean> {
	try {
		const r = await fetch(`${VISION_BASE}/vision/start`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ source: SOURCE_LABEL, mode: 'push' }),
		});
		const json = (await r.json().catch(() => ({}))) as { status?: string; error?: string };
		if (json.status === 'streaming') {
			log('vision', `push mode started (source=${SOURCE_LABEL})`);
			return true;
		}
		log('vision', `start failed: ${json.error ?? 'unknown'}`);
		return false;
	} catch (err) {
		log('vision', `voice-agent unreachable on :${VISION_PORT} — ${(err as Error).message}`);
		return false;
	}
}

async function stopVisionPush(): Promise<void> {
	try {
		await fetch(`${VISION_BASE}/vision/stop`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: '{}',
		});
		log('vision', 'push mode stopped');
	} catch {}
}

async function sendVisionFrame(buf: Buffer, mimeType = 'image/jpeg'): Promise<boolean> {
	try {
		const r = await fetch(`${VISION_BASE}/vision/frame`, {
			method: 'POST',
			headers: { 'Content-Type': mimeType },
			body: buf,
		});
		return r.ok;
	} catch {
		return false;
	}
}

// ── Task bridge: drop a text task for the agent to react to ────────────────

function writeTask(text: string): string | null {
	try {
		const stamp = Date.now();
		const path = join(TASKS_DIR, `task-${stamp}.txt`);
		writeFileSync(path, `task: ${text}\n`);
		log('task', `wrote ${path}`);
		return path;
	} catch (err) {
		log('task', `write failed: ${(err as Error).message}`);
		return null;
	}
}

// ── Bridge ─────────────────────────────────────────────────────────────────

async function start(): Promise<void> {
	if (!MENTRA_API_KEY) {
		console.error(`${ts()} [Mentra] MENTRA_API_KEY is required in .env`);
		process.exit(1);
	}
	const { AppServer } = loadSdk();

	class SutandoGlassesApp extends AppServer {
		async onSession(session: any, sessionId: string, userId: string): Promise<void> {
			log('session', `new session ${sessionId} (user ${userId})`);
			const caps = session.capabilities;
			if (caps) {
				log(
					'session',
					`device=${caps.modelName || 'unknown'} display=${caps.hasDisplay} camera=${caps.hasCamera} mic=${caps.hasMicrophone}`,
				);
			}
			// Register with DeviceSession registry (PR #739). Best-effort: bridge
			// keeps working in legacy mode if the endpoint isn't there. Per-session
			// deviceId so two simultaneous glasses pairings stay distinct.
			const deviceId = `mentra-live-${sessionId}`;
			await registerDevice(deviceId, caps);
			if (caps?.hasDisplay) {
				try { session.layouts.showTextWall('Sutando ready'); } catch {}
			}

			let frameTimer: ReturnType<typeof setInterval> | null = null;
			let streamUrls: any = null;
			let pushActive = false;

			const teardown = async () => {
				if (frameTimer) { clearInterval(frameTimer); frameTimer = null; }
				if (streamUrls) {
					try { await session.camera.stopManagedStream(); } catch {}
					streamUrls = null;
				}
				if (pushActive) { await stopVisionPush(); pushActive = false; }
				await unregisterDevice(deviceId);
			};

			// ── Start the managed video stream + poll thumbnails ──
			async function startCameraStream() {
				if (streamUrls) { log('camera', 'stream already active'); return; }
				if (!caps?.hasCamera) { log('camera', 'device has no camera'); return; }
				try {
					streamUrls = await session.camera.startManagedStream({
						quality: '720p',
						enableWebRTC: true,
					});
					log('camera', `stream up — thumbnail=${streamUrls?.thumbnailUrl ?? 'n/a'}`);
					if (caps?.hasDisplay) {
						try { session.layouts.showTextWall('Streaming'); } catch {}
					}
				} catch (err) {
					log('camera', `startManagedStream failed: ${(err as Error).message}`);
					if (caps?.hasDisplay) {
						try { session.layouts.showTextWall('Stream failed'); } catch {}
					}
					return;
				}

				pushActive = await startVisionPush();
				if (!pushActive) {
					// Voice agent isn't up — keep the camera open but skip the
					// vision pipeline. Frames will resume if the agent comes
					// back; the next interval re-attempts via the same path.
					log('camera', 'voice-agent not ready — frames will be dropped until it is');
				}

				if (streamUrls?.thumbnailUrl) {
					const tick = async () => {
						const url = streamUrls?.thumbnailUrl;
						if (!url) return;
						try {
							const res = await fetch(url);
							if (!res.ok) return;
							const buf = Buffer.from(await res.arrayBuffer());
							if (buf.byteLength < 2048) return; // skip blank/tiny frames
							if (!pushActive) {
								// Lazily try to (re-)enter push mode; voice-agent
								// may have started up after we did.
								pushActive = await startVisionPush();
								if (!pushActive) return;
							}
							const ok = await sendVisionFrame(buf, 'image/jpeg');
							if (!ok) pushActive = false; // poll will retry next tick
						} catch {
							// thumbnail fetch failed — drop this frame
						}
					};
					void tick();
					frameTimer = setInterval(tick, FRAME_INTERVAL);
					log('camera', `frames every ${FRAME_INTERVAL}ms via thumbnail`);
				}

				try {
					session.camera.onManagedStreamStatus(async (status: any) => {
						log('camera', `status=${status?.status}`);
						if (status?.status === 'disconnected' || status?.status === 'error') {
							await teardown();
						}
					});
				} catch {}
			}

			// ── Photo tap: single hi-res frame + text-input prompt ──
			try {
				session.onPhotoTaken(async (photo: any) => {
					const buf: Buffer | null = photo?.buffer
						? Buffer.isBuffer(photo.buffer) ? photo.buffer : Buffer.from(photo.buffer)
						: photo?.base64
							? Buffer.from(String(photo.base64), 'base64')
							: null;
					if (!buf || buf.byteLength === 0) {
						log('photo', 'photo arrived without a usable buffer');
						return;
					}
					log('photo', `${Math.round(buf.byteLength / 1024)}KB`);
					// Ensure push mode is on so the frame lands in vision context.
					if (!pushActive) pushActive = await startVisionPush();
					if (pushActive) {
						const ok = await sendVisionFrame(buf, photo?.mimeType || 'image/jpeg');
						if (!ok) log('photo', 'vision frame post failed');
					}
					// Always also drop a task — even without a live voice session,
					// the agent will pick it up and react.
					writeTask('I just took a photo with my glasses. What do you see?');
					if (caps?.hasDisplay) {
						try { session.layouts.showTextWall('Photo sent'); } catch {}
					}
				});
				log('system', 'photo listener registered');
			} catch (err) {
				log('system', `onPhotoTaken not available: ${(err as Error).message}`);
			}

			// ── Voice transcription: forward as a task so the agent reacts ──
			try {
				session.events.onTranscription((data: any) => {
					if (!data?.isFinal) return;
					const text = (data.text || '').trim();
					if (!text) return;
					log('voice', `user: ${text.slice(0, 80)}`);
					writeTask(text);
				});
			} catch {}

			// ── Lifecycle ──
			session.events?.onDisconnect?.(async () => {
				log('session', 'disconnected');
				await teardown();
			});

			// Start the camera stream immediately when the session opens.
			await startCameraStream();
		}
	}

	const app = new (SutandoGlassesApp as any)({
		packageName: MENTRA_PACKAGE,
		apiKey: MENTRA_API_KEY,
		port: MENTRA_PORT,
	});

	log('system', `starting on :${MENTRA_PORT} (pkg ${MENTRA_PACKAGE})`);
	await app.start();
	log('system', `ready — vision pipeline at ${VISION_BASE}`);
}

start().catch((err) => {
	console.error(`${ts()} [Mentra] fatal: ${err?.stack || err}`);
	process.exit(1);
});
