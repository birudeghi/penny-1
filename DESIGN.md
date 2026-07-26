# Penny design

The care record is a document before it is an app: paper ground, hairline rules, and one hue per
entry kind carried on a small timeline node rather than on a filled card.

That sentence is the whole design, and it is lifted verbatim from the comment that opens the brand
layer in `globals.css`. Everything below is the working-out.

## Where this came from, and what it is

This design does not exist in `frontend/`. It exists in the Next.js prototype at `penny/feed/` on
the `front-end` branch, and its reasoning lives in that prototype's component docstrings. The
prototype is Next 16 + Tailwind v4 + shadcn; the shipped app is React + Vite + CSS modules. So this
file is the **design intent and its justification**, not a description of the current stylesheet.
Nothing here is implemented in `frontend/` yet.

It is written down for one reason: the reasoning was one branch away from being lost, and the
reasoning is the valuable part. A hex value can be re-picked in an afternoon. The argument for *why
the card came back*, or *why there are no filter tabs*, cannot — it was paid for in iterations that
are no longer visible in the diff.

Where the source says something better than a paraphrase would, it is quoted. Read the quotes as
the normative part; the surrounding prose is scaffolding.

Sources, all reachable as `git show origin/front-end:penny/feed/<path>`:

| Decision | Source |
| --- | --- |
| Token system, light and dark | `src/app/globals.css` |
| Page shell, one measure | `src/app/page.tsx` |
| One face, light by default | `src/app/layout.tsx` |
| No filters; year grouping | `src/components/timeline.tsx` |
| Entry grid, spine, card, date split | `src/components/timeline-entry.tsx` |
| The node | `src/components/kind-node.tsx` |
| Chrome; person header | `src/components/top-bar.tsx` |
| Summary closed until asked | `src/components/clinical-summary.tsx` |
| Evidence folded away | `src/components/evidence.tsx` |
| Standing facts beside the timeline | `src/components/context-column.tsx` |
| Composer overlays, never pushes | `src/components/add-update.tsx` |
| Person header and vitals band rationale | `src/lib/data.ts` |

---

## 1. Colour

All colour is `oklch`. Two layers: a shadcn-shaped neutral base, and a Penny brand layer on top of
it. Both are exposed to Tailwind through `@theme inline`, so `--symptom` is usable as
`text-symptom` / `bg-symptom`.

### 1.1 The ground, the band, and the rule

These three carry the "document" half of the thesis. They are the only surfaces that are not white
and not text, and each has a job.

```css
--ground: oklch(0.963 0.002 255);   /* the page */
--band:   oklch(0.935 0.003 255);   /* surfaces that recede */
--rule:   oklch(0.897 0.004 255);   /* every hairline */
```

The value of `--ground` is the one number in the system with a written post-mortem, and it is worth
keeping:

> Neutral grey, and a real step down from the cards. The warm near-white this replaced sat at
> L 0.994 against the cards' 1.0 — six thousandths of separation, which read as a colour cast
> rather than as a background, and left the white composer invisible on it. 0.963 is far enough
> that every white surface lifts, and neutral so nothing goes yellow. `band` is a further step down
> again, for the surfaces that recede.

Two rules follow from that and are load-bearing elsewhere in the system:

- **Cards are pure white (`--card: oklch(1 0 0)`) and the page is not.** This is what lets a card
  have an edge without a border (§4.6), and what makes the collapsed composer read as the first
  item in the list rather than as chrome.
- **`--ground` is also the node's ring colour.** The spine passes *behind* each disc because the
  disc wears a 4px ring in the page colour (§3.3). Change `--ground` and the spine breaks.

`--band` is used for: the clinical summary block, the vitals `<dl>`, the photo placeholder behind an
entry image, and hover fills on quiet buttons. `--rule` is used for: the spine, the year-marker dot,
card hover borders, the citation-chip border, and the horizontal separators *inside* the summary
panel — never as a full-width rule across the page (§5.2).

In dark, the three collapse toward the base palette and `--rule` becomes an alpha so it works over
any card:

```css
--ground: oklch(0.16 0.006 250);
--band:   oklch(0.22 0.008 250);
--rule:   oklch(1 0 0 / 12%);
```

### 1.2 The semantic hues

One hue per entry kind. They appear on the timeline node, on the composer's kind chips, and on
inline text where a kind is named. They are never used as a card fill.

