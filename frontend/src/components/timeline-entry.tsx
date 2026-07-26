import { useState } from 'react'

import { Evidence } from '@/components/evidence'
import { EventEditor } from '@/components/event-editor'
import { KindNode } from '@/components/kind-node'
import { PhotoLightbox } from '@/components/photo-lightbox'
import { useDeleteEvent, useUpdateEvent } from '@/api/queries'
import { formatTimelineDate } from '@/lib/datetime'
import { usePeople } from '@/lib/people'
import { entryTimeLabel, provenanceOf, timelineKind } from '@/lib/timeline'
import { cn } from '@/lib/utils'
import type { Event, Photo } from '@/types/api'

const WEEKDAY: Record<string, string> = {
  Mon: 'Monday',
  Tue: 'Tuesday',
  Wed: 'Wednesday',
  Thu: 'Thursday',
  Fri: 'Friday',
  Sat: 'Saturday',
  Sun: 'Sunday',
}

/**
 * Splits "Sat 25 Jul" into the two lines the gutter prints.
 *
 * Done here rather than in the data so each entry keeps one short date string — the presentation
 * decided how to break it, and the record should not have to be rewritten when that changes again.
 */
function splitDate(date: string): { day: string; weekday: string } {
  const [abbrev, ...rest] = date.split(' ')
  const full = abbrev && WEEKDAY[abbrev]
  if (!full) return { day: date, weekday: '' }
  // Wednesday and Thursday are the two longest weekday names — spelled out
  // under a short date, they wrap in the narrow gutter and stop it looking
  // quiet. Dropping just those two keeps the rest legible without the clutter.
  const weekday = abbrev === 'Wed' || abbrev === 'Thu' ? '' : full
  return { day: rest.join(' '), weekday }
}

/**
 * One entry in the record: a date in the margin, a node on the spine, and a card holding what
 * happened.
 *
 * The card is back because the entries now run to years rather than a week — at that length
 * continuous text stops reading as a list and starts reading as a wall. A white card on tinted
 * ground gives each entry an edge without a rule having to draw one.
 *
 * THE EDIT BUTTON IS NOT IN THE DESIGN, and it is here anyway. The prototype's entries were
 * fixtures; these were written by a language model reading somebody's family chat, and it gets
 * things wrong. A record a family cannot correct is a record they stop trusting the first time
 * they find a wrong dose in it. It stays out of the way — muted, and only on hover or keyboard
 * focus — because correcting is rare and reading is not. It is out of the way ONLY where hovering
 * is possible: `sm:pointer-fine:opacity-0` means a touchscreen shows it at rest, because a control
 * revealed by a hover that a finger cannot perform is a control that does not exist, and the
 * failure it causes is the specific one above — a wrong dose nobody could reach the button to fix.
 *
 * ── BELOW `sm` THE SPINE AND THE DATE GUTTER ARE GONE, DELIBERATELY ───────────────────────────
 *
 * The three-column grid costs 170px before the first character of content: a 104px date gutter, a
 * 26px node column, and two 20px gaps. Inside a 375px phone with the shell's own 32px margins that
 * leaves 141px for the record itself — a headline wrapping every three words, which is not a
 * degraded reading experience but an unreadable one. This is the record a family opens at 3am in a
 * hospital corridor, on a phone, so `lg` and up cannot be the only layout that works.
 *
 * The design's rules for this row, and what each becomes:
 *
 * 1. THE DATE GUTTER IS LEFT-ALIGNED so the date's first character sits on the same vertical as
 *    the care recipient's name and every year marker — one straight line down the left edge of the
 *    whole page. Below `sm` the gutter is dropped and the date moves inside the card, above the
 *    headline. That rule is *preserved by dropping it*: with no gutter, the card itself starts on
 *    the page's left edge, so the one straight line is still there and now the record is on it.
 *    Keeping a 104px gutter on a phone would have been the one thing that broke the rule it exists
 *    to serve.
 *
 * 2. THE SPINE RUNS UNBROKEN THROUGH EVERY NODE. It goes, and losing it is the decision rather
 *    than an oversight. Its geometry is not decoration: `left-[137px]` is derived from
 *    `104 + 20 + 13`, so a spine on a phone would be a line with nothing left of it, drawn 137px
 *    into a 311px measure. Its job is continuity across a history that runs for pages, and at a
 *    phone's two-and-a-half entries per screen there is almost no distance for it to carry
 *    continuity over. So it costs 12% of the measure to do a job the screen has already done.
 *
 * 3. THE KIND NODE SURVIVES, because it is not the spine's ornament — it is the only thing that
 *    says what kind of entry this is (see `kind-node.tsx`). It moves inside the card at the small
 *    18px disc, beside the date, and WITHOUT its ring: `ring-4 ring-ground` is a mask that only
 *    works on the ground colour, and on a white card it would read as a visible grey halo.
 *
 * 4. THE CARD'S BORDER IS TRANSPARENT UNTIL HOVER so nothing shifts by a pixel. Unchanged at every
 *    width — the border is still reserved, never added — even though a phone will rarely fire the
 *    hover that colours it.
 *
 * 5. THE PHOTOGRAPH IS FILED, NOT PUBLISHED — a square in the card's right margin. It stops being
 *    a margin object and becomes a capped-width block under the text, and it stops being deleted
 *    outright below `sm`, which is what it used to be. The comment on the card's inner grid has
 *    the argument, including why that one switches a breakpoint later than everything here.
 *
 * The breakpoint is `sm` (640px), not `lg`. Between 640 and 1024 the grid costs 170px of a 576px
 * measure, which is tight but honest; below 640 it is most of the screen. `lg` is where the context
 * column arrives, which is a separate question about a separate column.
 */
