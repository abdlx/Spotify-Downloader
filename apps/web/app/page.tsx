"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";

type Collection = {
  id: string; name: string; type: string; source_url: string; artwork_url?: string; description?: string;
  track_count: number; resolved_count: number; status: string; error?: string; latest_job_id?: string;
  created_at: string; updated_at: string;
};
type Track = {
  id: string; job_item_id?: string; position: number; title: string; artists: string[]; album?: string; artwork_url?: string;
  duration_ms?: number; status: string; progress?: number; error?: string; version_tokens: string[];
  match_title?: string; match_channel?: string; match_duration_ms?: number; confidence?: number;
};
type Job = {
  id: string; collection_id: string; collection_name: string; artwork_url?: string; collection_type: string;
  status: string; total: number; resolved: number; matched: number; completed: number; failed: number; pending: number; downloading: number;
  progress_percent: number; created_at: string; error?: string;
};
type Settings = {
  filename_template: string; bitrate: string; concurrency: number; prefer_official: boolean;
  duration_tolerance_seconds: number; low_confidence_threshold: number; save_cover: boolean;
  network_mode: string; proxy_url: string; proxy_username: string; proxy_password: string;
  proxy_password_configured?: boolean;
};

const API = "/api";

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.detail?.message || "Something went wrong. Please try again.");
  }
  return response.json();
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></>,
    downloads: <><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/></>,
    library: <><path d="M4 19V5"/><path d="M9 19V5"/><path d="M14 19V5"/><path d="m18 5 3 14"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63h.01A1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9v.01A1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"/></>,
    music: <><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></>,
    link: <><path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    play: <path d="m8 5 11 7-11 7Z"/>,
    pause: <><path d="M9 5v14"/><path d="M15 5v14"/></>,
    x: <><path d="m6 6 12 12"/><path d="m18 6-12 12"/></>,
    retry: <><path d="M20 6v5h-5"/><path d="M19 11a7.5 7.5 0 1 0 .2 4"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    alert: <><path d="M12 3 2.5 20h19Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></>,
    spark: <><path d="m12 3 1.2 4.8L18 9l-4.8 1.2L12 15l-1.2-4.8L6 9l4.8-1.2Z"/><path d="m19 15 .5 2 2 .5-2 .5-.5 2-.5-2-2-.5 2-.5Z"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[name]}</svg>;
}

