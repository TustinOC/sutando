/**
 * Thin status strip that sits below ConversationPanels in the sticky
 * chrome. Surfaces the global hotkey cheat-sheet plus three live status
 * indicators (core loop, presenter mode, meeting mode) so the user
 * always has the same at-a-glance context the legacy `#status-bar`
 * provided.
 *
 * Presenter mode takes precedence over meeting mode — the legacy
 * `renderModeBadge` suppressed the meeting pill while presenter was
 * active to avoid double-stacking. We honor that here.
 */

import CoreStatusIndicator from '@/components/atoms/core-status-indicator';
import HotkeyHints from '@/components/atoms/hotkey-hints';
import MeetingModeBadge from '@/components/atoms/meeting-mode-badge';
import PresenterModeBadge from '@/components/atoms/presenter-mode-badge';
import { useCoreStatus } from '@/hooks/useCoreStatus';
import { usePresenterMode } from '@/hooks/usePresenterMode';
import { useVoiceMode } from '@/hooks/useVoiceMode';

export default function ConversationStatusStrip() {
	const core = useCoreStatus();
	const presenter = usePresenterMode();
	const voiceMode = useVoiceMode();
	const showMeeting = !presenter && voiceMode === 'meeting';
	return (
		<div className="border-b border-(--border)/70 bg-(--surface)/60 backdrop-blur-[14px] backdrop-saturate-140">
			<div className="mx-auto flex w-full max-w-[920px] flex-wrap items-center justify-center gap-x-4 gap-y-1.5 px-5 py-1.5">
				<HotkeyHints />
				<CoreStatusIndicator status={core.status} step={core.step} />
				<PresenterModeBadge active={presenter} />
				<MeetingModeBadge active={showMeeting} />
			</div>
		</div>
	);
}
