"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Bell,
  Calendar,
  CalendarCheck,
  Check,
  ChevronRight,
  Database,
  FileText,
  Filter,
  Home,
  Inbox,
  Loader2,
  Mic,
  Phone,
  PhoneIncoming,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldAlert,
  Trash2,
  Upload,
  UsersRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ChangeEvent, PointerEvent, ReactNode, useEffect, useState } from "react";
import {
  PROPERTY_ID,
  getPropertySummary,
  listCalls,
  listLeads,
  getCall,
  getProperty,
  uploadDocument,
  type PropertySummary,
  type CallListItem,
  type CallDetail,
  type LeadListItem,
  type PropertyDetail,
} from "../lib/api";

type View = "home" | "calls" | "call-detail" | "leads" | "knowledge" | "settings";
type DataMode = "ready" | "loading" | "empty" | "error" | "denied";

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatDateGroup(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const days = diff / 86400000;
  if (days < 1) return "Today";
  if (days < 2) return "Yesterday";
  if (days < 7) return "This week";
  return "Older";
}

function mapCallStatus(status: string): string {
  if (status === "active") return "Live";
  if (status === "completed") return "Completed";
  if (status === "escalated") return "Escalated";
  if (status === "abandoned") return "Abandoned";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function mapLeadStatusDisplay(status: string): string {
  const map: Record<string, string> = {
    new: "New",
    contacted: "Contacted",
    toured: "Toured",
    applied: "Applied",
    closed: "Closed",
    lost: "Lost",
  };
  return map[status] ?? status;
}

function scoreFromTier(tier: string | null): number | null {
  if (tier === "hot") return 90;
  if (tier === "warm") return 60;
  if (tier === "cold") return 30;
  return null;
}

function capitalize(s: string | null | undefined): string {
  if (!s) return "Unknown";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------------------------------------------------------------------------
// Nav definitions (badges wired in Sidebar via props)
// ---------------------------------------------------------------------------

const navItemDefs: Array<{ id: View; label: string; icon: LucideIcon }> = [
  { id: "home", label: "Dashboard", icon: Home },
  { id: "calls", label: "Calls", icon: Phone },
  { id: "leads", label: "Leads", icon: UsersRound },
  { id: "knowledge", label: "Knowledge", icon: Database },
  { id: "settings", label: "Settings", icon: Settings },
];

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const [view, setView] = useState<View>("home");
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  const [dataMode, setDataMode] = useState<DataMode>("loading");

  const [summary, setSummary] = useState<PropertySummary | null>(null);
  const [calls, setCalls] = useState<CallListItem[]>([]);
  const [leads, setLeads] = useState<LeadListItem[]>([]);
  const [property, setProperty] = useState<PropertyDetail | null>(null);

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    Promise.all([
      getPropertySummary(PROPERTY_ID, today),
      listCalls(PROPERTY_ID),
      listLeads(PROPERTY_ID),
      getProperty(PROPERTY_ID),
    ])
      .then(([sum, callsRes, leadsRes, prop]) => {
        setSummary(sum);
        setCalls(callsRes.items);
        setLeads(leadsRes.items);
        setProperty(prop);
        setDataMode("ready");
      })
      .catch(() => setDataMode("error"));
  }, []);

  const openCall = (callId: string) => {
    setSelectedCallId(callId);
    setView("call-detail");
  };

  const title =
    view === "call-detail"
      ? "Call detail"
      : navItemDefs.find((item) => item.id === view)?.label ?? "Dashboard";

  return (
    <>
      <LiquidGlassFilters />
      <ForestBackground />
      <div className="app" data-sidebar="full" data-theme="light" data-density="comfortable">
        <Sidebar
          active={view === "call-detail" ? "calls" : view}
          onNavigate={setView}
          totalCalls={calls.length}
          totalLeads={leads.length}
        />
        <main className="main">
          <Topbar title={title} dataMode={dataMode} onDataModeChange={setDataMode} />
          <StateGate mode={dataMode} view={title} onReset={() => window.location.reload()}>
            {view === "home" && (
              <HomeDashboard summary={summary} calls={calls} onOpenCall={openCall} onNavigate={setView} />
            )}
            {view === "calls" && <CallsView calls={calls} onOpenCall={openCall} />}
            {view === "call-detail" && selectedCallId && (
              <CallDetailView callId={selectedCallId} onBack={() => setView("calls")} />
            )}
            {view === "leads" && <LeadsView leads={leads} onOpenCall={openCall} />}
            {view === "knowledge" && <KnowledgeView />}
            {view === "settings" && <SettingsView property={property} />}
          </StateGate>
        </main>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

function LiquidGlassFilters() {
  return (
    <svg className="liquid-glass-defs" aria-hidden="true" focusable="false">
      <defs>
        <filter id="liquid-glass-distortion" x="-24%" y="-24%" width="148%" height="148%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.012 0.018" numOctaves="2" seed="8" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="18" xChannelSelector="R" yChannelSelector="G" result="distorted" />
          <feGaussianBlur in="distorted" stdDeviation="0.45" result="softened" />
          <feColorMatrix in="softened" type="saturate" values="1.18" />
        </filter>
      </defs>
    </svg>
  );
}

function ForestBackground() {
  return (
    <div className="forest-background" aria-hidden="true">
      <div className="forest-photo" />
      <div className="forest-mist" />
      <div className="forest-depth" />
      <div className="forest-tint" />
    </div>
  );
}

function Sidebar({
  active,
  onNavigate,
  totalCalls,
  totalLeads,
}: {
  active: View;
  onNavigate: (view: View) => void;
  totalCalls: number;
  totalLeads: number;
}) {
  const navItems = navItemDefs.map((item) => ({
    ...item,
    badge:
      item.id === "calls" && totalCalls > 0
        ? String(totalCalls)
        : item.id === "leads" && totalLeads > 0
        ? String(totalLeads)
        : undefined,
  }));

  return (
    <aside className="sidebar">
      <div className="brand">
        <WaxwingMark />
        <span className="wordmark">Waxwing Voice</span>
      </div>

      <div className="nav-section-label">Workspace</div>
      {navItems.slice(0, 4).map((item) => (
        <NavButton key={item.id} item={item} active={active === item.id} onNavigate={onNavigate} />
      ))}

      <div className="nav-section-label">Configure</div>
      <NavButton item={navItems[4]} active={active === "settings"} onNavigate={onNavigate} />

      <div className="agent-card">
        <div className="agent-row">
          <div className="orb" data-state="idle">
            <span className="orb-dot" />
          </div>
          <div>
            <div className="agent-name">Waxwing Voice</div>
            <div className="agent-status">Listening for calls</div>
          </div>
        </div>
        <div className="agent-meta">
          <span className="inline-flex items-center gap-1">
            <PhoneIncoming size={11} /> {totalCalls} today
          </span>
          <span style={{ opacity: 0.5 }}>-</span>
          <span>v0.1</span>
        </div>
      </div>
    </aside>
  );
}

function NavButton({
  item,
  active,
  onNavigate,
}: {
  item: { id: View; label: string; icon: LucideIcon; badge?: string };
  active: boolean;
  onNavigate: (view: View) => void;
}) {
  const Icon = item.icon;
  return (
    <button className={active ? "nav-item active" : "nav-item"} data-label={item.label} onClick={() => onNavigate(item.id)}>
      <span className="nav-icon">
        <Icon size={18} />
      </span>
      <span className="nav-label">{item.label}</span>
      {item.badge && <span className="nav-badge">{item.badge}</span>}
    </button>
  );
}

function Topbar({
  title,
  dataMode,
  onDataModeChange,
}: {
  title: string;
  dataMode: DataMode;
  onDataModeChange: (mode: DataMode) => void;
}) {
  const apiUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>{title}</h1>
        <span className="greet">Today, {new Date().toLocaleDateString("en-US", { month: "long", day: "numeric" })}</span>
      </div>
      <div className="topbar-actions">
        <div className={apiUrl ? "api-pill connected" : "api-pill"}>
          <span className="dot" />
          {apiUrl ? apiUrl : "API mock mode"}
        </div>
        <select className="state-select" value={dataMode} onChange={(event) => onDataModeChange(event.target.value as DataMode)} aria-label="Preview data state">
          <option value="ready">Ready</option>
          <option value="loading">Loading</option>
          <option value="empty">Empty</option>
          <option value="error">Error</option>
          <option value="denied">Denied</option>
        </select>
        <button className="btn icon ghost" aria-label="Notifications">
          <Bell size={16} />
        </button>
        <div className="avatar" style={{ background: "var(--sand-300)", color: "var(--teal-700)" }}>
          HW
        </div>
      </div>
    </header>
  );
}