function formatDuration(ms?: number) {
  if (!ms) return "—";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function humanDate(value: string) {
  const date = new Date(value);
  const today = new Date();
  return date.toDateString() === today.toDateString() ? "Today" : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function titleStatus(status: string) {
  return status.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
}

function Art({ src, name, size = "medium" }: { src?: string; name: string; size?: "small" | "medium" | "large" }) {
  return <div className={`art art-${size}`}>{src ? <img src={src} alt="" /> : <Icon name="music" size={size === "large" ? 36 : 18} />}<span className="art-shine" /></div>;
}

function StatusPill({ status, progress, confidence }: { status: string; progress?: number; confidence?: number }) {
  let tone = "neutral";
  if (["COMPLETE", "READY", "RESOLVED"].includes(status)) tone = "success";
  if (status === "COMPLETE_WITH_WARNINGS" || status === "FAILED") tone = status === "FAILED" ? "danger" : "warning";
  if (["SEARCHING", "MATCHED", "DOWNLOADING", "TRANSCODING", "TAGGING", "RESOLVING"].includes(status)) tone = "active";
  if (confidence !== undefined && confidence < .72 && ["MATCHED", "COMPLETE"].includes(status)) tone = "warning";
  const label = status === "DOWNLOADING" && progress ? `Downloading ${Math.round(progress)}%` : titleStatus(status);
  return <span className={`status status-${tone}`}><span className="status-dot" />{label}</span>;
}

function Shell({ page, setPage, children }: { page: string; setPage: (p: string) => void; children: ReactNode }) {
  const nav = [["home", "Home"], ["downloads", "Downloads"], ["library", "Library"], ["settings", "Settings"]];
  return <div className="shell">
    <aside className="sidebar">
      <button className="brand" onClick={() => setPage("home")}><span className="brand-mark"><Icon name="music" /></span><span><strong>Lilt</strong><small>LOCAL LIBRARY</small></span></button>
      <nav>{nav.map(([icon, label]) => <button key={label} className={page === label.toLowerCase() ? "active" : ""} onClick={() => setPage(label.toLowerCase())}><Icon name={icon} /><span>{label}</span></button>)}</nav>
      <div className="local-card"><span className="pulse" /><div><strong>Running locally</strong><small>Your files stay on this device</small></div></div>
      <p className="legal-note">Only download media you’re authorized to save.</p>
    </aside>
    <main>{children}</main>
    <nav className="mobile-nav">{nav.map(([icon, label]) => <button key={label} className={page === label.toLowerCase() ? "active" : ""} onClick={() => setPage(label.toLowerCase())}><Icon name={icon} /><span>{label}</span></button>)}</nav>
  </div>;
}

function Empty({ icon, title, text, action }: { icon: string; title: string; text: string; action?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={28} /></span><h3>{title}</h3><p>{text}</p>{action}</div>;
}

function Home({ selected, selectedJobId, onSelect, onJobSelect, onNavigate }: { selected: Collection | null; selectedJobId: string | null; onSelect: (c: Collection) => void; onJobSelect: (id: string | null) => void; onNavigate: (p: string) => void }) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [collection, setCollection] = useState<Collection | null>(selected);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [trackTotal, setTrackTotal] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    if (!collection?.id) return;
    const latest = await api<Collection>(`/collections/${collection.id}`);
    setCollection(latest); onSelect(latest);
    const jobQuery = selectedJobId ? `&job_id=${encodeURIComponent(selectedJobId)}` : "";
    const trackData = await api<{items: Track[]; total: number}>(`/collections/${collection.id}/tracks?limit=${Math.max(100, tracks.length || 100)}${jobQuery}`);
    setTracks(trackData.items); setTrackTotal(trackData.total);
    const jobId = selectedJobId || latest.latest_job_id;
    if (jobId) {
      const latestJob = await api<Job>(`/jobs/${jobId}?include_items=false`);
      setJob(latestJob);
    }
  }, [collection?.id, selectedJobId, tracks.length, onSelect]);

  useEffect(() => { if (selected?.id !== collection?.id) { setCollection(selected); setTracks([]); setJob(null); } }, [selected?.id]);
  useEffect(() => { if (collection?.id) refresh().catch(() => {}); }, [collection?.id, selectedJobId]);
  useEffect(() => {
    if (!collection) return;
    const source = new EventSource(`${API}/events`);
    source.onmessage = () => {};
    const schedule = () => { if (!refreshTimer.current) refreshTimer.current = setTimeout(() => { refreshTimer.current = null; refresh().catch(() => {}); }, 350); };
    ["collection.resolving", "collection.resolved", "collection.failed", "track.resolved", "track.matching", "track.matched", "track.download.started", "track.download.progress", "track.transcoding", "track.tagging", "track.retry", "track.retry_scheduled", "track.complete", "track.failed", "job.queued", "job.pause", "job.resume", "job.cancel", "job.progress", "job.complete"].forEach(name => source.addEventListener(name, schedule));
    return () => { source.close(); if (refreshTimer.current) clearTimeout(refreshTimer.current); };
  }, [collection?.id, refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setLoading(true);
    try {
      const result = await api<{collection: Collection}>("/resolve", { method: "POST", body: JSON.stringify({ url }) });
      setCollection(result.collection); onSelect(result.collection); onJobSelect(null); setTracks([]); setJob(null);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not resolve this link."); }
    finally { setLoading(false); }
  }

  async function downloadAll() {
    if (!collection) return;
    setLoading(true); setError("");
    try { const value = await api<Job>(`/collections/${collection.id}/download`, { method: "POST" }); onJobSelect(value.id); setJob(value); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not start the download."); }
    finally { setLoading(false); }
  }

  async function action(name: string) {
    if (!job) return;
    try { setJob(await api<Job>(`/jobs/${job.id}/${name}`, { method: "POST" })); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Action failed."); }
  }

  async function retryTrack(itemId: string) {
    try { setJob(await api<Job>(`/job-items/${itemId}/retry`, { method: "POST" })); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not retry this track."); }
  }

  async function loadMore() {
    if (!collection) return;
    const jobQuery = selectedJobId ? `&job_id=${encodeURIComponent(selectedJobId)}` : "";
    const page = await api<{items: Track[]; total: number}>(`/collections/${collection.id}/tracks?offset=${tracks.length}&limit=100${jobQuery}`);
    setTracks(previous => [...previous, ...page.items]); setTrackTotal(page.total);
  }

  const resolving = collection?.status === "RESOLVING";
  const activeJob = job && ["QUEUED", "DOWNLOADING", "PAUSED", "CANCEL_REQUESTED"].includes(job.status);
  return <div className="page home-page">
    <header className="topbar"><div><p className="eyebrow">GOOD TO SEE YOU</p><h1>Build your music library</h1></div><button className="history-link" onClick={() => onNavigate("downloads")}><Icon name="downloads" /> View downloads</button></header>
    <section className="hero-card">
      <div className="hero-copy"><span className="hero-icon"><Icon name="spark" size={24} /></span><h2>What would you like to save?</h2><p>Paste a public Spotify track, album, or playlist. You’ll review everything before any download begins.</p></div>
      <form onSubmit={submit} className="url-form">
        <label htmlFor="spotify-url">Spotify link</label>
        <div className={`url-box ${error ? "invalid" : ""}`}><Icon name="link" /><input id="spotify-url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://open.spotify.com/playlist/…" /><button disabled={loading || !url.trim()}>{loading && !collection ? <span className="spinner" /> : <Icon name="search" />} Fetch</button></div>
        <div className="input-foot"><span>{error ? <><Icon name="alert" size={15} /> {error}</> : "Supports tracks, albums, and playlists — including large libraries."}</span><span className="privacy">No account required</span></div>
      </form>
    </section>

    {!collection ? <section className="getting-started"><div><span>01</span><strong>Paste a Spotify link</strong><p>Public tracks, albums, and playlists are supported.</p></div><div><span>02</span><strong>Review every track</strong><p>We preserve exact editions, remixes, live and acoustic versions.</p></div><div><span>03</span><strong>Download locally</strong><p>Finished MP3s appear directly in your downloads folder.</p></div></section> :
      <section className="collection-panel">
        <div className="collection-head">
          <Art src={collection.artwork_url} name={collection.name} size="large" />
          <div className="collection-copy"><p className="eyebrow">{collection.type.toUpperCase()}</p><h2>{collection.name}</h2><p>{collection.track_count || collection.resolved_count} tracks{collection.track_count ? ` • about ${Math.round(collection.track_count * 3.6 / 60)} hours` : ""}</p>{resolving && <div className="resolve-line"><span className="spinner dark" /> Resolved {collection.resolved_count} / {collection.track_count || "…"}</div>}</div>
          <div className="collection-actions">
            {!resolving && !activeJob && <button className="primary" onClick={downloadAll} disabled={loading}><Icon name="downloads" /> Download all</button>}
            {job?.status === "DOWNLOADING" && <><button className="secondary" onClick={() => action("pause")}><Icon name="pause" /> Pause</button><button className="ghost danger" onClick={() => action("cancel")}><Icon name="x" /> Cancel</button></>}
            {job?.status === "PAUSED" && <><button className="primary" onClick={() => action("resume")}><Icon name="play" /> Resume</button><button className="ghost danger" onClick={() => action("cancel")}><Icon name="x" /> Cancel</button></>}
            {job && job.failed > 0 && !activeJob && <button className="secondary" onClick={() => action("retry-failed")}><Icon name="retry" /> Retry failed</button>}
          </div>
        </div>
        {job && job.total > 0 && <div className="job-progress"><div className="progress-meta"><strong>{job.completed} / {job.total}</strong><span>{job.progress_percent || Math.round(job.completed / job.total * 100)}%</span></div><div className="progress-track"><span style={{ width: `${job.progress_percent || job.completed / job.total * 100}%` }} /></div><div className="progress-stats"><span><i className="dot active" />{job.downloading || 0} active</span><span><i className="dot queued" />{job.pending || 0} queued</span><span><i className="dot failed" />{job.failed || 0} failed</span></div></div>}
        <div className="track-table">
          <div className="track-row table-head"><span>#</span><span>Song</span><span>Match</span><span>Time</span><span>Status</span></div>
          {tracks.map(track => <div className="track-row" key={`${track.id}-${track.position}`}>
            <span className="track-number">{track.position}</span>
            <span className="song-cell"><Art src={track.artwork_url} name={track.title} size="small" /><span><strong>{track.title}</strong><small>{track.artists.join(", ")}{track.version_tokens.length ? <em>{track.version_tokens.join(" · ")}</em> : null}</small></span></span>
            <span className="match-cell">{track.match_title ? <span><strong>{track.match_title}</strong><small>{track.match_channel}{track.confidence !== undefined ? ` • ${Math.round(track.confidence * 100)}%` : ""}</small></span> : <span className="muted">Matched when downloading</span>}</span>
            <span className="duration">{formatDuration(track.duration_ms)}</span>
            <span><StatusPill status={track.status} progress={track.progress} confidence={track.confidence} />{track.error && <small className="row-error" title={track.error}>{track.error.split("\n")[0]}</small>}{track.status === "FAILED" && track.job_item_id && <button className="row-retry" onClick={() => retryTrack(track.job_item_id!)}><Icon name="retry" size={12} /> Retry</button>}</span>
          </div>)}
          {!tracks.length && resolving && <div className="table-loading"><span className="spinner dark" /> Waiting for Spotify metadata…</div>}
        </div>
        {tracks.length < trackTotal && <button className="load-more" onClick={loadMore}>Show 100 more tracks <Icon name="chevron" size={16} /></button>}
      </section>}
  </div>;
}

function Downloads({ onOpen }: { onOpen: (collectionId: string, jobId: string) => void }) {
  const [jobs, setJobs] = useState<Job[]>([]); const [loading, setLoading] = useState(true);
  const load = useCallback(() => api<{items: Job[]}>("/jobs").then(r => setJobs(r.items)).finally(() => setLoading(false)), []);
  useEffect(() => { load(); const source = new EventSource(`${API}/events`); const refresh = () => load(); ["job.queued", "job.complete", "track.complete", "track.failed"].forEach(e => source.addEventListener(e, refresh)); return () => source.close(); }, [load]);
  return <div className="page"><header className="topbar"><div><p className="eyebrow">ACTIVITY</p><h1>Downloads</h1><p className="subtitle">Current and past download jobs.</p></div></header>
    <section className="list-panel">{!loading && !jobs.length ? <Empty icon="downloads" title="Nothing downloaded yet" text="Your download history will appear here." /> : jobs.map(job => <button className="job-row" key={job.id} onClick={() => onOpen(job.collection_id, job.id)}><Art src={job.artwork_url} name={job.collection_name} /><span className="job-name"><strong>{job.collection_name}</strong><small>{job.total} tracks • {humanDate(job.created_at)}</small></span><span className="job-count"><strong>{job.completed}</strong><small>complete</small></span>{job.failed > 0 && <span className="job-count failed-text"><strong>{job.failed}</strong><small>failed</small></span>}<StatusPill status={job.status} /><Icon name="chevron" /></button>)}</section>
  </div>;
}

function Library({ onOpen }: { onOpen: (collection: Collection) => void }) {
  const [collections, setCollections] = useState<Collection[]>([]); const [loading, setLoading] = useState(true);
  useEffect(() => { api<{items: Collection[]}>("/collections").then(r => setCollections(r.items)).finally(() => setLoading(false)); }, []);
  return <div className="page"><header className="topbar"><div><p className="eyebrow">YOUR COLLECTIONS</p><h1>Library</h1><p className="subtitle">A fast index of everything you’ve resolved.</p></div></header>
    {!loading && !collections.length ? <Empty icon="library" title="Your library is waiting" text="Fetch a Spotify collection and it will stay indexed here." /> : <section className="library-grid">{collections.map(collection => <button className="library-card" key={collection.id} onClick={() => onOpen(collection)}><Art src={collection.artwork_url} name={collection.name} size="large" /><span><small>{collection.type}</small><strong>{collection.name}</strong><p>{collection.resolved_count} tracks</p><StatusPill status={collection.status} /></span></button>)}</section>}
  </div>;
}

function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null); const [message, setMessage] = useState(""); const [error, setError] = useState("");
  useEffect(() => { api<Settings>("/settings").then(setSettings).catch(e => setError(e.message)); }, []);
  if (!settings) return <div className="page"><header className="topbar"><div><p className="eyebrow">PREFERENCES</p><h1>Settings</h1></div></header><div className="table-loading"><span className="spinner dark" /> Loading settings…</div></div>;
  function update<K extends keyof Settings>(key: K, value: Settings[K]) { setSettings(current => current ? { ...current, [key]: value } : current); setMessage(""); }
  async function save(event: FormEvent) { event.preventDefault(); setError(""); try { const payload: Partial<Settings> = { ...settings }; if (!payload.proxy_password) delete payload.proxy_password; delete payload.proxy_password_configured; const saved = await api<Settings>("/settings", { method: "PATCH", body: JSON.stringify(payload) }); setSettings(saved); setMessage("Settings saved"); } catch (e) { setError(e instanceof Error ? e.message : "Could not save settings."); } }
  return <div className="page"><header className="topbar"><div><p className="eyebrow">PREFERENCES</p><h1>Settings</h1><p className="subtitle">Changes apply to newly started tracks.</p></div></header>
    <form className="settings-form" onSubmit={save}>
      <section className="settings-card"><div className="settings-title"><span><Icon name="downloads" /></span><div><h2>Downloads</h2><p>Format, naming, and parallel work.</p></div></div>
        <div className="field full"><label>Download directory</label><input value="/downloads" disabled /><small>Maps to the project’s <code>./downloads</code> folder on your computer.</small></div>
        <div className="field full"><label>Filename template</label><input value={settings.filename_template} onChange={e => update("filename_template", e.target.value)} /><small>Available: {"{list-name}, {list-position}, {artist}, {title}, {album}"}</small></div>
        <div className="field"><label>Format</label><select value="mp3" disabled><option>MP3</option></select></div>
        <div className="field"><label>MP3 bitrate</label><select value={settings.bitrate} onChange={e => update("bitrate", e.target.value)}><option value="auto">Auto (source-aware)</option><option value="128k">128 kbps</option><option value="192k">192 kbps</option><option value="256k">256 kbps</option><option value="320k">320 kbps</option></select></div>
        <div className="field"><label>Concurrent downloads</label><input type="number" min="1" max="5" value={settings.concurrency} onChange={e => update("concurrency", Number(e.target.value))} /><small>2 is the reliable default.</small></div>
        <label className="toggle-row"><span><strong>Save cover.jpg</strong><small>Also save artwork alongside downloaded tracks.</small></span><input type="checkbox" checked={settings.save_cover} onChange={e => update("save_cover", e.target.checked)} /><i /></label>
      </section>
      <section className="settings-card"><div className="settings-title"><span><Icon name="search" /></span><div><h2>Matching</h2><p>How YouTube candidates are scored.</p></div></div>
        <label className="toggle-row"><span><strong>Prefer official audio</strong><small>Gives a small ranking boost to official sources.</small></span><input type="checkbox" checked={settings.prefer_official} onChange={e => update("prefer_official", e.target.checked)} /><i /></label>
        <div className="field"><label>Duration tolerance</label><div className="suffix"><input type="number" min="2" max="60" value={settings.duration_tolerance_seconds} onChange={e => update("duration_tolerance_seconds", Number(e.target.value))} /><span>seconds</span></div></div>
        <div className="field"><label>Low-confidence threshold</label><div className="suffix"><input type="number" min="0" max="100" value={Math.round(settings.low_confidence_threshold * 100)} onChange={e => update("low_confidence_threshold", Number(e.target.value) / 100)} /><span>%</span></div></div>
      </section>
      <section className="settings-card"><div className="settings-title"><span><Icon name="link" /></span><div><h2>Network</h2><p>Optional legitimate proxy configuration.</p></div></div>
        <div className="field"><label>Connection</label><select value={settings.network_mode} onChange={e => update("network_mode", e.target.value)}><option value="direct">Direct</option><option value="http">HTTP / HTTPS proxy</option><option value="socks">SOCKS proxy</option></select></div>
        {settings.network_mode !== "direct" && <><div className="field full"><label>Proxy URL</label><input value={settings.proxy_url} onChange={e => update("proxy_url", e.target.value)} placeholder={settings.network_mode === "socks" ? "socks5h://127.0.0.1:1080" : "http://127.0.0.1:8080"} /></div><div className="field"><label>Username</label><input value={settings.proxy_username} onChange={e => update("proxy_username", e.target.value)} autoComplete="off" /></div><div className="field"><label>Password</label><input type="password" value={settings.proxy_password} onChange={e => update("proxy_password", e.target.value)} placeholder={settings.proxy_password_configured ? "Saved — leave blank to keep" : "Optional"} autoComplete="new-password" /></div></>}
      </section>
      <div className="save-bar"><span className={error ? "save-error" : "save-message"}>{error || message}</span><button className="primary">Save settings</button></div>
    </form>
  </div>;
}

export default function App() {
  const [page, setPage] = useState("home"); const [selected, setSelected] = useState<Collection | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  function openCollection(collection: Collection) { setSelected(collection); setSelectedJobId(null); setPage("home"); }
  async function openJob(collectionId: string, jobId: string) { try { setSelected(await api<Collection>(`/collections/${collectionId}`)); setSelectedJobId(jobId); setPage("home"); } catch {} }
  return <Shell page={page} setPage={setPage}>{page === "home" && <Home selected={selected} selectedJobId={selectedJobId} onSelect={setSelected} onJobSelect={setSelectedJobId} onNavigate={setPage} />}{page === "downloads" && <Downloads onOpen={openJob} />}{page === "library" && <Library onOpen={openCollection} />}{page === "settings" && <SettingsPage />}</Shell>;
}
