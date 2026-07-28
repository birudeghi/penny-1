import { useState } from 'react'
import { ChevronRight } from 'lucide-react'

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { ChatBubble } from '@/components/chat-bubble'
import { cn } from '@/lib/utils'
import type { SourceExcerpt } from '@/types/api'

/**
 * The messages an entry was drawn from, folded away by default.
 *
 * The entry states a conclusion; this is where you check it. Keeping it closed means the record
 * reads as a record rather than a chat log, but the evidence is always one click away — and it is
 * the whole reason the family believes the line above it. An LLM wrote the headline; the only
 * reason anyone trusts "Ramipril reduced to 2.5 mg" is that they can open this and read the
 * sentence Liz actually typed, with her name and the exact minute she sent it.
 *
 * A SPOKEN QUOTE IS NAMED ON THE CLOSED ROW, not only inside it. `transcribed` means nobody typed
 * those words: a model listened to a voice note and wrote down what it thought it heard, which is
 * a second inference stacked under the first, and it fails in a specific and dangerous way —
 * speech-to-text mishears proper nouns, so "Apixaban" and "a pixie band" are the same sound and
 * only one of them is a blood thinner. The one person who could catch that is a family member who
 * knows the recording is still sitting in WhatsApp, and they will not open twenty entries to find
 * out which one was spoken. So the count is on the outside, and the sentence saying what to do
 * about it is on the inside.
 *
 * Nothing renders when there are no excerpts. A hand-written entry has no evidence to show, and
 * the entry's own provenance line already says so — an empty disclosure would invite a click that
 * opens nothing.
 */
export function Evidence({
  excerpts,
  timeZone,
}: {
  excerpts: SourceExcerpt[]
  timeZone: string
}) {
  const [open, setOpen] = useState(false)

  if (excerpts.length === 0) return null

  const spoken = excerpts.filter((excerpt) => excerpt.transcribed).length

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        className={cn(
          // `min-h-11` is 44px: this is the control that opens the verbatim message behind an
          // entry, and on a phone it measured 40. The pill's own padding sets its look; the
          // floor only ever grows the target, never the type.
          'inline-flex min-h-11 items-center gap-1.5 rounded-full border border-rule px-3.5 py-[7px]',
          'text-[12.5px] font-medium text-penny transition-colors hover:bg-band',
        )}
      >
        {open ? 'Hide the evidence' : triggerLabel(excerpts.length, spoken)}
        <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
      </CollapsibleTrigger>

      <CollapsibleContent>
        <div className="mt-4 flex max-w-[520px] flex-col gap-2">
          {excerpts.map((excerpt) => (
            <ChatBubble key={excerpt.message_id} excerpt={excerpt} timeZone={timeZone} />
          ))}

          {spoken > 0 && (
            <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
              {spoken === 1 ? 'One of these was transcribed' : `${spoken} of these were transcribed`}{' '}
              automatically from a voice note — the original recording is still in WhatsApp.
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/** The closed row carries the voice-note count; see the component docstring for why. */
function triggerLabel(total: number, spoken: number): string {
  if (spoken === 0) return 'See the evidence'
  if (spoken === total) return total === 1 ? 'See the voice note' : 'See the voice notes'
  return `See the evidence · ${spoken} spoken`
}
