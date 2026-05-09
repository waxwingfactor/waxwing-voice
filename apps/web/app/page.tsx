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
  UsersRound
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ChangeEvent, PointerEvent, ReactNode, useState } from "react";

type View = "home" | "calls" | "call-detail" | "leads" | "knowledge" | "settings";
type DataMode = "ready" | "loading" | "empty" | "error" | "denied";
type LeadStatus = "New" | "Qualified" | "Needs follow-up" | "Not qualified";
type CallIntent = "Tour" | "Availability" | "Pricing" | "Maintenance" | "Unknown";

type CallRecord = {
  id: string;
  caller: string;
  phone: string;
  property: string;
  intent: CallIntent;
  time: string;
  date: "Today" | "Yesterday" | "This week";
  duration: string;
  summary: string;
  status: "Completed" | "Live" | "Escalated";
  leadStatus: LeadStatus;
  score: number | null;
  escalated: boolean;
  booked: boolean;
  followUpSent: boolean;
  moveIn: string;
  budget: string;
  unitPreference: string;
  tourStatus: string;
  booking: string;
  emailStatus: string;
  handoffStatus: string;
  actionItems: string[];
  transcript: Array<{ speaker: "Waxwing Voice" | "Caller"; time: string; text: string }>;
};

type LeadRecord = {
  id: string;
  name: string;
  phone: string;
  email: string;
  status: LeadStatus;
  moveIn: string;
  budget: string;
  unitPreference: string;
  tourStatus: string;
  lastSummary: string;
  lastCallId: string;
};