| Token | Light | Dark | What it is FOR |
| --- | --- | --- | --- |
| `--penny` | `oklch(0.34 0.062 250)` | `oklch(0.78 0.07 250)` | Penny's own voice and Penny's own actions: the wordmark avatar, every action link ("Copy", "Download PDF", "See the evidence", "Generate clinical summary"), the composer's send button, Penny's chat bubbles. **Medication** has no hue of its own and uses this one, so the timeline's five kinds are carried on four colours plus the house blue. In light it is byte-identical to `--primary` and `--ring`. |
| `--symptom` | `oklch(0.52 0.135 38)` | `oklch(0.74 0.112 44)` | Something is wrong, or has changed for the worse: the symptom node, the "Watch for" heading in the summary, the `Changed` flag and left border on a medication, an allergy in the vitals band. In light it is byte-identical to `--destructive`. The highest chroma in the palette by a margin, and the only hue allowed to catch the eye. |
| `--appointment` | `oklch(0.6 0.105 74)` | `oklch(0.8 0.098 82)` | Something scheduled: the appointment node. |
| `--wellbeing` | `oklch(0.49 0.082 155)` | `oklch(0.75 0.075 155)` | How she is in herself, as distinct from her symptoms: the wellbeing node, and the play control and waveform on a voice note. |
| `--admin` | `oklch(0.52 0.014 250)` | `oklch(0.72 0.018 250)` | Paperwork — letters, forms, filings. Chroma 0.014 is barely a colour at all: admin gets a node so it is findable on the spine, not so it is noticed. |
| `--person-owen` | `oklch(0.47 0.088 305)` | `oklch(0.75 0.078 302)` | The one person hue that is not already a kind hue (§1.2, *people borrow the kind hues*). |

**People borrow the kind hues.** There is no separate person palette: Yuval is `symptom`, Liz is
`appointment`, Matthew is `wellbeing`, Penny is `penny`, and only Owen needs a colour of his own.
That is a deliberate economy — a chat bubble tinted `appointment-wash` is Liz, and the page never
has to hold more than six hues at once.

Lightness is chosen so every hue passes as body text on white in light mode (L 0.49–0.6, with
`--penny` darker still at 0.34), and inverts to L 0.72–0.8 in dark. Chroma is capped low across the
board; `--symptom` at 0.135 is the ceiling.

### 1.3 The washes

Every hue has a wash: the same hue at ~L 0.96 with chroma dropped to a fifth. Washes are the *fill*
form of a hue, used where a surface needs to be identified rather than emphasised.

```css
--penny-wash:       oklch(0.962 0.014 250);
--symptom-wash:     oklch(0.965 0.020 38);
--appointment-wash: oklch(0.968 0.024 78);
--wellbeing-wash:   oklch(0.965 0.016 155);
--admin-wash:       oklch(0.966 0.004 250);
--owen-wash:        oklch(0.963 0.018 305);
```

They fill exactly three things:

1. **Chat bubbles in the evidence drawer** — tinted by *sender*, so a thread is readable as a
   conversation without printing four avatars' worth of names.
2. **The selected kind chip in the composer** — wash fill plus the hue as text.
3. **The person avatar in the page header** — `bg-penny-wash` with `text-penny`.

Note the wash lightnesses sit at 0.962–0.968, i.e. *above* `--ground` (0.963) and well above
`--band` (0.935). A wash is a lift off the page, never a recess. `--admin-wash` at chroma 0.004 is
functionally the ground with a hint of temperature — admin is quiet in both forms.

In dark the washes become L 0.25–0.27, a lift off a 0.16 ground, keeping the relationship even
though the absolute values invert.

### 1.4 The neutral base

Standard shadcn slots, all on hue 250 so the neutrals share a temperature with `--penny`:

| Token | Light | Dark |
| --- | --- | --- |
| `--background` | `oklch(1 0 0)` | `oklch(0.16 0.006 250)` |
| `--foreground` | `oklch(0.205 0.005 250)` | `oklch(0.97 0.002 250)` |
| `--card` / `--popover` | `oklch(1 0 0)` | `oklch(0.21 0.008 250)` |
| `--primary` | `oklch(0.34 0.062 250)` | `oklch(0.78 0.062 250)` |
| `--muted` / `--secondary` / `--accent` | `oklch(0.968 0.003 250)` | `oklch(0.27 0.008 250)` |
| `--muted-foreground` | `oklch(0.55 0.012 250)` | `oklch(0.7 0.012 250)` |
| `--destructive` | `oklch(0.52 0.135 38)` | `oklch(0.7 0.14 38)` |
| `--border` / `--input` | `oklch(0.918 0.004 250)` | `oklch(1 0 0 / 12%)` / `15%` |
| `--ring` | `oklch(0.34 0.062 250)` | `oklch(0.62 0.05 250)` |

