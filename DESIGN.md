# TTS Studio design system

The web workspace is an Operate surface. Users paste text, choose a real provider capability and turn a queued task into a recording. The user selected the light, side-by-side composition.

## Palette and typography

White editor and form surfaces sit on `#f3f5f4`. Main text is `#24332e`, secondary text `#65736c`, borders `#dfe5e1`. Primary actions use `#176b5b` and hover `#0c5346`. Pale sage indicates selected tasks and completed audio. Error states use muted red; interrupted work uses amber.

Use Segoe UI, PingFang SC and Microsoft YaHei with a sans-serif fallback. Page titles are 30 px, section titles 16 px and controls 13 px; mobile titles are 25 px. Audio times and measured values use tabular numerals.

## Layout and components

The desktop shell is capped at 1256 px. The main grid gives text the flexible column and settings a 350 px column. Generation actions follow text, with the recording dock immediately below. Below 760 px, editor, settings, actions, player and history stack in reading order. Administration uses a channel list and an inline editing form.

Panels have one border, 14 px corners and no decorative shadows. Inputs have 7 px corners. Icons are consistent authored 24 px SVG line geometry. Native select, range, checkbox and file input semantics are retained. Every field has a label and every icon-only action has an accessible name.

## Behavior and review

Channel definitions determine visible controls. Stale controls are removed before submission. Native audio drives playback, seeking and volume; polling does not reset playback. Real segment progress changes into a completed recording with a restrained state-color transition. Reduced-motion preferences remove transitions.

Reviewed in a live browser at desktop and 390 px mobile widths. No horizontal overflow was observed. Real Edge synthesis, audio playback/download, MiMo preset/design/clone generation, anonymous history restoration and administrator channel create/delete were exercised. Screenshots are development-only under `.impeccable/review/`; the interface ships no raster assets. The review accepted the chosen task-oriented layout.
