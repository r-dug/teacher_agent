/**
 * WebSocket event types — mirrors the backend protocol exactly.
 * All messages are JSON objects with a discriminated `event` field.
 */

// ── Curriculum ─────────────────────────────────────────────────────────────

export interface CurriculumState {
  title: string
  idx: number
  total: number
}

export interface CurriculumSection {
  title: string | null
  content: string
}

export interface CurriculumData {
  sections: CurriculumSection[]
  idx?: number   // current section index (present when resuming a lesson)
}

// ── Outbound (client → server) ─────────────────────────────────────────────

export type ClientEvent =
  | { event: 'audio_input'; data: string; sample_rate: number }
  | { event: 'tool_result'; invocation_id: string; result: { drawing?: string; photo?: string } }
  | { event: 'set_instructions'; instructions: string }
  | { event: 'set_voice'; voice: string }
  | { event: 'set_stt_language'; language: string | null }
  | { event: 'reconnect'; last_turn_id: string }
  | { event: 'start_lesson' }
  | { event: 'cancel_turn' }
  | { event: 'image_input'; data: string; caption?: string }
  | { event: 'ping' }

// ── Inbound (server → client) ──────────────────────────────────────────────

export type ServerEvent =
  | { event: 'transcription'; text: string; turn_id: string }
  | { event: 'text_chunk'; text: string; turn_idx: number }
  | { event: 'audio_chunk'; data: string; sample_rate: number; turn_idx: number; chunk_idx: number }
  | { event: 'chunk_complete'; turn_idx: number; chunk_idx: number }
  | { event: 'chunk_ready'; tag: string; turn_idx: number; chunk_idx: number }
  | { event: 'turn_complete'; turn_id: string }
  | { event: 'turn_interrupted' }
  | { event: 'turn_start' }
  | { event: 'show_slide'; page_start: number; page_end: number; caption: string }
  | { event: 'open_sketchpad'; prompt: string; invocation_id: string; text_bg?: string; im_bg?: string }
  | { event: 'take_photo'; prompt: string; invocation_id: string }
  | { event: 'section_advanced'; curriculum: CurriculumState }
  | { event: 'curriculum_complete' }
  | { event: 'decompose_complete'; lesson_id: string; curriculum: CurriculumData }
  | { event: 'history'; turns: Array<{
      role: 'user' | 'assistant'
      text: string
      figures?: Array<
        | { type: 'slide'; page: number; caption: string }
        | { type: 'drawing'; data: string; prompt: string }
      >
    }> }
  | { event: 'tts_playing'; playing: boolean }
  | { event: 'status'; message: string }
  | { event: 'error'; message: string }
  | { event: 'response_end' }
  | { event: 'pong' }

// ── REST shapes ────────────────────────────────────────────────────────────

export interface Lesson {
  id: string
  title: string
  pdf_path: string | null
  current_section_idx: number
  completed: boolean
  created_at: string
  updated_at: string
}

export interface Persona {
  id: string
  name: string
  instructions: string
  user_id: string | null
}

export interface Voice {
  id: string
  lang_code: string
  is_default: boolean
}

export interface SttLanguage {
  name: string
  code: string | null  // null = auto-detect
}
