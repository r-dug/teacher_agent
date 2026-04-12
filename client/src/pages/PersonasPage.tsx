import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Persona, Course } from '@/lib/types'

interface PersonasPageProps {
  sessionId: string
  isAdmin: boolean
}

function slug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}

const TEMPLATE_VARIABLES = [
  { name: '{{identity}}', desc: 'Default teacher identity line' },
  { name: '{{goal}}', desc: 'Student lesson goal block' },
  { name: '{{constraints}}', desc: 'Voice format, conciseness, no-answer-leaking rules' },
  { name: '{{grounding}}', desc: 'Teach-from-section, search_content guidance' },
  { name: '{{progress}}', desc: '"Section N of M. Already covered: ..."' },
  { name: '{{section}}', desc: 'Full section content with title + page range' },
  { name: '{{section_title}}', desc: 'Just the section title' },
  { name: '{{section_content}}', desc: 'Just the section body text' },
  { name: '{{page_range}}', desc: '"(pages N-M)" or empty' },
  { name: '{{concepts}}', desc: 'KEY CONCEPTS TO VERIFY list' },
  { name: '{{approach}}', desc: 'TEACH/MARK/QUIZ loop instructions' },
  { name: '{{tools}}', desc: 'Tool-principles block (sketchpad, quiz, etc.)' },
  { name: '{{title}}', desc: 'Lesson title' },
  { name: '{{covered}}', desc: 'Covered section titles' },
]

const MAX_INSTRUCTIONS = 5000
const MAX_VOICE = 500

