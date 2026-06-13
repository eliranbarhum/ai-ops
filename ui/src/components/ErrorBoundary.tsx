import { Component, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-[#0C1B2E] flex items-center justify-center p-8">
          <div className="max-w-lg w-full rounded-xl border border-red-800/60 bg-[#132034] p-8 text-center space-y-5">
            <div className="flex justify-center">
              <div className="w-14 h-14 rounded-full bg-red-900/30 flex items-center justify-center">
                <AlertTriangle size={28} className="text-red-400" />
              </div>
            </div>
            <div>
              <h2 className="text-lg font-bold text-white mb-1">Something went wrong</h2>
              <p className="text-sm text-slate-400">{this.state.error.message}</p>
            </div>
            <details className="text-left">
              <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-400">Show stack trace</summary>
              <pre className="mt-2 text-[10px] text-slate-500 bg-black/30 rounded p-3 overflow-auto max-h-40 whitespace-pre-wrap">
                {this.state.error.stack}
              </pre>
            </details>
            <button
              onClick={() => this.setState({ error: null })}
              className="flex items-center gap-2 mx-auto px-5 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
            >
              <RefreshCw size={14} /> Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