type KnowledgeDoc = {
  id: string;
  name: string;
  status: "Indexed" | "Processing" | "Failed";
  uploaded: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

const calls: CallRecord[] = [
  {
    id: "C-2840",
    caller: "Marcus Lee",
    phone: "(512) 555-0193",
    property: "5520 Travis Heights Blvd",
    intent: "Tour",
    time: "14m ago",
    date: "Today",
    duration: "4:02",
    summary: "Qualified three-person household. Wants a Saturday showing for the Travis Heights three-bedroom.",
    status: "Completed",
    leadStatus: "Qualified",
    score: 92,
    escalated: false,
    booked: true,
    followUpSent: true,
    moveIn: "Jun 15",
    budget: "$3,400",
    unitPreference: "3 bed / 2.5 bath",
    tourStatus: "Booked for Sat, May 9 at 11:00 AM",
    booking: "Sat, May 9 at 11:00 AM with Hannah Walker",
    emailStatus: "Confirmation sent",
    handoffStatus: "No handoff needed",
    actionItems: ["Send gate code before showing", "Attach application link in follow-up"],
    transcript: [
      { speaker: "Waxwing Voice", time: "0:00", text: "Thanks for calling Hunter Property Management. Are you calling about an available unit?" },
      { speaker: "Caller", time: "0:06", text: "Yes, the three-bedroom on Travis Heights." },
      { speaker: "Waxwing Voice", time: "1:24", text: "You meet the initial criteria. Would you like to schedule a showing?" },
      { speaker: "Caller", time: "1:40", text: "Saturday morning works great." }
    ]
  },
  {
    id: "C-2839",
    caller: "Priya Ramaswamy",
    phone: "(737) 555-0102",
    property: "210 Mueller Ave",
    intent: "Availability",
    time: "32m ago",
    date: "Today",
    duration: "3:18",
    summary: "Asked about May availability, pet policy, and lease length. Qualified and requested weekday evening tour.",
    status: "Completed",
    leadStatus: "Qualified",
    score: 88,
    escalated: false,
    booked: true,
    followUpSent: true,
    moveIn: "May 25",
    budget: "$2,250",
    unitPreference: "2 bed / 1 bath",
    tourStatus: "Booked for Mon, May 11 at 5:30 PM",
    booking: "Mon, May 11 at 5:30 PM with leasing team",
    emailStatus: "Confirmation sent",
    handoffStatus: "No handoff needed",
    actionItems: ["Confirm pet deposit amount"],
    transcript: [
      { speaker: "Waxwing Voice", time: "0:00", text: "I can help with availability and scheduling." },
      { speaker: "Caller", time: "0:18", text: "I need something available around the end of May." },
      { speaker: "Waxwing Voice", time: "2:44", text: "I have an evening showing available Monday." }
    ]
  },
  {
    id: "C-2838",
    caller: "Daniel Ortiz",
    phone: "(512) 555-0177",
    property: "904 Brazos St #3B",
    intent: "Pricing",
    time: "1h ago",
    date: "Today",
    duration: "2:45",
    summary: "Asked about one-bedroom pricing. Did not meet credit criteria and requested a human follow-up.",
    status: "Escalated",
    leadStatus: "Needs follow-up",
    score: 41,
    escalated: true,
    booked: false,
    followUpSent: false,
    moveIn: "Jun 1",
    budget: "$1,900",
    unitPreference: "1 bed / 1 bath",
    tourStatus: "Not booked",
    booking: "No booking",
    emailStatus: "Follow-up pending",
    handoffStatus: "Assigned to Hannah Walker",
    actionItems: ["Call back about guarantor policy", "Send alternative listings"],
    transcript: [
      { speaker: "Caller", time: "0:11", text: "My credit may be an issue. Can someone explain the policy?" },
      { speaker: "Waxwing Voice", time: "1:58", text: "I can have the property team follow up on that." }
    ]
  },
  {
    id: "C-2837",
    caller: "Jasmine Powell",
    phone: "(512) 555-0124",
    property: "78 Lamar Lofts #1204",
    intent: "Tour",
    time: "2h ago",
    date: "Today",
    duration: "5:11",
    summary: "Strong lead for Lamar Lofts. Asked about parking, pets, and move-in timing.",
    status: "Completed",
    leadStatus: "Qualified",
    score: 95,
    escalated: false,
    booked: true,
    followUpSent: true,
    moveIn: "Available now",
    budget: "$2,900",
    unitPreference: "2 bed / 2 bath",
    tourStatus: "Booked for Tue, May 12 at 6:00 PM",
    booking: "Tue, May 12 at 6:00 PM",
    emailStatus: "Confirmation sent",
    handoffStatus: "No handoff needed",
    actionItems: ["Include parking map in confirmation"],
    transcript: [
      { speaker: "Waxwing Voice", time: "0:00", text: "That home is available now and allows scheduled evening tours." },
      { speaker: "Caller", time: "3:11", text: "Great, Tuesday evening would be perfect." }
    ]
  },
  {
    id: "C-2836",
    caller: "Tyler Brooks",
    phone: "(737) 555-0188",
    property: "1842 Cedar Ridge Dr",
    intent: "Unknown",
    time: "3h ago",
    date: "Today",
    duration: "1:32",
    summary: "Asked about month-to-month options. Not a fit for current leasing rules.",
    status: "Completed",
    leadStatus: "Not qualified",
    score: 28,
    escalated: false,
    booked: false,
    followUpSent: false,
    moveIn: "Jul 1",
    budget: "$2,500",
    unitPreference: "Flexible",
    tourStatus: "Not booked",
    booking: "No booking",
    emailStatus: "Not sent",
    handoffStatus: "No handoff needed",
    actionItems: [],
    transcript: [
      { speaker: "Caller", time: "0:22", text: "Do you offer month-to-month leases?" },
      { speaker: "Waxwing Voice", time: "0:48", text: "The available homes currently require a twelve-month lease." }
    ]
  },
  {
    id: "C-2835",
    caller: "Aisha Nguyen",
    phone: "(512) 555-0156",
    property: "5520 Travis Heights Blvd",
    intent: "Tour",
    time: "4h ago",
    date: "Today",
    duration: "3:50",
    summary: "Qualified family of four. Requested Sunday afternoon showing and school boundary details.",
    status: "Completed",
    leadStatus: "Qualified",
    score: 84,
    escalated: false,
    booked: true,
    followUpSent: true,
    moveIn: "Jun 30",
    budget: "$3,500",
    unitPreference: "3 bed",
    tourStatus: "Booked for Sun, May 10 at 2:00 PM",
    booking: "Sun, May 10 at 2:00 PM",
    emailStatus: "Confirmation sent",
    handoffStatus: "No handoff needed",
    actionItems: ["Send school boundary note"],
    transcript: [
      { speaker: "Caller", time: "0:31", text: "Can we see it this Sunday?" },
      { speaker: "Waxwing Voice", time: "2:10", text: "Sunday at 2:00 PM is available." }
    ]
  }
];

const leads: LeadRecord[] = calls
  .filter((call) => call.leadStatus !== "Not qualified")
  .map((call) => ({
    id: call.id.replace("C", "L"),
    name: call.caller,
    phone: call.phone,
    email: `${call.caller.split(" ")[0].toLowerCase()}@example.com`,
    status: call.leadStatus,
    moveIn: call.moveIn,
    budget: call.budget,
    unitPreference: call.unitPreference,
    tourStatus: call.tourStatus,
    lastSummary: call.summary,
    lastCallId: call.id
  }));

const initialDocs: KnowledgeDoc[] = [
  { id: "D-101", name: "leasing-criteria.pdf", status: "Indexed", uploaded: "Today, 8:10 AM" },
  { id: "D-102", name: "cedar-ridge-factsheet.pdf", status: "Processing", uploaded: "Today, 9:22 AM" },
  { id: "D-103", name: "old-fee-schedule.pdf", status: "Failed", uploaded: "Yesterday" }
];

const propertyFacts = [
  { label: "Rent range", value: "$1,875 - $3,200" },
  { label: "Pet policy", value: "Cats and dogs vary by property" },
  { label: "Credit requirement", value: "600 minimum, exceptions require handoff" },
  { label: "Income rule", value: "3x monthly rent combined" },
  { label: "Last indexed", value: "May 9, 2026 at 9:28 AM" }
];

const navItems: Array<{ id: View; label: string; icon: LucideIcon; badge?: string }> = [
  { id: "home", label: "Dashboard", icon: Home },
  { id: "calls", label: "Calls", icon: Phone, badge: String(calls.length) },
  { id: "leads", label: "Leads", icon: UsersRound, badge: String(leads.length) },
  { id: "knowledge", label: "Knowledge", icon: Database },
  { id: "settings", label: "Settings", icon: Settings }
];

export default function App() {
  const [view, setView] = useState<View>("home");
  const [selectedCallId, setSelectedCallId] = useState(calls[0].id);
  const [dataMode, setDataMode] = useState<DataMode>("ready");
  const selectedCall = calls.find((call) => call.id === selectedCallId) ?? calls[0];

  const openCall = (callId: string) => {
    setSelectedCallId(callId);
    setView("call-detail");
  };

  const title = view === "call-detail" ? "Call detail" : navItems.find((item) => item.id === view)?.label ?? "Dashboard";

  return (
    <>
      <LiquidGlassFilters />
      <ForestBackground />
      <div className="app" data-sidebar="full" data-theme="light" data-density="comfortable">
        <Sidebar active={view === "call-detail" ? "calls" : view} onNavigate={setView} />
        <main className="main">
          <Topbar title={title} dataMode={dataMode} onDataModeChange={setDataMode} />
          <StateGate mode={dataMode} view={title} onReset={() => setDataMode("ready")}>
            {view === "home" && <HomeDashboard onOpenCall={openCall} />}
            {view === "calls" && <CallsView onOpenCall={openCall} />}
            {view === "call-detail" && <CallDetailView call={selectedCall} onBack={() => setView("calls")} />}
            {view === "leads" && <LeadsView onOpenCall={openCall} />}
            {view === "knowledge" && <KnowledgeView />}
            {view === "settings" && <SettingsView />}
          </StateGate>
        </main>
      </div>
    </>
  );
}

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

function Sidebar({ active, onNavigate }: { active: View; onNavigate: (view: View) => void }) {
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
            <PhoneIncoming size={11} /> {calls.length} today
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
  onNavigate
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
  onDataModeChange
}: {
  title: string;
  dataMode: DataMode;
  onDataModeChange: (mode: DataMode) => void;
}) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>{title}</h1>
        <span className="greet">Today, May 9</span>
      </div>
      <div className="topbar-actions">
        <div className={API_BASE_URL ? "api-pill connected" : "api-pill"}>
          <span className="dot" />
          {API_BASE_URL || "API mock mode"}
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
  children
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
      message: "Fetching the latest dashboard data and preparing the workspace."
    },
    empty: {
      icon: Inbox,
      title: `No ${view.toLowerCase()} data yet`,
      message: "This page is ready for the first records from Harsha's backend APIs."
    },
    error: {
      icon: AlertTriangle,
      title: `Could not load ${view.toLowerCase()}`,
      message: "The page keeps its failure state visible and gives the manager a clear retry path.",
      action: "Retry"
    },
    denied: {
      icon: ShieldAlert,
      title: "Permission required",
      message: "Authenticated access can block this page without exposing private call or lead data.",
      action: "Request access"
    }
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

