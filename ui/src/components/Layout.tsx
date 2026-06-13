import { Sidebar, type Page } from './Sidebar'

export function Layout({ page, navigate, children }: {
  page: Page
  navigate: (p: Page) => void
  children: React.ReactNode
}) {
  const fullBleed = page === 'kubectl'
  return (
    <div className="flex h-screen bg-vmware-dark overflow-hidden">
      <a href="#main" className="skip-link">Skip to content</a>
      <Sidebar page={page} navigate={navigate} />
      <main id="main" className={`flex-1 min-h-0 ${fullBleed ? 'overflow-hidden flex flex-col' : 'overflow-auto'}`}>
        {children}
      </main>
    </div>
  )
}
