const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const ENV_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";
const PROPERTY_ID = process.env.NEXT_PUBLIC_PROPERTY_ID ?? "";

export { PROPERTY_ID };

// ---------------------------------------------------------------------------
// Token storage — runtime-mutable. Reads from localStorage in the browser
// (key: "waxwing_token") and falls back to NEXT_PUBLIC_API_TOKEN for dev.
// Login flow calls setAuthToken(); sign-out calls clearAuthToken().
// ---------------------------------------------------------------------------

const TOKEN_KEY = "waxwing_token";
const USER_KEY = "waxwing_user";

let inMemoryToken: string | null = null;

function getStoredToken(): string {
  if (inMemoryToken !== null) return inMemoryToken;
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage.getItem(TOKEN_KEY);
      if (stored) {
        inMemoryToken = stored;
        return stored;
      }
    } catch {
      // localStorage might be unavailable (SSR, privacy mode) — fall through.
    }
  }
  return ENV_TOKEN;
}

export function setAuthToken(token: string): void {
  inMemoryToken = token;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(TOKEN_KEY, token);
    } catch {
      // localStorage write blocked (privacy mode) — in-memory still works.
    }
  }
}

export function clearAuthToken(): void {
  inMemoryToken = null;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(USER_KEY);
    } catch {
      // localStorage unavailable — in-memory clear above is sufficient.
    }
  }
}

export function setStoredUser(user: AuthUser): void {
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(USER_KEY, JSON.stringify(user));
    } catch {
      // localStorage write blocked — user info won't persist across reloads.
    }
  }
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function hasAuthToken(): boolean {
  if (typeof window === "undefined") return Boolean(ENV_TOKEN);
  try {
    if (window.localStorage.getItem(TOKEN_KEY)) return true;
  } catch {
    // localStorage read blocked — fall through to env fallback.
  }
  return Boolean(ENV_TOKEN);
}

// ---------------------------------------------------------------------------
// Error type — exposes both the backend error code and human message so
// callers can render diagnostic info (e.g. "PROPERTY_NOT_FOUND").
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;

  constructor(message: string, code: string, status: number, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; retryable?: boolean };
}

// ---------------------------------------------------------------------------
// Auth-failure observer — page.tsx subscribes to this so it can bounce the
// user to /login when any call returns 401. Keeps the api layer decoupled
// from the React tree.
// ---------------------------------------------------------------------------

type AuthErrorListener = () => void;
const authErrorListeners = new Set<AuthErrorListener>();

export function subscribeAuthError(listener: AuthErrorListener): () => void {
  authErrorListeners.add(listener);
  return () => { authErrorListeners.delete(listener); };
}

