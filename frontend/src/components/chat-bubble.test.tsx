import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import { ChatBubble } from '@/components/chat-bubble'
import { createPeople, PeopleProvider } from '@/lib/people'
import type { Member, SourceExcerpt } from '@/types/api'

/**
 * Who said this, and did anybody say it at all.
 *
 * This component renders the highest-consequence string in the record: a verbatim quote under a
 * family member's name, avatar and colour, offered as the reason to believe the entry above it.
 * There are three kinds of quote and only one of them is something a person typed.
 *
 * `transcribed` — somebody SAID it, and a speech model wrote it down. Mishearings land on names,
 * medicines and doses, which is exactly the class of error a family can catch and nobody else can.
 *
 * `derived_from_media` — NOBODY said it. A model looked at a photograph or read a document and
 * wrote a sentence about it. This flag travelled the whole way from `extraction.runner` to
 * `types/api.ts` and then went unread by every component for a while, which meant a model's
 * description of a photo appeared here under Sarah's name in Sarah's colour. That is the failure
 * these tests exist to keep fixed, and it is the reason the branch is checked FIRST: it is the
 * strongest claim, so if both flags ever arrived together the safe answer is the one that says
 * fewer humans are responsible for these words.
 *
 * The plain case is the regression guard for the other two. Adding a sibling `if` to a component
 * with no tests is how the transcribed branch quietly stops firing.
 */
afterEach(cleanup)

const MEMBERS: Member[] = [
  {
    id: 'm-1',
    display_name: 'Sarah',
    wa_jid: null,
    wa_lid: null,
    message_count: 12,
    first_seen_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-03-12T00:00:00Z',
  },
]

function excerpt(overrides: Partial<SourceExcerpt> = {}): SourceExcerpt {
  return {
    message_id: 'msg-1',
    sent_at: '2026-03-12T09:31:00Z',
    sender: 'Sarah',
    quote: 'Her ankle is worse than Sunday, I have sent a photo to the district nurse.',
    transcribed: false,
    ...overrides,
  }
}

function draw(source: SourceExcerpt, timeZone = 'Europe/London') {
  return render(
    <PeopleProvider value={createPeople(MEMBERS)}>
      <ChatBubble excerpt={source} timeZone={timeZone} />
    </PeopleProvider>,
  )
}

describe('ChatBubble', () => {
  it("renders a typed quote as the sender's own words, with no disclaimer", () => {
    draw(excerpt())
    expect(screen.getByText(/ankle is worse than Sunday/)).toBeTruthy()
    expect(screen.getByText('Sarah')).toBeTruthy()
    expect(screen.queryByText(/Penny/)).toBeNull()
    expect(screen.queryByText('Spoken')).toBeNull()
  })

  it('labels a transcribed quote as Spoken, so a misheard dose can be checked', () => {
    draw(excerpt({ transcribed: true, quote: 'Two of the white ones at night.' }))
    expect(screen.getByText('Spoken')).toBeTruthy()
    expect(screen.getByText(/Two of the white ones/)).toBeTruthy()
  })

  it('REGRESSION: the transcribed branch still fires now that a second branch exists', () => {
    // The derived branch was added beside this one. A mis-ordered condition would swallow it and
    // nothing would look broken — the quote still renders, just without the label that says a
    // machine heard it.
    draw(excerpt({ transcribed: true, derived_from_media: false }))
    expect(screen.getByText('Spoken')).toBeTruthy()
  })

  it("attributes a photo description to Penny, never to the family member who sent it", () => {
    draw(
      excerpt({
        derived_from_media: true,
        quote: 'Swelling on the left ankle, skin taut and shiny.',
      }),
    )
    expect(screen.getByText(/Penny’s description of a photo/)).toBeTruthy()
    expect(screen.getByText(/skin taut and shiny/)).toBeTruthy()
    // The sender's name still appears — she DID share the picture. What she did not do is write
    // the sentence, and the disclaimer is what separates those two facts.
    expect(screen.getByText('Sarah')).toBeTruthy()
    expect(screen.queryByText('Spoken')).toBeNull()
  })

  it('prefers the derived label when both flags somehow arrive, because it claims less', () => {
    draw(excerpt({ transcribed: true, derived_from_media: true }))
    expect(screen.getByText(/Penny’s description of a photo/)).toBeTruthy()
    expect(screen.queryByText('Spoken')).toBeNull()
  })

  it('treats a missing derived flag as the weaker claim, so a lost key cannot over-claim', () => {
    // The field is optional in the type precisely so an older response still renders. Absent must
    // mean "a person wrote this", never the reverse.
    const { derived_from_media: _omitted, ...withoutFlag } = excerpt({ derived_from_media: false })
    draw(withoutFlag as SourceExcerpt)
    expect(screen.queryByText(/Penny’s description/)).toBeNull()
  })

  it('renders no fabricated name when the sender never resolved', () => {
    draw(excerpt({ sender: '' }))
    // Whatever placeholder the people helper produces, it must not be another member's name.
    expect(screen.queryByText('Sarah')).toBeNull()
    expect(screen.getByText(/ankle is worse than Sunday/)).toBeTruthy()
  })

  it("renders the time in the household's zone, not the browser's", () => {
    // 09:31Z is 10:31 in Paris. The record has one clock and it belongs to the household, so two
    // siblings in two countries read the same minute against the same day.
    draw(excerpt(), 'Europe/Paris')
    expect(screen.getByText('10:31')).toBeTruthy()
  })

  it('carries the machine-readable timestamp on a <time> element', () => {
    const { container } = draw(excerpt())
    const stamp = container.querySelector('time')
    expect(stamp?.getAttribute('dateTime') ?? stamp?.getAttribute('datetime')).toBe(
      '2026-03-12T09:31:00Z',
    )
  })
})
