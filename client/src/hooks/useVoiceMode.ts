/**
 * Polls `${apiOrigin}/voice-mode` for the voice-agent mode sentinel.
 * Returns `'active'` (default) or `'meeting'`. Driven by switch_mode
 * tool / zoom-auto-flip on the backend; the React badge mirrors the
 * legacy `#mode-badge` element.
 *
 * 2s cadence matches the presenter poll — the legacy did both in one
 * tick, but two cheap fetches under different keys keeps each hook
 * single-purpose without a measurable cost.
 */

import { useEffect, useState } from 'react';
import { resolveConfig } from '@/lib/config';

const POLL_INTERVAL_MS = 2000;

export type VoiceMode = 'active' | 'meeting';

export function useVoiceMode(): VoiceMode {
	const [mode, setMode] = useState<VoiceMode>('active');

	useEffect(() => {
		const { apiOrigin } = resolveConfig();
		const controller = new AbortController();
		let cancelled = false;

		const tick = async () => {
			try {
				const res = await fetch(`${apiOrigin}/voice-mode`, { signal: controller.signal });
				if (!res.ok || cancelled) return;
				const data = (await res.json()) as { mode?: string };
				const next: VoiceMode = data.mode === 'meeting' ? 'meeting' : 'active';
				setMode(next);
			} catch (err) {
				if ((err as Error).name === 'AbortError') return;
				if (!cancelled) setMode('active');
			}
		};

		void tick();
		const id = window.setInterval(tick, POLL_INTERVAL_MS);
		return () => {
			cancelled = true;
			window.clearInterval(id);
			controller.abort();
		};
	}, []);

	return mode;
}
