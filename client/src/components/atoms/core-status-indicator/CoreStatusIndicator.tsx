/**
 * "Core: idle / Core: running… <step>" indicator. Mirrors the legacy
 * `#core-status-bar` span; data is fetched via `useCoreStatus()`.
 */

export interface CoreStatusIndicatorProps {
	status: 'idle' | 'running' | 'unknown';
	step: string;
}

export default function CoreStatusIndicator({ status, step }: CoreStatusIndicatorProps) {
	if (status === 'unknown') return null;
	const isRunning = status === 'running';
	const dotCls = isRunning ? 'bg-emerald-400 animate-live-pulse' : 'bg-(--text-faint)';
	const textCls = isRunning ? 'text-emerald-200' : 'text-(--text-faint)';
	const label = isRunning ? `Core: ${step || 'working'}` : 'Core: idle';
	return (
		<span
			className={`inline-flex max-w-[260px] items-center gap-1.5 truncate text-[11px] ${textCls}`}
			title={label}
		>
			<span className={`h-[6px] w-[6px] shrink-0 rounded-full ${dotCls}`} />
			<span className="truncate">{label}</span>
		</span>
	);
}
