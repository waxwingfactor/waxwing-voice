"use client";

import {
  AlertTriangle,
  Bell,
  Calendar,
  CalendarCheck,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  FileText,
  Filter,
  History,
  Home,
  Inbox,
  Loader2,
  LogOut,
  Mail,
  Mic,
  Phone,
  PhoneCall,
  PhoneIncoming,
  PhoneOff,
  RefreshCcw,
  Search,
  Send,
  Settings,
  ShieldAlert,
  Sparkles,
  Tag,
  Trash2,
  Upload,
  UserPlus,
  UsersRound,
  Volume2,
  Wrench,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  ChangeEvent,
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  PROPERTY_ID,
  clearAuthToken,
  deleteDocument,
  getBooking,
  getCalendarStatus,
  getCall,
  getEmail,
  getLead,
  getProperty,
  getPropertySummary,
  getStoredUser,
  hasAuthToken,
  listAuditLogs,
  listBookings,
  listCalls,
  listDocuments,
  listEmails,
  listLeads,
  loginUser,
  patchProperty,
  reindexDocument,
  setAuthToken,
  setStoredUser,
  subscribeAuthError,
  uploadDocument,
  type AuditLogItem,
  type AuthUser,
  type BookingDetail,
  type BookingListItem,
  type CalendarStatusResponse,
  type CallDetail,
  type CallEvent,
  type CallListItem,
  type DocumentListItem,
  type EmailDetail,
  type EmailListItem,
  type LeadDetail,
  type LeadListItem,
  type ListAuditLogsParams,
  type ListBookingsParams,
  type ListCallsParams,
  type ListEmailsParams,
  type ListLeadsParams,
  type PaginatedResponse,
  type PropertyDetail,
  type PropertyPatchBody,
  type PropertySummary,
} from "../lib/api";

type View =
  | "login"
  | "home"
  | "calls"
  | "call-detail"
  | "leads"
  | "lead-detail"
  | "bookings"
  | "booking-detail"
  | "emails"
  | "email-detail"
  | "audit-log"
  | "knowledge"
  | "settings";

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
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

/** Returns YYYY-MM-DD in the user's local timezone (NOT UTC). */
function formatLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  // Accept either "HH:MM:SS", "HH:MM", or full ISO timestamps.
  const m = /^(\d{2}):(\d{2})/.exec(value);
  if (m) {
    const hh = parseInt(m[1], 10);
    const mm = m[2];
    const ampm = hh >= 12 ? "pm" : "am";
    const display = ((hh + 11) % 12) + 1;
    return `${display}:${mm} ${ampm}`;
  }
  try {
    return new Date(value).toLocaleTimeString();
  } catch {
    return value;
  }
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

function errorCode(err: unknown): string {
  if (err instanceof ApiError) return err.code;
  return "UNKNOWN_ERROR";
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Unknown error";
}

function previewMetadata(meta: Record<string, unknown> | null | undefined): string {
  if (!meta) return "-";
  try {
    const json = JSON.stringify(meta);
    return json.length > 80 ? `${json.slice(0, 77)}...` : json;
  } catch {
    return "-";
  }
}

// ---------------------------------------------------------------------------
// Nav definitions
// ---------------------------------------------------------------------------

const navItemDefs: Array<{ id: View; label: string; icon: LucideIcon }> = [
  { id: "home", label: "Dashboard", icon: Home },
  { id: "calls", label: "Calls", icon: Phone },
  { id: "leads", label: "Leads", icon: UsersRound },
  { id: "bookings", label: "Bookings", icon: CalendarDays },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "audit-log", label: "Audit log", icon: History },
  { id: "knowledge", label: "Knowledge", icon: Database },
  { id: "settings", label: "Settings", icon: Settings },
];

