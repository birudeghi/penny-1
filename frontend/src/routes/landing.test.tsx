import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import { LandingRoute } from '@/routes/landing'

/**
 * The whole landing page exists to move one string — Penny's number — from the screen into the
 * family's clipboard. Two facts carry that job and both are asserted here.
 *
 * The first is the number itself. The build-time variable arrives as "" when the Railway variable
 * is unset (the Dockerfile ARG has no default), so the fallback to Penny's real number is the
 * production path for any build that forgets the env — and the display format is what a person
 * reads aloud over a kitchen table, so a US number must come out as +1 (747) 320-9317, not a
 * wall of digits.
 *
 * The second is what the copy actually copies: bare E.164 with the plus, because that is the
 * form WhatsApp's contact picker, the dialer and the contacts app all accept unmodified. Copying
 * the pretty display string would paste parentheses into a phone field.
 */
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderLanding() {
  return render(
    <MemoryRouter>
      <LandingRoute />
    </MemoryRouter>,
  )
}

/** jsdom has no navigator.clipboard; install one that resolves like the real thing. */
function stubClipboard() {
  const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return writeText
}

describe('LandingRoute', () => {
  it('falls back to Penny’s real number and reads it the way a person would say it', () => {
    renderLanding()
    // Both the number scene and the CTA carry the full number in their accessible name — a
    // screen-reader user gets the number without decoding a row of tiles.
    const buttons = screen.getAllByRole('button', {
      name: "Copy Penny's WhatsApp number +1 (747) 320-9317 to the clipboard",
    })
    expect(buttons.length).toBe(2)
  })

  it('copies bare E.164 to the clipboard and says so on the button', async () => {
    const writeText = stubClipboard()
    renderLanding()
    // The CTA's accessible name is its aria-label, not its visible words — click through the text.
    fireEvent.click(screen.getByText('let Penny in'))
    // The plus and digits only — the display formatting must never reach the clipboard.
    expect(writeText).toHaveBeenCalledWith('+17473209317')
    // The confirmation is the CTA itself changing its word, plus the toast at the cursor.
    expect(await screen.findByText('number copied!')).toBeTruthy()
    expect(screen.getByText('copied to clipboard')).toBeTruthy()
  })
})
