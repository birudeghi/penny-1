import { Mic } from 'lucide-react'

import { PersonAvatar } from '@/components/person-avatar'
import { formatTime } from '@/lib/datetime'
import { usePeople } from '@/lib/people'
import { cn } from '@/lib/utils'
import type { SourceExcerpt } from '@/types/api'

/** Static bars — a waveform reads as a voice note without needing real audio. */
const WAVE = [4, 9, 14, 7, 16, 11, 6, 13, 15, 8, 5, 10, 14, 7, 10, 12, 4, 8]

/**
 * One verbatim WhatsApp message, as it looked in the chat.
 *
 * THREE THINGS FROM THE PROTOTYPE ARE GONE, and each is a thing the API cannot say rather than a
 * thing the design got wrong.
 *
 * There is no sent-side bubble. Penny reads the group, she does not write into the record, so
 * every excerpt is a family message and a right-aligned "outbound" variant would only ever be
 * rendered by inventing an author.
 *
 * There are no attachments. `SourceExcerpt` carries a quote, not media. The picture itself is
 * filed on the entry (`EntryPhotos`), where it can be opened; a second copy in the evidence
 * thread would be the same photograph twice on one card.
 *
 * There is no play button on a voice note. `transcribed` tells us a model listened to a
 * recording, not that we hold the audio — the recording is still in WhatsApp. The waveform stays
 * because it is the fastest way to see that these words were spoken, but the control that would
 * promise playback is a microphone, which promises nothing.
 *
 * The timestamp is on every bubble, to the minute, because the message time is a fact where the
 * entry's date is an inference — that difference is most of why anyone believes this panel.
 *
 * THREE KINDS OF QUOTE, AND WHO SAID IT IS THE POINT. A plain quote is what somebody typed. A
 * `transcribed` one is what somebody said, written down by a speech model. A `derived_from_media`
 * one is what NOBODY said: a model looked at a photograph or read a document and wrote a sentence
 * about it. That third case is the one that matters most to render, and it went unrendered for a
 * while — the flag was on the wire and no component read it, so a model's description of a photo
 * appeared in this thread under a family member's name, avatar and colour. Evidence attributed to
 * Sarah that Sarah never wrote is how a family stops trusting the record.
 *
 * The two flags are mutually exclusive on the wire, and `derived_from_media` is checked first
 * anyway: it is the stronger claim, so if they ever both arrived the safe branch is the one that
 * says fewer people are responsible for these words.
 */
export function ChatBubble({
  excerpt,
  timeZone,
}: {
  excerpt: SourceExcerpt
  timeZone: string
}) {
  const person = usePeople().bySender(excerpt.sender)

  return (
    <div className="flex items-start gap-1.5">
      {/* Level with the sender name rather than the bubble's foot, so a long
          message doesn't strand the avatar at the bottom. */}
      <PersonAvatar person={person} size="xs" className="mt-1.5" />

      <div className={cn('min-w-0 rounded-xl rounded-bl-sm px-2.5 py-1.5', person.tone.wash)}>
        <div className="flex items-baseline gap-1.5">
          <span className={cn('text-[10px] font-bold tracking-wide', person.tone.text)}>
            {person.name}
          </span>
          <time
            dateTime={excerpt.sent_at}
            className="text-[9.5px] text-muted-foreground tabular-nums"
          >
            {formatTime(excerpt.sent_at, timeZone)}
          </time>
        </div>

        {excerpt.derived_from_media ? (
          <>
            {/* The strongest disclaimer this component makes, and it is deliberately plain text
                rather than an icon: an icon is a thing you learn, and this has to be understood
                by somebody reading their mother's medical record for the first time. The name
                above is still the sender's, because they DID share the picture — what they did
                not do is write these words about it. */}
            <p className="mt-1 text-[9px] font-medium text-muted-foreground">
              Penny’s description of a photo
            </p>
            <p className="mt-0.5 text-[13px] leading-snug break-words italic">{excerpt.quote}</p>
          </>
        ) : excerpt.transcribed ? (
          <>
            <div className="mt-1 flex items-center gap-2">
              <span className="grid size-5 shrink-0 place-items-center rounded-full bg-wellbeing">
                <Mic className="size-2.5 text-white dark:text-background" strokeWidth={2.4} />
              </span>
              <span className="flex h-4 flex-1 items-center gap-px" aria-hidden>
                {WAVE.map((h, i) => (
                  <i
                    key={i}
                    className="block w-px rounded-full bg-wellbeing/55"
                    style={{ height: `${h}px` }}
                  />
                ))}
              </span>
              <span className="text-[9px] font-medium text-wellbeing">Spoken</span>
            </div>
            <p className="mt-1 text-[13px] leading-snug break-words">{excerpt.quote}</p>
          </>
        ) : (
          /* `break-words` because a quote is verbatim and families paste links: one 200-character
             NHS URL with no space in it is wider than a phone, and without this it pushes the
             bubble out through the card and gives the whole page a horizontal scrollbar. */
          <p className="text-[13px] leading-snug break-words">{excerpt.quote}</p>
        )}
      </div>
    </div>
  )
}
