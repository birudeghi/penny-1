import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import { KindNode } from '@/components/kind-node'
import { KIND_STYLE, type TimelineKind } from '@/lib/timeline'

/**
 * What the disc on the spine says to a reader who cannot see it.
 *
 * These are not tests of an icon. The disc is the only thing on an entry that says whether the
 * family is looking at a symptom, a dose change or an appointment — the headline is a sentence
 * about a day, and nothing else on the card names the kind — so when the disc was `aria-hidden`
 * the record was untyped for every screen-reader user, and typed by hue alone for every reader who
 * cannot separate a 0.6-lightness orange from a 0.52-lightness red. The assertions below are the
 * cheapest permanent guard against that regression: they fail the moment somebody hides the disc
 * again to quieten the accessibility tree.
 */
afterEach(cleanup)

const KINDS = Object.keys(KIND_STYLE) as TimelineKind[]

describe('KindNode', () => {
  it.each(KINDS)(
    'names the %s kind out loud, so the kind is never carried by hue alone',
    (kind) => {
      render(<KindNode kind={kind} />)
      expect(screen.getByRole('img', { name: KIND_STYLE[kind].label })).toBeDefined()
    },
  )

  it('announces the kind exactly once, so the glyph inside the disc is not read a second time', () => {
    render(<KindNode kind="appointment" />)
    expect(screen.getAllByRole('img')).toHaveLength(1)
  })

  it('keeps its name at the small size the column beside the record uses', () => {
    render(<KindNode kind="wellbeing" size="sm" />)
    expect(screen.getByRole('img', { name: 'Wellbeing' })).toBeDefined()
  })

  it('rings itself in the ground colour only on the spine, so the line passes behind it', () => {
    const { container } = render(<KindNode kind="symptom" onSpine />)
    const disc = container.firstElementChild
    expect(disc?.className).toContain('ring-4')
    expect(disc?.className).toContain('ring-ground')
  })

  it('wears no ring off the spine, where a ground-coloured halo would show against the card', () => {
    const { container } = render(<KindNode kind="symptom" size="sm" />)
    expect(container.firstElementChild?.className).not.toContain('ring-4')
  })
})
