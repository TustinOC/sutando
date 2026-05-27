/**
 * Polls `${apiOrigin}/presenter` for the presenter-mode sentinel.
 * Returns `true` when the sentinel exists and the ISO expiry inside it
 * hasn't elapsed. Silent-fails to `false` when the endpoint isn't
 * reachable — same behavior as the legacy `_PRESENTER_URL` poll.
 *
 * 2s cadence matches the legacy `setInterval(..., 2000)` so the pulsing
 * badge feels the same.
 */

import { useEffect, useState } from 'react';
import { resolveConfig } from '@/lib/config';

const POLL_INTERVAL_MS = 2000;

export function usePresenterMode(): boolean {
	const [active, setActive] = useState(false);

	useEffect(() => {
		const { apiOrigin } = resolveConfig();
		const controller = new AbortController();
		let cancelled = false;

		const tick = async () => {
			try {
				const res = await fetch(`${apiOrigin}/presenter`, { signal: controller.signal });
				if (!res.ok || cancelled) return;
				const data = (await res.json()) as { active?: boolean };
				setActive(!!data.active);
			} catch (err) {
				if ((err as Error).name === 'AbortError') return;
				if (!cancelled) setActive(false);
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

	return active;
}
