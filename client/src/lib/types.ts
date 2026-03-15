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
  page_start?: number
  page_end?: number
}

export interface CurriculumData {
  sections: CurriculumSection[]
  idx?: number   // current section index (present when resuming a lesson)
}

// ── Outbound (client → server) ─────────────────────────────────────────────

export type ClientEvent =
  | { event: 'audio_input'; data: string; sample_rate: number }
  | { event: 'tool_result'; invocation_id: string; result: {
      drawing?: string
      photo?: string
      video_frames?: string[]
      // code editor
      code?: string; stdout?: string; stderr?: string; exit_code?: number
      // html/css editor
      html?: string; css?: string
      // timer
      timed_out?: boolean; answer?: string; elapsed_seconds?: number
    } }
  | { event: 'run_code'; invocation_id: string; code: string; runtime: string }
  | { event: 'set_instructions'; instructions: string }
  | { event: 'set_voice'; voice: string }
  | { event: 'set_stt_language'; language: string | null }
  | { event: 'set_stt_model'; model_size: string }
  | { event: 'reconnect'; last_turn_id: string }
  | { event: 'start_lesson' }
  | { event: 'cancel_turn' }
  | { event: 'image_input'; data: string; caption?: string }
  | { event: 'text_message'; text: string }
  | { event: 'transcribe_only'; data: string; sample_rate: number }
  | { event: 'voice_message'; data: string; mime_type: string }
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
  | { event: 'record_video'; prompt: string; invocation_id: string }
  | { event: 'open_code_editor'; prompt: string; language: string; starter_code?: string; invocation_id: string }
  | { event: 'open_html_editor'; prompt: string; starter_html?: string; starter_css?: string; invocation_id: string }
  | { event: 'start_timer'; prompt: string; duration_seconds: number; invocation_id: string }
  | { event: 'code_stdout'; invocation_id: string; data: string }
  | { event: 'code_stderr'; invocation_id: string; data: string }
  | { event: 'code_done'; invocation_id: string; exit_code: number; elapsed_ms: number }
  | { event: 'code_error'; invocation_id: string; message: string }
  | { event: 'section_advanced'; curriculum: CurriculumState }
  | { event: 'curriculum_complete' }
  | { event: 'decompose_start' }
  | { event: 'decompose_complete'; lesson_id: string; curriculum: CurriculumData }
  | { event: 'history'; turns: Array<{
      role: 'user' | 'assistant'
      text: string
      figures?: Array<
        | { type: 'slide'; page: number; caption: string }
        | { type: 'drawing'; data: string; prompt: string }
      >
    }> }
  | { event: 'transcription_only'; text: string }
  | { event: 'tts_playing'; playing: boolean }
  | { event: 'status'; message: string }
  | { event: 'error'; message: string }
  | { event: 'response_end' }
  | { event: 'pong' }

// ── REST shapes ────────────────────────────────────────────────────────────

export interface Course {
  id: string
  user_id: string
  title: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface Lesson {
  id: string
  course_id: string | null
  title: string
  description: string | null
  pdf_path: string | null
  current_section_idx: number
  completed: boolean
  section_count: number
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

export interface SttModel {
  id: string
  is_default: boolean
}
