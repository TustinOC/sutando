/**
 * Server-pushed content surface ported from the legacy `#dynamic-region`.
 * Renders whichever shape the `dynamic_content` skill last wrote to
 * `state/dynamic-content.json` (served by agent-api at /dynamic-content):
 *
 *   - audio / image / video → media player or <img>; YouTube URLs are
 *     embedded via iframe to match the legacy behavior
 *   - document             → preformatted text block (whitespace preserved)
 *   - html                 → raw HTML interpolation (same trust model as
 *                            the legacy — the skill that writes this file
 *                            is the user's own automation)
 *   - everything else      → title + content as plain text
 *
 * Hidden entirely when there's nothing to show so the conversation page
 * doesn't reserve dead space.
 */

import { useMemo } from 'react';
import { useDynamicContent, type DynamicContent } from '@/hooks/useDynamicContent';
import { resolveConfig } from '@/lib/config';

const youtubeId = (src: string): string | null => {
	const m = src.match(/(?:v=|youtu\.be\/)([\w-]+)/);
	return m ? m[1] : null;
};

const resolveMediaSrc = (src: string | undefined, agentApiOrigin: string): string => {
	if (!src) return '';
	if (src.startsWith('http://') || src.startsWith('https://')) return src;
	return `${agentApiOrigin}/media/${src.replace(/^\/+/, '')}`;
};

const PANEL_BASE =
	'rounded-2xl border border-(--border)/70 bg-(--surface)/80 px-5 py-4 shadow-[0_18px_40px_-26px_rgba(0,0,0,0.45)]';

interface BodyProps {
	content: DynamicContent;
	mediaSrc: string;
}

function MediaTitle({ title }: { title?: string }) {
	if (!title) return null;
	return <div className="mb-2 text-sm font-semibold text-(--text)">{title}</div>;
}

function MediaCaption({ caption }: { caption?: string }) {
	if (!caption) return null;
	return <div className="mt-1.5 text-[11px] text-(--text-faint)">{caption}</div>;
}

function ContentBody({ content, mediaSrc }: BodyProps) {
	switch (content.type) {
		case 'audio':
			return (
				<>
					<MediaTitle title={content.title} />
					{/* biome-ignore lint/a11y/useMediaCaption: skill chooses captions when relevant */}
					<audio controls autoPlay className="w-full">
						<source src={mediaSrc} />
					</audio>
					<MediaCaption caption={content.caption} />
				</>
			);
		case 'image':
			return (
				<>
					<MediaTitle title={content.title} />
					<img src={mediaSrc} alt={content.title ?? ''} className="max-w-full rounded-lg" />
					<MediaCaption caption={content.caption} />
				</>
			);
		case 'video': {
			const yt = content.src ? youtubeId(content.src) : null;
			if (yt) {
				return (
					<>
						<MediaTitle title={content.title} />
						<iframe
							className="aspect-video w-full rounded-lg border-0"
							src={`https://www.youtube.com/embed/${yt}`}
							title={content.title ?? 'YouTube video'}
							allowFullScreen
						/>
						<MediaCaption caption={content.caption} />
					</>
				);
			}
			return (
				<>
					<MediaTitle title={content.title} />
					{/* biome-ignore lint/a11y/useMediaCaption: skill chooses captions when relevant */}
					<video controls autoPlay className="max-w-full rounded-lg">
						<source src={mediaSrc} />
					</video>
					<MediaCaption caption={content.caption} />
				</>
			);
		}
		case 'document':
			return (
				<>
					<MediaTitle title={content.title} />
					<div className="whitespace-pre-wrap text-[14px] leading-relaxed text-(--text)">
						{content.content ?? ''}
					</div>
					<MediaCaption caption={content.caption} />
				</>
			);
		case 'html':
			// Same trust model as the legacy `renderDynamicContent('html')`:
			// the skill writing dynamic-content.json is the user's own
			// automation, not an untrusted producer.
			return (
				<div
					className="text-[14px] leading-relaxed text-(--text)"
					// biome-ignore lint/security/noDangerouslySetInnerHtml: matches legacy trust model
					dangerouslySetInnerHTML={{ __html: content.content ?? '' }}
				/>
			);
		default:
			return (
				<>
					<MediaTitle title={content.title} />
					<p className="m-0 text-[14px] leading-relaxed text-(--text-muted)">
						{content.content ?? JSON.stringify(content)}
					</p>
				</>
			);
	}
}

export default function DynamicContentPanel() {
	const content = useDynamicContent();
	const { agentApiOrigin } = useMemo(() => resolveConfig(), []);
	if (!content) return null;
	const mediaSrc = resolveMediaSrc(content.src, agentApiOrigin);
	return (
		<aside
			className={`${PANEL_BASE} mx-auto mb-4 w-full max-w-[920px]`}
			role="region"
			aria-label="Dynamic content"
		>
			<ContentBody content={content} mediaSrc={mediaSrc} />
		</aside>
	);
}
