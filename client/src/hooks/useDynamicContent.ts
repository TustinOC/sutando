/**
 * Polls `${agentApiOrigin}/dynamic-content` for server-pushed cards
 * (documents, media, decision prompts, raw HTML, contextual chips).
 * The backend writes the latest payload to `state/dynamic-content.json`
 * via the `dynamic_content` skill / tool; we render whatever shows up.
 *
 * Returns `null` when there's nothing to display so the panel can
 * collapse without leaving a hole in the layout.
 *
 * Cadence matches the legacy 3s poll. Errors silent-fail to `null` —
 * a stale or missing file isn't worth a UI error toast.
 */

import { useEffect, useState } from 'react';
import { resolveConfig } from '@/lib/config';

const POLL_INTERVAL_MS = 3000;

export type DynamicContentType = 'audio' | 'image' | 'video' | 'document' | 'html';

export interface DynamicContent {
	type: DynamicContentType | string;
	title?: string;
	caption?: string;
	src?: string;
	content?: string;
	[key: string]: unknown;
}

const isMeaningful = (data: unknown): data is DynamicContent => {
	if (!data || typeof data !== 'object') return false;
	const d = data as Record<string, unknown>;
	return typeof d.type === 'string' && d.type.length > 0;
};

export function useDynamicContent(): DynamicContent | null {
	const [content, setContent] = useState<DynamicContent | null>(null);

	useEffect(() => {
		const { agentApiOrigin } = resolveConfig();
		const controller = new AbortController();
		let cancelled = false;

		const tick = async () => {
			try {
				const res = await fetch(`${agentApiOrigin}/dynamic-content`, { signal: controller.signal });
				if (!res.ok || cancelled) return;
				const data = await res.json();
				setContent(isMeaningful(data) ? data : null);
			} catch (err) {
				if ((err as Error).name === 'AbortError') return;
				if (!cancelled) setContent(null);
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

	return content;
}
