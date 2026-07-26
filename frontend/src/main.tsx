import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'

import { App } from './App'
import { queryClient } from './api/queryClient'
// `design.css` first: it declares the cascade-layer order (`theme, base, components, utilities`)
// that `index.css` then puts its element defaults into. Loaded the other way round, the layers
// would be ordered by first mention and `base` would outrank `theme`.
import './design.css'
import './index.css'

const root = document.getElementById('root')
if (!root) throw new Error('index.html is missing #root')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