`--muted-foreground` at L 0.55 does a lot of work: every date, every summary paragraph, every
provenance line and every eyebrow label is muted, and only headlines, values and links are
foreground. The page has essentially two text weights of *colour* before it has any of *weight*.

`--background` is white but the body is not on it — `@layer base` sets `body { @apply bg-ground }`.
`--background` survives as the knockout colour for glyphs in dark mode (`dark:text-background`).

### 1.5 Radius

`--radius: 0.625rem` (10px), with the scale derived rather than listed: `sm` ×0.6 (6px), `md` ×0.8
(8px), `lg` ×1 (10px), `xl` ×1.4 (14px), `2xl` ×1.8 (18px).

In practice: `xl` (14px) for the two card-shaped things — the entry card and the composer panel;
`lg` (10px) for blocks that are not cards — the summary panel, the vitals band, an entry photograph;
`full` for nodes, avatars, chips and pills.

---

## 2. Type

**One face.** From `layout.tsx`:

> The only face on the page — wordmark, headings and body all set in it.

Inter, loaded as `--font-sans`, `antialiased`, applied to `html`. There is a `--font-mono` slot but
it is used once, for a voice-note duration.

### 2.1 The scale, as actually used

Sizes are set in explicit pixels rather than by step name, because the scale is a reading document's
scale and not a UI kit's — the gaps between 12.5, 13 and 13.5 are real decisions about which line
recedes.

| px | Weight / treatment | Where |
| --- | --- | --- |
| 30 | semibold, `tracking-tight`, `leading-none` | The person's name — the only h1 |
| 22 | semibold | Initial in the 68px person avatar |
| 19 | semibold, `tracking-tight`, `leading-none` | Wordmark "Penny"; clinical summary title |
| 17.5 | semibold, `tracking-tight`, `leading-snug`, `text-balance` | **Entry headline** — and the composer's headline input, at the same size and weight, so you write into the shape you will read |
| 15.5 | regular, `leading-relaxed`, `text-balance`, max 76ch | Clinical summary lead paragraph |
| 14.5 | regular, `leading-relaxed`, max 76ch | Clinical summary section bodies. Also the muted "84 · Croydon" beside the name, at `tracking-normal` |
| 14 | regular, `leading-relaxed`, muted, max 68ch | **Entry summary.** Also: date-gutter day (semibold, `leading-none`, `tracking-tight`), medication name (semibold), condition name (medium), composer detail field, collapsed composer bar (muted) |
| 13.5 | regular | Top-bar search; the summary trigger; vitals values (`leading-snug`); carer names (medium); summary list items (`leading-snug`) |
| 13 | regular, muted | Weekday under the date (`leading-none`); the entry's time (`tabular-nums`); Copy / Download PDF / panel actions; chat-bubble text (`leading-snug`) |
| 12.5 | regular, muted | **Provenance line**; the Evidence trigger (medium); summary meta ("Generated … · 27 entries"); medication and condition detail; the ⌘K key |
| 12 | semibold, `tracking-[0.06em]`, muted | **Year marker.** Also composer kind chips (medium) and the composer's "Filed to today" |
| 11.5 | semibold, `tracking-wide` | Top-bar avatar initials. Also citation chips and the summary disclaimer, both regular and muted |
| 10.5 | semibold, `tracking-[0.1em]`, muted | **The eyebrow** — every panel label ("Medications", "Living with", "Cared by"), every clinical-summary section heading, every vitals label |
| 10 | bold, `tracking-wide`, in the person's hue | Sender name on a chat bubble |
| 9.5 | bold, `tracking-[0.09em]`, `--symptom` | The `Changed` flag on a medication |
| 9 | mono, `tabular-nums`, muted | Voice-note duration |

### 2.2 The weight and tracking rules

