import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router'

import { Layout } from '@/routes/layout'

/**
 * Where a signed-out visitor lands, which just changed underneath every existing family.
 *
 * The Gate used to send a 401 to /login; it now sends it to /start, the marketing page. That is
 * the right front door for a stranger — but it silently rerouted every family whose session has
 * expired, and nothing else in the suite would notice if a typo sent them to a 404 instead. This
 * pins the one behaviour the redesign changed: signed out, off the login screen, you are shown
 * the marketing page, and the login form is still one visible click away from there.
 */
afterEach(cleanup)

vi.mock('@/api/queries', () => ({
  // A resolved session of null is exactly what a 401 from GET /api/me becomes.
  useSession: () => ({ isPending: false, data: null }),
  useLogout: () => ({ isPending: false, mutate: vi.fn() }),
}))

describe('Layout gate, signed out', () => {
  it('sends a signed-out visit to /start, the marketing page — no longer straight to /login', async () => {
    const router = createMemoryRouter(
      [
        { path: '/start', element: <p>marketing front door</p> },
        {
          path: '/',
          element: <Layout />,
          children: [{ index: true, element: <p>the family feed</p> }],
        },
      ],
      { initialEntries: ['/'] },
    )
    render(<RouterProvider router={router} />)
    expect(await screen.findByText('marketing front door')).toBeTruthy()
    // The redirect replaced the feed entirely; nothing protected leaked through the gate.
    expect(screen.queryByText('the family feed')).toBeNull()
    expect(router.state.location.pathname).toBe('/start')
  })
})