export function PersonasPage({ sessionId, isAdmin }: PersonasPageProps) {
  const navigate = useNavigate()
  const [personas, setPersonas] = useState<Persona[]>([])
  const [courses, setCourses] = useState<Course[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Create / edit form state
  const [formMode, setFormMode] = useState<'closed' | 'create' | 'edit'>('closed')
  const [editId, setEditId] = useState<string | null>(null)
  const [formName, setFormName] = useState('')
  const [formInstructions, setFormInstructions] = useState('')
  const [formVoiceInstructions, setFormVoiceInstructions] = useState('')
  const [saving, setSaving] = useState(false)
  const [showVarRef, setShowVarRef] = useState(false)

  // Course assignment saving
  const [savingCourseId, setSavingCourseId] = useState<string | null>(null)

  const headers = { 'X-Session-Id': sessionId }

  const isTemplate = formInstructions.includes('{{') && formInstructions.includes('}}')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [pRes, cRes] = await Promise.all([
        fetch('/api/admin/personas', { headers }),
        fetch('/api/courses', { headers }),
      ])
      if (!pRes.ok) throw new Error(`Failed to load personas (${pRes.status})`)
      const pData = await pRes.json()
      setPersonas(Array.isArray(pData) ? pData : [])
      if (cRes.ok) {
        const cData = await cRes.json()
        setCourses(Array.isArray(cData) ? cData : [])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (!isAdmin) { navigate('/', { replace: true }); return }
    void loadData()
  }, [isAdmin, navigate, loadData])

  function openCreate() {
    setFormMode('create')
    setEditId(null)
    setFormName('')
    setFormInstructions('')
    setFormVoiceInstructions('')
    setShowVarRef(false)
  }

  function openEdit(p: Persona) {
    setFormMode('edit')
    setEditId(p.id)
    setFormName(p.name)
    setFormInstructions(p.instructions)
    setFormVoiceInstructions(p.voice_instructions || '')
    setShowVarRef(false)
  }

  function closeForm() {
    setFormMode('closed')
    setEditId(null)
  }

  async function handleSave() {
    if (!formName.trim() || !formInstructions.trim()) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      if (formMode === 'create') {
        const resp = await fetch('/api/admin/personas', {
          method: 'POST',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: slug(formName),
            name: formName.trim(),
            instructions: formInstructions.trim(),
            voice_instructions: formVoiceInstructions.trim(),
          }),
        })
        if (!resp.ok) {
          const d = await resp.json().catch(() => ({}))
          throw new Error(d.detail || `Create failed (${resp.status})`)
        }
        setSuccess('Persona created.')
      } else if (formMode === 'edit' && editId) {
        const resp = await fetch(`/api/admin/personas/${editId}`, {
          method: 'PATCH',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName.trim(),
            instructions: formInstructions.trim(),
            voice_instructions: formVoiceInstructions.trim(),
          }),
        })
        if (!resp.ok) {
          const d = await resp.json().catch(() => ({}))
          throw new Error(d.detail || `Update failed (${resp.status})`)
        }
        setSuccess('Persona updated.')
      }
      closeForm()
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(p: Persona) {
    if (!window.confirm(`Delete persona "${p.name}"? This cannot be undone.`)) return
    setError(null)
    setSuccess(null)
    try {
      const resp = await fetch(`/api/admin/personas/${p.id}`, {
        method: 'DELETE',
        headers,
      })
      if (!resp.ok && resp.status !== 204) {
        const d = await resp.json().catch(() => ({}))
        throw new Error(d.detail || `Delete failed (${resp.status})`)
      }
      setSuccess('Persona deleted.')
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function handleCoursePersonaChange(courseId: string, personaId: string | null) {
    setSavingCourseId(courseId)
    setError(null)
    setSuccess(null)
    try {
      const resp = await fetch(`/api/courses/${courseId}`, {
        method: 'PATCH',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_persona_id: personaId }),
      })
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        throw new Error(d.detail || `Update failed (${resp.status})`)
      }
      setSuccess('Course default persona updated.')
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    } finally {
      setSavingCourseId(null)
    }
  }

  function personaLabel(p: Persona): string {
    if (p.user_id === null) return 'Global'
    return 'User'
  }

  function hasTemplate(p: Persona): boolean {
    return p.instructions.includes('{{') && p.instructions.includes('}}')
  }

  if (!isAdmin) return null

  return (
    <div className="min-h-screen bg-[hsl(var(--background))] text-[hsl(var(--foreground))]">
      {/* Header */}
      <div className="sticky top-0 z-10 border-b border-[hsl(var(--border))] bg-[hsl(var(--background)/0.95)] backdrop-blur px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
          >
            &larr; Back
          </button>
          <span className="font-semibold text-sm">Persona Management</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[hsl(var(--destructive)/0.15)] text-[hsl(var(--destructive))]">Admin</span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void loadData()} disabled={loading}>
            Refresh
          </Button>
          <Button size="sm" onClick={openCreate} disabled={formMode !== 'closed'}>
            New Persona
          </Button>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        {/* Banners */}
        {error && (
          <div className="rounded-md border border-[hsl(var(--destructive)/0.45)] bg-[hsl(var(--destructive)/0.1)] px-3 py-2 text-sm text-[hsl(var(--destructive))]">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-md border border-[hsl(var(--primary)/0.4)] bg-[hsl(var(--primary)/0.12)] px-3 py-2 text-sm">
            {success}
          </div>
        )}

        {/* Create / Edit Form */}
        {formMode !== 'closed' && (
          <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.3)] p-4 space-y-4">
            <h3 className="text-sm font-medium">
              {formMode === 'create' ? 'Create Persona' : `Edit: ${editId}`}
            </h3>

            {/* Name */}
            <div>
              <label className="block text-xs text-[hsl(var(--muted-foreground))] mb-1">Name</label>
              <input
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1.5 text-sm"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. Socratic, Tsumugi, NYC Tutor"
              />
            </div>

            {/* System Prompt / Instructions */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-[hsl(var(--muted-foreground))]">
                  System Prompt
                  <span className="ml-1.5 text-[hsl(var(--muted-foreground))]">({formInstructions.length}/{MAX_INSTRUCTIONS})</span>
                </label>
                <div className="flex items-center gap-2">
                  {isTemplate && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[hsl(var(--primary)/0.15)] text-[hsl(var(--primary))]">
                      Template mode
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => setShowVarRef(!showVarRef)}
                    className="text-[10px] px-1.5 py-0.5 rounded border border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
                  >
                    {showVarRef ? 'Hide' : 'Show'} variables
                  </button>
                </div>
              </div>

              {/* Variable reference panel */}
              {showVarRef && (
                <div className="mb-2 rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] p-3 text-xs space-y-1.5">
                  <p className="text-[hsl(var(--muted-foreground))]">
                    Use <code className="text-[hsl(var(--primary))]">{'{{variable}}'}</code> placeholders to build a full system prompt template.
                    Without placeholders, the text is used as a personality description injected into the default template.
                  </p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-2">
                    {TEMPLATE_VARIABLES.map((v) => (
                      <div key={v.name} className="flex gap-2">
                        <code
                          className="text-[hsl(var(--primary))] cursor-pointer hover:underline shrink-0"
                          title="Click to insert"
                          onClick={() => setFormInstructions((prev) => prev + v.name)}
                        >
                          {v.name}
                        </code>
                        <span className="text-[hsl(var(--muted-foreground))] truncate">{v.desc}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <textarea
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1.5 text-sm min-h-[180px] resize-y font-mono"
                value={formInstructions}
                onChange={(e) => setFormInstructions(e.target.value.slice(0, MAX_INSTRUCTIONS))}
                placeholder={
                  'Simple mode:\n'
                  + 'You are Tsumugi, a brilliant but condescending tutor...\n\n'
                  + 'Template mode (use {{variables}}):\n'
                  + 'You are a custom teacher.\n{{constraints}}\n{{section}}\n{{approach}}\n{{tools}}'
                }
              />
            </div>

            {/* Voice Instructions */}
            <div>
              <label className="block text-xs text-[hsl(var(--muted-foreground))] mb-1">
                Voice Instructions
                <span className="ml-1.5 text-[hsl(var(--muted-foreground))]">({formVoiceInstructions.length}/{MAX_VOICE})</span>
              </label>
              <textarea
                className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1.5 text-sm min-h-[60px] resize-y"
                value={formVoiceInstructions}
                onChange={(e) => setFormVoiceInstructions(e.target.value.slice(0, MAX_VOICE))}
                placeholder='Speaking style for TTS (e.g. "Speak warmly, slow pace, native Japanese accent on vocabulary")'
              />
            </div>

            {/* Actions */}
            <div className="flex gap-2">
              <Button size="sm" onClick={() => void handleSave()} disabled={saving || !formName.trim() || !formInstructions.trim()}>
                {saving ? 'Saving...' : formMode === 'create' ? 'Create' : 'Save'}
              </Button>
              <Button variant="outline" size="sm" onClick={closeForm} disabled={saving}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Personas Table */}
        <div>
          <h2 className="text-sm font-medium mb-2 text-[hsl(var(--muted-foreground))]">Personas</h2>
          <div className="overflow-x-auto rounded-lg border border-[hsl(var(--border))]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--muted))]">
                  <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Name</th>
                  <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">System Prompt</th>
                  <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Voice</th>
                  <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Type</th>
                  <th className="px-3 py-2 text-right font-medium text-[hsl(var(--muted-foreground))]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                      Loading...
                    </td>
                  </tr>
                )}
                {!loading && personas.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-center text-[hsl(var(--muted-foreground))]">
                      No personas found.
                    </td>
                  </tr>
                )}
                {!loading && personas.map((p) => (
                  <tr key={p.id} className="border-b border-[hsl(var(--border))] last:border-0">
                    <td className="px-3 py-2 font-medium whitespace-nowrap">
                      {p.name}
                      {hasTemplate(p) && (
                        <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-[hsl(var(--primary)/0.12)] text-[hsl(var(--primary))]">
                          tmpl
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 max-w-xs">
                      <span className="text-[hsl(var(--muted-foreground))] line-clamp-2 text-xs">
                        {p.instructions}
                      </span>
                    </td>
                    <td className="px-3 py-2 max-w-[10rem]">
                      <span className="text-[hsl(var(--muted-foreground))] line-clamp-2 text-xs">
                        {p.voice_instructions || <span className="italic">none</span>}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={p.user_id === null ? 'default' : 'outline'}>
                        {personaLabel(p)}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="outline" size="sm" onClick={() => openEdit(p)}>
                          Edit
                        </Button>
                        <Button variant="destructive" size="sm" onClick={() => void handleDelete(p)}>
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Course Default Persona Assignment */}
        {courses.length > 0 && (
          <div>
            <h2 className="text-sm font-medium mb-2 text-[hsl(var(--muted-foreground))]">Course Default Personas</h2>
            <div className="overflow-x-auto rounded-lg border border-[hsl(var(--border))]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[hsl(var(--border))] bg-[hsl(var(--muted))]">
                    <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Course</th>
                    <th className="px-3 py-2 text-left font-medium text-[hsl(var(--muted-foreground))]">Default Persona</th>
                  </tr>
                </thead>
                <tbody>
                  {courses.map((c) => (
                    <tr key={c.id} className="border-b border-[hsl(var(--border))] last:border-0">
                      <td className="px-3 py-2 font-medium">{c.title}</td>
                      <td className="px-3 py-2">
                        <select
                          className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1 text-sm"
                          value={c.default_persona_id || ''}
                          disabled={savingCourseId === c.id}
                          onChange={(e) => {
                            const val = e.target.value || null
                            void handleCoursePersonaChange(c.id, val)
                          }}
                        >
                          <option value="">None</option>
                          {personas.map((p) => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                        {savingCourseId === c.id && (
                          <span className="ml-2 text-xs text-[hsl(var(--muted-foreground))]">Saving...</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