function HomeDashboard({ onOpenCall }: { onOpenCall: (id: string) => void }) {
  const qualified = calls.filter((call) => call.leadStatus === "Qualified").length;
  const booked = calls.filter((call) => call.booked).length;
  const escalated = calls.filter((call) => call.escalated).length;
  const followUps = calls.filter((call) => call.followUpSent).length;
  const actionItems = calls.reduce((total, call) => total + call.actionItems.length, 0);

  const kpis = [
    { label: "Calls today", value: calls.length, delta: "+12%", icon: Phone, trend: [4, 5, 4, 6, 5, 7, 6] },
    { label: "New leads", value: leads.length, delta: "+3", icon: UsersRound, trend: [2, 2, 3, 3, 4, 4, 5] },
    { label: "Tours booked", value: booked, delta: "+5", icon: CalendarCheck, trend: [1, 1, 2, 2, 3, 4, 4] },
    { label: "Escalated calls", value: escalated, delta: "-1", icon: ShieldAlert, trend: [3, 3, 2, 2, 1, 2, 1] },
    { label: "Follow-ups sent", value: followUps, delta: "+4", icon: Send, trend: [1, 2, 2, 3, 4, 3, 4] },
    { label: "Open action items", value: actionItems, delta: "+2", icon: Check, trend: [5, 4, 6, 5, 7, 8, 7] }
  ];

  return (
    <section className="page stagger">
      <HeroCard />
      <div className="kpi-grid">
        {kpis.map((kpi) => (
          <KpiCard key={kpi.label} {...kpi} />
        ))}
      </div>
      <div className="content-grid">
        <div className="card flat table-card">
          <CardHeader title="Recent calls" subtitle={`${qualified} qualified leads - ${booked} tours booked`} />
          <CallsTable rows={calls.slice(0, 5)} onOpenCall={onOpenCall} compact />
        </div>
        <div className="stack">
          <ShowingsCard />
        </div>
      </div>
    </section>
  );
}