export function TimelineEntry({ event, timeZone }: { event: Event; timeZone: string }) {
  const [editing, setEditing] = useState(false)
  const people = usePeople()
  const update = useUpdateEvent()
  const remove = useDeleteEvent()

  const date = formatTimelineDate(event.occurred_at, timeZone)
  const { day, weekday } = splitDate(date)
  const at = entryTimeLabel(event, timeZone)
  const kind = timelineKind(event)

  return (
    <article className="relative pb-7 sm:grid sm:grid-cols-[104px_26px_minmax(0,1fr)] sm:gap-x-5">
      {/* The spine runs the full height of every entry, so consecutive nodes
          join into one unbroken line down the page. Gone below `sm`; the
          component docstring argues why that is a decision and not a loss. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-[137px] hidden w-px -translate-x-1/2 bg-rule sm:block"
      />

      {/* Left-aligned, not right: it puts the date's first character on the
          same vertical as the care recipient's avatar and every year marker,
          so one straight line runs down the left edge of the whole page. */}
      <div className="hidden pt-3.5 sm:block">
        <div className="text-[14px] leading-none font-semibold tracking-tight">{day}</div>
        {weekday && (
          <div className="mt-1.5 text-[13px] leading-none text-muted-foreground">{weekday}</div>
        )}
      </div>

      <span className="relative z-10 mt-3 hidden sm:block">
        <KindNode kind={kind} onSpine />
      </span>

      {editing ? (
        <EventEditor
          event={event}
          timeZone={timeZone}
          isSaving={update.isPending}
          error={update.error}
          onCancel={() => {
            update.reset()
            setEditing(false)
          }}
          onSave={(patch) => {
            // Nothing changed: close rather than send an empty PATCH that would still stamp
            // `edited_at` and permanently freeze the row against re-extraction.
            if (Object.keys(patch).length === 0) {
              setEditing(false)
              return
            }
            update.mutate({ id: event.id, patch }, { onSuccess: () => setEditing(false) })
          }}
          onDelete={() => {
            setEditing(false)
            remove.mutate(event.id)
          }}
        />
      ) : (
        /* The border is transparent until hover rather than absent, so the card
           does not shift by a pixel when the outline arrives. At rest the white
           against the tinted ground is edge enough. */
        <div
          className={cn(
            'group min-w-0 rounded-xl border border-transparent bg-card p-5',
            'shadow-[0_1px_2px_rgb(0_0_0/0.03)] transition-colors hover:border-rule',
          )}
        >
          {/*
            A grid at `md`, and plain block flow below it, because the three pieces want two
            different orders and only one of them may be the DOM's.

            Reading down a phone the order is the entry, then the photograph filed with it, then
            the control that opens the messages behind it. On a wide screen the photograph moves
            into a column of its own on the right and the evidence trigger sits directly under the
            provenance line, where the design puts it — time, headline, summary, provenance,
            trigger, in one unbroken run. Placing the two text blocks in column one and spanning
            the photograph down both rows gets that second order out of the first, so nothing is
            duplicated and no reader is handed a sequence that does not match what they see.

            AND IT SWITCHES AT `md`, NOT AT `sm` LIKE THE ROW AROUND IT. The two breakpoints are
            measuring different things. The row's is about the 170px margin: at 640px that margin
            has stopped being most of the screen, so the gutter comes back. The photograph's is
            about what is left after it: at 640px, 170px of margin plus a 168px square plus the
            gaps leaves under 200px for the headline, which is a worse card than one with a wide
            photograph under the text. So the square waits for 768px and is full width until then.
          */}
          <div className="md:grid md:grid-cols-[minmax(0,1fr)_168px] md:gap-x-5">
            <div className="min-w-0 md:col-start-1 md:row-start-1">
              <div className="flex items-center gap-2.5 sm:items-baseline sm:gap-3">
                {/* Below `sm` this row is the date gutter, folded into the card: the node the
                    spine used to carry, then the date the margin used to print. Two elements
                    rendered here and hidden at `sm` rather than moved by CSS — `display: none`
                    keeps the hidden copy out of the accessibility tree too, so the kind is
                    announced exactly once at every width. */}
                <span className="sm:hidden">
                  <KindNode kind={kind} size="sm" />
                </span>
                <span className="text-[13px] font-semibold tracking-tight tabular-nums sm:hidden">
                  {date}
                </span>

                {at && (
                  <span className="text-[13px] text-muted-foreground tabular-nums">{at}</span>
                )}
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  // Twenty identical "Edit" buttons are indistinguishable to a screen reader, so
                  // the entry's title goes in the accessible name rather than in a tooltip.
                  aria-label={`Edit “${event.title}”`}
                  className={cn(
                    'ml-auto text-[12.5px] text-muted-foreground transition-opacity',
                    'sm:pointer-fine:opacity-0 group-hover:opacity-100 hover:text-foreground',
                    'focus-visible:opacity-100',
                    // A 44px TARGET AROUND A 23px WORD. The padding grows the hit area and the
                    // negative margin gives the space back to the layout, so the button lands in
                    // exactly the same place it did before and is now tappable. This is the
                    // control that corrects a wrong dose; measured at 23x24 on a phone, which is
                    // a quarter of the minimum, on the device where the correction gets typed.
                    'inline-flex min-h-[44px] items-center px-3 py-3 -my-3 -mr-3',
                  )}
                >
                  Edit
                </button>
              </div>

              <h3 className="mt-1.5 text-[17.5px] leading-snug font-semibold tracking-tight text-balance">
                {event.title}
              </h3>

              {event.body && (
                <p className="mt-1.5 max-w-[68ch] text-[14px] leading-relaxed break-words text-muted-foreground">
                  {event.body}
                </p>
              )}

              <p className="mt-2.5 text-[12.5px] text-muted-foreground">
                {provenanceOf(event, timeZone, people)}
              </p>
            </div>

            {/*
              Inside the card, and beside the text: the photographs are filed with the entry, not
              published under it. Narrower than `md` the column stops existing and they become the
              width of the card — capped at 280px, so that on a phone they are as big as the
              screen can make them and on a tablet they are photographs filed under an entry
              rather than an entry reduced to captioning them. This used to be `hidden sm:block`,
              which on the one screen that matters most deleted the best evidence in the record.

              ONE PHOTOGRAPH IS 168px; TWO OR MORE WIDEN THE COLUMN. DESIGN.md §3.6 said a fixed
              168px square because for a long time one was all there was. Three photographs at
              168px are three unreadable crops, and a picture of a wound that cannot be read is
              decoration — the one thing the "filed, not published" rule says it must not be. What
              does NOT change is the card's height: the strip runs horizontally precisely so that
              photo count can never drive entry height. A vertical stack was drawn and rejected
              for exactly that. The timeline's rhythm is the design; nothing inside a card may set
              the card's height except its prose.
            */}
            <EntryPhotos
              eventId={event.id}
              photos={event.photos ?? []}
              withheld={event.photos_withheld ?? 0}
              unavailable={event.photos_unavailable ?? 0}
              timeZone={timeZone}
            />

            {/* `empty:hidden` because `Evidence` renders nothing for a hand-written entry, and a
                margin under nothing is still a margin — a card with an invisible 14px of extra
                foot is exactly the kind of drift that makes a list of cards look hand-spaced. */}
            <div className="mt-3.5 empty:hidden md:col-start-1 md:row-start-2">
              <Evidence excerpts={event.source_excerpts} timeZone={timeZone} />
            </div>
          </div>
        </div>
      )}
    </article>
  )
}