Four weights, and only four: **semibold (600)** for anything that is a name, a value or a heading;
**medium (500)** for interactive labels — the Evidence trigger, kind chips, carer names, a link
inside a vitals value; **bold (700)** only at 9.5–10px, where semibold stops registering as emphasis
at all; **regular** for every sentence.

There is no light weight and no italic anywhere.

Tracking moves opposite to size: `tracking-tight` on everything 14px and up that is set in semibold,
`tracking-normal` in the one place a small run of text sits inside a tight heading and must not
inherit it, and positive tracking (0.06em → 0.1em) on the small caps-ish labels at 9.5–12px. The
10.5px eyebrow at 0.1em is the system's one repeating ornament; it appears in the context column,
the summary sections, and the vitals band, and it is what makes those three read as the same kind of
object.

`tabular-nums` on times, `text-balance` on the two pieces of prose that are allowed to be
typographically nice (entry headline, summary lead), and hard measures — 68ch on an entry summary,
76ch on summary prose, 520px on the evidence thread — so no line ever runs the width of a 1400px
page.

---

## 3. The timeline layout contract

This is the part with the most precise commitments, and the part most easily broken by an
unsuspecting edit.

### 3.1 The page shell

```
TopBar        sticky top-0, z-40, h-72px, bg-ground/75, backdrop-blur-md, NO border
container     mx-auto max-w-[1400px] px-8 xl:px-12 pb-28
  PersonHeader   pt-9 pb-6 — 68px avatar + name row; the summary panel opens full-width beneath
  flex gap-14
    Timeline       min-w-0 flex-1
    ContextColumn  w-[330px] shrink-0, sticky top-6, max-h-[calc(100vh-3rem)], self-start, hidden until lg
```

The container is the whole argument for the page:

> One measure for the whole document: the name and the timeline both start on the same left edge,
> which is what makes the page read as a record rather than as stacked widgets.

The context column is sticky, and `self-start` is load-bearing rather than cosmetic:

> Sticky and self-start: these facts stay true for every year the timeline scrolls through, so they
> stay on screen instead of being left behind at the top of a history that runs for pages.
> `self-start` is what stops the flex row stretching the column to full height and killing the
> stick.

### 3.2 The grid

Every row of the timeline — entries and year markers alike — is the same three columns:

```
grid-cols-[104px_26px_minmax(0,1fr)]  gap-x-5
│─── 104 ───│─20─│ 26 │─20─│──── content ────
 date gutter       node        the card
```

Which puts the node column's centre at `104 + 20 + 13 = 137px`, and that is exactly where the spine
is drawn: `left-[137px] w-px -translate-x-1/2`. The 137 is not a magic number to be nudged; it is
derived from the first two columns and the gap, and if any of those change it must be recomputed or
the spine will miss the discs.

The third column is `minmax(0, 1fr)`, not `1fr`, and the entry card is `min-w-0` — without both, a
long unbroken token in a summary blows the grid out instead of wrapping.

Vertical: an entry is `pb-7`; the date gutter starts at `pt-3.5`; the node wrapper is `mt-3`; the
card is `p-5`. Those three offsets are what put the date's cap-height, the node's centre and the
card's first line of text on approximately one optical line.

### 3.3 The spine and the node

The spine is one `w-px` element per row, absolutely positioned, `inset-y-0`:

> The spine runs the full height of every entry, so consecutive nodes join into one unbroken line
> down the page.

It is per-row rather than one element down the whole timeline so a row can own its own segment — which
is what lets the year marker start its spine lower down (§3.4) without any row knowing about its
neighbours.

The node is a filled disc with the glyph knocked out in white:

> Luma puts a plain grey dot on the spine. The dot is the right size and the right silhouette, so
> this keeps it and fills it — one solid disc in the kind's hue. It signposts the type from the left
> margin without the ring and the tinted glyph making a small object out of three colours.

Sizes:

| | disc | glyph | stroke |
| --- | --- | --- | --- |
| `md` (the spine) | 26px | 13px | 2.1 |
| `sm` (composer chips) | 18px | 10px | 2.1 |

`md`'s 26px is the grid's middle column, exactly. The glyph is always `text-white`, flipping to
`dark:text-background` so it knocks out of the disc in either theme.

The ring exists only on the spine, and only for one reason:

> Adds a ring in the ground colour so the timeline spine passes behind the disc rather than
> appearing to touch it. Only the spine needs it.