// Workspace items show in the upper section; configure items at the bottom.
const WORKSPACE_NAV_IDS: View[] = ["home", "calls", "leads", "bookings", "emails", "audit-log", "knowledge"];
const CONFIGURE_NAV_IDS: View[] = ["settings"];

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const [view, setView] = useState<View>("home");
  const [authChecked, setAuthChecked] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  const [selectedBookingId, setSelectedBookingId] = useState<string | null>(null);
  const [selectedEmailId, setSelectedEmailId] = useState<string | null>(null);

  // Sidebar badge counts — fetched once at startup just for display.
  const [navCounts, setNavCounts] = useState<{ calls: number; leads: number }>({ calls: 0, leads: 0 });

  // On mount: decide login state from localStorage; subscribe to 401s globally.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!hasAuthToken()) {
      setView("login");
    } else {
      setUser(getStoredUser());
    }
    setAuthChecked(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    return subscribeAuthError(() => {
      // Any API call returning 401 bounces back to login.
      setUser(null);
      setView("login");
    });
  }, []);

  // Refresh badge counts whenever we have a logged-in session.
  useEffect(() => {
    if (view === "login") return;
    listCalls(PROPERTY_ID, { page_size: 1 })
      .then((res) => setNavCounts((c) => ({ ...c, calls: res.total })))
      .catch(() => {});
    listLeads(PROPERTY_ID, { page_size: 1 })
      .then((res) => setNavCounts((c) => ({ ...c, leads: res.total })))
      .catch(() => {});
  }, [view]);

  const openCall = (callId: string) => {
    setSelectedCallId(callId);
    setView("call-detail");
  };

  const openLead = (leadId: string) => {
    setSelectedLeadId(leadId);
    setView("lead-detail");
  };

  const openBooking = (bookingId: string) => {
    setSelectedBookingId(bookingId);
    setView("booking-detail");
  };

  const openEmail = (emailId: string) => {
    setSelectedEmailId(emailId);
    setView("email-detail");
  };

  const handleLoginSuccess = (loggedInUser: AuthUser) => {
    setUser(loggedInUser);
    setView("home");
  };

  const handleSignOut = () => {
    clearAuthToken();
    setUser(null);
    setView("login");
  };

  const title =
    view === "call-detail"
      ? "Call detail"
      : view === "lead-detail"
      ? "Lead detail"
      : view === "booking-detail"
      ? "Booking detail"
      : view === "email-detail"
      ? "Email detail"
      : view === "login"
      ? "Sign in"
      : navItemDefs.find((item) => item.id === view)?.label ?? "Dashboard";

  const sidebarActive: View =
    view === "call-detail"
      ? "calls"
      : view === "lead-detail"
      ? "leads"
      : view === "booking-detail"
      ? "bookings"
      : view === "email-detail"
      ? "emails"
      : view;

  if (!authChecked) {
    return (
      <>
        <LiquidGlassFilters />
        <ForestBackground />
      </>
    );
  }

  if (view === "login") {
    return (
      <>
        <LiquidGlassFilters />
        <ForestBackground />
        <LoginView onSuccess={handleLoginSuccess} />
      </>
    );
  }

  return (
    <>
      <LiquidGlassFilters />
      <ForestBackground />
      <div className="app" data-sidebar="full" data-theme="light" data-density="comfortable">
        <Sidebar
          active={sidebarActive}
          onNavigate={setView}
          totalCalls={navCounts.calls}
          totalLeads={navCounts.leads}
        />
        <main className="main">
          <Topbar title={title} user={user} onSignOut={handleSignOut} />
          {view === "home" && <HomeDashboard onOpenCall={openCall} onNavigate={setView} />}
          {view === "calls" && <CallsView onOpenCall={openCall} />}
          {view === "call-detail" && selectedCallId && (
            <CallDetailView
              callId={selectedCallId}
              onBack={() => setView("calls")}
              onOpenLead={openLead}
            />
          )}
          {view === "leads" && <LeadsView onOpenLead={openLead} />}
          {view === "lead-detail" && selectedLeadId && (
            <LeadDetailView
              leadId={selectedLeadId}
              onBack={() => setView("leads")}
              onOpenCall={openCall}
            />
          )}
          {view === "bookings" && <BookingsView onOpenBooking={openBooking} />}
          {view === "booking-detail" && selectedBookingId && (
            <BookingDetailView
              bookingId={selectedBookingId}
              onBack={() => setView("bookings")}
              onOpenLead={openLead}
              onOpenCall={openCall}
            />
          )}
          {view === "emails" && <EmailsView onOpenEmail={openEmail} />}
          {view === "email-detail" && selectedEmailId && (
            <EmailDetailView
              emailId={selectedEmailId}
              onBack={() => setView("emails")}
              onOpenLead={openLead}
              onOpenCall={openCall}
            />
          )}
          {view === "audit-log" && <AuditLogView />}
          {view === "knowledge" && <KnowledgeView />}
          {view === "settings" && <SettingsView />}
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

  const workspace = navItems.filter((item) => WORKSPACE_NAV_IDS.includes(item.id));
  const configure = navItems.filter((item) => CONFIGURE_NAV_IDS.includes(item.id));

  return (
    <aside className="sidebar">
      <div className="brand">
        <WaxwingMark />
        <span className="wordmark">Waxwing Voice</span>
      </div>

      <div className="nav-section-label">Workspace</div>
      {workspace.map((item) => (
        <NavButton key={item.id} item={item} active={active === item.id} onNavigate={onNavigate} />
      ))}

      <div className="nav-section-label">Configure</div>
      {configure.map((item) => (
        <NavButton key={item.id} item={item} active={active === item.id} onNavigate={onNavigate} />
      ))}

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
            <PhoneIncoming size={11} /> {totalCalls} total
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
  user,
  onSignOut,
}: {
  title: string;
  user: AuthUser | null;
  onSignOut: () => void;
}) {
  const apiUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function handleClick(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [menuOpen]);

  const initialsForUser = user?.name
    ? user.name.split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase()
    : "WW";

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
        <button className="btn icon ghost" aria-label="Notifications">
          <Bell size={16} />
        </button>
        <div className="user-menu" ref={menuRef}>
          <button
            className="user-menu-trigger"
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <div className="avatar" style={{ background: "var(--sand-300)", color: "var(--teal-700)" }}>
              {initialsForUser}
            </div>
            {user && (
              <div className="user-menu-meta">
                <div className="strong" style={{ fontSize: 13 }}>{user.name}</div>
                <div className="subtext">{user.role}</div>
              </div>
            )}
            <ChevronDown size={14} />
          </button>
          {menuOpen && (
            <div className="user-menu-dropdown" role="menu">
              {user && (
                <div className="user-menu-header">
                  <div className="strong">{user.name}</div>
                  <div className="subtext mono">{user.email}</div>
                </div>
              )}
              <button
                className="user-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  onSignOut();
                }}
              >
                <LogOut size={14} /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Section status helper — each major section renders its own loading/error
// state instead of one bulk gate that drops everything.
// ---------------------------------------------------------------------------

type SectionState<T> = {
  data: T | null;
  loading: boolean;
  error: { code: string; message: string } | null;
};

function emptyState<T>(): SectionState<T> {
  return { data: null, loading: true, error: null };
}

function SectionStatus({
  loading,
  error,
  noun,
  onRetry,
}: {
  loading: boolean;
  error: { code: string; message: string } | null;
  noun: string;
  onRetry?: () => void;
}) {
  if (loading) {
    return (
      <div className="state-card card">
        <div className="state-icon spinning"><Loader2 size={28} /></div>
        <div>
          <h2>Loading {noun}</h2>
          <p>Fetching the latest data from the Waxwing backend...</p>
        </div>
      </div>
    );
  }
  return (
    <div className="state-card card">
      <div className="state-icon"><AlertTriangle size={28} /></div>
      <div>
        <h2>Could not load {noun}</h2>
        <p>
          <span className="mono">{error?.code ?? "UNKNOWN_ERROR"}</span> - {error?.message ?? "Request failed."}
        </p>
        {onRetry && (
          <button className="btn primary" onClick={onRetry}>Retry</button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast — minimal transient banner shown at the bottom of the page.
// ---------------------------------------------------------------------------

type ToastTone = "success" | "warn" | "danger";
type ToastEntry = { tone: ToastTone; text: string };

function useToast(): { toast: ToastEntry | null; show: (entry: ToastEntry) => void } {
  const [toast, setToast] = useState<ToastEntry | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((entry: ToastEntry) => {
    setToast(entry);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3500);
  }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { toast, show };
}

function Toast({ entry }: { entry: ToastEntry | null }) {
  if (!entry) return null;
  return (
    <div className={`toast ${entry.tone}`} role="status" aria-live="polite">
      {entry.text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Login view
// ---------------------------------------------------------------------------

function LoginView({ onSuccess }: { onSuccess: (user: AuthUser) => void }) {
  const [email, setEmail] = useState("admin@sunsetapartments.example");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<{ code: string; message: string } | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setErr(null);
    try {
      const res = await loginUser(email, password);
      setAuthToken(res.access_token);
      setStoredUser(res.user);
      onSuccess(res.user);
    } catch (caught) {
      setErr({ code: errorCode(caught), message: errorMessage(caught) });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-card card">
        <div className="login-brand">
          <WaxwingMark />
          <span className="wordmark">Waxwing Voice</span>
        </div>
        <h1>Sign in</h1>
        <p className="login-sub">Enter your work email to manage Waxwing Voice for your property.</p>

        <form onSubmit={handleSubmit} className="login-form">
          <label className="login-label">
            <span>Email</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
          </label>
          <label className="login-label">
            <span>Password</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </label>

          {err && (
            <div className="notice-banner warn" role="alert">
              <AlertTriangle size={16} />
              <div>
                <strong>Sign-in failed</strong>
                <span className="mono">{err.code}</span> - {err.message}
              </div>
            </div>
          )}

          <button type="submit" className="btn primary" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <div className="login-hint">
          <strong>Demo credentials</strong>
          <div className="mono subtext">admin@sunsetapartments.example</div>
          <div className="mono subtext">demo</div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Home dashboard
// ---------------------------------------------------------------------------

function HomeDashboard({
  onOpenCall,
  onNavigate,
}: {
  onOpenCall: (id: string) => void;
  onNavigate: (view: View) => void;
}) {
  const [summaryState, setSummaryState] = useState<SectionState<PropertySummary>>(emptyState());
  const [callsState, setCallsState] = useState<SectionState<CallListItem[]>>(emptyState());
  const [summaryVersion, setSummaryVersion] = useState(0);
  const [callsVersion, setCallsVersion] = useState(0);

  const loadSummary = useCallback(() => setSummaryVersion((v) => v + 1), []);
  const loadCalls = useCallback(() => setCallsVersion((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    const today = formatLocalDate(new Date());
    getPropertySummary(PROPERTY_ID, today).then(
      (data) => { if (!cancelled) setSummaryState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setSummaryState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [summaryVersion]);

  useEffect(() => {
    let cancelled = false;
    listCalls(PROPERTY_ID, { page_size: 5 }).then(
      (res) => { if (!cancelled) setCallsState({ data: res.items, loading: false, error: null }); },
      (err) => { if (!cancelled) setCallsState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [callsVersion]);

  const summary = summaryState.data;
  const calls = callsState.data ?? [];

  const kpis = [
    { label: "Calls today", value: summary?.calls_today ?? 0, icon: Phone },
    { label: "New leads", value: summary?.new_leads_today ?? 0, icon: UsersRound },
    { label: "Tours booked", value: summary?.tours_booked_today ?? 0, icon: CalendarCheck },
    { label: "Escalated calls", value: summary?.escalations_today ?? 0, icon: ShieldAlert },
    { label: "Follow-ups sent", value: summary?.follow_ups_sent_today ?? 0, icon: Send },
    { label: "Open action items", value: summary?.open_action_items ?? 0, icon: Check },
  ];

  return (
    <section className="page stagger">
      {summaryState.loading || summaryState.error ? (
        <SectionStatus
          loading={summaryState.loading}
          error={summaryState.error}
          noun="today's summary"
          onRetry={loadSummary}
        />
      ) : (
        <>
          <HeroCard summary={summary} onNavigate={onNavigate} />
          <div className="kpi-grid">
            {kpis.map((kpi) => (
              <KpiCard key={kpi.label} {...kpi} />
            ))}
          </div>
        </>
      )}

      <div className="content-grid">
        <div className="card flat table-card">
          <CardHeader
            title="Recent calls"
            subtitle={`${summary?.calls_today ?? 0} calls today`}
          />
          {callsState.loading || callsState.error ? (
            <SectionStatus
              loading={callsState.loading}
              error={callsState.error}
              noun="recent calls"
              onRetry={loadCalls}
            />
          ) : calls.length > 0 ? (
            <CallsTable rows={calls} onOpenCall={onOpenCall} compact />
          ) : (
            <InlineEmpty title="No calls recorded yet" />
          )}
        </div>
        <div className="stack">
          <RecentActivityCard
            calls={calls}
            loading={callsState.loading}
            error={callsState.error}
          />
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
            {summary?.property_name ?? "Loading..."}
          </div>
          <h2>
            Waxwing Voice handled {summary?.calls_today ?? 0} calls today
            {summary && summary.new_leads_today > 0 ? ` - ${summary.new_leads_today} new leads captured.` : "."}
          </h2>
          <p>
            {summary
              ? `${summary.tours_booked_today} tour${summary.tours_booked_today !== 1 ? "s" : ""} booked. ${summary.escalations_today} escalation${summary.escalations_today !== 1 ? "s" : ""} today.`
              : "Fetching metrics..."}
          </p>
        </div>
        <button className="btn primary" onClick={() => onNavigate("calls")}>
          Review queue <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: LucideIcon;
}) {
  return (
    <div className="kpi">
      <span className="kpi-glass" aria-hidden="true" />
      <div className="kpi-label">
        <Icon size={14} /> {label}
      </div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-badge">Today</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Calls view — server-side pagination + filters (status, date range, q)
// ---------------------------------------------------------------------------

const CALL_STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "Active", value: "active" },
  { label: "Completed", value: "completed" },
  { label: "Escalated", value: "escalated" },
  { label: "Abandoned", value: "abandoned" },
];

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

function CallsView({ onOpenCall }: { onOpenCall: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [query, setQuery] = useState("");
  const [retry, setRetry] = useState(0);

  const debouncedQuery = useDebounced(query, 300);

  const [state, setState] = useState<SectionState<PaginatedResponse<CallListItem>>>(emptyState());

  const load = useCallback(() => setRetry((r) => r + 1), []);

  // Filter setters reset page to 1 — done at event time, not in an effect.
  const setStatusFilter = (v: string) => { setStatus(v); setPage(1); };
  const setDateFromFilter = (v: string) => { setDateFrom(v); setPage(1); };
  const setDateToFilter = (v: string) => { setDateTo(v); setPage(1); };

  // Reset to page 1 whenever the debounced search query actually changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setPage(1); }, [debouncedQuery]);

  useEffect(() => {
    let cancelled = false;
    const params: ListCallsParams = { page, page_size: pageSize };
    if (status) params.status = status;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (debouncedQuery) params.q = debouncedQuery;
    listCalls(PROPERTY_ID, params).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, status, dateFrom, dateTo, debouncedQuery, retry]);

  const data = state.data;
  const rows = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search caller phone or summary..." />
        <SelectField
          label="Status"
          value={status}
          onChange={setStatusFilter}
          options={CALL_STATUS_OPTIONS}
        />
        <DateField label="From" value={dateFrom} onChange={setDateFromFilter} />
        <DateField label="To" value={dateTo} onChange={setDateToFilter} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader
          title="Call history"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="calls" onRetry={load} />
        ) : rows.length ? (
          <>
            <CallsTable rows={rows} onOpenCall={onOpenCall} />
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
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
// Call detail view (self-fetching) — now renders call_events + next_steps
// ---------------------------------------------------------------------------

function CallDetailView({
  callId,
  onBack,
  onOpenLead,
}: {
  callId: string;
  onBack: () => void;
  onOpenLead: (leadId: string) => void;
}) {
  const [state, setState] = useState<SectionState<CallDetail>>(emptyState());
  const [retry, setRetry] = useState(0);

  const load = useCallback(() => setRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    getCall(callId).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [callId, retry]);

  if (state.loading || state.error) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to calls</button>
        <SectionStatus loading={state.loading} error={state.error} noun="call detail" onRetry={load} />
      </section>
    );
  }

  const call = state.data;
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
              subtitle={`${mapCallStatus(call.status)} - ${formatDuration(call.duration)}`}
            />
            {call.summary && <p className="detail-summary">{call.summary}</p>}
            {call.next_steps && (
              <div className="notice-banner info" style={{ marginTop: 12 }}>
                <Sparkles size={16} />
                <div>
                  <strong>Next steps</strong>
                  {call.next_steps}
                </div>
              </div>
            )}
            <div className="fact-grid" style={{ marginTop: 12 }}>
              <Fact label="Status" value={mapCallStatus(call.status)} />
              <Fact label="Intent" value={capitalize(call.primary_intent)} />
              <Fact label="Duration" value={formatDuration(call.duration)} />
              <Fact label="Sentiment" value={capitalize(call.sentiment)} />
            </div>
            {call.lead_id && (
              <div style={{ marginTop: 12 }}>
                <button className="btn ghost fit" onClick={() => onOpenLead(call.lead_id!)}>
                  View captured lead <ChevronRight size={14} />
                </button>
              </div>
            )}
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

          <div className="card">
            <CardHeader title="Call timeline" subtitle={`${call.call_events.length} events`} />
            {call.call_events.length > 0 ? (
              <CallTimeline events={call.call_events} />
            ) : (
              <InlineEmpty title="No events recorded" />
            )}
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
// Call timeline
// ---------------------------------------------------------------------------

interface EventRender {
  icon: LucideIcon;
  label: string;
  tone?: "warn" | "danger" | "success";
}

const EVENT_RENDER: Record<string, EventRender> = {
  call_started: { icon: PhoneCall, label: "Call started", tone: "success" },
  call_ended: { icon: PhoneOff, label: "Call ended" },
  lead_captured: { icon: UserPlus, label: "Lead captured", tone: "success" },
  tour_booked: { icon: CalendarCheck, label: "Tour booked", tone: "success" },
  email_sent: { icon: Send, label: "Email sent" },
  escalated: { icon: ShieldAlert, label: "Escalated to human", tone: "warn" },
  knowledge_retrieved: { icon: Database, label: "Knowledge retrieved" },
  tool_called: { icon: Wrench, label: "Tool called" },
  tool_failed: { icon: AlertTriangle, label: "Tool failed", tone: "danger" },
  interruption_detected: { icon: Zap, label: "Interruption detected", tone: "warn" },
  silence_detected: { icon: Volume2, label: "Silence detected", tone: "warn" },
};

function summarizePayload(event: CallEvent): string {
  const p = event.payload;
  if (!p) return "";
  // Try a handful of common keys; otherwise compact-render a JSON preview.
  const interesting = ["tool", "tool_name", "intent", "subject", "to", "recipient", "reason", "duration_ms", "chunks", "name"];
  const parts: string[] = [];
  for (const key of interesting) {
    if (key in p) parts.push(`${key}: ${String((p as Record<string, unknown>)[key])}`);
  }
  if (parts.length) return parts.join(" - ");
  try {
    const json = JSON.stringify(p);
    return json.length > 120 ? `${json.slice(0, 117)}...` : json;
  } catch {
    return "";
  }
}

function CallTimeline({ events }: { events: CallEvent[] }) {
  const sorted = [...events].sort(
    (a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime()
  );
  return (
    <div className="timeline">
      {sorted.map((event) => {
        const render = EVENT_RENDER[event.event_type] ?? { icon: Tag, label: event.event_type };
        const Icon = render.icon;
        const sub = summarizePayload(event);
        return (
          <div key={event.id} className="timeline-item">
            <span className={render.tone ? `timeline-icon ${render.tone}` : "timeline-icon"}>
              <Icon size={14} />
            </span>
            <div className="timeline-body">
              <div className="timeline-title">{render.label}</div>
              {sub && <div className="timeline-sub mono">{sub}</div>}
            </div>
            <div className="timeline-time">{new Date(event.occurred_at).toLocaleTimeString()}</div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Leads view — server-side pagination + filters + text search
// ---------------------------------------------------------------------------

const LEAD_STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "New", value: "new" },
  { label: "Contacted", value: "contacted" },
  { label: "Toured", value: "toured" },
  { label: "Applied", value: "applied" },
  { label: "Closed", value: "closed" },
  { label: "Lost", value: "lost" },
];

const LEAD_SCORE_OPTIONS = [
  { label: "All", value: "" },
  { label: "Hot", value: "hot" },
  { label: "Warm", value: "warm" },
  { label: "Cold", value: "cold" },
];

const TOUR_INTEREST_OPTIONS = [
  { label: "All", value: "" },
  { label: "Interested", value: "true" },
  { label: "Not interested", value: "false" },
];

function LeadsView({ onOpenLead }: { onOpenLead: (leadId: string) => void }) {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [status, setStatus] = useState("");
  const [score, setScore] = useState("");
  const [tour, setTour] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [query, setQuery] = useState("");
  const [retry, setRetry] = useState(0);

  const debouncedQuery = useDebounced(query, 300);

  const [state, setState] = useState<SectionState<PaginatedResponse<LeadListItem>>>(emptyState());

  const load = useCallback(() => setRetry((r) => r + 1), []);

  // Filter setters reset page to 1 — at event time, not in an effect.
  const setStatusFilter = (v: string) => { setStatus(v); setPage(1); };
  const setScoreFilter = (v: string) => { setScore(v); setPage(1); };
  const setTourFilter = (v: string) => { setTour(v); setPage(1); };
  const setDateFromFilter = (v: string) => { setDateFrom(v); setPage(1); };
  const setDateToFilter = (v: string) => { setDateTo(v); setPage(1); };

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setPage(1); }, [debouncedQuery]);

  useEffect(() => {
    let cancelled = false;
    const params: ListLeadsParams = { page, page_size: pageSize };
    if (status) params.lead_status = status;
    if (score) params.lead_score = score;
    if (tour) params.tour_interest = tour === "true";
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (debouncedQuery) params.q = debouncedQuery;
    listLeads(PROPERTY_ID, params).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, status, score, tour, dateFrom, dateTo, debouncedQuery, retry]);

  const data = state.data;
  const rows = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={query} onChange={setQuery} placeholder="Search name, phone, or email..." />
        <SelectField label="Status" value={status} onChange={setStatusFilter} options={LEAD_STATUS_OPTIONS} />
        <SelectField label="Score" value={score} onChange={setScoreFilter} options={LEAD_SCORE_OPTIONS} />
        <SelectField label="Tour" value={tour} onChange={setTourFilter} options={TOUR_INTEREST_OPTIONS} />
        <DateField label="From" value={dateFrom} onChange={setDateFromFilter} />
        <DateField label="To" value={dateTo} onChange={setDateToFilter} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader
          title="Leads"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="leads" onRetry={load} />
        ) : rows.length ? (
          <>
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
                {rows.map((lead) => (
                  <tr
                    key={lead.id}
                    onClick={() => onOpenLead(lead.id)}
                    style={{ cursor: "pointer" }}
                  >
                    <td>
                      <div className="person-cell">
                        <div className="avatar">{initials(lead.name ?? lead.phone ?? "?")}</div>
                        <div>
                          <div className="strong">{lead.name ?? "Unknown"}</div>
                          <div className="mono subtext">{lead.phone ?? "-"}</div>
                          <div className="subtext">{lead.email ?? "-"}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <LeadStatusPill value={lead.lead_status} />
                    </td>
                    <td>
                      {lead.lead_score
                        ? <ScoreBar value={scoreFromTier(lead.lead_score) ?? 0} />
                        : "-"}
                    </td>
                    <td>{lead.desired_unit_type ?? "-"}</td>
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
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
        ) : (
          <InlineEmpty title="No leads match those filters" />
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Lead detail view
// ---------------------------------------------------------------------------

function LeadDetailView({
  leadId,
  onBack,
  onOpenCall,
}: {
  leadId: string;
  onBack: () => void;
  onOpenCall: (callId: string) => void;
}) {
  const [state, setState] = useState<SectionState<LeadDetail>>(emptyState());
  const [retry, setRetry] = useState(0);

  const load = useCallback(() => setRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    getLead(leadId).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [leadId, retry]);

  if (state.loading || state.error) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to leads</button>
        <SectionStatus loading={state.loading} error={state.error} noun="lead detail" onRetry={load} />
      </section>
    );
  }

  const lead = state.data;
  if (!lead) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to leads</button>
        <InlineEmpty title="Lead not found" />
      </section>
    );
  }

  const petInfo = lead.pet_info ? JSON.stringify(lead.pet_info) : null;

  return (
    <section className="page stack">
      <button className="btn ghost fit" onClick={onBack}>Back to leads</button>
      <div className="lead-detail-grid">
        <div className="stack">
          <div className="card">
            <CardHeader
              title={lead.name ?? "Unnamed lead"}
              subtitle={`${mapLeadStatusDisplay(lead.lead_status)} - created ${formatRelativeTime(lead.created_at)}`}
            />
            <div className="fact-grid" style={{ marginTop: 8 }}>
              <Fact label="Phone" value={lead.phone ?? "-"} />
              <Fact label="Email" value={lead.email ?? "-"} />
              <Fact label="Status" value={mapLeadStatusDisplay(lead.lead_status)} />
              <Fact label="Score" value={lead.lead_score ? capitalize(lead.lead_score) : "-"} />
              <Fact label="Tour interest" value={lead.tour_interest ? "Yes" : "No"} />
              <Fact label="Urgency" value={lead.urgency ? capitalize(lead.urgency) : "-"} />
            </div>
          </div>

          <div className="card">
            <CardHeader title="Qualification" subtitle="Captured by Waxwing Voice during the call" />
            <div className="fact-grid">
              <Fact label="Budget" value={lead.budget != null ? `$${lead.budget}` : "-"} />
              <Fact label="Move-in date" value={lead.move_in_date ?? "-"} />
              <Fact label="Desired unit type" value={lead.desired_unit_type ?? "-"} />
              <Fact label="Occupants" value={lead.number_of_occupants != null ? String(lead.number_of_occupants) : "-"} />
              <Fact label="Pet info" value={petInfo ?? "-"} />
              <Fact label="How heard" value={lead.how_heard ?? "-"} />
            </div>
            {lead.reason_for_moving && (
              <div style={{ marginTop: 12 }}>
                <div className="card-sub" style={{ marginBottom: 4 }}>Reason for moving</div>
                <p style={{ fontSize: 13, color: "var(--ink-700)", lineHeight: 1.6 }}>{lead.reason_for_moving}</p>
              </div>
            )}
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <CardHeader title="Source" subtitle="Where this lead came from" />
            {lead.call_id ? (
              <button className="btn ghost fit" onClick={() => onOpenCall(lead.call_id!)}>
                View source call <ChevronRight size={14} />
              </button>
            ) : (
              <InlineEmpty title="Not linked to a call" />
            )}
            <div className="fact-grid" style={{ marginTop: 12 }}>
              <Fact label="Created" value={new Date(lead.created_at).toLocaleString()} />
              <Fact label="Updated" value={new Date(lead.updated_at).toLocaleString()} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Bookings view
// ---------------------------------------------------------------------------

const BOOKING_STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "Pending", value: "pending" },
  { label: "Confirmed", value: "confirmed" },
  { label: "Cancelled", value: "cancelled" },
  { label: "Completed", value: "completed" },
];

function bookingPillClass(status: string): string {
  if (status === "confirmed" || status === "completed") return "success";
  if (status === "cancelled") return "danger";
  if (status === "pending") return "warn";
  return "";
}

function BookingsView({ onOpenBooking }: { onOpenBooking: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [retry, setRetry] = useState(0);

  const [state, setState] = useState<SectionState<PaginatedResponse<BookingListItem>>>(emptyState());

  const load = useCallback(() => setRetry((r) => r + 1), []);

  const setStatusFilter = (v: string) => { setStatus(v); setPage(1); };
  const setDateFromFilter = (v: string) => { setDateFrom(v); setPage(1); };
  const setDateToFilter = (v: string) => { setDateTo(v); setPage(1); };

  useEffect(() => {
    let cancelled = false;
    const params: ListBookingsParams = { page, page_size: pageSize };
    if (status) params.status = status;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    listBookings(PROPERTY_ID, params).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, status, dateFrom, dateTo, retry]);

  const data = state.data;
  const rows = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <FiltersBar>
        <SelectField label="Status" value={status} onChange={setStatusFilter} options={BOOKING_STATUS_OPTIONS} />
        <DateField label="From" value={dateFrom} onChange={setDateFromFilter} />
        <DateField label="To" value={dateTo} onChange={setDateToFilter} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader
          title="Tour bookings"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="bookings" onRetry={load} />
        ) : rows.length ? (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Time</th>
                  <th>Status</th>
                  <th>Lead</th>
                  <th>Call</th>
                  <th style={{ textAlign: "right" }}>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={b.id} onClick={() => onOpenBooking(b.id)}>
                    <td className="strong">{b.tour_date}</td>
                    <td className="mono">
                      {formatTime(b.start_time)}
                      {b.end_time ? ` - ${formatTime(b.end_time)}` : ""}
                    </td>
                    <td>
                      <span className={`pill ${bookingPillClass(b.status)}`}>{capitalize(b.status)}</span>
                    </td>
                    <td className="mono subtext">{b.lead_id ? `${b.lead_id.slice(0, 8)}...` : "-"}</td>
                    <td className="mono subtext">{b.call_id ? `${b.call_id.slice(0, 8)}...` : "-"}</td>
                    <td style={{ textAlign: "right" }} className="subtext">{formatRelativeTime(b.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
        ) : (
          <InlineEmpty title="No bookings match those filters" />
        )}
      </div>
    </section>
  );
}

function BookingDetailView({
  bookingId,
  onBack,
  onOpenLead,
  onOpenCall,
}: {
  bookingId: string;
  onBack: () => void;
  onOpenLead: (leadId: string) => void;
  onOpenCall: (callId: string) => void;
}) {
  const [state, setState] = useState<SectionState<BookingDetail>>(emptyState());
  const [retry, setRetry] = useState(0);

  const load = useCallback(() => setRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    getBooking(bookingId).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [bookingId, retry]);

  if (state.loading || state.error) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to bookings</button>
        <SectionStatus loading={state.loading} error={state.error} noun="booking detail" onRetry={load} />
      </section>
    );
  }

  const booking = state.data;
  if (!booking) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to bookings</button>
        <InlineEmpty title="Booking not found" />
      </section>
    );
  }

  return (
    <section className="page stack">
      <button className="btn ghost fit" onClick={onBack}>Back to bookings</button>
      <div className="lead-detail-grid">
        <div className="stack">
          <div className="card">
            <CardHeader
              title={`Tour on ${booking.tour_date}`}
              subtitle={`${formatTime(booking.start_time)}${booking.end_time ? ` - ${formatTime(booking.end_time)}` : ""}`}
            />
            <div className="fact-grid" style={{ marginTop: 8 }}>
              <Fact label="Status" value={capitalize(booking.status)} />
              <Fact label="Date" value={booking.tour_date} />
              <Fact label="Start" value={formatTime(booking.start_time)} />
              <Fact label="End" value={formatTime(booking.end_time)} />
              {booking.calendar_event_id && (
                <Fact label="Calendar event" value={booking.calendar_event_id} />
              )}
              <Fact label="Created" value={new Date(booking.created_at).toLocaleString()} />
            </div>
            {booking.notes && (
              <div style={{ marginTop: 12 }}>
                <div className="card-sub" style={{ marginBottom: 4 }}>Notes</div>
                <p style={{ fontSize: 13, color: "var(--ink-700)", lineHeight: 1.6 }}>{booking.notes}</p>
              </div>
            )}
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <CardHeader title="Linked records" subtitle="Lead and call associated with this tour" />
            <div className="stack small" style={{ marginTop: 4 }}>
              {booking.lead_id ? (
                <button className="btn ghost fit" onClick={() => onOpenLead(booking.lead_id!)}>
                  View lead <ChevronRight size={14} />
                </button>
              ) : (
                <InlineEmpty title="No linked lead" />
              )}
              {booking.call_id ? (
                <button className="btn ghost fit" onClick={() => onOpenCall(booking.call_id!)}>
                  View source call <ChevronRight size={14} />
                </button>
              ) : (
                <InlineEmpty title="No linked call" />
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Emails view
// ---------------------------------------------------------------------------

const EMAIL_TEMPLATE_OPTIONS = [
  { label: "All", value: "" },
  { label: "Tour confirmation", value: "tour_confirmation" },
  { label: "Follow up", value: "follow_up" },
  { label: "Lead response", value: "lead_response" },
  { label: "General", value: "general" },
];

const EMAIL_STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "Sent", value: "sent" },
  { label: "Failed", value: "failed" },
  { label: "Pending", value: "pending" },
];

function emailPillClass(status: string): string {
  if (status === "sent") return "success";
  if (status === "failed") return "danger";
  if (status === "pending") return "warn";
  return "";
}

function EmailsView({ onOpenEmail }: { onOpenEmail: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [templateType, setTemplateType] = useState("");
  const [deliveryStatus, setDeliveryStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [retry, setRetry] = useState(0);

  const [state, setState] = useState<SectionState<PaginatedResponse<EmailListItem>>>(emptyState());

  const load = useCallback(() => setRetry((r) => r + 1), []);

  const setTemplateFilter = (v: string) => { setTemplateType(v); setPage(1); };
  const setDeliveryFilter = (v: string) => { setDeliveryStatus(v); setPage(1); };
  const setDateFromFilter = (v: string) => { setDateFrom(v); setPage(1); };
  const setDateToFilter = (v: string) => { setDateTo(v); setPage(1); };

  useEffect(() => {
    let cancelled = false;
    const params: ListEmailsParams = { page, page_size: pageSize };
    if (templateType) params.template_type = templateType;
    if (deliveryStatus) params.delivery_status = deliveryStatus;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    listEmails(PROPERTY_ID, params).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, templateType, deliveryStatus, dateFrom, dateTo, retry]);

  const data = state.data;
  const rows = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <FiltersBar>
        <SelectField label="Template" value={templateType} onChange={setTemplateFilter} options={EMAIL_TEMPLATE_OPTIONS} />
        <SelectField label="Status" value={deliveryStatus} onChange={setDeliveryFilter} options={EMAIL_STATUS_OPTIONS} />
        <DateField label="From" value={dateFrom} onChange={setDateFromFilter} />
        <DateField label="To" value={dateTo} onChange={setDateToFilter} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader
          title="Emails"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="emails" onRetry={load} />
        ) : rows.length ? (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Recipient</th>
                  <th>Subject</th>
                  <th>Template</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((email) => (
                  <tr key={email.id} onClick={() => onOpenEmail(email.id)}>
                    <td className="mono">{email.recipient}</td>
                    <td className="strong">{email.subject || "(no subject)"}</td>
                    <td>{capitalize(email.template_type.replace(/_/g, " "))}</td>
                    <td>
                      <span className={`pill ${emailPillClass(email.delivery_status)}`}>
                        {capitalize(email.delivery_status)}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }} className="subtext">{formatRelativeTime(email.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
        ) : (
          <InlineEmpty title="No emails match those filters" />
        )}
      </div>
    </section>
  );
}

function EmailDetailView({
  emailId,
  onBack,
  onOpenLead,
  onOpenCall,
}: {
  emailId: string;
  onBack: () => void;
  onOpenLead: (leadId: string) => void;
  onOpenCall: (callId: string) => void;
}) {
  const [state, setState] = useState<SectionState<EmailDetail>>(emptyState());
  const [retry, setRetry] = useState(0);

  const load = useCallback(() => setRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    getEmail(emailId).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [emailId, retry]);

  if (state.loading || state.error) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to emails</button>
        <SectionStatus loading={state.loading} error={state.error} noun="email detail" onRetry={load} />
      </section>
    );
  }

  const email = state.data;
  if (!email) {
    return (
      <section className="page stack">
        <button className="btn ghost fit" onClick={onBack}>Back to emails</button>
        <InlineEmpty title="Email not found" />
      </section>
    );
  }

  return (
    <section className="page stack">
      <button className="btn ghost fit" onClick={onBack}>Back to emails</button>
      <div className="lead-detail-grid">
        <div className="stack">
          <div className="card">
            <CardHeader
              title={email.subject || "(no subject)"}
              subtitle={`To ${email.recipient} - ${capitalize(email.template_type.replace(/_/g, " "))}`}
            />
            <div className="fact-grid" style={{ marginTop: 8 }}>
              <Fact label="Recipient" value={email.recipient} />
              <Fact label="Template" value={email.template_type} />
              <Fact label="Status" value={capitalize(email.delivery_status)} />
              <Fact label="Created" value={new Date(email.created_at).toLocaleString()} />
              {email.sent_at && <Fact label="Sent at" value={new Date(email.sent_at).toLocaleString()} />}
            </div>
            {email.error_message && (
              <div className="notice-banner warn" style={{ marginTop: 12 }}>
                <AlertTriangle size={16} />
                <div>
                  <strong>Delivery error</strong>
                  {email.error_message}
                </div>
              </div>
            )}
          </div>
          <div className="card">
            <CardHeader title="Body" subtitle="Full message body sent to the recipient" />
            <pre className="email-body">{email.body ?? "(empty body)"}</pre>
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <CardHeader title="Linked records" subtitle="Lead and call associated with this email" />
            <div className="stack small" style={{ marginTop: 4 }}>
              {email.lead_id ? (
                <button className="btn ghost fit" onClick={() => onOpenLead(email.lead_id!)}>
                  View lead <ChevronRight size={14} />
                </button>
              ) : (
                <InlineEmpty title="No linked lead" />
              )}
              {email.call_id ? (
                <button className="btn ghost fit" onClick={() => onOpenCall(email.call_id!)}>
                  View source call <ChevronRight size={14} />
                </button>
              ) : (
                <InlineEmpty title="No linked call" />
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Audit log view
// ---------------------------------------------------------------------------

const ENTITY_TYPE_OPTIONS = [
  { label: "All", value: "" },
  { label: "Call", value: "call" },
  { label: "Lead", value: "lead" },
  { label: "Booking", value: "booking" },
  { label: "Email", value: "email" },
  { label: "Document", value: "document" },
  { label: "Property", value: "property" },
];

const ACTOR_TYPE_OPTIONS = [
  { label: "All", value: "" },
  { label: "User", value: "USER" },
  { label: "Voice agent", value: "VOICE_AGENT" },
  { label: "System", value: "SYSTEM" },
];

function AuditLogView() {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [actorType, setActorType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [retry, setRetry] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const debouncedAction = useDebounced(action, 300);

  const [state, setState] = useState<SectionState<PaginatedResponse<AuditLogItem>>>(emptyState());

  const load = useCallback(() => setRetry((r) => r + 1), []);

  const setEntityFilter = (v: string) => { setEntityType(v); setPage(1); };
  const setActorFilter = (v: string) => { setActorType(v); setPage(1); };
  const setDateFromFilter = (v: string) => { setDateFrom(v); setPage(1); };
  const setDateToFilter = (v: string) => { setDateTo(v); setPage(1); };

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setPage(1); }, [debouncedAction]);

  useEffect(() => {
    let cancelled = false;
    const params: ListAuditLogsParams = { page, page_size: pageSize };
    if (debouncedAction) params.action = debouncedAction;
    if (entityType) params.entity_type = entityType;
    if (actorType) params.actor_type = actorType;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    listAuditLogs(PROPERTY_ID, params).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, debouncedAction, entityType, actorType, dateFrom, dateTo, retry]);

  const data = state.data;
  const rows = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <FiltersBar>
        <SearchField value={action} onChange={setAction} placeholder="Filter by action..." />
        <SelectField label="Entity" value={entityType} onChange={setEntityFilter} options={ENTITY_TYPE_OPTIONS} />
        <SelectField label="Actor" value={actorType} onChange={setActorFilter} options={ACTOR_TYPE_OPTIONS} />
        <DateField label="From" value={dateFrom} onChange={setDateFromFilter} />
        <DateField label="To" value={dateTo} onChange={setDateToFilter} />
      </FiltersBar>
      <div className="card flat table-card">
        <CardHeader
          title="Audit log"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="audit log" onRetry={load} />
        ) : rows.length ? (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Entity</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <AuditLogRow
                    key={row.id}
                    row={row}
                    expanded={expandedId === row.id}
                    onToggle={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
                  />
                ))}
              </tbody>
            </table>
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
        ) : (
          <InlineEmpty title="No audit entries match those filters" />
        )}
      </div>
    </section>
  );
}

function AuditLogRow({
  row,
  expanded,
  onToggle,
}: {
  row: AuditLogItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td className="subtext">{new Date(row.created_at).toLocaleString()}</td>
        <td>
          <span className="pill teal">{row.actor_type}</span>
          <div className="mono subtext" style={{ marginTop: 4 }}>{row.actor_id.slice(0, 8)}...</div>
        </td>
        <td className="strong">{row.action}</td>
        <td>
          <div>{row.entity_type}</div>
          <div className="mono subtext">{row.entity_id.slice(0, 8)}...</div>
        </td>
        <td className="mono subtext">{previewMetadata(row.metadata)}</td>
      </tr>
      {expanded && (
        <tr className="audit-expanded-row">
          <td colSpan={5}>
            <pre className="audit-metadata">
              {row.metadata ? JSON.stringify(row.metadata, null, 2) : "(no metadata)"}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Knowledge view — list/upload/delete/reindex all wired to the real API.
// ---------------------------------------------------------------------------

function KnowledgeView() {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<SectionState<PaginatedResponse<DocumentListItem>>>(emptyState());
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<{ code: string; message: string } | null>(null);
  const { toast, show } = useToast();

  const load = useCallback(() => setRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    listDocuments(PROPERTY_ID, { page, page_size: pageSize }).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [page, pageSize, retry]);

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = "";
    setUploading(true);
    setUploadError(null);
    try {
      await uploadDocument(PROPERTY_ID, file);
      show({ tone: "success", text: `Uploaded ${file.name}` });
      // Reset to page 1 to surface the new doc.
      setPage(1);
      load();
    } catch (err) {
      const code = errorCode(err);
      const msg = errorMessage(err);
      setUploadError({ code, message: msg });
      show({ tone: "danger", text: `Upload failed: ${msg}` });
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (doc: DocumentListItem) => {
    if (typeof window !== "undefined") {
      const ok = window.confirm(`Delete "${doc.file_name}"? This removes it from retrieval immediately.`);
      if (!ok) return;
    }
    setBusyId(doc.id);
    try {
      const res = await deleteDocument(doc.id);
      show({ tone: "success", text: `Deleted ${doc.file_name} (${res.chunks_deleted} chunks removed)` });
      load();
    } catch (err) {
      show({ tone: "danger", text: `${errorCode(err)} - ${errorMessage(err)}` });
    } finally {
      setBusyId(null);
    }
  };

  const handleReindex = async (doc: DocumentListItem) => {
    setBusyId(doc.id);
    try {
      const res = await reindexDocument(doc.id);
      show({ tone: "success", text: `Re-indexed ${doc.file_name} (${res.chunks_created} chunks)` });
      load();
    } catch (err) {
      // Backend currently returns 422 REINDEX_NOT_AVAILABLE — surface it as
      // a friendly toast so the user sees that the call was attempted.
      show({ tone: "warn", text: `${errorCode(err)} - ${errorMessage(err)}` });
    } finally {
      setBusyId(null);
    }
  };

  const data = state.data;
  const docs = data?.items ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="page stack">
      <div className="knowledge-grid">
        <div className="card">
          <CardHeader title="Property knowledge" subtitle="Documents uploaded here power Waxwing Voice on calls" />
          <div className="health-row">
            <HealthIndicator label="Total documents" value={String(data?.total ?? 0)} tone="success" />
            <HealthIndicator
              label="Failed"
              value={String(docs.filter((d) => d.processing_status === "failed").length)}
              tone="warn"
            />
          </div>
        </div>
        <div className="card">
          <CardHeader title="Document upload" subtitle="PDFs, DOCX, and TXT files for retrieval" />
          <label className="upload-zone">
            <Upload size={22} />
            <span>{uploading ? "Uploading..." : "Choose a knowledge document"}</span>
            <input type="file" accept=".pdf,.docx,.txt" onChange={handleUpload} disabled={uploading} />
          </label>
          {uploadError && (
            <div className="notice-banner warn" style={{ marginTop: 12 }}>
              <AlertTriangle size={16} />
              <div>
                <strong>Upload failed</strong>
                <span className="mono">{uploadError.code}</span> - {uploadError.message}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card flat table-card">
        <CardHeader
          title="All documents"
          subtitle={data ? `${data.total} total - page ${data.page} of ${totalPages}` : "Loading..."}
        />
        {state.loading || state.error ? (
          <SectionStatus loading={state.loading} error={state.error} noun="documents" onRetry={load} />
        ) : docs.length ? (
          <>
            <div className="doc-list" style={{ padding: "0 20px 16px" }}>
              {docs.map((doc) => {
                const status = doc.processing_status;
                const pillCls =
                  status === "indexed" ? "success" : status === "failed" ? "danger" : "warn";
                return (
                  <div key={doc.id} className="doc-row">
                    <FileText size={16} />
                    <div>
                      <div className="strong">{doc.file_name}</div>
                      <div className="subtext">
                        {formatRelativeTime(doc.created_at)} - {doc.chunk_count} chunks - {doc.file_type}
                      </div>
                    </div>
                    <span className={`pill ${pillCls}`}>{capitalize(status)}</span>
                    <button
                      className="btn icon ghost"
                      aria-label={`Re-index ${doc.file_name}`}
                      onClick={() => handleReindex(doc)}
                      disabled={busyId === doc.id}
                    >
                      <RefreshCcw size={14} />
                    </button>
                    <button
                      className="btn icon ghost"
                      aria-label={`Delete ${doc.file_name}`}
                      onClick={() => handleDelete(doc)}
                      disabled={busyId === doc.id}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
            <Pagination
              page={data!.page}
              pageSize={data!.page_size}
              total={data!.total}
              onPage={setPage}
            />
          </>
        ) : (
          <InlineEmpty title="No documents uploaded yet" />
        )}
      </div>
      <Toast entry={toast} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Settings view — editable + Save + real calendar status.
// ---------------------------------------------------------------------------

type EditableSettings = {
  name: string;
  address: string;
  description: string;
  amenities: string;
  office_hours: string;
  leasing_policies: string;
  maintenance_instructions: string;
  escalation_contacts: string;
  business_hour_rules: string;
  call_handling_rules: string;
};

function asJsonString(value: unknown): string {
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "";
  }
}

function propertyToEditable(p: PropertyDetail): EditableSettings {
  return {
    name: p.name ?? "",
    address: p.address ?? "",
    description: p.description ?? "",
    amenities: asJsonString(p.amenities),
    office_hours: asJsonString(p.office_hours),
    leasing_policies: p.leasing_policies ?? "",
    maintenance_instructions: p.maintenance_instructions ?? "",
    escalation_contacts: asJsonString(p.escalation_contacts),
    business_hour_rules: asJsonString(p.business_hour_rules),
    call_handling_rules: asJsonString(p.call_handling_rules),
  };
}

function buildPatch(original: PropertyDetail, edited: EditableSettings): { body: PropertyPatchBody; jsonError: string | null } {
  const body: PropertyPatchBody = {};
  let jsonError: string | null = null;

  // Plain string fields — patch only when the value changed.
  if (edited.name !== (original.name ?? "")) body.name = edited.name;
  if (edited.address !== (original.address ?? "")) body.address = edited.address || null;
  if (edited.description !== (original.description ?? "")) body.description = edited.description || null;
  if (edited.leasing_policies !== (original.leasing_policies ?? "")) body.leasing_policies = edited.leasing_policies || null;
  if (edited.maintenance_instructions !== (original.maintenance_instructions ?? "")) body.maintenance_instructions = edited.maintenance_instructions || null;

  // Parse one JSON field: returns undefined if unchanged or invalid (and sets
  // jsonError), null if cleared to empty, or the parsed value otherwise.
  function parseJsonField<T>(label: string, value: string, originalValue: unknown): T | null | undefined {
    if (value === asJsonString(originalValue)) return undefined;
    if (!value.trim()) return null;
    try {
      return JSON.parse(value) as T;
    } catch {
      jsonError = `Invalid JSON in ${label}`;
      return undefined;
    }
  }

  const amenities = parseJsonField<Record<string, unknown>>("amenities", edited.amenities, original.amenities);
  if (amenities !== undefined) body.amenities = amenities;

  const officeHours = parseJsonField<Record<string, unknown>>("office hours", edited.office_hours, original.office_hours);
  if (officeHours !== undefined) body.office_hours = officeHours;

  const escalation = parseJsonField<Record<string, unknown>[]>("escalation contacts", edited.escalation_contacts, original.escalation_contacts);
  if (escalation !== undefined) body.escalation_contacts = escalation;

  const bizRules = parseJsonField<Record<string, unknown>>("business hour rules", edited.business_hour_rules, original.business_hour_rules);
  if (bizRules !== undefined) body.business_hour_rules = bizRules;

  const callRules = parseJsonField<Record<string, unknown>>("call handling rules", edited.call_handling_rules, original.call_handling_rules);
  if (callRules !== undefined) body.call_handling_rules = callRules;

  return { body, jsonError };
}

function SettingsView() {
  const [state, setState] = useState<SectionState<PropertyDetail>>(emptyState());
  const [retry, setRetry] = useState(0);
  const [edits, setEdits] = useState<EditableSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<{ code: string; message: string } | null>(null);
  const { toast, show } = useToast();

  const [calStatus, setCalStatus] = useState<SectionState<CalendarStatusResponse>>(emptyState());
  const [calRetry, setCalRetry] = useState(0);

  const load = useCallback(() => setRetry((r) => r + 1), []);
  const loadCal = useCallback(() => setCalRetry((r) => r + 1), []);

  useEffect(() => {
    let cancelled = false;
    getProperty(PROPERTY_ID).then(
      (data) => {
        if (cancelled) return;
        setState({ data, loading: false, error: null });
        setEdits(propertyToEditable(data));
      },
      (err) => { if (!cancelled) setState({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [retry]);

  useEffect(() => {
    let cancelled = false;
    getCalendarStatus().then(
      (data) => { if (!cancelled) setCalStatus({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setCalStatus({ data: null, loading: false, error: { code: errorCode(err), message: errorMessage(err) } }); }
    );
    return () => { cancelled = true; };
  }, [calRetry]);

  if (state.loading || state.error) {
    return (
      <section className="page stack">
        <SectionStatus loading={state.loading} error={state.error} noun="settings" onRetry={load} />
      </section>
    );
  }

  const property = state.data!;
  const e = edits!;

  const setField = <K extends keyof EditableSettings>(key: K, value: EditableSettings[K]) => {
    setEdits((cur) => (cur ? { ...cur, [key]: value } : cur));
  };

  const handleSave = async () => {
    if (!edits) return;
    const { body, jsonError } = buildPatch(property, edits);
    if (jsonError) {
      setSaveError({ code: "INVALID_JSON", message: jsonError });
      show({ tone: "danger", text: jsonError });
      return;
    }
    if (Object.keys(body).length === 0) {
      show({ tone: "warn", text: "No changes to save" });
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await patchProperty(PROPERTY_ID, body);
      setState({ data: updated, loading: false, error: null });
      setEdits(propertyToEditable(updated));
      show({ tone: "success", text: "Saved" });
    } catch (err) {
      setSaveError({ code: errorCode(err), message: errorMessage(err) });
      show({ tone: "danger", text: `${errorCode(err)} - ${errorMessage(err)}` });
    } finally {
      setSaving(false);
    }
  };

  const calData = calStatus.data;

  return (
    <section className="page stack">
      <div className="settings-action-row">
        <div className="card-sub">Edit property knobs and save when ready.</div>
        <button className="btn primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save changes"}
        </button>
      </div>

      {saveError && (
        <div className="notice-banner warn">
          <AlertTriangle size={16} />
          <div>
            <strong>Save failed</strong>
            <span className="mono">{saveError.code}</span> - {saveError.message}
          </div>
        </div>
      )}

      <div className="settings-grid">
        <div className="card wide">
          <CardHeader title="Property" subtitle="Name, address, and high-level description" />
          <div className="stack small">
            <label className="settings-label">
              <span>Property name</span>
              <input
                className="field"
                value={e.name}
                onChange={(ev) => setField("name", ev.target.value)}
              />
            </label>
            <label className="settings-label">
              <span>Address</span>
              <input
                className="field"
                value={e.address}
                onChange={(ev) => setField("address", ev.target.value)}
              />
            </label>
            <label className="settings-label">
              <span>Description</span>
              <textarea
                className="textarea"
                rows={3}
                value={e.description}
                onChange={(ev) => setField("description", ev.target.value)}
              />
            </label>
          </div>
        </div>

        <div className="card">
          <CardHeader title="Office hours" subtitle="JSON object describing weekly hours" />
          <textarea
            className="textarea mono"
            rows={6}
            value={e.office_hours}
            placeholder='{"mon": "9-5", ...}'
            onChange={(ev) => setField("office_hours", ev.target.value)}
          />
        </div>

        <div className="card">
          <CardHeader title="Amenities" subtitle="JSON describing available amenities" />
          <textarea
            className="textarea mono"
            rows={6}
            value={e.amenities}
            placeholder='{"pool": true, ...}'
            onChange={(ev) => setField("amenities", ev.target.value)}
          />
        </div>

        <div className="card">
          <CardHeader title="Escalation contacts" subtitle="Array of contact objects (JSON)" />
          <textarea
            className="textarea mono"
            rows={6}
            value={e.escalation_contacts}
            placeholder='[{"name":"Manager","phone":"+1..."}]'
            onChange={(ev) => setField("escalation_contacts", ev.target.value)}
          />
        </div>

        <div className="card">
          <CardHeader title="Business hour rules" subtitle="JSON rules for after-hours behavior" />
          <textarea
            className="textarea mono"
            rows={6}
            value={e.business_hour_rules}
            onChange={(ev) => setField("business_hour_rules", ev.target.value)}
          />
        </div>

        <div className="card">
          <CardHeader title="Call handling rules" subtitle="JSON behavior knobs for the voice agent" />
          <textarea
            className="textarea mono"
            rows={6}
            value={e.call_handling_rules}
            onChange={(ev) => setField("call_handling_rules", ev.target.value)}
          />
        </div>

        <div className="card wide">
          <CardHeader title="Leasing policies" subtitle="Plain-text rules the agent must respect" />
          <textarea
            className="textarea"
            rows={4}
            value={e.leasing_policies}
            onChange={(ev) => setField("leasing_policies", ev.target.value)}
          />
        </div>

        <div className="card wide">
          <CardHeader title="Maintenance instructions" subtitle="Plain-text guidance for maintenance triage" />
          <textarea
            className="textarea"
            rows={4}
            value={e.maintenance_instructions}
            onChange={(ev) => setField("maintenance_instructions", ev.target.value)}
          />
        </div>

        <div className="card wide">
          <CardHeader title="Calendar connection" subtitle="External integration status" />
          <div className="integration-row">
            <Calendar size={18} />
            <div>
              <div className="strong">Google Calendar</div>
              <div className="subtext">
                {calData?.calendar_id
                  ? <>Calendar <span className="mono">{calData.calendar_id}</span></>
                  : calStatus.loading ? "Checking..." : "No calendar configured"}
              </div>
              {calData?.last_check_at && (
                <div className="subtext">Last checked {formatRelativeTime(calData.last_check_at)}</div>
              )}
            </div>
            {calStatus.loading ? (
              <span className="pill neutral">Checking...</span>
            ) : calStatus.error ? (
              <span className="pill danger">{calStatus.error.code}</span>
            ) : calData?.connected ? (
              <span className="pill success">Connected</span>
            ) : (
              <span className="pill danger">{calData?.errors?.[0] ?? "Disconnected"}</span>
            )}
          </div>
          {!calStatus.loading && (Boolean(calStatus.error) || (calData?.errors?.length ?? 0) > 0) && (
            <div className="notice-banner warn" style={{ marginTop: 10 }}>
              <AlertTriangle size={16} />
              <div>
                <strong>Calendar errors</strong>
                {calStatus.error
                  ? `${calStatus.error.code} - ${calStatus.error.message}`
                  : calData!.errors.join(" / ")}
                <button
                  className="btn ghost fit"
                  style={{ marginTop: 6 }}
                  onClick={loadCal}
                >
                  Re-check
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="card wide">
          <CardHeader title="Voice settings" subtitle="Manager-readable voice behavior values" />
          <select className="field" value="Warm and concise" disabled>
            <option>Warm and concise</option>
          </select>
          <div className="preview-line">
            <Mic size={14} /> Hi, thanks for calling {property?.name ?? "the property"}. This is Waxwing Voice.
          </div>
        </div>
      </div>
      <Toast entry={toast} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Recent activity card
// ---------------------------------------------------------------------------

function RecentActivityCard({
  calls,
  loading,
  error,
}: {
  calls: CallListItem[];
  loading: boolean;
  error: { code: string; message: string } | null;
}) {
  const recent = calls.slice(0, 4);
  return (
    <div className="card">
      <CardHeader title="Recent activity" subtitle={`${calls.length} recent calls`} icon={Calendar} />
      {loading ? (
        <InlineEmpty title="Loading..." />
      ) : error ? (
        <InlineEmpty title={`${error.code} - ${error.message}`} />
      ) : (
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
      )}
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

function SearchField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="search-field" style={{ flex: 1, minWidth: 200 }}>
      <Search size={15} />
      <input
        type="search"
        value={value}
        placeholder={placeholder ?? "Search..."}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

interface SelectOption { label: string; value: string; }

function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: SelectOption[] }) {
  return (
    <label className="select-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value || "_all"} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="select-field">
      <span>{label}</span>
      <input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        style={{
          background: "var(--bg-elev)",
          border: "1px solid var(--line)",
          borderRadius: "var(--r-sm)",
          padding: "6px 8px",
          fontSize: 13,
          color: "var(--ink-900)",
          fontFamily: "inherit",
        }}
      />
    </label>
  );
}

function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div className="pagination">
      <span className="pager-info">
        {from}-{to} of {total}
      </span>
      <span className="pager-actions">
        <button
          className="btn ghost"
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page <= 1}
        >
          <ChevronLeft size={14} /> Prev
        </button>
        <span className="pager-info">Page {page} of {totalPages}</span>
        <button
          className="btn ghost"
          onClick={() => onPage(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
        >
          Next <ChevronRight size={14} />
        </button>
      </span>
    </div>
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