/**
 * The path the media endpoint serves a message's photo on.
 *
 * SAME ORIGIN, and that is the whole authentication story: the session cookie rides along
 * automatically, so there is no signed URL, no token in a query string, and nothing to end up in a
 * browser history, a referrer header or a screenshot of a URL bar. A care record's photographs are
 * the most identifying thing in it.
 */
function mediaSrc(messageId: string): string {
  return `/api/media/${encodeURIComponent(messageId)}`
}

/**
 * The photographs filed with one entry: one square, or a strip of up to four.
 *
 * THE CAPTION SAYS THE WEAKEST THING THAT IS TRUE OF ALL OF THEM, and that constraint comes from
 * the layout. There is one caption under a group of up to four photographs, so there is no wording
 * that can say "the first two are evidence and the third is not" without becoming a sentence
 * nobody reads. So the group under-claims on purpose: the moment any photograph in it is uncited,
 * the whole caption drops to "shared around this time". The exact per-photograph claim exists, and
 * it lives in the lightbox, where there is room for one sentence about one picture.
 *
 * Overclaiming is the failure being avoided. A photograph that merely happened to be sent nearby,
 * rendered under "Photos from Sarah with this entry", is a holiday snap presented as evidence for
 * a symptom — and this record's whole argument is that a family can trust what it says about where
 * things came from.
 *
 * PENNY'S DESCRIPTION IS NOT HERE, and that is DESIGN.md §4.5: interpretation is closed until
 * asked for. A model's reading of a photograph is interpretation in exactly the sense the clinical
 * summary is, and leaving one open under the record puts a machine's guess permanently between the
 * family and the thing it read. It is one click away, in the lightbox and in the evidence drawer,
 * both of which are already folded.
 *
 * "+N MORE" IS A SENTENCE, NEVER A CONTROL. Expanding the strip in place would grow the entry and
 * push every entry below it down the page while somebody is reading — DESIGN.md §4.6, the same
 * rule that makes the composer overlay instead of expand. The lightbox pages through everything
 * instead, so the cap costs nothing.
 */
