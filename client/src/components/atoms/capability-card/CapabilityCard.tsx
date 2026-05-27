/**
 * "What I can do" panel — six dt/dd capability rows shown in the empty
 * state of the conversation page. Ported from the legacy HTML UI's
 * `.caps-panel`. Pure presentational atom.
 */

interface Capability {
	readonly heading: string;
	readonly detail: string;
}

const CAPABILITIES: readonly Capability[] = [
	{ heading: 'Voice + screen', detail: "control any Mac app by voice; read what's on screen" },
	{ heading: 'Comms', detail: 'phone, iMessage, Telegram, Discord' },
	{ heading: 'Calendar + email', detail: 'Gmail / Google Calendar; draft replies; book meetings' },
	{ heading: 'Autonomous work', detail: 'ships code, summarizes news, runs benchmarks' },
	{ heading: 'Fleet', detail: 'coordinates with other Sutandos under access tiers' },
	{ heading: 'Skills', detail: 'try /list-skills to see all installed' },
];

export default function CapabilityCard() {
	return (
		<section
			className="w-full rounded-2xl border border-(--border)/70 bg-(--surface-elev)/60 px-5 py-4 text-left shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_40px_-26px_rgba(0,0,0,0.45)]"
			aria-label="What Sutando can do"
		>
			<p className="m-0 mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-(--text-faint)">
				What I can do
			</p>
			<dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
				{CAPABILITIES.map((c) => (
					<div key={c.heading} className="flex flex-col gap-0.5">
						<dt className="text-[12px] font-medium text-(--text)">{c.heading}</dt>
						<dd className="m-0 text-[12px] leading-snug text-(--text-muted)">{c.detail}</dd>
					</div>
				))}
			</dl>
		</section>
	);
}
