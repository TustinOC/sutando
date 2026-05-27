/**
 * "● Meeting" pill — visible when the voice agent's mode sentinel is
 * "meeting" (set by switch_mode / zoom-auto-flip). Suppressed if
 * presenter mode is also active to avoid double-stacking — presenter
 * takes precedence in the legacy UI, so we mirror that here.
 */

export interface MeetingModeBadgeProps {
	active: boolean;
}

export default function MeetingModeBadge({ active }: MeetingModeBadgeProps) {
	if (!active) return null;
	return (
		<span
			className="inline-flex items-center gap-1.5 rounded-full border border-sky-400/40 bg-sky-500/12 px-2.5 py-0.5 text-[11px] font-medium text-sky-200"
			role="status"
			aria-label="Meeting mode is active"
		>
			<span aria-hidden="true">●</span>
			Meeting
		</span>
	);
}