function EntryPhotos({
  eventId,
  photos,
  withheld,
  unavailable,
  timeZone,
}: {
  eventId: string
  photos: Photo[]
  withheld: number
  unavailable: number
  timeZone: string
}) {
  const [openAt, setOpenAt] = useState<number | null>(null)

  // An entry with neither photographs nor placeholders renders nothing at all — no element, no
  // reserved column, no empty block. A card with an invisible gap in it is the drift that makes a
  // list of cards look hand-spaced.
  if (photos.length === 0 && unavailable === 0) return null

  const single = photos.length === 1

  return (
    <div className="mt-4 w-full max-w-[280px] md:col-start-2 md:row-span-2 md:row-start-1 md:mt-0 md:self-start md:[--photo-col:168px] md:w-[var(--photo-col)] md:data-[strip=true]:w-[248px]">
      {photos.length > 0 && (
        <figure className="w-full" data-strip={!single}>
          <div className={cn('grid gap-1.5', single ? 'grid-cols-1' : 'grid-cols-3')}>
            {photos.map((photo, index) => (
              <EntryPhoto
                // The message id, so a re-extraction that files a different photograph under the
                // same entry gets a fresh element and does not inherit the previous one's failure.
                key={photo.message_id}
                photo={photo}
                square={!single}
                onOpen={() => setOpenAt(index)}
              />
            ))}
          </div>

          <figcaption className="mt-1.5 text-[12.5px] leading-snug text-muted-foreground">
            {groupCaption(photos, timeZone)}
            {withheld > 0 && <span className="block">+{withheld} more</span>}
          </figcaption>
        </figure>
      )}

      {/* An absence nobody explains reads as "nothing happened" — the same failure `RereadBanner`
          exists to prevent one level up. A WhatsApp export ships `<image omitted>` and no file, so
          for imported history this is permanent and the only honest thing is to say so. */}
      {unavailable > 0 && (
        <p
          className={cn(
            'text-[12.5px] leading-snug text-muted-foreground',
            photos.length > 0 ? 'mt-1.5' : '',
          )}
        >
          {unavailable === 1 ? 'A photo was' : `${unavailable} photos were`} shared here. WhatsApp
          exports do not include the files.
        </p>
      )}

      <PhotoLightbox
        eventId={openAt === null ? null : eventId}
        photos={photos}
        index={openAt ?? 0}
        timeZone={timeZone}
        onIndexChange={setOpenAt}
        onClose={() => setOpenAt(null)}
      />
    </div>
  )
}