function HeroCard() {
  return (
    <div className="card hero-card">
      <svg className="hero-peaks" width="280" height="180" viewBox="0 0 280 180" fill="none" aria-hidden="true">
        <path d="M10 170 L70 30 L130 170" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M40 120 L100 120" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        <path d="M150 30 L210 170 L270 30" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="hero-content">
        <div>
          <div className="eyebrow">Good afternoon, Hunter Property Management</div>
          <h2>Waxwing Voice handled 8 calls today - 5 qualified.</h2>
          <p>Three showings are booked for this weekend. Marcus Lee scored highest at 92.</p>
        </div>
        <button className="btn primary">
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
  trend
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

function CallsView({ onOpenCall }: { onOpenCall: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [date, setDate] = useState("All");
  const [property, setProperty] = useState("All");
  const [intent, setIntent] = useState("All");
  const [leadStatus, setLeadStatus] = useState("All");
  const [special, setSpecial] = useState("All");

  const properties = unique(calls.map((call) => call.property));
  const filtered = calls.filter((call) => {
    const text = `${call.caller} ${call.phone} ${call.property} ${call.summary}`.toLowerCase();
    return (
      text.includes(query.toLowerCase()) &&
      (date === "All" || call.date === date) &&
      (property === "All" || call.property === property) &&
      (intent === "All" || call.intent === intent) &&
      (leadStatus === "All" || call.leadStatus === leadStatus) &&
      (special === "All" ||
        (special === "Booked" && call.booked) ||
        (special === "Follow-up sent" && call.followUpSent) ||
        (special === "Escalated" && call.escalated))
    );
  });

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search caller, phone, property" />
        <SelectField label="Date" value={date} onChange={setDate} options={["All", "Today", "Yesterday", "This week"]} />
        <SelectField label="Property" value={property} onChange={setProperty} options={["All", ...properties]} />
        <SelectField label="Intent" value={intent} onChange={setIntent} options={["All", "Tour", "Availability", "Pricing", "Maintenance", "Unknown"]} />
        <SelectField label="Lead" value={leadStatus} onChange={setLeadStatus} options={["All", "New", "Qualified", "Needs follow-up", "Not qualified"]} />
        <SelectField label="Flag" value={special} onChange={setSpecial} options={["All", "Booked", "Follow-up sent", "Escalated"]} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader title="Call history" subtitle={`${filtered.length} matching calls`} />
        {filtered.length ? <CallsTable rows={filtered} onOpenCall={onOpenCall} /> : <InlineEmpty title="No calls match those filters" />}
      </div>
    </section>
  );
}

function CallsTable({ rows, onOpenCall, compact = false }: { rows: CallRecord[]; onOpenCall: (id: string) => void; compact?: boolean }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Caller</th>
          <th>Property</th>
          {!compact && <th>Intent</th>}
          <th>Status</th>
          {!compact && <th>Summary</th>}
          <th>Score</th>
          <th style={{ textAlign: "right" }}>Time</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((call) => (
          <tr key={call.id} onClick={() => onOpenCall(call.id)}>
            <td>
              <div className="person-cell">
                <div className="avatar">{initials(call.caller)}</div>
                <div>
                  <div className="strong">{call.caller}</div>
                  <div className="mono subtext">{call.phone}</div>
                </div>
              </div>
            </td>
            <td>{call.property}</td>
            {!compact && <td>{call.intent}</td>}
            <td>
              <div className="status-stack">
                <StatusPill value={call.leadStatus} />
                {call.escalated && <span className="pill warn">Escalated</span>}
              </div>
            </td>
            {!compact && <td className="summary-cell">{call.summary}</td>}
            <td>{call.score == null ? "-" : <ScoreBar value={call.score} />}</td>
            <td style={{ textAlign: "right" }}>
              <div>{call.time}</div>
              <div className="mono subtext">{call.duration}</div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CallDetailView({ call, onBack }: { call: CallRecord; onBack: () => void }) {
  return (
    <section className="page stack">
      <button className="btn ghost fit" onClick={onBack}>
        Back to calls
      </button>
      <div className="detail-grid">
        <div className="stack">
          <div className="card">
            <CardHeader title={call.caller} subtitle={`${call.phone} - ${call.property}`} />
            <p className="detail-summary">{call.summary}</p>
            <div className="fact-grid">
              <Fact label="Lead status" value={call.leadStatus} />
              <Fact label="Intent" value={call.intent} />
              <Fact label="Duration" value={call.duration} />
              <Fact label="Handoff" value={call.handoffStatus} />
            </div>
          </div>
          <div className="card">
            <CardHeader title="Transcript" subtitle="Speaker-separated call transcript" />
            <div className="transcript">
              {call.transcript.map((line) => (
                <div key={`${line.time}-${line.text}`} className={line.speaker === "Waxwing Voice" ? "transcript-line agent" : "transcript-line"}>
                  <span className="mono">{line.time}</span>
                  <strong>{line.speaker}</strong>
                  <p>{line.text}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="stack">
          <InfoCard title="Extracted lead fields" rows={[
            ["Move-in", call.moveIn],
            ["Budget", call.budget],
            ["Unit preference", call.unitPreference],
            ["Tour status", call.tourStatus]
          ]} />
          <InfoCard title="Related booking" rows={[["Booking", call.booking], ["Email", call.emailStatus]]} />
          <div className="card">
            <CardHeader title="Action items" subtitle={`${call.actionItems.length} open`} />
            {call.actionItems.length ? (
              <ul className="check-list">
                {call.actionItems.map((item) => (
                  <li key={item}>
                    <Check size={14} /> {item}
                  </li>
                ))}
              </ul>
            ) : (
              <InlineEmpty title="No open action items" />
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function LeadsView({ onOpenCall }: { onOpenCall: (id: string) => void }) {
  const [status, setStatus] = useState("All");
  const [query, setQuery] = useState("");
  const filtered = leads.filter((lead) => {
    const text = `${lead.name} ${lead.phone} ${lead.email} ${lead.unitPreference}`.toLowerCase();
    return text.includes(query.toLowerCase()) && (status === "All" || lead.status === status);
  });

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search leads" />
        <SelectField label="Status" value={status} onChange={setStatus} options={["All", "New", "Qualified", "Needs follow-up"]} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader title="Leads" subtitle={`${filtered.length} active leads`} />
        {filtered.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Lead</th>
                <th>Status</th>
                <th>Move-in</th>
                <th>Budget</th>
                <th>Unit</th>
                <th>Tour</th>
                <th>Last call</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((lead) => (
                <tr key={lead.id} onClick={() => onOpenCall(lead.lastCallId)}>
                  <td>
                    <div className="person-cell">
                      <div className="avatar">{initials(lead.name)}</div>
                      <div>
                        <div className="strong">{lead.name}</div>
                        <div className="mono subtext">{lead.phone}</div>
                        <div className="subtext">{lead.email}</div>
                      </div>
                    </div>
                  </td>
                  <td><StatusPill value={lead.status} /></td>
                  <td>{lead.moveIn}</td>
                  <td>{lead.budget}</td>
                  <td>{lead.unitPreference}</td>
                  <td>{lead.tourStatus}</td>
                  <td className="summary-cell">{lead.lastSummary}</td>
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

function KnowledgeView() {
  const [docs, setDocs] = useState(initialDocs);
  const [facts, setFacts] = useState(propertyFacts);
  const [processing, setProcessing] = useState(false);

  const handleUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setDocs((current) => [
      { id: `D-${Date.now()}`, name: file.name, status: "Processing", uploaded: "Just now" },
      ...current
    ]);
    event.target.value = "";
  };

  const reindex = () => {
    setProcessing(true);
    window.setTimeout(() => setProcessing(false), 900);
  };

  return (
    <section className="page stack">
      <div className="knowledge-grid">
        <div className="card">
          <CardHeader title="Property knowledge" subtitle="Structured facts Waxwing Voice can use on calls" />
          <div className="editable-facts">
            {facts.map((fact, index) => (
              <label key={fact.label} className="field-label">
                {fact.label}
                <input
                  className="field"
                  value={fact.value}
                  onChange={(event) => {
                    const next = [...facts];
                    next[index] = { ...fact, value: event.target.value };
                    setFacts(next);
                  }}
                />
              </label>
            ))}
          </div>
          <div className="health-row">
            <HealthIndicator label="Knowledge health" value="Good" tone="success" />
            <HealthIndicator label="Failed docs" value={String(docs.filter((doc) => doc.status === "Failed").length)} tone="warn" />
            <button className="btn primary" onClick={reindex}>
              <RefreshCw size={14} className={processing ? "spin" : ""} /> Re-index
            </button>
          </div>
        </div>
        <div className="card">
          <CardHeader title="Document upload" subtitle="PDFs and property files for retrieval" />
          <label className="upload-zone">
            <Upload size={22} />
            <span>Choose a knowledge document</span>
            <input type="file" onChange={handleUpload} />
          </label>
          <div className="doc-list">
            {docs.map((doc) => (
              <div key={doc.id} className="doc-row">
                <FileText size={16} />
                <div>
                  <div className="strong">{doc.name}</div>
                  <div className="subtext">{doc.uploaded}</div>
                </div>
                <span className={`pill ${doc.status === "Indexed" ? "success" : doc.status === "Failed" ? "danger" : "warn"}`}>{doc.status}</span>
                <button className="btn icon ghost" aria-label={`Delete ${doc.name}`} onClick={() => setDocs((current) => current.filter((item) => item.id !== doc.id))}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function SettingsView() {
  const [hours, setHours] = useState("Mon-Fri 9:00 AM - 6:00 PM; Sat 10:00 AM - 3:00 PM");
  const [contact, setContact] = useState("Hannah Walker - hannah@hunterpm.com - (512) 555-0100");
  const [template, setTemplate] = useState("Thanks for calling Hunter Property Management. Your showing is confirmed for {{showing_time}} at {{property}}.");
  const [voice, setVoice] = useState("Warm and concise");
  const [rules, setRules] = useState("Escalate pricing exceptions, Fair Housing questions, application denials, and maintenance emergencies.");

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
              <div className="subtext">Connected to Hunter Property Management staging</div>
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
            <Mic size={14} /> Hi, thanks for calling Hunter Property Management. This is Waxwing Voice.
          </div>
        </div>
        <div className="card wide">
          <CardHeader title="Email templates" subtitle="Used after confirmed bookings and follow-ups" />
          <Textarea value={template} onChange={setTemplate} rows={4} />
        </div>
        <div className="card wide">
          <CardHeader title="Property-specific rules" subtitle="Rules the agent must respect before answering or booking" />
          <Textarea value={rules} onChange={setRules} rows={4} />
        </div>
      </div>
    </section>
  );
}

function ShowingsCard() {
  const booked = calls.filter((call) => call.booked);
  return (
    <div className="card">
      <CardHeader title="Upcoming showings" subtitle={`${booked.length} booked`} icon={Calendar} />
      <div className="stack small">
        {booked.slice(0, 4).map((call) => {
          const showing = parseShowingDate(call.booking);

          return (
            <div key={call.id} className="showing-row">
              <div className="date-tile" aria-label={`${showing.weekday}, ${showing.month} ${showing.day}`}>
                <span>{showing.weekday}</span>
                <strong>{showing.day}</strong>
                <em>{showing.month}</em>
              </div>
              <div>
                <div className="strong">{call.caller}</div>
                <div className="subtext">{call.property}</div>
                <div className="subtext">{showing.time}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function parseShowingDate(booking: string) {
  const match = booking.match(/^(\w{3}),\s+(\w{3})\s+(\d{1,2})\s+at\s+(.+?)(?:\s+with\s+.+)?$/);

  return {
    weekday: match?.[1] ?? "Now",
    month: match?.[2] ?? "",
    day: match?.[3] ?? "",
    time: match?.[4] ?? booking
  };
}

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

function StatusPill({ value }: { value: LeadStatus }) {
  const cls = value === "Qualified" ? "success" : value === "Not qualified" ? "danger" : value === "Needs follow-up" ? "warn" : "teal";
  return <span className={`pill ${cls}`}>{value}</span>;
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

function unique(values: string[]) {
  return Array.from(new Set(values));
}

function initials(name: string) {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2);
}
