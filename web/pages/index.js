import { useState, useEffect, useRef } from "react";

const API = "http://localhost:8010";

export default function Home() {
  // Search state
  const [location, setLocation] = useState("San Francisco, CA");
  const [hours, setHours] = useState(2);
  const [checkin, setCheckin] = useState("");
  const [checkout, setCheckout] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Autocomplete state
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const suggestionsRef = useRef(null);
  const debounceTimer = useRef(null);

  // Coordinates from autocomplete (avoids re-geocoding)
  const [selectedCoords, setSelectedCoords] = useState(null);

  // Expand / site-detail state
  const [expandedCard, setExpandedCard] = useState(null); // facility id or null
  const [siteDetails, setSiteDetails] = useState({}); // { [facilityId]: { loading, error, data } }

  // Alerts state
  const [alertEmail, setAlertEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [alertsTab, setAlertsTab] = useState("search"); // "search" | "alerts"
  const [myAlerts, setMyAlerts] = useState([]);
  const [alertMsg, setAlertMsg] = useState("");

  function isValidEmail(email) {
    return /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(email.trim());
  }
  function handleEmailChange(val) {
    setAlertEmail(val);
    if (val && !isValidEmail(val)) setEmailError("Please enter a valid email address");
    else setEmailError("");
  }

  useEffect(() => {
    function handleClickOutside(event) {
      if (suggestionsRef.current && !suggestionsRef.current.contains(event.target)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // --- Autocomplete ---
  async function fetchSuggestions(query) {
    if (query.length < 2) { setSuggestions([]); return; }
    setLoadingSuggestions(true);
    try {
      const res = await fetch(`${API}/autocomplete?q=${encodeURIComponent(query)}`);
      if (res.ok) {
        const data = await res.json();
        setSuggestions(data);
        setShowSuggestions(data.length > 0);
      }
    } catch (err) { console.error("Autocomplete error:", err); }
    finally { setLoadingSuggestions(false); }
  }

  function handleLocationChange(value) {
    setLocation(value);
    setSelectedCoords(null); // clear — user is typing something new
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => fetchSuggestions(value), 300);
  }

  function selectSuggestion(s) {
    setLocation(s.display);
    setSelectedCoords({ lat: s.lat, lng: s.lng, state_code: s.state_code });
    setSuggestions([]);
    setShowSuggestions(false);
  }

  // --- Search ---
  async function doSearch(e) {
    e && e.preventDefault();
    setShowSuggestions(false);
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ location, max_hours: hours });
      if (selectedCoords) {
        params.append("lat", selectedCoords.lat);
        params.append("lng", selectedCoords.lng);
        if (selectedCoords.state_code) params.append("state", selectedCoords.state_code);
      }
      if (checkin) params.append("checkin", checkin);
      if (checkout) params.append("checkout", checkout);
      const res = await fetch(`${API}/search?${params}`);
      if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || "Search failed"); }
      setResults(await res.json());
    } catch (err) { setError(err.message); setResults([]); }
    finally { setLoading(false); }
  }

  // --- Expand campground to show individual sites ---
  async function toggleExpand(facilityId, r) {
    if (expandedCard === facilityId) { setExpandedCard(null); return; }
    setExpandedCard(facilityId);
    // Already fetched?
    if (siteDetails[facilityId]?.data) return;
    if (!checkin || !checkout) {
      setSiteDetails(prev => ({ ...prev, [facilityId]: { loading: false, error: "Set check-in/out dates to see site availability.", data: null } }));
      return;
    }
    setSiteDetails(prev => ({ ...prev, [facilityId]: { loading: true, error: null, data: null } }));
    try {
      const res = await fetch(`${API}/availability/${r.availability_id || facilityId}?checkin=${checkin}&checkout=${checkout}`);
      if (!res.ok) throw new Error("Failed to load sites");
      const data = await res.json();
      setSiteDetails(prev => ({ ...prev, [facilityId]: { loading: false, error: null, data } }));
    } catch (err) {
      setSiteDetails(prev => ({ ...prev, [facilityId]: { loading: false, error: err.message, data: null } }));
    }
  }

  // --- Alert modal state ---
  const [alertModal, setAlertModal] = useState(null); // { facilityId, facilityName, reservationUrl } or null for area
  const [alertCreating, setAlertCreating] = useState(false);

  function openAlertModal(facilityId, facilityName, reservationUrl) {
    if (!checkin || !checkout) { setAlertMsg("⚠️ Set check-in and check-out dates first."); return; }
    setAlertModal({ facilityId, facilityName, reservationUrl });
  }

  // --- Alerts ---
  async function createAlert() {
    if (!alertEmail) { setAlertMsg("⚠️ Enter your email above first."); return; }
    if (!isValidEmail(alertEmail)) { setAlertMsg("⚠️ Please enter a valid email address."); setEmailError("Please enter a valid email address"); return; }
    if (!checkin || !checkout) { setAlertMsg("⚠️ Set check-in/out dates before creating an alert."); return; }
    setAlertCreating(true);
    try {
      const body = {
        email: alertEmail, checkin, checkout, max_drive_hours: parseFloat(hours),
        ...(alertModal?.facilityId
          ? { facility_id: alertModal.facilityId, facility_name: alertModal.facilityName, reservation_url: alertModal.reservationUrl }
          : { location, ...(selectedCoords ? { lat: selectedCoords.lat, lng: selectedCoords.lng } : {}) }),
      };
      const res = await fetch(`${API}/alerts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      setAlertMsg(`✅ ${data.message || "Alert created!"}`);
      setAlertModal(null);
    } catch { setAlertMsg("❌ Failed to create alert."); }
    finally { setAlertCreating(false); }
  }

  async function loadMyAlerts() {
    if (!alertEmail) { setAlertMsg("⚠️ Enter your email to see alerts."); return; }
    if (!isValidEmail(alertEmail)) { setAlertMsg("⚠️ Please enter a valid email address."); setEmailError("Please enter a valid email address"); return; }
    try { const res = await fetch(`${API}/alerts?email=${encodeURIComponent(alertEmail)}`); setMyAlerts(await res.json()); } catch { setMyAlerts([]); }
  }

  async function deleteAlert(id) { await fetch(`${API}/alerts/${id}`, { method: "DELETE" }); loadMyAlerts(); }

  return (
    <main style={{ fontFamily: "'Segoe UI', Arial, sans-serif", background: "#f9fafb", minHeight: "100vh" }}>
      {/* Header */}
      <header style={{ background: "#2c5f2d", color: "white", padding: "16px 24px" }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h1 style={{ margin: 0, fontSize: 24 }}>🏕️ Campsite Finder</h1>
          <nav style={{ display: "flex", gap: 12 }}>
            <button onClick={() => setAlertsTab("search")} style={tabStyle(alertsTab === "search")}>Search</button>
            <button onClick={() => { setAlertsTab("alerts"); loadMyAlerts(); }} style={tabStyle(alertsTab === "alerts")}>🔔 My Alerts</button>
          </nav>
        </div>
      </header>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: 24 }}>
        {/* Email bar */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label style={{ fontWeight: 600, fontSize: 14 }}>Email for alerts:</label>
            <input type="email" value={alertEmail} onChange={(e) => handleEmailChange(e.target.value)}
              placeholder="you@email.com"
              style={{ padding: "6px 10px", borderRadius: 4, border: `1px solid ${emailError ? "#e53935" : "#ccc"}`, width: 260, outline: "none" }} />
            {alertEmail && !emailError && <span style={{ color: "#2e7d32", fontSize: 16 }}>✓</span>}
          </div>
          {emailError && <div style={{ color: "#e53935", fontSize: 12, marginTop: 4, marginLeft: 120 }}>{emailError}</div>}
        </div>

        {alertMsg && (
          <div style={{ padding: 12, background: "#d4edda", border: "1px solid #c3e6cb", borderRadius: 4, marginBottom: 16, color: "#155724" }}>
            {alertMsg}
            <button onClick={() => setAlertMsg("")} style={{ marginLeft: 12, background: "none", border: "none", cursor: "pointer", fontWeight: 700 }}>✕</button>
          </div>
        )}

        {alertsTab === "search" ? (
          <>
            {/* Search Form */}
            <form onSubmit={doSearch} style={{ marginBottom: 24, padding: 20, background: "white", borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {/* Location */}
              <div style={{ gridColumn: "1 / -1", position: "relative" }} ref={suggestionsRef}>
                <label style={labelStyle}>📍 Location</label>
                <input value={location} onChange={(e) => handleLocationChange(e.target.value)}
                  onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
                  placeholder="City, State or ZIP code" style={inputStyle} autoComplete="off" />
                {showSuggestions && suggestions.length > 0 && (
                  <div style={dropdownStyle}>
                    {suggestions.map((s, i) => (
                      <div key={i} onClick={() => selectSuggestion(s)} style={dropdownItemStyle}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "#f0f7f0")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "white")}>
                        <div style={{ fontWeight: 500 }}>{s.display}</div>
                        {s.full_address !== s.display && <div style={{ fontSize: 12, color: "#666", marginTop: 2 }}>{s.full_address}</div>}
                      </div>
                    ))}
                  </div>
                )}
                {loadingSuggestions ? <small style={{ color: "#666", fontStyle: "italic" }}>Loading suggestions...</small>
                  : <small style={{ color: "#999" }}>City, zip code, or address</small>}
              </div>
              <div><label style={labelStyle}>📅 Check-in</label><input type="date" value={checkin} onChange={(e) => setCheckin(e.target.value)} style={inputStyle} /></div>
              <div><label style={labelStyle}>📅 Check-out</label><input type="date" value={checkout} onChange={(e) => setCheckout(e.target.value)} style={inputStyle} /></div>
              <div><label style={labelStyle}>🚗 Max drive (hours)</label><input type="number" value={hours} onChange={(e) => setHours(e.target.value)} min="0.5" max="8" step="0.5" style={inputStyle} /></div>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
                <button type="submit" disabled={loading} style={primaryBtn(loading)}>{loading ? "Searching..." : "🔍 Search"}</button>
                <button type="button" onClick={() => openAlertModal(null, null, null)} style={secondaryBtn}>🔔 Alert area</button>
              </div>
            </form>

            {error && <div style={{ padding: 16, background: "#fee", border: "1px solid #fcc", borderRadius: 4, marginBottom: 16, color: "#c00" }}>{error}</div>}

            {results.length === 0 && !loading && <div style={{ textAlign: "center", padding: 48, color: "#666" }}><p style={{ fontSize: 18 }}>Enter your location and dates to find campsites nearby.</p></div>}

            {results.length > 0 && (
              <div>
                <h2 style={{ marginBottom: 16 }}>Found {results.length} campground{results.length !== 1 ? "s" : ""}</h2>
                <div style={{ display: "grid", gap: 16 }}>
                  {results.map((r) => {
                    const campUrl = r.reservation_url || `https://www.recreation.gov/camping/campgrounds/${r.id}`;
                    const isExpanded = expandedCard === r.id;
                    const sd = siteDetails[r.id];
                    return (
                    <div key={r.id} style={cardStyle}>
                      {/* Campground header */}
                      <div style={{ display: "flex", gap: 16 }}>
                        {r.image_url && (
                          <div style={{ width: 120, height: 90, flexShrink: 0, borderRadius: 6, overflow: "hidden", background: "#eee" }}>
                            <img src={r.image_url} alt={r.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} onError={e => { e.target.parentElement.style.display = "none"; }} />
                          </div>
                        )}
                        <div style={{ flex: 1, display: "flex", justifyContent: "space-between", alignItems: "start", gap: 16 }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                            <a href={campUrl} target="_blank" rel="noreferrer" style={{ margin: 0, color: "#2c5f2d", fontSize: 18, fontWeight: 700, textDecoration: "none" }}>
                              {r.name} ↗
                            </a>
                            {r.reservable && <span style={{ fontSize: 11, background: "#e8f5e9", color: "#2e7d32", padding: "2px 8px", borderRadius: 10 }}>Reservable</span>}
                          </div>
                          <span style={providerBadge(r.provider)}>{r.provider}</span>
                          {r.description && <p style={{ fontSize: 14, color: "#555", margin: "0 0 8px", lineHeight: 1.4 }}>{r.description.replace(/<[^>]+>/g, "").slice(0, 200)}</p>}
                          <div style={{ display: "flex", gap: 16, fontSize: 14, color: "#555", flexWrap: "wrap" }}>
                            <span>📍 {r.distance_miles} mi</span>
                            <span>🚗 {r.drive_hours} hrs</span>
                            {r.available_sites != null && <span>🏕️ {r.available_sites}/{r.total_sites} sites</span>}
                          </div>
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end", minWidth: 150 }}>
                          {r.available != null && <span style={availBadge(r.available)}>{r.available ? "✓ Available" : "✗ Full"}</span>}
                          <a href={campUrl} target="_blank" rel="noreferrer" style={linkBtn}>{r.provider === "ReserveCalifornia" ? "Book on ReserveCalifornia →" : r.provider === "National Park Service" ? "View on NPS.gov →" : "View on Rec.gov →"}</a>
                          {r.available === false ? (
                            <button onClick={() => openAlertModal(r.id, r.name, campUrl)} style={alertBtnPrimaryStyle}>🔔 Alert me when available</button>
                          ) : (
                            <button onClick={() => openAlertModal(r.id, r.name, campUrl)} style={alertBtnStyle}>🔔 Set alert</button>
                          )}
                        </div>
                        </div>
                      </div>

                      {/* Amenities & fees */}
                      {r.amenities && r.amenities.length > 0 && (
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8, paddingTop: 8, borderTop: "1px solid #f0f0f0" }}>
                          {r.amenities.map((a, i) => (
                            <span key={i} style={{ fontSize: 11, background: "#f5f5f5", color: "#666", padding: "2px 8px", borderRadius: 10 }}>{a}</span>
                          ))}
                        </div>
                      )}
                      {r.fees && r.fees.length > 0 && (
                        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6, fontSize: 13, color: "#666" }}>
                          {r.fees.map((f, i) => (
                            <span key={i}>💰 {f.title}: ${f.cost}</span>
                          ))}
                        </div>
                      )}

                      {/* Expand / collapse toggle */}
                      <button onClick={() => toggleExpand(r.id, r)}
                        style={{ marginTop: 10, background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "6px 14px", cursor: "pointer", fontSize: 13, color: "#2c5f2d", fontWeight: 600, width: "100%", textAlign: "left", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span>{isExpanded ? "▾ Hide individual campsites" : "▸ Show available campsites"}</span>
                        {sd?.loading && <span style={{ fontSize: 12, color: "#999" }}>Loading...</span>}
                      </button>

                      {/* Expanded site list */}
                      {isExpanded && (
                        <div style={{ marginTop: 10 }}>
                          {sd?.loading && (
                            <div style={{ padding: 16, textAlign: "center", color: "#999" }}>
                              <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⏳</span> Loading campsite availability...
                            </div>
                          )}
                          {sd?.error && (
                            <div style={{ padding: 12, background: "#fff8e1", border: "1px solid #ffe082", borderRadius: 6, color: "#f57f17", fontSize: 13 }}>
                              {sd.error}
                            </div>
                          )}
                          {sd?.data && (
                            <div>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, padding: "8px 0", borderBottom: "1px solid #eee" }}>
                                <span style={{ fontSize: 13, fontWeight: 600, color: "#555" }}>
                                  {sd.data.available_sites} of {sd.data.total_sites} sites available for {sd.data.checkin} → {sd.data.checkout}
                                </span>
                              </div>
                              <div style={{ maxHeight: 320, overflowY: "auto" }}>
                                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                                  <thead>
                                    <tr style={{ background: "#f5f5f5", position: "sticky", top: 0 }}>
                                      <th style={thStyle}>Site</th>
                                      <th style={thStyle}>Loop</th>
                                      <th style={thStyle}>Type</th>
                                      <th style={thStyle}>Max people</th>
                                      <th style={thStyle}>Status</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {sd.data.site_details.map((site) => (
                                      <tr key={site.site_id} style={{ borderBottom: "1px solid #f0f0f0", background: site.available ? "#f6fff6" : "white" }}>
                                        <td style={tdStyle}>
                                          <span style={{ fontWeight: 600 }}>{site.site_name || site.site_id}</span>
                                        </td>
                                        <td style={tdStyle}>{site.loop || "—"}</td>
                                        <td style={tdStyle}>{site.site_type || "—"}</td>
                                        <td style={tdStyle}>{site.max_people || "—"}</td>
                                        <td style={tdStyle}>
                                          {site.available ? (
                                            <span style={{ color: "#2e7d32", fontWeight: 600 }}>✅ Available ({site.available_nights}/{site.total_nights_needed} nights)</span>
                                          ) : (
                                            <span style={{ color: "#999" }}>❌ {site.available_nights}/{site.total_nights_needed} nights</span>
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
                      )}
                    </div>
                  )})}
                </div>
              </div>
            )}
          </>
        ) : (
          /* ALERTS TAB */
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2>🔔 My Alerts</h2>
              <button onClick={loadMyAlerts} style={secondaryBtn}>Refresh</button>
            </div>
            {myAlerts.length === 0 ? (
              <div style={{ textAlign: "center", padding: 48, color: "#666" }}>
                <p>No alerts yet. Search for campsites and click "Set alert" to get notified!</p>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 12 }}>
                {myAlerts.map((a) => {
                  const alertUrl = a.reservation_url || (a.facility_id ? `https://www.recreation.gov/camping/campgrounds/${a.facility_id}` : null);
                  return (
                  <div key={a.id} style={{ ...cardStyle, borderLeft: a.active ? "4px solid #2c5f2d" : "4px solid #ccc" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
                      <div>
                        <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 16 }}>
                          {alertUrl ? (
                            <a href={alertUrl} target="_blank" rel="noreferrer" style={{ color: "#2c5f2d", textDecoration: "none" }}>
                              {a.facility_name || "Campsite"} ↗
                            </a>
                          ) : (
                            <span>📍 {a.location_query || "Area Alert"}</span>
                          )}
                        </div>
                        <div style={{ fontSize: 13, color: "#666", marginBottom: 4 }}>
                          📅 {a.checkin} → {a.checkout} &nbsp;|&nbsp; 🚗 {a.max_drive_hours}h max
                        </div>
                        <div style={{ display: "flex", gap: 12, fontSize: 12, color: "#999", flexWrap: "wrap" }}>
                          <span>{a.active ? "🟢 Active" : "⚪ Inactive"}</span>
                          {a.times_notified > 0 && <span>📧 Notified {a.times_notified}×</span>}
                          {a.last_checked_at && <span>🕐 Last checked: {new Date(a.last_checked_at + "Z").toLocaleString()}</span>}
                        </div>
                        {alertUrl && (
                          <a href={alertUrl} target="_blank" rel="noreferrer"
                            style={{ fontSize: 12, color: "#2c5f2d", textDecoration: "none", marginTop: 4, display: "inline-block" }}>
                            {a.facility_id?.startsWith("rc_") ? "View on ReserveCalifornia →" : "View campsite on Recreation.gov →"}
                          </a>
                        )}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <button onClick={() => deleteAlert(a.id)}
                          style={{ background: "#fee", color: "#c00", border: "1px solid #fcc", borderRadius: 4, padding: "6px 12px", cursor: "pointer", fontSize: 13 }}>
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>
                )})}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ALERT CONFIRMATION MODAL */}
      {alertModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={(e) => e.target === e.currentTarget && setAlertModal(null)}>
          <div style={{ background: "white", borderRadius: 12, padding: 28, maxWidth: 480, width: "90%", boxShadow: "0 8px 30px rgba(0,0,0,0.25)" }}>
            <h3 style={{ margin: "0 0 16px", color: "#2c5f2d" }}>🔔 Set Availability Alert</h3>

            {alertModal.facilityId ? (
              <div style={{ padding: 12, background: "#f0f7f0", borderRadius: 6, marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{alertModal.facilityName}</div>
                {alertModal.reservationUrl && (
                  <a href={alertModal.reservationUrl} target="_blank" rel="noreferrer"
                    style={{ fontSize: 13, color: "#2c5f2d" }}>
                    View campsite on Recreation.gov ↗
                  </a>
                )}
              </div>
            ) : (
              <div style={{ padding: 12, background: "#f0f7f0", borderRadius: 6, marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>📍 Area Alert: {location}</div>
                <div style={{ fontSize: 13, color: "#666" }}>We'll monitor all campgrounds within {hours}h drive</div>
              </div>
            )}

            <div style={{ fontSize: 14, color: "#555", marginBottom: 16 }}>
              <div>📅 <strong>{checkin}</strong> → <strong>{checkout}</strong></div>
              <div style={{ marginTop: 4 }}>🚗 Max drive: <strong>{hours} hours</strong></div>
            </div>

            {!alertEmail || emailError ? (
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: "block", marginBottom: 4, fontWeight: 600, fontSize: 13 }}>Your email:</label>
                <input type="email" value={alertEmail} onChange={(e) => handleEmailChange(e.target.value)}
                  placeholder="you@email.com" autoFocus
                  style={{ width: "100%", padding: 10, fontSize: 14, borderRadius: 6, border: `1px solid ${emailError ? "#e53935" : "#ddd"}`, boxSizing: "border-box" }} />
                {emailError && <div style={{ color: "#e53935", fontSize: 12, marginTop: 4 }}>{emailError}</div>}
              </div>
            ) : null}

            <p style={{ fontSize: 13, color: "#888", margin: "0 0 16px" }}>
              We'll check every 30 minutes and email <strong>{alertEmail || "you"}</strong> when a site becomes available.
            </p>

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button onClick={() => setAlertModal(null)}
                style={{ padding: "8px 18px", background: "white", border: "1px solid #ccc", borderRadius: 6, fontSize: 14, cursor: "pointer" }}>
                Cancel
              </button>
              <button onClick={createAlert} disabled={alertCreating || !alertEmail || !!emailError}
                style={{ padding: "8px 18px", background: (alertCreating || !alertEmail || !!emailError) ? "#ccc" : "#2c5f2d", color: "white", border: "none", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: (alertCreating || !alertEmail || !!emailError) ? "not-allowed" : "pointer" }}>
                {alertCreating ? "Creating..." : "🔔 Create Alert"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

/* --- Styles --- */
const labelStyle = { display: "block", marginBottom: 4, fontWeight: 600, fontSize: 14 };
const inputStyle = { width: "100%", padding: 10, fontSize: 14, borderRadius: 6, border: "1px solid #ddd", boxSizing: "border-box" };
const cardStyle = { border: "1px solid #e0e0e0", padding: 16, borderRadius: 8, background: "white", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const thStyle = { textAlign: "left", padding: "6px 10px", fontSize: 12, color: "#777", fontWeight: 600, borderBottom: "2px solid #e0e0e0" };
const tdStyle = { padding: "6px 10px", verticalAlign: "middle" };
const dropdownStyle = { position: "absolute", top: "100%", left: 0, right: 0, background: "white", border: "1px solid #ccc", borderRadius: "0 0 6px 6px", boxShadow: "0 4px 12px rgba(0,0,0,0.12)", zIndex: 1000, maxHeight: 220, overflowY: "auto" };
const dropdownItemStyle = { padding: "10px 14px", cursor: "pointer", borderBottom: "1px solid #f0f0f0", fontSize: 14 };
const linkBtn = { fontSize: 13, color: "#2c5f2d", textDecoration: "none", fontWeight: 600 };
const alertBtnStyle = { fontSize: 12, background: "#fff8e1", color: "#f57f17", border: "1px solid #ffe082", borderRadius: 4, padding: "4px 10px", cursor: "pointer", fontWeight: 600, whiteSpace: "nowrap" };
const alertBtnPrimaryStyle = { fontSize: 13, background: "#2c5f2d", color: "white", border: "none", borderRadius: 6, padding: "8px 14px", cursor: "pointer", fontWeight: 700, whiteSpace: "nowrap", boxShadow: "0 2px 6px rgba(44,95,45,0.3)" };
const secondaryBtn = { padding: "10px 16px", background: "white", color: "#2c5f2d", border: "2px solid #2c5f2d", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" };
function tabStyle(active) { return { background: active ? "white" : "transparent", color: active ? "#2c5f2d" : "white", border: "none", borderRadius: 4, padding: "6px 16px", cursor: "pointer", fontWeight: 600, fontSize: 14 }; }
function primaryBtn(disabled) { return { flex: 1, padding: "10px 20px", background: disabled ? "#ccc" : "#2c5f2d", color: "white", border: "none", borderRadius: 6, fontSize: 15, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }; }
function availBadge(avail) { return { padding: "4px 12px", borderRadius: 12, fontSize: 12, fontWeight: 600, background: avail ? "#d4edda" : "#f8d7da", color: avail ? "#155724" : "#721c24" }; }
function providerBadge(provider) {
  const c = { "Recreation.gov": { bg: "#e8f5e9", color: "#2e7d32" }, "National Park Service": { bg: "#fff3e0", color: "#e65100" }, "ReserveCalifornia": { bg: "#e3f2fd", color: "#1565c0" } }[provider] || { bg: "#f5f5f5", color: "#666" };
  return { fontSize: 11, background: c.bg, color: c.color, padding: "2px 10px", borderRadius: 10, fontWeight: 600, display: "inline-block", marginBottom: 6 };
}