/** The weakest claim true of every photograph in the group. See `EntryPhotos`. */
function groupCaption(photos: Photo[], timeZone: string): string {
  const first = photos[0]
  if (!first) return ''
  const when = formatTimelineDate(first.sent_at, timeZone)
  const senders = [...new Set(photos.map((photo) => photo.sender).filter(Boolean))]
  const who = senders.length === 1 ? senders[0] : null
  // One uncited photograph in the group drops the whole caption to the weaker sentence. It is not
  // possible to be precise per photograph here, so it is precise in the safe direction.
  if (photos.some((photo) => !photo.cited)) {
    return who ? `Shared by ${who} around this time — ${when}` : `Shared around this time — ${when}`
  }
  const noun = photos.length === 1 ? 'Photo' : 'Photos'
  return who ? `${noun} from ${who} — ${when}` : `${noun} shared in the chat — ${when}`
}

/**
 * One photograph in the card. A button, because it opens the lightbox.
 *
 * IT HOLDS ITS SLOT WHEN IT FAILS. The media endpoint can 404 on a message whose bytes WhatsApp
 * dropped, 401 on an expired session, or 500 on a bad day. The previous version swapped the image
 * for a differently-sized text block, which was fine when a card had exactly one photograph and
 * is not fine in a strip: one failure would slide its neighbours sideways under a reader's eye,
 * which is DESIGN.md §4.6 in miniature. So the failed state keeps the same box and puts the words
 * inside it.
 *
 * THE BAND IS THE PLACEHOLDER, not a spinner. `--band` is already spec'd as the surface behind an
 * entry photograph (§1.1), the box is sized before the bytes arrive, and there is no motion system
 * to spin anything with (§5.2). A lazily-loaded strip that reserves its space simply fills in.
 *
 * Exported so its contract can be tested without a query client and a people provider around it.
 */
export function EntryPhoto({
  photo,
  square = false,
  onOpen,
}: {
  photo: Photo
  /** In a strip every thumb is square; alone it keeps the taller 4:3 on a phone. */
  square?: boolean
  onOpen?: () => void
}) {
  const [failed, setFailed] = useState(false)
  const derived = photo.description?.trim() || null

  return (
    // `lg` (10px) is the radius for a block that is not a card — DESIGN.md §1.5 — and `--band` is
    // the surface a photograph sits on, so the block behind a photo that has not arrived is the
    // same colour as the block behind one that has.
    <button
      type="button"
      onClick={onOpen}
      aria-label={altFor(derived)}
      className={cn(
        'block w-full overflow-hidden rounded-lg bg-band',
        square ? 'aspect-square' : 'aspect-[4/3] md:aspect-square',
      )}
    >
      {failed ? (
        /* Same box, different contents — never a broken-image glyph, and never a resize. */
        <span className="flex size-full items-center justify-center px-2 py-1.5 text-left text-[11px] leading-tight text-muted-foreground">
          {derived ? 'Photo unavailable — Penny’s description is in the evidence' : 'Photo unavailable'}
        </span>
      ) : (
        <img
          src={mediaSrc(photo.message_id)}
          alt={altFor(derived)}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className="size-full object-cover"
        />
      )}
    </button>
  )
}

/**
 * The accessible name for one thumbnail.
 *
 * NAMED AS PENNY'S, ALWAYS. A screen-reader user receives one string where a sighted reader gets a
 * picture and a caption, so if that string were the bare description then a model's guess would
 * arrive with exactly the authority of something the family wrote. On a stored photograph the
 * description is never the family's caption — the webhook drops captions at the door and a message
 * that already has text is never downloaded — so attributing it to Penny is the truth, not a hedge.
 */
function altFor(derived: string | null): string {
  if (!derived) return 'Photo shared in the chat. Penny has not described it. Open it larger.'
  return `Photo shared in the chat. Penny's description: ${derived}. Open it larger.`
}
