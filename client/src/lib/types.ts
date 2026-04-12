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

export interface SectionAsset {
  type: 'pdf_pages' | 'ai_image'
  page_start?: number | null
  page_end?: number | null
  image_path?: string | null
  caption?: string | null
}

export interface CurriculumSection {
  title: string | null
  content: string
  page_start?: number
  page_end?: number
  assets?: SectionAsset[]
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
      // quiz
      selected_index?: number; correct?: boolean | boolean[]
      // fill in the blank
      student_answers?: string[]
      // flashcard deck
      results?: Array<{ card_index: number; self_grade: 'correct' | 'incorrect' }>
      // ordering exercise
      student_order?: number[]
    } }
  | { event: 'run_code'; invocation_id: string; code: string; runtime: string }
  | { event: 'set_instructions'; instructions: string; voice_instructions?: string; tts_voice?: string; tts_speed?: number; tts_format?: string; tts_prep_prompt?: string }
  | { event: 'set_voice'; voice: string }
  | { event: 'set_voice_arch'; voice_arch: string }
  | { event: 'set_stt_provider'; provider: string }
  | { event: 'set_stt_language'; language: string | null }
  | { event: 'set_stt_model'; model_size: string }
  | { event: 'realtime_stream_start' }
  | { event: 'realtime_stream_chunk'; data: string; sample_rate: number }
  | { event: 'realtime_stream_stop' }
  | { event: 'reconnect'; last_turn_id: string }
  | { event: 'start_lesson' }
  | { event: 'cancel_turn' }
  | { event: 'image_input'; data: string; caption?: string }
  | { event: 'text_message'; text: string }
  | { event: 'transcribe_only'; data: string; sample_rate: number }
  | { event: 'voice_message'; data: string; mime_type: string }
  | { event: 'set_image_gen'; enabled: boolean }
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
  | { event: 'open_sketchpad'; prompt: string; invocation_id: string; text_bg?: string; im_bg?: string; duration_seconds?: number }
  | { event: 'take_photo'; prompt: string; invocation_id: string }
  | { event: 'record_video'; prompt: string; invocation_id: string }
  | { event: 'open_code_editor'; prompt: string; language: string; starter_code?: string; invocation_id: string; duration_seconds?: number }
  | { event: 'open_html_editor'; prompt: string; starter_html?: string; starter_css?: string; invocation_id: string; duration_seconds?: number }
  | { event: 'start_timer'; prompt: string; duration_seconds: number; invocation_id: string }
  | { event: 'show_progress'; sections: Array<{ title: string; idx: number; completed: boolean }>; current_idx: number; tasks: Array<{ concept: string; status: string }> }
  | { event: 'play_audio_clip'; text: string; speed: number }
  | { event: 'text_input'; prompt: string; placeholder?: string; multiline?: boolean; invocation_id: string; duration_seconds?: number }
  | { event: 'show_quiz'; prompt: string; choices: Array<{ label: string; text: string }>; correct_index: number; invocation_id: string; duration_seconds?: number }
  | { event: 'fill_in_the_blank'; prompt: string; template: string; answers: string[]; invocation_id: string; duration_seconds?: number }
  | { event: 'show_flashcard_deck'; prompt: string; cards: Array<{ front: string; back: string }>; invocation_id: string; duration_seconds?: number }
  | { event: 'ordering_exercise'; prompt: string; items: string[]; correct_order: number[]; invocation_id: string; duration_seconds?: number }
  | { event: 'capabilities'; image_gen_available: boolean }
  | { event: 'generating_image'; caption: string }
  | { event: 'show_image'; image_url: string; caption: string; prompt: string }
  | { event: 'generation_failed'; reason: string }
  | { event: 'code_stdout'; invocation_id: string; data: string }
  | { event: 'code_stderr'; invocation_id: string; data: string }
  | { event: 'code_done'; invocation_id: string; exit_code: number; elapsed_ms: number }
  | { event: 'code_error'; invocation_id: string; message: string }
  | { event: 'section_advanced'; curriculum: CurriculumState }
  | { event: 'curriculum_complete' }
  | { event: 'points_awarded'; points: number; total: number; reason: string }
  | { event: 'decompose_start' }
  | { event: 'decompose_complete'; lesson_id: string; curriculum: CurriculumData }
  | { event: 'history'; turns: Array<{
      role: 'user' | 'assistant'
      text: string
      figures?: Array<
        | { type: 'drawing'; data: string; prompt: string }
        | { type: 'generated_image'; image_url: string; caption: string; prompt: string }
      >
    }> }
  | { event: 'transcription_only'; text: string }
  | { event: 'tts_playing'; playing: boolean }
  | { event: 'status'; message: string }
  | { event: 'error'; message: string }
  | { event: 'response_end' }
  | { event: 'pong' }

// ── REST shapes ────────────────────────────────────────────────────────────

export interface VisualAidSourceConfig {
  enabled: boolean
  persistence: 'persistent' | 'ephemeral'
  timing: 'prefetched' | 'spontaneous'
}

export interface VisualAidConfig {
  pdf_pages?: boolean
  generated_images?: VisualAidSourceConfig
  web_images?: VisualAidSourceConfig
}

export interface Course {
  id: string
  user_id: string
  title: string
  description: string | null
  visual_aid_config?: VisualAidConfig
  default_persona_id?: string | null
  created_at: string
  updated_at: string
}

export interface Lesson {
  id: string
  course_id: string | null
  title: string
  description: string | null
  pdf_path: string | null
  visual_aid_config?: VisualAidConfig
  current_section_idx: number
  completed: boolean
  section_count: number
  persona_id?: string | null
  created_at: string
  updated_at: string
}

export interface IamUser {
  id: string
  email: string | null
  display_name: string | null
  email_verified: boolean
  is_admin: boolean
  bootstrap_managed: boolean
  created_at: string
}

export interface Persona {
  id: string
  name: string
  instructions: string
  voice_instructions: string
  tts_voice: string
  tts_speed: number
  tts_format: string
  tts_prep_prompt: string
  user_id: string | null
}

export interface Voice {
  id: string
  lang_code: string
  is_default: boolean
}

export interface LeaderboardEntry {
  rank: number
  username: string
  total_points: number
  lessons_completed: number
  sections_advanced: number
  current_streak: number
  longest_streak: number
  is_self: boolean
}

export interface LeaderboardResponse {
  entries: LeaderboardEntry[]
  self_rank: number | null
  self_points: number | null
}

export interface UserStats {
  username: string
  total_points: number
  lessons_completed: number
  sections_advanced: number
  current_streak: number
  longest_streak: number
  rank: number | null
}

export interface SttLanguage {
  name: string
  code: string | null  // null = auto-detect
}

export interface SttModel {
  id: string
  is_default: boolean
}

export interface SttProvider {
  id: string
  is_default: boolean
}

export interface VoiceArch {
  id: string
  label: string
  is_default: boolean
}
