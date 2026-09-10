// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { DocumentTitle } from '../src/client/DocumentTitle.tsx'

afterEach(() => {
  cleanup()
  document.title = ''
  vi.unstubAllEnvs()
})

describe('DocumentTitle', () => {
  it('projects a durable title and restores the product title', () => {
    vi.stubEnv('DSH_CLIENT_TITLE', 'Intelligent Q&A Workbench')
    document.title = 'stale title'
    const mounted = render(<DocumentTitle productTitle="Intelligent Q&A Workbench" />)
    expect(document.title).toBe('Intelligent Q&A Workbench')
    mounted.rerender(<DocumentTitle title="First title" productTitle="Intelligent Q&A Workbench" />)
    expect(document.title).toBe('First title — Intelligent Q&A Workbench')
    mounted.rerender(<DocumentTitle title="Revised title" productTitle="Intelligent Q&A Workbench" />)
    expect(document.title).toBe('Revised title — Intelligent Q&A Workbench')
    mounted.rerender(<DocumentTitle productTitle="Intelligent Q&A Workbench" />)
    expect(document.title).toBe('Intelligent Q&A Workbench')
    mounted.unmount()
    expect(document.title).toBe('Intelligent Q&A Workbench')
  })

  it('uses the generic title when the build provides no title', () => {
    vi.stubEnv('DSH_CLIENT_TITLE', '')
    delete process.env.DSH_CLIENT_TITLE
    const mounted = render(<DocumentTitle title="First title" productTitle="Intelligent Q&A Workbench" />)
    expect(document.title).toBe('First title — Intelligent Q&A Workbench')
    mounted.unmount()
    expect(document.title).toBe('Intelligent Q&A Workbench')
  })
})
