/**
 * Pulsing 🎤 PRESENTER MODE pill — visible only when the /presenter
 * sentinel reports active. Mirrors the legacy `#presenter-badge` so the
 * user has a clear visual cue that Sutando's bridges (Discord, Slack,
 * Telegram) are currently muted for a talk / screen-share.
 */

export interface PresenterModeBadgeProps {
	active: boolean;
}

export default function PresenterModeBadge({ active }: PresenterModeBadgeProps) {
	if (!active) return null;
	return (
		<span
			className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/40 bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-amber-200 shadow-[0_0_12px_-4px_rgba(251,191,36,0.5)] animate-live-pulse-fast"
			role="status"
			aria-label="Presenter mode is active — notifications are muted"
		>
			<span aria-hidden="true">🎤</span>
			Presenter mode
		</span>
	);
}