function notifyAuthError(): void {
  for (const l of authErrorListeners) {
    try { l(); } catch {
      // Listener errors must not break the fetch flow.
    }
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const body: ErrorEnvelope = await res.json().catch(() => ({}));
    const code = body?.error?.code ?? `HTTP_${res.status}`;
    const message = body?.error?.message ?? `API error ${res.status}`;
    const retryable = body?.error?.retryable ?? false;
    if (res.status === 401) notifyAuthError();
    throw new ApiError(message, code, res.status, retryable);
  }
  // 204 No Content — nothing to parse.
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
  company_id: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface PropertySummary {
  property_id: string;
  property_name: string;
  date: string;
  calls_today: number;
  new_leads_today: number;
  tours_booked_today: number;
  escalations_today: number;
  follow_ups_sent_today: number;
  open_action_items: number;
}

export interface CallListItem {
  id: string;
  caller_phone: string;
  status: string;
  primary_intent: string | null;
  sentiment: string | null;
  escalation_flag: boolean;
  duration: number | null;
  started_at: string | null;
  created_at: string;
}

export interface TranscriptSegment {
  id: string;
  speaker: string;
  text: string;
  /** Seconds (float) since call start. */
  timestamp: number;
}

export interface CallEvent {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface CallDetail extends CallListItem {
  property_id: string;
  company_id: string;
  twilio_call_sid: string | null;
  ended_at: string | null;
  escalation_status: string | null;
  summary: string | null;
  action_items: string[] | null;
  next_steps: string | null;
  lead_fields_extracted: Record<string, unknown> | null;
  updated_at: string;
  transcript_segments: TranscriptSegment[];
  call_events: CallEvent[];
  lead_id: string | null;
}

export interface LeadListItem {
  id: string;
  property_id: string;
  call_id: string | null;
  name: string | null;
  phone: string | null;
  email: string | null;
  lead_status: string;
  lead_score: string | null;
  tour_interest: boolean;
  urgency: string | null;
  desired_unit_type: string | null;
  created_at: string;
}

export interface LeadDetail extends LeadListItem {
  company_id: string;
  budget: number | null;
  move_in_date: string | null;
  pet_info: Record<string, unknown> | null;
  number_of_occupants: number | null;
  reason_for_moving: string | null;
  how_heard: string | null;
  updated_at: string;
}

export interface PropertyDetail {
  id: string;
  name: string;
  address: string | null;
  description: string | null;
  amenities: Record<string, unknown> | null;
  office_hours: Record<string, unknown> | null;
  leasing_policies: string | null;
  maintenance_instructions: string | null;
  escalation_contacts: Record<string, unknown>[] | null;
  business_hour_rules: Record<string, unknown> | null;
  call_handling_rules: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface PropertyPatchBody {
  name?: string;
  address?: string | null;
  description?: string | null;
  amenities?: Record<string, unknown> | null;
  office_hours?: Record<string, unknown> | null;
  leasing_policies?: string | null;
  maintenance_instructions?: string | null;
  escalation_contacts?: Record<string, unknown>[] | null;
  business_hour_rules?: Record<string, unknown> | null;
  call_handling_rules?: Record<string, unknown> | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentUploadResponse {
  document_id: string;
  file_name: string;
  chunk_count: number;
  processing_status: string;
}

export interface DocumentListItem {
  id: string;
  property_id: string;
  file_name: string;
  file_type: string;
  processing_status: string;
  chunk_count: number;
  created_at: string;
}

export interface DocumentDetail extends DocumentListItem {
  file_size?: number | null;
  s3_key?: string | null;
  extracted_text_preview?: string | null;
  updated_at?: string;
}

export interface ReindexResponse {
  document_id: string;
  reindexed: boolean;
  chunks_created: number;
}

export interface DeleteDocumentResponse {
  deleted: boolean;
  document_id: string;
  chunks_deleted: number;
}

export interface CalendarStatusResponse {
  connected: boolean;
  calendar_id: string | null;
  service_account_configured: boolean;
  last_check_at: string;
  errors: string[];
}

export interface BookingListItem {
  id: string;
  property_id: string;
  lead_id: string | null;
  call_id: string | null;
  tour_date: string;
  start_time: string;
  end_time: string;
  status: string;
  created_at: string;
}

export interface BookingDetail extends BookingListItem {
  company_id?: string;
  calendar_event_id?: string | null;
  notes?: string | null;
  updated_at?: string;
}

export interface EmailListItem {
  id: string;
  property_id: string;
  lead_id: string | null;
  call_id: string | null;
  recipient: string;
  template_type: string;
  delivery_status: string;
  subject: string;
  created_at: string;
}

export interface EmailDetail extends EmailListItem {
  body?: string | null;
  sent_at?: string | null;
  error_message?: string | null;
  updated_at?: string;
}

export interface AuditLogItem {
  id: string;
  actor_type: string;
  actor_id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface CallPatchBody {
  status?: string;
  ended_at?: string | null;
  duration?: number | null;
  primary_intent?: string | null;
  sentiment?: string | null;
  escalation_status?: string | null;
  escalation_flag?: boolean;
}

// ---------------------------------------------------------------------------
// Param shapes for list endpoints — these mirror the actual filters the
// backend accepts. `q` is now supported on calls + leads for substring search.
// ---------------------------------------------------------------------------

export interface ListCallsParams {
  page?: number;
  page_size?: number;
  status?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
}

export interface ListLeadsParams {
  page?: number;
  page_size?: number;
  /** "hot" | "warm" | "cold" */
  lead_score?: string;
  /** "new" | "contacted" | "toured" | "applied" | "closed" | "lost" */
  lead_status?: string;
  tour_interest?: boolean;
  date_from?: string;
  date_to?: string;
  q?: string;
}

export interface ListDocumentsParams {
  page?: number;
  page_size?: number;
}

export interface ListBookingsParams {
  page?: number;
  page_size?: number;
  status?: string;
  lead_id?: string;
  date_from?: string;
  date_to?: string;
}

export interface ListEmailsParams {
  page?: number;
  page_size?: number;
  lead_id?: string;
  call_id?: string;
  template_type?: string;
  delivery_status?: string;
  date_from?: string;
  date_to?: string;
}

export interface ListAuditLogsParams {
  page?: number;
  page_size?: number;
  action?: string;
  entity_type?: string;
  actor_type?: string;
  date_from?: string;
  date_to?: string;
}

function buildQuery(base: Record<string, string>, params: Record<string, unknown>): string {
  const qs = new URLSearchParams(base);
  for (const [key, raw] of Object.entries(params)) {
    if (raw === undefined || raw === null || raw === "") continue;
    qs.set(key, typeof raw === "boolean" ? String(raw) : String(raw));
  }
  return qs.toString();
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function loginUser(email: string, password: string): Promise<LoginResponse> {
  // /v1/auth/login does NOT require an existing token — bypass the apiFetch
  // header logic so we don't attach a stale Bearer.
  const res = await fetch(`${BASE}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body: ErrorEnvelope = await res.json().catch(() => ({}));
    const code = body?.error?.code ?? `HTTP_${res.status}`;
    const message = body?.error?.message ?? `Login failed ${res.status}`;
    const retryable = body?.error?.retryable ?? false;
    throw new ApiError(message, code, res.status, retryable);
  }
  return res.json() as Promise<LoginResponse>;
}

// ---------------------------------------------------------------------------
// Properties
// ---------------------------------------------------------------------------

export function getPropertySummary(propertyId: string, date: string) {
  return apiFetch<PropertySummary>(
    `/v1/properties/${propertyId}/summary?date=${date}`
  );
}

export function getProperty(propertyId: string) {
  return apiFetch<PropertyDetail>(`/v1/properties/${propertyId}`);
}

export function patchProperty(propertyId: string, body: PropertyPatchBody) {
  return apiFetch<PropertyDetail>(`/v1/properties/${propertyId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

export function listCalls(propertyId: string, params: ListCallsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    {
      page: params.page,
      status: params.status,
      date_from: params.date_from,
      date_to: params.date_to,
      q: params.q,
    }
  );
  return apiFetch<PaginatedResponse<CallListItem>>(`/v1/calls/?${qs}`);
}

export function getCall(callId: string) {
  return apiFetch<CallDetail>(`/v1/calls/${callId}`);
}

export function patchCall(callId: string, body: CallPatchBody) {
  return apiFetch<CallDetail>(`/v1/calls/${callId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Leads
// ---------------------------------------------------------------------------

export function listLeads(propertyId: string, params: ListLeadsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    {
      page: params.page,
      lead_score: params.lead_score,
      lead_status: params.lead_status,
      tour_interest: params.tour_interest,
      date_from: params.date_from,
      date_to: params.date_to,
      q: params.q,
    }
  );
  return apiFetch<PaginatedResponse<LeadListItem>>(`/v1/leads/?${qs}`);
}

export function getLead(leadId: string) {
  return apiFetch<LeadDetail>(`/v1/leads/${leadId}`);
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

export function listDocuments(propertyId: string, params: ListDocumentsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    { page: params.page }
  );
  return apiFetch<PaginatedResponse<DocumentListItem>>(`/v1/documents/?${qs}`);
}

export function getDocument(documentId: string) {
  return apiFetch<DocumentDetail>(`/v1/documents/${documentId}`);
}

export function deleteDocument(documentId: string) {
  return apiFetch<DeleteDocumentResponse>(`/v1/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function reindexDocument(documentId: string) {
  return apiFetch<ReindexResponse>(`/v1/documents/${documentId}/reindex`, {
    method: "POST",
  });
}

export async function uploadDocument(propertyId: string, file: File): Promise<DocumentUploadResponse> {
  const form = new FormData();
  form.append("property_id", propertyId);
  form.append("file", file);
  const token = getStoredToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}/v1/documents/upload`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    const body: ErrorEnvelope = await res.json().catch(() => ({}));
    const code = body?.error?.code ?? `HTTP_${res.status}`;
    const message = body?.error?.message ?? `Upload failed ${res.status}`;
    const retryable = body?.error?.retryable ?? false;
    if (res.status === 401) notifyAuthError();
    throw new ApiError(message, code, res.status, retryable);
  }
  return res.json() as Promise<DocumentUploadResponse>;
}

// ---------------------------------------------------------------------------
// Calendar status
// ---------------------------------------------------------------------------

export function getCalendarStatus() {
  return apiFetch<CalendarStatusResponse>(`/v1/integrations/calendar/status`);
}

// ---------------------------------------------------------------------------
// Bookings
// ---------------------------------------------------------------------------

export function listBookings(propertyId: string, params: ListBookingsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    {
      page: params.page,
      status: params.status,
      lead_id: params.lead_id,
      date_from: params.date_from,
      date_to: params.date_to,
    }
  );
  return apiFetch<PaginatedResponse<BookingListItem>>(`/v1/bookings/?${qs}`);
}

export function getBooking(bookingId: string) {
  return apiFetch<BookingDetail>(`/v1/bookings/${bookingId}`);
}

// ---------------------------------------------------------------------------
// Emails
// ---------------------------------------------------------------------------

export function listEmails(propertyId: string, params: ListEmailsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    {
      page: params.page,
      lead_id: params.lead_id,
      call_id: params.call_id,
      template_type: params.template_type,
      delivery_status: params.delivery_status,
      date_from: params.date_from,
      date_to: params.date_to,
    }
  );
  return apiFetch<PaginatedResponse<EmailListItem>>(`/v1/emails/?${qs}`);
}

export function getEmail(emailId: string) {
  return apiFetch<EmailDetail>(`/v1/emails/${emailId}`);
}

// ---------------------------------------------------------------------------
// Audit logs
// ---------------------------------------------------------------------------

export function listAuditLogs(propertyId: string, params: ListAuditLogsParams = {}) {
  const qs = buildQuery(
    { property_id: propertyId, page_size: String(params.page_size ?? 20) },
    {
      page: params.page,
      action: params.action,
      entity_type: params.entity_type,
      actor_type: params.actor_type,
      date_from: params.date_from,
      date_to: params.date_to,
    }
  );
  return apiFetch<PaginatedResponse<AuditLogItem>>(`/v1/audit-logs/?${qs}`);
}
