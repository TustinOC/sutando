/**
 * Polls `${agentApiOrigin}/core-status` for the proactive-loop status
 * (`{status: 'idle' | 'running', step?: string}`). Returns the latest
 * snapshot — and `'unknown'` until the first response, so the indicator
 * stays hidden instead of flashing "idle" on load.
 *
 * Mirrors the legacy 3-second poll cadence so the new React badge feels
 * exactly like the old `#core-status-bar`.
 */

import { useEffect, useState } from 'react';
import { resolveConfig } from '@/lib/config';

const POLL_INTERVAL_MS = 3000;

export type CoreLoopStatus = 'idle' | 'running' | 'unknown';

export interface CoreStatus {
	status: CoreLoopStatus;
	step: string;
}

const INITIAL: CoreStatus = { status: 'unknown', step: '' };

export function useCoreStatus(): CoreStatus {
	const [snapshot, setSnapshot] = useState<CoreStatus>(INITIAL);

	useEffect(() => {
		const { agentApiOrigin } = resolveConfig();
		const controller = new AbortController();
		let cancelled = false;

		const tick = async () => {
			try {
				const res = await fetch(`${agentApiOrigin}/core-status`, { signal: controller.signal });
				if (!res.ok || cancelled) return;
				const data = (await res.json()) as { status?: string; step?: string };
				const status: CoreLoopStatus = data.status === 'running' ? 'running' : 'idle';
				setSnapshot({ status, step: data.step ?? '' });
			} catch (err) {
				if ((err as Error).name === 'AbortError') return;
				if (!cancelled) setSnapshot((prev) => (prev.status === 'unknown' ? prev : { status: 'unknown', step: '' }));
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

	return snapshot;
}
