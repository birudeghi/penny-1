import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { EntryPhoto } from '@/components/timeline-entry'

/**
 * The photograph filed with an entry, and who is speaking in the words beside it.
 *
 * Two things are being defended here and neither is cosmetic.
 *
 * The first is attribution. The photo is the family's — somebody held a phone over a swollen ankle
 * and pressed send — and the description is a model's account of what it thinks it can see. A
 * description that arrives looking like a caption reads as something the family wrote, and a model
 * that calls a pressure sore "a mild rash" has then produced a sentence a GP could act on. The
 * `alt` is the sharp edge of this, because a screen-reader user receives that one string and
 * nothing else: bare derived text there is indistinguishable from a human caption.
 *
 * The second is what is left when the bytes do not arrive. `/api/media/{id}` can 404 on a message
 * whose media WhatsApp has dropped, 401 on an expired session, or 500 on a bad day, and the
 * browser's answer to all three is a torn-page glyph. The most valuable artefact in the record
 * failing to a broken-image icon, in a product a family opens because they are worried, is the
 * worst version of this component. It degrades to the text instead, labelled as Penny's.
 */
afterEach(cleanup)

const DESCRIPTION = 'A raised red rash across the inside of the left forearm.'

describe('EntryPhoto', () => {
  it('fetches the photo from the media endpoint on this origin, keyed by the message id', () => {
    render(<EntryPhoto messageId="msg-42" description={DESCRIPTION} />)
    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/msg-42')
  })

  it('escapes the message id, so an id with a slash in it cannot reach for another path', () => {
    render(<EntryPhoto messageId="a/b" description={null} />)
    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/a%2Fb')
  })

  it("attributes the description to Penny in the alt, so it never arrives as the family's words", () => {
    render(<EntryPhoto messageId="msg-42" description={DESCRIPTION} />)
    const alt = screen.getByRole('img').getAttribute('alt') ?? ''
    expect(alt).toContain("Penny's description")
    expect(alt).toContain(DESCRIPTION)
  })

  it('still says what the image is when Penny has not described it', () => {
    render(<EntryPhoto messageId="msg-42" description={null} />)
    const alt = screen.getByRole('img').getAttribute('alt') ?? ''
    expect(alt).toContain('Photo shared in the chat')
    expect(alt).not.toBe('')
  })

  it('treats a description of nothing but whitespace as no description at all', () => {
    render(<EntryPhoto messageId="msg-42" description="   " />)
    expect(screen.getByRole('img').getAttribute('alt')).toBe(
      'Photo shared in the chat. Penny has not described it.',
    )
  })

  it('says the photo came from the chat while it is the photo you are looking at', () => {
    render(<EntryPhoto messageId="msg-42" description={DESCRIPTION} />)
    expect(screen.getByText('Photo shared in the chat')).toBeDefined()
  })

  it('degrades to the derived description when the photo fails, never to a broken image', () => {
    render(<EntryPhoto messageId="msg-42" description={DESCRIPTION} />)
    fireEvent.error(screen.getByRole('img'))

    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText(DESCRIPTION)).toBeDefined()
  })

  it('names the failure and names Penny when it falls back to her description', () => {
    render(<EntryPhoto messageId="msg-42" description={DESCRIPTION} />)
    fireEvent.error(screen.getByRole('img'))

    const caption = screen.getByText(/could not be loaded/)
    expect(caption.textContent).toContain("Penny's description")
  })

  it('says plainly that there is nothing to show when a photo fails and was never described', () => {
    render(<EntryPhoto messageId="msg-42" description={null} />)
    fireEvent.error(screen.getByRole('img'))

    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText(/could not be loaded, and Penny has not described it/)).toBeDefined()
  })
})