`ring-4 ring-ground` — 4px, in the page colour. It is a mask, not decoration. On any surface that is
not `--ground` it will show as a visible halo, so a node must not be placed on a card or a band
without revisiting this.

The `sm` disc reuse in the composer is a deliberate commitment, not a convenience:

> The same disc labels the composer's kind chips, at `sm`. Reusing it there rather than drawing a
> second set of icons is the point: whatever you pick when writing an update is exactly what you
> will later scan for on the spine.

Glyphs (lucide): symptom `Activity`, medication `Pill`, appointment `CalendarDays`, wellbeing
`Heart`, admin `FileText`.

### 3.4 Year markers

The year is printed once, where it changes:

> Kept quiet on purpose: it is orientation, not a headline, and it is what lets every date below it
> stay short — "Sat 25 Jul", never "Sat 25 Jul 2026".

Rendered on the same three columns as an entry —

> Same three columns as an entry, so the year sits in the date gutter and its dot lands dead on the
> spine that runs through every node below it.

— with `pt-4 pb-6`, the year at 12px/semibold/0.06em/muted in column one, and in column two a **7px
disc in `--rule`** with the same `ring-4 ring-ground`. Its spine segment is `top-[22px] bottom-0`
rather than `inset-y-0`, so the line begins at the dot and runs down into the first entry instead of
striking through the year above it.

The grouping itself is a rule, not a convenience:

> It does not bucket: a list that came back out of order shows up as a repeated year marker rather
> than being silently tidied into looking right.

That is the visual half of a data commitment: the UI will not conceal a mis-ordered feed. If the API
returns 2026, 2025, 2026, the page prints three year markers, and the bug is visible.

### 3.5 The date gutter split

The gutter prints "25 Jul" on one line and "Saturday" beneath it, from a single `"Sat 25 Jul"`
string. The split happens in the component:

> Done here rather than in the data so the 27 entries keep one short date string each — the
> presentation decided how to break it, and the record should not have to be rewritten when that
> changes again.

And two weekdays are dropped entirely:

> Wednesday and Thursday are the two longest weekday names — spelled out under a short date, they
> wrap in the narrow gutter and stop it looking quiet. Dropping just those two keeps the rest
> legible without the clutter.

The gutter is **left-aligned, not right**, which is the less obvious choice and the more important
one:

> Left-aligned, not right: it puts the date's first character on the same vertical as the avatar,
> the composer and every year marker, so one straight line runs down the left edge of the whole
> page.

### 3.6 The card

```
rounded-xl  border border-transparent  bg-card  p-5
shadow-[0_1px_2px_rgb(0_0_0/0.03)]  transition-colors  hover:border-rule
```

Inside, in order: time (13px muted, tabular) → headline (17.5px semibold) → summary (14px muted,
68ch) → provenance (12.5px muted) → the Evidence trigger. A photograph, when present, sits to the
right:

> Inside the card, and square: the photograph is filed with the entry, not published under it.

**One photograph is a fixed 168px square. Two or more widen the column.** The square was written
as the only case because it was the only case; when photo attachment shipped it stopped being
true. Three photographs at 168px are three unreadable crops, and a photograph of a wound that
cannot be read is decoration — the one thing the quote above says it must not be. So the column
grows to fit a horizontal strip, and the quote survives intact: the strip still sits *beside* the
text, never under it.

What does **not** move is the card's height. The strip is horizontal precisely so that photo count
cannot drive entry height — a vertical stack was drawn, and rejected, because it made a
three-photo entry two and a half times taller than a photoless one and put a void in the text
column. The timeline's rhythm is the design (§3.2); nothing inside a card may set the card's
height except its prose.

Below `md` the column does not exist and the photographs run full width under the text, capped at
280px. See §5.1 for what is still undecided further down.

---

## 4. The rules this design commits to

Each of these is a thing the design will keep paying for. The reason is given as the source gives
it, because the reason is what makes the rule survive the next redesign meeting.

### 4.1 No filter tabs, no scope controls

> No filters and no scope tabs: a row of controls above the first entry is the surest way to make a
> record look like a dashboard. Search in the top bar is the escape hatch now that the history runs
> to years rather than days.

The failure mode named here is specific: not "clutter", but *the record ceasing to read as a record*.
A filter row also implies the timeline is a query result, when the commitment is that it is the whole
record in order. Search is the pressure-release valve, and it lives in the chrome where it cannot be
mistaken for a view control.

