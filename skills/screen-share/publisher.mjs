#!/usr/bin/env node
/**
 * publisher.mjs — Mac-side LiveKit screen publisher for AG2 Space calls.
 *
 * Joins the call's LiveKit room as a second device of the agent and publishes
 * the FULL Mac screen as a SCREENSHARE video track, so the owner watches the
 * agent operate the computer live (room !IRFFSZUgpGLulIVNbg design; owner
 * decision 2026-06-12: full screen, not a single window).
 *
 * Capture path: ffmpeg avfoundation (screen device) → rawvideo I420 frames on
 * stdout → VideoSource.captureFrame. The terminal this runs from must hold
 * macOS Screen Recording permission (same requirement as
 * src/screen-capture-server.py).
 *
 * Auth: takes a ready LiveKit JWT — this process NEVER sees a Matrix token
 * (WORKER-PROTOCOL: the relay/EC2 side is the only Matrix speaker). Mint via
 * mint-jwt-via-ec2.sh (exchange runs server-side, only {url, jwt} crosses).
 *
 * Usage:
 *   node publisher.mjs --url wss://... --token <lk-jwt> [--fps 10]
 *                      [--width 1280] [--screen-index auto]
 * Stops on: SIGTERM/SIGINT (unpublish + disconnect), or automatically when
 * the last remote participant leaves (call ended).
 */
import { spawn, spawnSync } from 'node:child_process';
import process from 'node:process';
import { createRequire } from 'node:module';
import {
  Room, RoomEvent, LocalVideoTrack, TrackPublishOptions, TrackSource,
  VideoSource, VideoFrame, VideoBufferType,
} from '@livekit/rtc-node';

// Bundled static ffmpeg — /opt/homebrew is owned by another account on this
// multi-user Mac, so a system ffmpeg can't be assumed.
const FFMPEG = createRequire(import.meta.url)('ffmpeg-static');

const args = {};
for (let i = 2; i < process.argv.length; i += 2) {
  args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
}
const URL_ = args.url;
const TOKEN = args.token;
const FPS = Number(args.fps || 10);
const OUT_W = Number(args.width || 1280);
if (!URL_ || !TOKEN) {
  console.error('usage: publisher.mjs --url <wss://sfu> --token <lk-jwt> [--fps 10] [--width 1280]');
  process.exit(2);
}

const log = (m) => console.log(`[screen-share] ${new Date().toISOString()} ${m}`);

/** Native screen size via ffmpeg probe (first "Capture screen" device). */
function detectScreen() {
  const r = spawnSync(FFMPEG, ['-f', 'avfoundation', '-list_devices', 'true', '-i', ''],
    { encoding: 'utf8' });
  const m = String(r.stderr || '').match(/\[(\d+)\]\s+Capture screen \d+/);
  if (!m) throw new Error('no "Capture screen" avfoundation device — Screen Recording permission missing?');
  return Number(m[1]);
}

const screenIdx = args['screen-index'] && args['screen-index'] !== 'auto'
  ? Number(args['screen-index']) : detectScreen();

// Probe native resolution from a 1-frame capture's stderr (avfoundation
// reports the stream size), then derive even-sized output dims.
function probeNative(idx) {
  const r = spawnSync(FFMPEG, ['-f', 'avfoundation', '-capture_cursor', '1',
    '-i', `${idx}:none`, '-frames:v', '1', '-f', 'null', '-'],
    { encoding: 'utf8', timeout: 15000 });
  // avfoundation reports the stream as "rawvideo (...), uyvy422, WxH, ..."
  const m = String(r.stderr || '').match(/Stream #0.*?(\d{3,5})x(\d{3,5})/);
  if (!m) throw new Error('could not probe native screen resolution');
  return { w: Number(m[1]), h: Number(m[2]) };
}

const native = probeNative(screenIdx);
const W = OUT_W % 2 ? OUT_W - 1 : OUT_W;
const H = (() => { const h = Math.round((native.h / native.w) * W); return h % 2 ? h - 1 : h; })();
log(`screen ${screenIdx}: native ${native.w}x${native.h} → publishing ${W}x${H} @ ${FPS}fps`);

const room = new Room();
const source = new VideoSource(W, H);
const track = LocalVideoTrack.createVideoTrack('mac-screen', source);

let ffmpeg = null;
let stopping = false;

async function stop(reason) {
  if (stopping) return;
  stopping = true;
  log(`stopping (${reason})`);
  try { ffmpeg?.kill('SIGTERM'); } catch {}
  try { await room.localParticipant?.unpublishTrack(track.sid); } catch {}
  try { await room.disconnect(); } catch {}
  process.exit(0);
}
process.on('SIGTERM', () => stop('SIGTERM'));
process.on('SIGINT', () => stop('SIGINT'));

await room.connect(URL_, TOKEN, { autoSubscribe: false, dynacast: true });
log(`connected as ${room.localParticipant?.identity} to room "${room.name}" `
  + `(${room.remoteParticipants.size} remote participant(s))`);

// Call over → leave. Small grace so a brief everyone-reconnect doesn't kill us.
room.on(RoomEvent.ParticipantDisconnected, () => {
  if (room.remoteParticipants.size === 0) {
    setTimeout(() => { if (room.remoteParticipants.size === 0) stop('room empty'); }, 10_000);
  }
});
room.on(RoomEvent.Disconnected, () => stop('room disconnected'));

const publishOpts = new TrackPublishOptions({ source: TrackSource.SOURCE_SCREENSHARE });
await room.localParticipant.publishTrack(track, publishOpts);
log('screenshare track published');

// ffmpeg: continuous I420 frames, exact W×H so each frame is W*H*1.5 bytes.
const frameBytes = W * H * 1.5;
ffmpeg = spawn(FFMPEG, [
  '-f', 'avfoundation', '-capture_cursor', '1',
  '-framerate', String(FPS), '-i', `${screenIdx}:none`,
  // avfoundation screen devices report a bogus 1000k tbr and ffmpeg then
  // duplicates frames to match — the fps filter pins the real output cadence.
  '-vf', `fps=${FPS},scale=${W}:${H}`, '-pix_fmt', 'yuv420p',
  '-f', 'rawvideo', 'pipe:1',
], { stdio: ['ignore', 'pipe', 'pipe'] });
ffmpeg.stderr.on('data', (d) => {
  const s = String(d);
  if (/error|denied|permission/i.test(s)) log(`ffmpeg: ${s.trim().slice(0, 300)}`);
});
ffmpeg.on('exit', (code) => { if (!stopping) { log(`ffmpeg exited (${code})`); stop('capture ended'); } });

let buf = Buffer.alloc(0);
let frames = 0;
ffmpeg.stdout.on('data', (chunk) => {
  buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
  while (buf.length >= frameBytes) {
    const frameBuf = buf.subarray(0, frameBytes);
    buf = buf.subarray(frameBytes);
    const frame = new VideoFrame(new Uint8Array(frameBuf), W, H, VideoBufferType.I420);
    source.captureFrame(frame);
    frames += 1;
    if (frames === 1) log('first frame captured + pushed');
    if (frames % (FPS * 60) === 0) log(`${frames} frames pushed`);
  }
});
