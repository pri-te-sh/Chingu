# Pixel portal UX audit

Date: 2026-09-10. Live site: https://pixel.priteshbhavsar.com/portal. Reviewed signed-in administrator experience at approximately 1512×861 desktop and 390×844 mobile viewport sizes. Local source cross-check: `5aa1b9a`, `backend/pixel/portal/index.html`.

## Assessment

The portal has a recognizable visual identity and useful functionality. Its biggest weakness is information hierarchy: operational diagnostics, setup instructions, everyday conversation and long-term records compete for the same space. Empty space and long lists are symptoms of that structure, not simply excessive padding.

Keep the face, yellow accent, retro headings and personality. Rework page composition, list presentation and mobile ordering before adding more features.

This audit covers Home, Talk, History, Memory, Pixels (Identity, Voice & Brain, Device), and Household. No messages were sent, microphone enabled, settings saved, memory modified, firmware installed or records deleted. Search text was cleared and the temporary viewport override reset. Entering Talk used its normal simulator connection. Voice Lab, security exploitation, signed-out onboarding and actual audio quality are outside this review. Mobile observations are browser viewport checks, not a physical-phone test.

## Prioritized findings

### UX01 — High: Home stretches a short chart into a very tall empty panel

**Observed:** the latency card measured about **703×1035 pixels** on desktop, but the chart and legend occupy only its upper portion. Its right-hand neighbour contains two stacked prose cards, determining the row height. The chat input starts around **1742 pixels down** on desktop and **1902 pixels down** on mobile with the current content.

**Cause:** the Home `.g21` grid stretches the left card to match the right column. Source: portal lines 29, 172–175. Separately, the empty chat box reserves 340 pixels on desktop (260 on mobile), creating another large blank region before its composer.

**Change:** move the conversation composer into the opening section. Give diagnostics a compact, content-sized card in a secondary area. Use independent card rows or explicit top alignment rather than equal-height columns for unrelated content. An empty conversation needs a short invitation and composer, not a reserved transcript-sized void.

**Acceptance:** at desktop and 390-pixel widths, a conversation action is visible without scrolling; expanding a summary never increases the chart card's height. No unrelated card is stretched merely to balance a neighbour.

### UX02 — High: History is a stream of turns rather than browsable conversations

**Observed:** **200 turn rows**, a document height of approximately **26,958 pixels**, and individual exchanges such as a greeting or one-word answer each taking a full row with diagnostics. The Days sidebar has only three entries and disappears while the long conversation column continues.

**Change:** group turns into sessions, separated by conversation breaks. Default to recent sessions with a date, device, time range and short excerpt. Open a session to read its full thread. Initially show around 20 sessions; add pagination or Load more. Keep date/device/search filters available while browsing. Put technical metadata behind a Details control per session/turn.

**Acceptance:** the default history view is useful within one or two screens; finding a conversation does not require scanning hundreds of greetings and acknowledgements.

### UX03 — High: History's “All” and search scope become misleading as data grows

**Source-confirmed:** `loadTurns()` requests the latest 500 turns, derives all day counts from that subset, and searches only the downloaded subset. There is no route to older records once the account exceeds that limit. The UI calls this “ALL.” Date/time labels are raw timestamp slices, rather than visibly localized dates/times. Source: lines 410–414.

**Change:** server-side paginated search and date/device filters, with a visible result count and scope. Use “Recent conversations” if the data is intentionally limited. Format times in the household timezone and use Today/Yesterday with full date/year where needed.

**Acceptance:** a known conversation older than 500 turns remains discoverable; results explain their scope; dates match the user's configured timezone.

### UX04 — High: Mobile Pixels places administration ahead of the selected device

**Observed:** at 390 pixels, page width is **431 pixels**, causing horizontal clipping. The selected device detail begins roughly **1561 pixels down**, below three device cards, always-expanded pairing instructions, and the administrator's firmware release list.

**Change:** on mobile, put a compact device selector first and the selected device details immediately underneath. Move “Add Pixel” into a button-driven setup flow. Move release publishing/history to a separate admin view. Fix grid min-content overflow with `minmax(0,1fr)`, appropriate `min-width:0`, and wrapping on the actual overflowing elements; hiding horizontal overflow alone would mask inaccessible content.

**Acceptance:** page scroll width never exceeds viewport width at 360/390/430 pixels; the selected device's name, status and primary controls appear within the first viewport. Admin status must not push normal device controls farther down.

