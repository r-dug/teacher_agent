import { useState, useRef, useCallback } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { vscodeDark } from '@uiw/codemirror-theme-vscode'
import { Play, RotateCcw, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface HtmlCssEditorProps {
  prompt: string
  starterHtml?: string
  starterCss?: string
  invocationId: string
  onSubmit: (invocationId: string, html: string, css: string) => void
  onCancel: () => void
}

type Tab = 'html' | 'css'

export function HtmlCssEditor({
  prompt,
  starterHtml = '',
  starterCss = '',
  invocationId,
  onSubmit,
  onCancel,
}: HtmlCssEditorProps) {
  const [htmlCode, setHtmlCode] = useState(starterHtml)
  const [cssCode, setCssCode] = useState(starterCss)
  const [activeTab, setActiveTab] = useState<Tab>('html')
  const [previewSrc, setPreviewSrc] = useState<string | null>(null)
  const [hasRun, setHasRun] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const buildSrcdoc = useCallback((h: string, c: string) => {
    return `<!DOCTYPE html><html><head><style>${c}</style></head><body>${h}</body></html>`
  }, [])

  const handleRun = useCallback(() => {
    setPreviewSrc(buildSrcdoc(htmlCode, cssCode))
    setHasRun(true)
  }, [htmlCode, cssCode, buildSrcdoc])

  const handleReset = useCallback(() => {
    setHtmlCode(starterHtml)
    setCssCode(starterCss)
    setPreviewSrc(null)
    setHasRun(false)
  }, [starterHtml, starterCss])

  const handleSubmit = useCallback(() => {
    onSubmit(invocationId, htmlCode, cssCode)
  }, [invocationId, htmlCode, cssCode, onSubmit])

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[hsl(var(--background))]">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-[hsl(var(--border))] px-4 py-3">
        <span className="flex-1 text-sm font-medium">{prompt}</span>
        <span className="rounded bg-[hsl(var(--muted))] px-2 py-0.5 font-mono text-xs text-[hsl(var(--muted-foreground))]">
          HTML / CSS
        </span>
      </div>

      {/* Editor + Preview */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: tabbed editor */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Tabs */}
          <div className="flex border-b border-[hsl(var(--border))]">
            {(['html', 'css'] as Tab[]).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={cn(
                  'px-4 py-2 text-xs font-semibold uppercase tracking-wide transition-colors',
                  activeTab === tab
                    ? 'border-b-2 border-[hsl(var(--primary))] text-[hsl(var(--primary))]'
                    : 'text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]'
                )}
              >
                {tab.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Editor */}
          <div className="flex-1 overflow-hidden">
            {activeTab === 'html' ? (
              <CodeMirror
                key="html"
                value={htmlCode}
                onChange={setHtmlCode}
                extensions={[html()]}
                theme={vscodeDark}
                className="h-full overflow-auto text-sm"
                style={{ height: '100%' }}
                basicSetup={{ lineNumbers: true, bracketMatching: true, closeBrackets: true }}
              />
            ) : (
              <CodeMirror
                key="css"
                value={cssCode}
                onChange={setCssCode}
                extensions={[css()]}
                theme={vscodeDark}
                className="h-full overflow-auto text-sm"
                style={{ height: '100%' }}
                basicSetup={{ lineNumbers: true, bracketMatching: true, closeBrackets: true }}
              />
            )}
          </div>
        </div>

        {/* Right: iframe preview */}
        <div className="flex w-[50%] flex-col border-l border-[hsl(var(--border))] bg-white">
          <div className="border-b border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Preview
            </span>
          </div>
          {previewSrc ? (
            <iframe
              ref={iframeRef}
              srcDoc={previewSrc}
              sandbox="allow-scripts"
              className="flex-1 border-none"
              title="HTML preview"
            />
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">
              Click Run to preview
            </div>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 border-t border-[hsl(var(--border))] px-4 py-3">
        <Button variant="default" size="sm" onClick={handleRun} className="gap-1.5">
          <Play className="h-3.5 w-3.5" />
          Run
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          className="gap-1.5"
          title="Reset to starter code"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset
        </Button>

        <div className="flex-1" />

        <Button variant="outline" size="sm" onClick={onCancel} className="gap-1.5">
          <X className="h-3.5 w-3.5" />
          Cancel
        </Button>

        <Button
          variant="default"
          size="sm"
          onClick={handleSubmit}
          disabled={!hasRun}
          className="gap-1.5"
          title={hasRun ? undefined : 'Run your code first'}
        >
          <Check className="h-3.5 w-3.5" />
          Submit
        </Button>
      </div>
    </div>
  )
}
