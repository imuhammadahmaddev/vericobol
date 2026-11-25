---
inclusion: fileMatch
fileMatchPattern: ["frontend/**/*.ts", "frontend/**/*.tsx", "frontend/**/*.css"]
---

# VeriCOBOL: Frontend Design Rules

## Layout
Split-screen: legacy mainframe (left) vs. modern fintech dashboard (right), with the Mathematical Parity Proof panel in the center/bottom.

## Left pane: "Legacy Mainframe" (IBM 3270)
- Background `#0A0E0C`, text and glow `#00FF66`.
- Monospace font, subtle phosphor `text-shadow`, optional scanline overlay, blinking block cursor.
- Shows COBOL source and raw GnuCOBOL stdout.

## Right pane: "Resurrected Cloud Dashboard"
- Clean modern fintech look in the Dark_Fintech_Theme (see below): dark slate surfaces, rounded cards, sans-serif font, generous spacing.
- Interactive sliders and inputs wired to the verified Python API. Live charts. Plain-English business rules. "Download Verified Python + API" button.
- Render money as strings from the API. Do not do financial math with JS `Number`; display only.

## Dark_Fintech_Theme
Applies to all Themed_Components: PipelineStepper, CloudDashboard, ExportTab, VerifiedPythonTab, AuditTrail and DynamicChart. It matches FieldSliderInput and the dark emerald ParityBanner.

| Role | Tailwind classes |
|---|---|
| Container | `bg-[#0F172A]` |
| Inner card | `bg-[#1E293B]` |
| Border | `border-slate-800` or `border-slate-700` |
| Primary text | `text-slate-100` |
| Secondary text | `text-slate-400` |
| Verified / live accent | emerald (`#10B981`) |
| Bug / drift accent | crimson (`#EF4444`) |

Do not use light surfaces (`bg-white`, `bg-slate-50`) in Themed_Components.

## Legacy_Panel (IBM 3270)
- Keep background `#0A0E0C` and text `#00FF66`. Do not apply the Dark_Fintech_Theme colors to it.
- The Legacy_Panel and `CobolSourceEditor` use `flex flex-col h-full`, with `flex-1` on the code area, so they stretch to full height.

## Parity Proof panel
- Headline such as `200 / 200 Exact Penny Matches — 0 Hallucinations`.
- Verified badge: emerald `#10B981`.
- Bug alerts: crimson `#EF4444`.
- Inspectable counterexample diff log showing inputs, COBOL output, Python output, and the auto-repair.

## Design tokens
| Token | Value |
|---|---|
| `--mainframe-bg` | `#0A0E0C` |
| `--phosphor` | `#00FF66` |
| `--verified` | `#10B981` |
| `--bug` | `#EF4444` |

## Cold-start UX
- On page load, silently call `GET {NEXT_PUBLIC_API_URL}/health` (retry with backoff).
- Show a themed status bar: "Establishing 3270 Mainframe Connection... Warming up GnuCOBOL compiler...".
- Disable run actions until healthy. Allow browsing presets while waiting.
- Cold start can take 30-60 s. Show progress, never a raw error. Show a friendly retry after a long timeout.

## General
- No login UI. Next.js App Router, TypeScript strict, Tailwind CSS.
- Accessible contrast and keyboard navigation.