### UX05 — High: Memory displays every fact as an editable, clipped input

**Observed:** 11 saved facts are already cumbersome. Long sentences disappear inside single-line inputs. Type badges, dates, pin controls and delete controls compete with the text. Facts are sorted by type but not visibly grouped into sections.

**Change:** default to read mode with wrapping text, one or two lines plus Expand when needed. Offer explicit Edit, with a multiline editor and Save/Cancel. Add filter chips such as All, Pinned, Preferences, Projects and Facts; show counts and a sort choice such as Recently updated. Keep learned/confirmed dates in secondary details. Paginate larger collections instead of rendering all editing controls at once.

**Acceptance:** users can read complete facts without focusing or horizontally scrolling an input. Editing one fact does not make the entire page look like an unsaved form.

### UX06 — Medium: Search with no matches falsely says the memory is empty

**Reproduced:** searching Memory for an unmatched term showed “Pixel hasn't learned anything yet” despite 11 existing facts. History uses the same generic “no conversations yet” outcome for filtered zero results in source.

**Change:** distinguish Loading, genuinely empty, No matches, and Error. No matches should say “No memories match this search” with Clear search; preserve total count. Load failure should provide Retry rather than leaving placeholder text.

**Acceptance:** zero search results never imply that saved records vanished. Loading does not briefly present a definitive empty-state message.

### UX07 — High: The global Pixel selector does not communicate what it controls

**Observed:** the header selects the physical Pixel, but Talk opens a browser simulator. History explicitly includes every household Pixel, while Memory is shared household-wide. The device list also contains two identically named “Simulator” entries with no visible distinguishing information.

**Change:** put scope where the action is. Talk should explicitly select “This browser” or a named device. History should have its own All Pixels/device filter. Memory should say “Shared household memory” without suggesting that selecting another device changes the records. Hide or disable irrelevant global selection, or replace it with page-specific controls. Distinguish browser simulators by user-friendly name and last used date; consider placing them under Browser sessions rather than alongside physical hardware.

**Acceptance:** a user can correctly predict the destination of a message and scope of a list from its header, without reading explanatory paragraphs.

### UX08 — Medium: Talk exposes debugging states ahead of conversation

**Observed:** “VIRTUAL PIXEL · FULL PIPELINE,” STT engine selection, five chunk-state labels, Events, and typed input share the main screen. On mobile, the face/controls/help fill the first screen; typed input follows Last Turn and Events below it.

**Change:** keep face, connection/listening state, conversation thread and composer together. Offer a clear voice control and “Type instead” at the same point. Move engine selection, chunks and events behind Diagnostics. Use plain states such as Ready, Listening, Thinking, Speaking, Reconnecting. Consider a compact face on mobile so input stays visible.

**Acceptance:** starting a typed or voice interaction is equally discoverable; no technical vocabulary is needed to understand whether Pixel is ready.

### UX09 — Medium: Summary and follow-up lists will grow without a browsing structure

**Observed:** Memory shows all daily summaries expanded in a narrow right column; just two long summaries already dominate it. Follow-ups show raw dates, including a past date, without clear Overdue/Today/Upcoming grouping. Home repeats the full daily-summary paragraph.

**Change:** Home gets a two- or three-line recap and at most three upcoming/relevant items. Memory gets a dedicated Summaries view with a date picker or collapsed dated rows. Separate follow-ups into Overdue, Today, Upcoming and Completed, with explicit due-date semantics. Distinguish a conversational follow-up from a scheduled notification/reminder; do not imply a notification service from a date badge alone.

**Acceptance:** 30 days of summaries and 50 follow-ups remain easy to browse. Default views prioritize actionable/current information; historical detail is available on demand.

### UX10 — Medium: Device maintenance and everyday personalization are mixed

**Observed:** Pixels permanently displays a lengthy “Add a Pixel” guide and every firmware release alongside the selected device. Unpair/Remove occupy the top-right of its header. The Device tab has a Save & Push button even though its visible fields are status/readout or separate command actions.

**Change:** device overview should prioritize identity, online state, customization and update availability. Use Add Pixel to launch a guided setup drawer/page. Put release history/publishing in Administration. Put Unpair/Remove in a labelled management menu with clear consequences. Render Save only for editable settings, with dirty/saving/saved feedback.

**Acceptance:** an owner who already has a paired device never has to scroll through setup instructions to reach it. Every visible Save control has a clear edit scope.

### UX11 — Medium: Typography and repeated metadata make scanning tiring

