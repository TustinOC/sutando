/**
 * Global hotkey cheat-sheet strip. The hotkeys themselves are wired in
 * the menu-bar HUD (Swift) / `useAgentSse` SSE bridge — this atom is a
 * pure visual reminder so users discover them without consulting docs.
 */

interface Hotkey {
	readonly combo: string;
	readonly label: string;
}

const HOTKEYS: readonly Hotkey[] = [
	{ combo: '⌃C', label: 'drop context' },
	{ combo: '⌃S', label: 'drop screenshot' },
	{ combo: '⌃V', label: 'voice' },
	{ combo: '⌃M', label: 'mute' },
];

const KBD_CLASS =
	'inline-flex items-center justify-center rounded-md border border-(--border) bg-(--surface-elev)/70 px-1.5 py-0.5 font-mono text-[11px] font-medium text-(--text)';

export default function HotkeyHints() {
	return (
		<ul
			className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-(--text-muted)"
			aria-label="Global hotkeys"
		>
			{HOTKEYS.map((h, i) => (
				<li key={h.combo} className="flex items-center gap-1.5">
					<kbd className={KBD_CLASS}>{h.combo}</kbd>
					<span>{h.label}</span>
					{i < HOTKEYS.length - 1 ? (
						<span aria-hidden="true" className="ml-2 text-(--text-faint)">
							·
						</span>
					) : null}
				</li>
			))}
		</ul>
	);
}
