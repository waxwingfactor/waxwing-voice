const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";
const PROPERTY_ID = process.env.NEXT_PUBLIC_PROPERTY_ID ?? "";

export { PROPERTY_ID };

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error?.message ?? `API error ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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
  summary: string | null;
  duration: number | null;
  started_at: string | null;
  created_at: string;
}

export interface TranscriptSegment {
  id: string;
  speaker: string;
  text: string;
  timestamp: number;
}

export interface CallEvent {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CallDetail extends CallListItem {
  property_id: string;
  company_id: string;
  twilio_call_sid: string | null;
  ended_at: string | null;
  escalation_status: string | null;
  action_items: string[] | null;
  next_steps: string[] | null;
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

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export function getPropertySummary(propertyId: string, date: string) {
  return apiFetch<PropertySummary>(
    `/v1/properties/${propertyId}/summary?date=${date}`
  );
}

export function listCalls(propertyId: string, params: Record<string, string> = {}) {
  const qs = new URLSearchParams({ property_id: propertyId, page_size: "20", ...params });
  return apiFetch<PaginatedResponse<CallListItem>>(`/v1/calls/?${qs}`);
}

export function getCall(callId: string) {
  return apiFetch<CallDetail>(`/v1/calls/${callId}`);
}

export function listLeads(propertyId: string, params: Record<string, string> = {}) {
  const qs = new URLSearchParams({ property_id: propertyId, page_size: "20", ...params });
  return apiFetch<PaginatedResponse<LeadListItem>>(`/v1/leads/?${qs}`);
}

export function getLead(leadId: string) {
  return apiFetch<LeadDetail>(`/v1/leads/${leadId}`);
}

export function getProperty(propertyId: string) {
  return apiFetch<PropertyDetail>(`/v1/properties/${propertyId}`);
}

export async function uploadDocument(propertyId: string, file: File) {
  const form = new FormData();
  form.append("property_id", propertyId);
  form.append("file", file);
  const res = await fetch(`${BASE}/v1/documents/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}` },
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error?.message ?? `Upload failed ${res.status}`);
  }
  return res.json();
}