**Observed:** pixel type is used for long prose, helper copy, forms and dense history. Scanlines, low-emphasis grey text, heavy borders and small labels accumulate visual noise. Every history turn repeats expression intensity, multiple timings, duration and model name.

**Change:** retain pixel type for brand/headings and a few prominent controls; use a readable body font for prose and records. Reduce scanline strength behind text and use fewer border layers. Standardize spacing around a small scale, for example 8/12/16/24 pixels. Use row dividers and whitespace to group related content; avoid boxing every sub-element.

**Acceptance:** conversation and memory text remain comfortably readable at 100% zoom. Metadata is accessible without competing with the user's content. Perform a separate contrast and keyboard audit before claiming accessibility compliance.

### UX12 — Medium: Common controls lack clear labels, feedback and recovery

**Observed/source-confirmed:** fact delete buttons are repeated “✕” controls without descriptive accessible names in the inspected tree; facts save through input onchange rather than an explicit Save. Delete actions immediately call the API. General page-load errors are caught and logged to the console (`go()`) without a page-level error/retry interface. Navigation uses replaceState, so switching portal pages does not create normal browser Back history.

**Change:** descriptive labels such as “Delete this memory,” explicit editing state, and a visible confirmation/recovery pattern for destructive actions. Provide Undo only if the backend can really restore the data. Use inline errors with Retry. Adopt route navigation that supports Back and preserves search/selected-session position.

**Acceptance:** keyboard and screen-reader users can identify row actions; users know when changes are saved; failed requests are visible; Back returns to the prior portal view with context.

### UX13 — Medium: Household settings assume technical knowledge and contain stale guidance

**Observed:** routine name/location settings sit beside model identifiers, thinking toggles and memory extraction controls. Refresh interval and new-session interval wrap awkwardly. Members copy says invitations arrive with Google sign-in although the app already supports Google sign-in.

**Change:** group into Household details, World brief, Members, and Advanced memory settings. Use a timezone picker and units adjacent to each field. Keep invitation status accurate (“Invitations not available yet,” if so). Clarify which Save applies to which group, or use one coherent settings save bar.

**Acceptance:** basic household configuration is understandable without knowing model names, extraction passes or context windows.

## Recommended page structure

| Page | First content | Secondary content | On demand |
|---|---|---|---|
| Home | Pixel face/status + Talk action/composer | Short recap; a few follow-ups; compact world brief | Full recap, diagnostics |
| Talk | Explicit destination; thread; voice/text input | Listening/speaking state | Engine/pipeline/event diagnostics |
| History | Search + date/device scope + recent session list | Session previews | Full conversation; per-turn technical details |
| Memory | Category filters + readable facts | Pinned/recent items | Edit drawer, follow-ups view, dated summaries |
| Pixels | Compact selector + selected device | Appearance/personality; update status | Add-device flow, management actions, admin releases |
| Household | Personal/shared settings | Members and world-brief preferences | Model/extraction settings |

Keep existing top-level pages initially; fix composition and scope before committing to a larger navigation redesign. This makes the first implementation pass smaller and easier to evaluate.

## Delivery order

1. **Layout and access:** Home card stretch/composer placement; mobile Pixels overflow/order; move setup and release lists behind explicit actions.
2. **Long lists:** History session grouping and real pagination/search; Memory read/edit separation; collapsed dated summaries and follow-up groups.
3. **Clarity and feedback:** page-local scope/destination, correct empty/error states, meaningful Save controls, descriptive actions and navigation history.
4. **Visual polish:** body typography, border/scanline restraint, spacing consistency; keyboard/contrast checks and physical-phone validation.

Do not solve long pages by putting every list inside its own scroll box. That replaces one navigation problem with nested scrolling. Use page-level pagination, progressive disclosure and clear list/detail transitions.

## Suggested verification tasks after redesign

- Start a browser conversation from Home without scrolling or guessing its destination.
- Find a specific older conversation, including one beyond the latest 500 turns.
- Read, edit and save a long memory; search for a nonmatch and recover.
- Switch between two devices and immediately find the selected one's controls on a 390-pixel viewport.
- Find an overdue follow-up and a summary from last month without scanning expanded paragraphs.
- Navigate using keyboard and browser Back, keeping filters and selection intact.

Use sanitized fixtures for empty, loading, error and large-data states (for example 1,000 turns, 100 facts, 30 summaries and multiple devices). These are proposed test conditions, not measurements already performed.
