import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { EntryPhoto } from '@/components/timeline-entry'
import type { Photo } from '@/types/api'

/**
 * The photograph filed with an entry, and who is speaking in the words beside it.
 *
 * Three things are being defended here and none is cosmetic.
 *
 * The first is attribution. The photo is the family's — somebody held a phone over a swollen ankle
 * and pressed send — and the description is a model's account of what it thinks it can see. A
 * description that arrives looking like a caption reads as something the family wrote, and a model
 * that calls a pressure sore "a mild rash" has then produced a sentence a GP could act on. The
 * accessible name is the sharp edge of this, because a screen-reader user receives that one string
 * and nothing else: bare derived text there is indistinguishable from a human caption. On a stored
 * photograph that attribution is provably safe — the webhook drops WhatsApp captions before the
 * row is written and a message that already has text is never downloaded, so no row can carry both
 * bytes and a human's words.
 *
 * The second is what is left when the bytes do not arrive. `/api/media/{id}` can 404 on a message
 * whose media WhatsApp has dropped, 401 on an expired session, or 500 on a bad day, and the
 * browser's answer to all three is a torn-page glyph. The most valuable artefact in the record
 * failing to a broken-image icon, in a product a family opens because they are worried, is the
 * worst version of this component.
 *
 * The third is new, and it is why the failure state changed shape: a thumbnail now sits in a strip
 * of up to four. The old version swapped the image for a differently-sized text block, which was
 * harmless when a card had exactly one photograph and is not harmless in a row — one failure would
 * slide its neighbours sideways while somebody is looking at them, which is DESIGN.md §4.6 in
 * miniature. The box has to survive its own failure.
 */
afterEach(cleanup)

function photo(overrides: Partial<Photo> = {}): Photo {
  return {
    message_id: 'msg-42',
    sent_at: '2026-03-12T09:31:00Z',
    sender: 'Sarah',
    description: 'A raised red rash across the inside of the left forearm.',
    cited: true,
    ...overrides,
  }
}

/** The element that holds the box — a button, because a thumbnail opens the lightbox. */
function box(): HTMLElement {
  return screen.getByRole('button')
}

describe('EntryPhoto', () => {
  it('fetches the photo from the media endpoint on this origin, keyed by the message id', () => {
    render(<EntryPhoto photo={photo()} />)
    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/msg-42')
  })

  it('escapes the message id, so an id with a slash in it cannot reach for another path', () => {
    render(<EntryPhoto photo={photo({ message_id: 'a/b' })} />)
    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/a%2Fb')
  })

  it("attributes the description to Penny, so it never arrives as the family's words", () => {
    render(<EntryPhoto photo={photo()} />)
    const label = box().getAttribute('aria-label') ?? ''
    expect(label).toContain("Penny's description")
    expect(label).toContain('raised red rash')
    // Sarah shared the picture. She did not write this sentence about it.
    expect(label).not.toMatch(/Sarah('s)? description/)
  })

  it('still says what the image is when Penny has not described it', () => {
    render(<EntryPhoto photo={photo({ description: '' })} />)
    expect(box().getAttribute('aria-label')).toContain('Penny has not described it')
  })

  it('treats a description of nothing but whitespace as no description at all', () => {
    render(<EntryPhoto photo={photo({ description: '   ' })} />)
    expect(box().getAttribute('aria-label')).toContain('Penny has not described it')
  })

  it('says the photo can be opened larger, because a thumbnail is for finding not reading', () => {
    render(<EntryPhoto photo={photo()} />)
    expect(box().getAttribute('aria-label')).toContain('Open it larger')
  })

  it('degrades to words when the photo fails, never to a broken image', () => {
    render(<EntryPhoto photo={photo()} />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText(/Photo unavailable/)).toBeTruthy()
  })

  it('points a failed photo at the evidence, where Penny’s description already lives', () => {
    render(<EntryPhoto photo={photo()} />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByText(/description is in the evidence/)).toBeTruthy()
  })

  it('says plainly that there is nothing to show when a photo fails and was never described', () => {
    render(<EntryPhoto photo={photo({ description: '' })} />)
    fireEvent.error(screen.getByRole('img'))
    const text = screen.getByText(/Photo unavailable/).textContent ?? ''
    expect(text).not.toContain('evidence')
  })

  it('KEEPS ITS BOX WHEN IT FAILS, so a strip does not reflow under the reader', () => {
    render(<EntryPhoto photo={photo()} square />)
    const before = box().className
    fireEvent.error(screen.getByRole('img'))
    expect(box().className).toBe(before)
    expect(before).toContain('aspect-square')
  })

  it('is square in a strip and taller when it is the only photograph', () => {
    const { unmount } = render(<EntryPhoto photo={photo()} square />)
    expect(box().className).toContain('aspect-square')
    unmount()
    render(<EntryPhoto photo={photo()} />)
    expect(box().className).toContain('aspect-[4/3]')
  })

  it('opens the lightbox when clicked', () => {
    const onOpen = vi.fn()
    render(<EntryPhoto photo={photo()} onOpen={onOpen} />)
    fireEvent.click(box())
    expect(onOpen).toHaveBeenCalledOnce()
  })
})