function StateGate({
  mode,
  view,
  onReset,
  children,
}: {
  mode: DataMode;
  view: string;
  onReset: () => void;
  children: ReactNode;
}) {
  if (mode === "ready") return <>{children}</>;

  const stateContent: Record<Exclude<DataMode, "ready">, { icon: LucideIcon; title: string; message: string; action?: string }> = {
    loading: {
      icon: Loader2,
      title: `Loading ${view.toLowerCase()}`,
      message: "Fetching the latest data from Waxwing backend.",
    },
    empty: {
      icon: Inbox,
      title: `No ${view.toLowerCase()} data yet`,
      message: "This page is ready — no records have been created yet.",
    },
    error: {
      icon: AlertTriangle,
      title: `Could not load ${view.toLowerCase()}`,
      message: "Could not reach the backend API. Check that it is running on port 8000.",
      action: "Retry",
    },
    denied: {
      icon: ShieldAlert,
      title: "Permission required",
      message: "Your token does not have access to this resource.",
      action: "Request access",
    },
  };

  const details = stateContent[mode];
  const Icon = details.icon;

  return (
    <section className="page">
      <div className="state-card card">
        <div className={mode === "loading" ? "state-icon spinning" : "state-icon"}>
          <Icon size={28} />
        </div>
        <div>
          <h2>{details.title}</h2>
          <p>{details.message}</p>
          <button className="btn primary" onClick={onReset}>
            {details.action ?? "Show data"}
          </button>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Home dashboard
// ---------------------------------------------------------------------------

function HomeDashboard({
  summary,
  calls,
  onOpenCall,
  onNavigate,
}: {
  summary: PropertySummary | null;
  calls: CallListItem[];
  onOpenCall: (id: string) => void;
  onNavigate: (view: View) => void;
}) {
  const kpis = [
    { label: "Calls today", value: summary?.calls_today ?? 0, delta: "+0%", icon: Phone, trend: [4, 5, 4, 6, 5, 7, summary?.calls_today ?? 0] },
    { label: "New leads", value: summary?.new_leads_today ?? 0, delta: "+0", icon: UsersRound, trend: [2, 2, 3, 3, 4, 4, summary?.new_leads_today ?? 0] },
    { label: "Tours booked", value: summary?.tours_booked_today ?? 0, delta: "+0", icon: CalendarCheck, trend: [1, 1, 2, 2, 3, 4, summary?.tours_booked_today ?? 0] },
    { label: "Escalated calls", value: summary?.escalations_today ?? 0, delta: "+0", icon: ShieldAlert, trend: [3, 3, 2, 2, 1, 2, summary?.escalations_today ?? 0] },
    { label: "Follow-ups sent", value: summary?.follow_ups_sent_today ?? 0, delta: "+0", icon: Send, trend: [1, 2, 2, 3, 4, 3, summary?.follow_ups_sent_today ?? 0] },
    { label: "Open action items", value: summary?.open_action_items ?? 0, delta: "+0", icon: Check, trend: [5, 4, 6, 5, 7, 8, summary?.open_action_items ?? 0] },
  ];

  return (
    <section className="page stagger">
      <HeroCard summary={summary} onNavigate={onNavigate} />
      <div className="kpi-grid">
        {kpis.map((kpi) => (
          <KpiCard key={kpi.label} {...kpi} />
        ))}
      </div>
      <div className="content-grid">
        <div className="card flat table-card">
          <CardHeader
            title="Recent calls"
            subtitle={`${summary?.calls_today ?? 0} calls today`}
          />
          {calls.length > 0 ? (
            <CallsTable rows={calls.slice(0, 5)} onOpenCall={onOpenCall} compact />
          ) : (
            <InlineEmpty title="No calls recorded yet" />
          )}
        </div>
        <div className="stack">
          <RecentActivityCard calls={calls} />
        </div>
      </div>
    </section>
  );
}

function HeroCard({ summary, onNavigate }: { summary: PropertySummary | null; onNavigate: (view: View) => void }) {
  return (
    <div className="card hero-card">
      <svg className="hero-peaks" width="280" height="180" viewBox="0 0 280 180" fill="none" aria-hidden="true">
        <path d="M10 170 L70 30 L130 170" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M40 120 L100 120" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        <path d="M150 30 L210 170 L270 30" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="hero-content">
        <div>
          <div className="eyebrow">
            {summary?.property_name ?? "Loading…"}
          </div>
          <h2>
            Waxwing Voice handled {summary?.calls_today ?? 0} calls today
            {summary && summary.new_leads_today > 0 ? ` — ${summary.new_leads_today} new leads captured.` : "."}
          </h2>
          <p>
            {summary
              ? `${summary.tours_booked_today} tour${summary.tours_booked_today !== 1 ? "s" : ""} booked. ${summary.escalations_today} escalation${summary.escalations_today !== 1 ? "s" : ""} today.`
              : "Fetching metrics…"}
          </p>
        </div>
        <button className="btn primary" onClick={() => onNavigate("calls")}>
          Review queue <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

function setRippleOrigin(event: PointerEvent<HTMLDivElement>) {
  const rect = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty("--ripple-x", `${event.clientX - rect.left}px`);
  event.currentTarget.style.setProperty("--ripple-y", `${event.clientY - rect.top}px`);
}

function KpiCard({
  label,
  value,
  delta,
  icon: Icon,
  trend,
}: {
  label: string;
  value: number;
  delta: string;
  icon: LucideIcon;
  trend: number[];
}) {
  const isDown = delta.startsWith("-");
  const DeltaIcon = isDown ? ArrowDown : ArrowUp;

  return (
    <div className="kpi" onPointerEnter={setRippleOrigin} onPointerMove={setRippleOrigin}>
      <span className="kpi-glass" aria-hidden="true" />
      <div className="kpi-label">
        <Icon size={14} /> {label}
      </div>
      <div className="kpi-value">{value}</div>
      <div className={isDown ? "kpi-delta down" : "kpi-delta"}>
        <DeltaIcon size={12} /> {delta} vs last week
      </div>
      <div className="kpi-spark">
        <Sparkline data={trend} tone={isDown ? "down" : "up"} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Calls view
// ---------------------------------------------------------------------------

function CallsView({ calls, onOpenCall }: { calls: CallListItem[]; onOpenCall: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("All");
  const [intent, setIntent] = useState("All");
  const [flag, setFlag] = useState("All");

  const filtered = calls.filter((call) => {
    const text = `${call.caller_phone ?? ""} ${call.primary_intent ?? ""} ${call.summary ?? ""}`.toLowerCase();
    return (
      text.includes(query.toLowerCase()) &&
      (status === "All" || call.status === status.toLowerCase()) &&
      (intent === "All" || (call.primary_intent ?? "").toLowerCase() === intent.toLowerCase()) &&
      (flag === "All" || (flag === "Escalated" && call.escalation_flag))
    );
  });

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search phone, intent, summary" />
        <SelectField label="Status" value={status} onChange={setStatus} options={["All", "Active", "Completed", "Escalated", "Abandoned"]} />
        <SelectField label="Intent" value={intent} onChange={setIntent} options={["All", "Tour", "Availability", "Pricing", "Maintenance", "Unknown"]} />
        <SelectField label="Flag" value={flag} onChange={setFlag} options={["All", "Escalated"]} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader title="Call history" subtitle={`${filtered.length} matching calls`} />
        {filtered.length ? (
          <CallsTable rows={filtered} onOpenCall={onOpenCall} />
        ) : (
          <InlineEmpty title="No calls match those filters" />
        )}
      </div>
    </section>
  );
}

function CallsTable({
  rows,
  onOpenCall,
  compact = false,
}: {
  rows: CallListItem[];
  onOpenCall: (id: string) => void;
  compact?: boolean;
}) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Caller</th>
          {!compact && <th>Intent</th>}
          <th>Status</th>
          {!compact && <th>Duration</th>}
          <th style={{ textAlign: "right" }}>Time</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((call) => (
          <tr key={call.id} onClick={() => onOpenCall(call.id)}>
            <td>
              <div className="person-cell">
                <div className="avatar">{call.caller_phone ? call.caller_phone.slice(-4) : "??"}</div>
                <div>
                  <div className="strong">{call.caller_phone ?? "Unknown"}</div>
                  <div className="mono subtext">{capitalize(call.primary_intent)}</div>
                </div>
              </div>
            </td>
            {!compact && <td>{capitalize(call.primary_intent)}</td>}
            <td>
              <div className="status-stack">
                <span
                  className={`pill ${
                    call.status === "completed"
                      ? "success"
                      : call.status === "active"
                      ? "teal"
                      : call.status === "escalated"
                      ? "warn"
                      : ""
                  }`}
                >
                  {mapCallStatus(call.status)}
                </span>
                {call.escalation_flag && <span className="pill warn">Escalated</span>}
              </div>
            </td>
            {!compact && <td className="mono">{formatDuration(call.duration)}</td>}
            <td style={{ textAlign: "right" }}>
              <div>{formatRelativeTime(call.created_at)}</div>
              <div className="mono subtext">{formatDuration(call.duration)}</div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Call detail view (self-fetching)
// ---------------------------------------------------------------------------

function CallDetailView({ callId, onBack }: { callId: string; onBack: () => void }) {
  const [call, setCall] = useState<CallDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getCall(callId)
      .then((data) => {
        setCall(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [callId]);

  if (loading) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to calls</button>
        <div className="state-card card">
          <div className="state-icon spinning"><Loader2 size={28} /></div>
          <div><h2>Loading call detail</h2><p>Fetching transcript and events…</p></div>
        </div>
      </section>
    );
  }

  if (!call) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to calls</button>
        <InlineEmpty title="Call not found" />
      </section>
    );
  }

  const extractedFields = Object.entries(call.lead_fields_extracted ?? {}).map<[string, string]>(
    ([k, v]) => [capitalize(k.replace(/_/g, " ")), String(v)]
  );

  return (
    <section className="page stack">
      <button className="btn ghost fit" onClick={onBack}>Back to calls</button>
      <div className="detail-grid">
        <div className="stack">
          <div className="card">
            <CardHeader
              title={call.caller_phone ?? "Unknown caller"}
              subtitle={`${mapCallStatus(call.status)} — ${formatDuration(call.duration)}`}
            />
            {call.summary && <p className="detail-summary">{call.summary}</p>}
            <div className="fact-grid">
              <Fact label="Status" value={mapCallStatus(call.status)} />
              <Fact label="Intent" value={capitalize(call.primary_intent)} />
              <Fact label="Duration" value={formatDuration(call.duration)} />
              <Fact label="Sentiment" value={capitalize(call.sentiment)} />
            </div>
          </div>
          <div className="card">
            <CardHeader title="Transcript" subtitle="Speaker-separated call transcript" />
            <div className="transcript">
              {call.transcript_segments.length > 0 ? (
                call.transcript_segments.map((seg) => (
                  <div
                    key={seg.id}
                    className={seg.speaker === "agent" ? "transcript-line agent" : "transcript-line"}
                  >
                    <span className="mono">{formatDuration(Math.round(seg.timestamp))}</span>
                    <strong>{seg.speaker === "agent" ? "Waxwing Voice" : "Caller"}</strong>
                    <p>{seg.text}</p>
                  </div>
                ))
              ) : (
                <InlineEmpty title="No transcript available for this call" />
              )}
            </div>
          </div>
        </div>
        <div className="stack">
          {extractedFields.length > 0 && (
            <InfoCard title="Extracted lead fields" rows={extractedFields} />
          )}
          <div className="card">
            <CardHeader title="Action items" subtitle={`${(call.action_items ?? []).length} open`} />
            {call.action_items?.length ? (
              <ul className="check-list">
                {call.action_items.map((item) => (
                  <li key={item}>
                    <Check size={14} /> {item}
                  </li>
                ))}
              </ul>
            ) : (
              <InlineEmpty title="No open action items" />
            )}
          </div>
          {call.escalation_flag && (
            <div className="card">
              <CardHeader title="Escalation" subtitle={call.escalation_status ?? "Flagged for review"} />
              <div className="fact-grid">
                <Fact label="Status" value={call.escalation_status ?? "Pending"} />
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Leads view
// ---------------------------------------------------------------------------

function LeadsView({ leads, onOpenCall }: { leads: LeadListItem[]; onOpenCall: (id: string) => void }) {
  const [status, setStatus] = useState("All");
  const [score, setScore] = useState("All");
  const [query, setQuery] = useState("");

  const filtered = leads.filter((lead) => {
    const text = `${lead.name ?? ""} ${lead.phone ?? ""} ${lead.email ?? ""} ${lead.desired_unit_type ?? ""}`.toLowerCase();
    return (
      text.includes(query.toLowerCase()) &&
      (status === "All" || lead.lead_status === status.toLowerCase()) &&
      (score === "All" || lead.lead_score === score.toLowerCase())
    );
  });

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search leads" />
        <SelectField
          label="Status"
          value={status}
          onChange={setStatus}
          options={["All", "New", "Contacted", "Toured", "Applied", "Closed", "Lost"]}
        />
        <SelectField label="Score" value={score} onChange={setScore} options={["All", "Hot", "Warm", "Cold"]} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader title="Leads" subtitle={`${filtered.length} active leads`} />
        {filtered.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Lead</th>
                <th>Status</th>
                <th>Score</th>
                <th>Unit preference</th>
                <th>Tour interest</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((lead) => (
                <tr
                  key={lead.id}
                  onClick={() => lead.call_id && onOpenCall(lead.call_id)}
                  style={{ cursor: lead.call_id ? "pointer" : "default" }}
                >
                  <td>
                    <div className="person-cell">
                      <div className="avatar">{initials(lead.name ?? lead.phone ?? "?")}</div>
                      <div>
                        <div className="strong">{lead.name ?? "Unknown"}</div>
                        <div className="mono subtext">{lead.phone ?? "—"}</div>
                        <div className="subtext">{lead.email ?? "—"}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <LeadStatusPill value={lead.lead_status} />
                  </td>
                  <td>
                    {lead.lead_score
                      ? <ScoreBar value={scoreFromTier(lead.lead_score) ?? 0} />
                      : "—"}
                  </td>
                  <td>{lead.desired_unit_type ?? "—"}</td>
                  <td>
                    {lead.tour_interest
                      ? <span className="pill success">Yes</span>
                      : <span className="pill">No</span>}
                  </td>
                  <td className="subtext">{formatRelativeTime(lead.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <InlineEmpty title="No leads match those filters" />
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Knowledge view (real upload)
// ---------------------------------------------------------------------------

type DocEntry = { id: string; name: string; status: string; uploaded: string };

function KnowledgeView() {
  const [docs, setDocs] = useState<DocEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const tempId = `temp-${Date.now()}`;
    setDocs((cur) => [{ id: tempId, name: file.name, status: "Processing", uploaded: "Just now" }, ...cur]);
    setUploading(true);
    event.target.value = "";

    try {
      const result = await uploadDocument(PROPERTY_ID, file);
      setDocs((cur) =>
        cur.map((doc) =>
          doc.id === tempId
            ? { ...doc, id: result.document_id, status: result.processing_status === "indexed" ? "Indexed" : "Processing" }
            : doc
        )
      );
    } catch {
      setDocs((cur) =>
        cur.map((doc) => (doc.id === tempId ? { ...doc, status: "Failed" } : doc))
      );
    } finally {
      setUploading(false);
    }
  };

  const reindex = () => {
    setProcessing(true);
    window.setTimeout(() => setProcessing(false), 900);
  };

  const failedCount = docs.filter((d) => d.status === "Failed").length;

  return (
    <section className="page stack">
      <div className="knowledge-grid">
        <div className="card">
          <CardHeader title="Property knowledge" subtitle="Documents uploaded here power Waxwing Voice on calls" />
          <div className="health-row">
            <HealthIndicator label="Knowledge health" value={docs.length > 0 ? "Good" : "Empty"} tone="success" />
            <HealthIndicator label="Failed docs" value={String(failedCount)} tone="warn" />
            <button className="btn primary" onClick={reindex}>
              <RefreshCw size={14} className={processing ? "spin" : ""} /> Re-index
            </button>
          </div>
        </div>
        <div className="card">
          <CardHeader title="Document upload" subtitle="PDFs, DOCX, and TXT files for retrieval" />
          <label className="upload-zone">
            <Upload size={22} />
            <span>{uploading ? "Uploading…" : "Choose a knowledge document"}</span>
            <input type="file" accept=".pdf,.docx,.txt" onChange={handleUpload} disabled={uploading} />
          </label>
          {docs.length > 0 && (
            <div className="doc-list">
              {docs.map((doc) => (
                <div key={doc.id} className="doc-row">
                  <FileText size={16} />
                  <div>
                    <div className="strong">{doc.name}</div>
                    <div className="subtext">{doc.uploaded}</div>
                  </div>
                  <span className={`pill ${doc.status === "Indexed" ? "success" : doc.status === "Failed" ? "danger" : "warn"}`}>
                    {doc.status}
                  </span>
                  <button
                    className="btn icon ghost"
                    aria-label={`Delete ${doc.name}`}
                    onClick={() => setDocs((cur) => cur.filter((item) => item.id !== doc.id))}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {docs.length === 0 && !uploading && (
            <InlineEmpty title="No documents uploaded yet" />
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Settings view (live property data)
// ---------------------------------------------------------------------------

function SettingsView({ property }: { property: PropertyDetail | null }) {
  const [hours, setHours] = useState("");
  const [contact, setContact] = useState("");
  const [rules, setRules] = useState("");
  const [voice, setVoice] = useState("Warm and concise");

  useEffect(() => {
    if (!property) return;
    setHours(property.office_hours ? JSON.stringify(property.office_hours, null, 2) : "");
    setContact(
      property.escalation_contacts
        ? property.escalation_contacts.map((c) => JSON.stringify(c)).join("\n")
        : ""
    );
    setRules(property.leasing_policies ?? "");
  }, [property]);

  return (
    <section className="page stack">
      <div className="settings-grid">
        <div className="card">
          <CardHeader title="Business hours" subtitle="Used for voicemail and escalation behavior" />
          <Textarea value={hours} onChange={setHours} />
        </div>
        <div className="card">
          <CardHeader title="Escalation contacts" subtitle="Visible handoff targets for call review" />
          <Textarea value={contact} onChange={setContact} />
        </div>
        <div className="card">
          <CardHeader title="Calendar connection" subtitle="Subbu will wire OAuth URLs when ready" />
          <div className="integration-row">
            <Calendar size={18} />
            <div>
              <div className="strong">Google Calendar</div>
              <div className="subtext">Connected to {property?.name ?? "property"}</div>
            </div>
            <span className="pill success">Connected</span>
          </div>
        </div>
        <div className="card">
          <CardHeader title="Voice settings" subtitle="Manager-readable voice behavior values" />
          <select className="field" value={voice} onChange={(event) => setVoice(event.target.value)}>
            <option>Warm and concise</option>
            <option>Direct and efficient</option>
            <option>Polished and formal</option>
          </select>
          <div className="preview-line">
            <Mic size={14} /> Hi, thanks for calling {property?.name ?? "the property"}. This is Waxwing Voice.
          </div>
        </div>
        <div className="card wide">
          <CardHeader title="Property-specific rules" subtitle="Rules the agent must respect before answering or booking" />
          <Textarea value={rules} onChange={setRules} rows={4} />
        </div>
        {property?.description && (
          <div className="card wide">
            <CardHeader title="Property description" subtitle="Shown to the voice agent for context" />
            <p style={{ fontSize: "0.875rem", color: "var(--ink-600)", lineHeight: 1.6 }}>{property.description}</p>
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Recent activity card (replaces ShowingsCard)
// ---------------------------------------------------------------------------

function RecentActivityCard({ calls }: { calls: CallListItem[] }) {
  const recent = calls.slice(0, 4);
  return (
    <div className="card">
      <CardHeader title="Recent activity" subtitle={`${calls.length} calls logged`} icon={Calendar} />
      <div className="stack small">
        {recent.map((call) => (
          <div key={call.id} className="showing-row">
            <div className="date-tile">
              <span>{formatDateGroup(call.created_at).slice(0, 3)}</span>
              <strong>{new Date(call.created_at).getDate()}</strong>
              <em>{new Date(call.created_at).toLocaleString("en", { month: "short" })}</em>
            </div>
            <div>
              <div className="strong">{call.caller_phone ?? "Unknown"}</div>
              <div className="subtext">{capitalize(call.primary_intent)}</div>
              <div className="subtext">{formatDuration(call.duration)}</div>
            </div>
          </div>
        ))}
        {recent.length === 0 && <InlineEmpty title="No calls yet today" />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared UI components
// ---------------------------------------------------------------------------

function FiltersBar({ children }: { children: ReactNode }) {
  return (
    <div className="filters card">
      <Filter size={16} />
      {children}
    </div>
  );
}

function SearchField({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <label className="search-field">
      <Search size={15} />
      <input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <label className="select-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option}>{option}</option>
        ))}
      </select>
    </label>
  );
}

function CardHeader({ title, subtitle, icon: Icon }: { title: string; subtitle?: string; icon?: LucideIcon }) {
  return (
    <div className="card-header">
      <div>
        <div className="card-title">{title}</div>
        {subtitle && <div className="card-sub">{subtitle}</div>}
      </div>
      {Icon && <Icon size={16} style={{ color: "var(--ink-400)" }} />}
    </div>
  );
}

function InfoCard({ title, rows }: { title: string; rows: Array<[string, string]> }) {
  return (
    <div className="card">
      <CardHeader title={title} />
      <div className="info-list">
        {rows.map(([label, value]) => (
          <Fact key={label} label={label} value={value} />
        ))}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Textarea({ value, onChange, rows = 3 }: { value: string; onChange: (value: string) => void; rows?: number }) {
  return <textarea className="textarea" value={value} rows={rows} onChange={(event) => onChange(event.target.value)} />;
}

function HealthIndicator({ label, value, tone }: { label: string; value: string; tone: "success" | "warn" }) {
  return (
    <div className={`health ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function LeadStatusPill({ value }: { value: string }) {
  const cls =
    value === "toured" || value === "applied" || value === "closed"
      ? "success"
      : value === "lost"
      ? "danger"
      : value === "contacted"
      ? "warn"
      : "teal";
  return <span className={`pill ${cls}`}>{mapLeadStatusDisplay(value)}</span>;
}

function ScoreBar({ value }: { value: number }) {
  const color = value >= 75 ? "var(--success)" : value >= 50 ? "var(--warn)" : "var(--danger)";
  return (
    <div className="score-bar">
      <span><i style={{ width: `${value}%`, background: color }} /></span>
      <strong className="mono">{value}</strong>
    </div>
  );
}

function InlineEmpty({ title }: { title: string }) {
  return (
    <div className="inline-empty">
      <Inbox size={20} />
      <span>{title}</span>
    </div>
  );
}

function Sparkline({ data, tone }: { data: number[]; tone: "up" | "down" }) {
  const width = 86;
  const height = 28;
  const padding = 3;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((value, index) => {
      const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
      const y = height - padding - ((value - min) / range) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const areaPoints = `${padding},${height - padding} ${points} ${width - padding},${height - padding}`;
  const color = tone === "down" ? "var(--danger)" : "var(--teal-500)";

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polygon points={areaPoints} fill={color} opacity="0.08" />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      {data.map((value, index) => {
        const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
        const y = height - padding - ((value - min) / range) * (height - padding * 2);
        return <circle key={`${value}-${index}`} cx={x} cy={y} r="1.7" fill={color} opacity={index === data.length - 1 ? 1 : 0.35} />;
      })}
    </svg>
  );
}

function WaxwingMark() {
  return (
    <svg width="28" height="20" viewBox="0 0 40 28" fill="none" aria-hidden="true">
      <path d="M3 25 L3 3 M3 3 L13 25 M13 25 L13 3" stroke="var(--teal-500)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M18 25 L24 5 L30 25 M21 18 L27 18" stroke="var(--teal-500)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function initials(name: string) {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}