### 4.2 No second horizontal rule

The top bar is sticky and border-less:

> Sticky and rule-less. The record scrolls for years, so the wordmark and search have to stay
> reachable — and with the ground tinted and the cards white, the blur alone separates the bar from
> what passes under it. A border would have drawn a second horizontal line into a layout whose whole
> job is to have none.

`bg-ground/75` + `backdrop-blur-md` does the separation. This generalises: the page has exactly one
long vertical line — the spine — and no long horizontal ones. Rules exist only *inside* enclosed
blocks (the summary panel's two `border-t` separators, the composer's footer divider), where they
are dividing a bounded object rather than slicing the page.

The icon rail was removed under the same logic:

> It replaces the icon rail: with one screen to look at, a permanent vertical navigation was
> four-fifths empty and pushed the record off-centre. Identity and search are the two things that
> genuinely belong at this level.

### 4.3 Cards earn their edge

The card is a reversal, and the reversal is documented:

> The card is back because the entries now run to years rather than a week — at that length
> continuous text stops reading as a list and starts reading as a wall. A white card on tinted
> ground gives each entry an edge without a rule having to draw one.

And the border is transparent rather than absent:

> The border is transparent until hover rather than absent, so the card does not shift by a pixel
> when the outline arrives. At rest the white against the warm ground is edge enough.

Two rules to carry forward: **an edge is made of value contrast, not stroke**, and **no hover state
may change layout**. Reserve the border at rest; only change its colour.

### 4.4 One composition

There is one page, one measure, one face, and one of each object.

- One measure — the h1 and the first entry share a left edge (§3.1).
- One face — Inter for wordmark, headings and body (§2).
- One disc — the composer's kind chip and the spine node are the same component (§3.3).
- One identity — the person moved out of the context column into the page header, because
  > Sits in the page header rather than the context column: the identity is the frame for everything
  > below it, not another panel competing with medications.

  and, from the column's own docstring: *"Identity moved out of here and into the page header: it was
  the one panel that framed everything else rather than sitting level with it."*
- One place for standing facts — the context column, beside rather than inside the timeline:
  > These do not happen on a day, so they have no place on a spine ordered by date — but they are the
  > context every entry is read against, which is why they sit alongside instead of on a second
  > screen.

  The vitals band inside it is the same argument at a smaller scale:
  > The four facts a clinician asks for before anything else. Kept as a band under the name because
  > they are read together, at a glance, and never scrolled to.
- One summary trigger, and it sits with identity, not in the timeline:
  > generating a summary is an act on the record as a whole, not on any one day, so it belongs with
  > identity rather than floating in the timeline.

### 4.5 Interpretation is closed until asked for

Two collapsibles, same principle from opposite ends.

The clinical summary — Penny's reading of the record — is closed:

> Closed until somebody asks. A summary is a reading of the record at a moment, not part of it —
> leaving one open above the timeline would put a stale interpretation between the family and the
> thing it interpreted.
>
> A tinted block, not a card and not a timeline entry: the point is that this is a different kind of
> object from the record beneath it — something you generate, print, hand over, and throw away.

The evidence — the messages an entry was drawn from — is also closed:

> The entry states a conclusion; this is where you check it. Keeping it closed means the record reads
> as a record rather than a chat log, but the evidence is always one click away — and it is the whole
> reason the family believes the line above it.

The citations inside the summary are the same promise one level up:

> a claim in a clinical summary is only as good as the ability to jump to the message behind it.

So the visual system has three tiers and they must stay visually distinct: **record** (white cards on
the spine) → **evidence** (wash-tinted bubbles, inside a card, folded) → **interpretation** (a
`--band` block, outside the timeline, folded). Anything that blurs those tiers — a summary styled as
a card, a chat bubble styled as an entry — breaks the argument, not just the look.

### 4.6 Nothing moves under the reader

The composer overlays rather than expands:

> Open, it *overlays* rather than expands. Growing the composer would push the whole timeline down
> the moment somebody clicked into it, so the entry you were looking at when you decided to add
> something moves out from under you. The collapsed bar stays in the flow holding its space; the
> panel floats over the record on a raised layer.

Same family as the transparent-border rule (§4.3). A record you are reading is not allowed to move
because you touched a control.

### 4.7 Light by default

