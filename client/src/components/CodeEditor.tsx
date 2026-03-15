import { useState, useRef, useEffect, useCallback } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { cpp } from '@codemirror/lang-cpp'
import { rust } from '@codemirror/lang-rust'
import { vscodeDark } from '@uiw/codemirror-theme-vscode'
import { Play, RotateCcw, Check, X, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface CodeOutput {
  stdout: string
  stderr: string
  exitCode: number | null
  elapsedMs: number | null
  running: boolean
}

interface CodeEditorProps {
  prompt: string
  language: string
  starterCode?: string
  invocationId: string
  output: CodeOutput
  onRun: (code: string, runtime: string) => void
  onSubmit: (invocationId: string, code: string) => void
  onCancel: () => void
}

function langExtension(language: string) {
  if (language === 'python' || language === 'python-ml') return python()
  if (language === 'javascript' || language === 'typescript') return javascript({ typescript: language === 'typescript' })
  if (language === 'c' || language === 'cpp') return cpp()
  if (language === 'rust') return rust()
  return []
}

const LANG_LABELS: Record<string, string> = {
  python: 'Python',
  'python-ml': 'Python · ML',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  c: 'C',
  cpp: 'C++',
  rust: 'Rust',
}

export function CodeEditor({
  prompt,
  language,
  starterCode = '',
  invocationId,
  output,
  onRun,
  onSubmit,
  onCancel,
}: CodeEditorProps) {
  const [code, setCode] = useState(starterCode)
  const [hasRun, setHasRun] = useState(false)
  const outputRef = useRef<HTMLDivElement>(null)

  // Auto-scroll output panel as chunks arrive
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [output.stdout, output.stderr])

  const handleRun = useCallback(() => {
    setHasRun(true)
    onRun(code, language)
  }, [code, language, onRun])

  const handleReset = useCallback(() => {
    setCode(starterCode)
  }, [starterCode])

  const handleSubmit = useCallback(() => {
    onSubmit(invocationId, code)
  }, [invocationId, code, onSubmit])

  const hasOutput = output.stdout || output.stderr || output.exitCode !== null

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[hsl(var(--background))]">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-[hsl(var(--border))] px-4 py-3">
        <span className="flex-1 text-sm font-medium">{prompt}</span>
        <span className="rounded bg-[hsl(var(--muted))] px-2 py-0.5 font-mono text-xs text-[hsl(var(--muted-foreground))]">
          {LANG_LABELS[language] ?? language}
        </span>
      </div>

      {/* Editor + Output */}
      <div className="flex flex-1 overflow-hidden">
        {/* Code editor */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <CodeMirror
            value={code}
            onChange={setCode}
            extensions={[langExtension(language)]}
            theme={vscodeDark}
            className="flex-1 overflow-auto text-sm"
            style={{ height: '100%' }}
            basicSetup={{
              lineNumbers: true,
              foldGutter: true,
              bracketMatching: true,
              closeBrackets: true,
              indentOnInput: true,
            }}
          />
        </div>

        {/* Output panel */}
        <div className="flex w-[40%] min-w-[240px] flex-col border-l border-[hsl(var(--border))] bg-[hsl(var(--card))]">
          {/* Output header */}
          <div className="flex items-center gap-2 border-b border-[hsl(var(--border))] px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Output
            </span>
            {output.running && (
              <Loader2 className="h-3 w-3 animate-spin text-[hsl(var(--muted-foreground))]" />
            )}
            {!output.running && output.exitCode !== null && (
              <span className={cn(
                'ml-auto rounded px-1.5 py-0.5 font-mono text-[10px]',
                output.exitCode === 0
                  ? 'bg-green-900/30 text-green-400'
                  : 'bg-red-900/30 text-red-400'
              )}>
                exit {output.exitCode}
                {output.elapsedMs !== null && ` · ${output.elapsedMs}ms`}
              </span>
            )}
          </div>

          {/* Output body */}
          <div
            ref={outputRef}
            className="flex-1 overflow-auto p-3 font-mono text-xs leading-relaxed"
          >
            {!hasOutput && !output.running && (
              <span className="text-[hsl(var(--muted-foreground))]">
                Click Run to execute your code.
              </span>
            )}
            {output.stdout && (
              <pre className="whitespace-pre-wrap text-[hsl(var(--foreground))]">
                {output.stdout}
              </pre>
            )}
            {output.stderr && (
              <pre className="whitespace-pre-wrap text-amber-400">
                {output.stderr}
              </pre>
            )}
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 border-t border-[hsl(var(--border))] px-4 py-3">
        <Button
          variant="default"
          size="sm"
          onClick={handleRun}
          disabled={output.running}
          className="gap-1.5"
        >
          {output.running
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Play className="h-3.5 w-3.5" />}
          Run
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          disabled={output.running}
          className="gap-1.5"
          title="Reset to starter code"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset
        </Button>

        <div className="flex-1" />

        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          className="gap-1.5"
        >
          <X className="h-3.5 w-3.5" />
          Cancel
        </Button>

        <Button
          variant="default"
          size="sm"
          onClick={handleSubmit}
          disabled={!hasRun || output.running}
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