> The record is a document: it is light unless someone asks otherwise.

`defaultTheme="light"`, `enableSystem={false}`. Dark mode is available and fully tokenised; it is not
the system default, and following the OS is explicitly declined.

---

## 5. What is deliberately not decided yet

These are open, not forgotten. Writing them down is the point of this section — an unwritten gap gets
filled by whoever touches the file next, badly.

### 5.1 Below `lg` — decided, except at the very bottom

This section used to say there was no small-screen design. There is one now, and these are the
decisions rather than escape hatches.

**The record.** Below `sm` the 104px date gutter and the spine are gone. The date moves inside the
card as an eyebrow beside the kind node, and the card starts on the page's left edge — so §3.5's
"one straight line down the left edge" is preserved by dropping the gutter rather than broken by
keeping it. `timeline-entry.tsx`'s docstring argues each of the three rules in full. The entry
photograph reflows to the card's width capped at 280px instead of disappearing; a photo of a rash
beats every description of one, and the phone is where it gets looked at.

**The standing facts.** The context column no longer vanishes. The four vitals render as a 2-up
band directly under the name and above the timeline, so allergies are on screen the moment the
page opens — the reader they exist for is deciding something in a hurry, and a field that costs
her a tap has already failed her. Stacked they measured 332px and pushed the first entry off the
bottom of a 390px screen; two-up they are 212px and the record starts at 534px, above the fold.

Medications, Living with and Cared by are reference rather than emergency. They collapse into
`<details>` blocks BELOW the timeline, because the record is what the page is for. "Coming up" is
not repeated on a phone: its desktop job is to sit level with a timeline you are scrolling past,
which has no small-screen equivalent, and its contents are entries in the record anyway.

Both layouts compose the SAME `VitalsBand` and `ProfileList`. There is one implementation of each
block and no second copy to drift — a duplicated band shipped once already and rendered twice.

**Touch.** 44px minimum on any coarse pointer or under 640px. The edit control was 23×24 — the
button that corrects a wrong dose, at a quarter of the minimum, on the device where the correction
gets typed. Its padding grows the target and a negative margin gives the space back, so it lands
where it always did. Applied by POINTER as well as by width: a small window on a laptop is still a
mouse, a large tablet is still a thumb.

**Still open: 360px and narrower.** At 360 the four nav items plus the wordmark do not fit on one
line and the header goes to 80px. Fixable only by changing what the navigation *is* on a phone —
an icon row, or a menu — and §4.2 deleted navigation on purpose, so that is a design decision
nobody has made rather than a bug to squash. 390px and up is correct today.

### 5.2 Motion

There is no motion system. The whole vocabulary today is `transition-colors` on hover targets, one
`rotate-90` on the evidence chevron, and `disableTransitionOnChange` on the theme provider. There are
no entry or exit transitions, no scroll behaviour, no stagger, and no durations or easings defined as
tokens — every transition is Tailwind's default.

Specifically undecided: whether opening the clinical summary or the evidence drawer animates its
height; whether the composer panel has an entrance; whether the timeline does anything on load; and —
required before any of that ships — a `prefers-reduced-motion` policy. Note that §4.6 constrains this
in advance: any motion added must not move content the reader is reading.

### 5.3 Dark mode has values but has not been reviewed

Every token has a dark value and they are internally consistent. But the design was drawn, argued and
judged in light, and `enableSystem={false}` means dark has never been anyone's default view. It is
untested, not finished.

Three things in particular are known to be assumptions rather than judgements:

- **The glyph knockout.** `text-white dark:text-background` puts a 0.16-lightness glyph on a
  0.72–0.8-lightness disc. Legible in theory at 13px and stroke 2.1; unverified in fact.
- **The ring trick.** `ring-4 ring-ground` in dark is a 0.16 ring on a 0.16 ground — correct, but the
  spine is now `oklch(1 0 0 / 12%)`, an alpha, so the spine-behind-disc effect depends on compositing
  rather than on an opaque mask. Worth looking at closely.
- **The washes.** Six tints at L 0.25–0.27 separated by chroma 0.008–0.036, carrying sender identity
  in the evidence thread. In light they are distinguishable; in dark, at that lightness and chroma,
  they may not be — in which case sender identity in dark needs to be carried by something other than
  the bubble fill.

Until someone sits with the record in dark for an hour, treat the dark column of every table above as
a starting point.
