/**
 * EchoMind Autonomous News Publisher — Frontend Client Layer
 * Centralized configuration communicating with the deployed backend.
 * Resilient against Render Free cold starts and database re-initialization.
 */

// ============================================================================
// 1. CENTRAL API BASE URL CONFIGURATION
// ============================================================================
const DEFAULT_API_BASE_URL = "https://echomind-ltwo.onrender.com";

// Resolve API base URL: query param ?api=... > window config > deployed production default
const urlParams = new URLSearchParams(window.location.search);
const API_BASE_URL = (urlParams.get("api") || window.__API_BASE_URL__ || DEFAULT_API_BASE_URL).replace(/\/$/, "");

console.log("[EchoMind Client] Configured API Base URL:", API_BASE_URL);

// ============================================================================
// 2. STATE MANAGEMENT & STORAGE
// ============================================================================
const STATE = {
  agentId: localStorage.getItem("echomind_agent_id") || "",
  personaName: localStorage.getItem("echomind_persona_name") || "",
  personaDomain: localStorage.getItem("echomind_persona_domain") || "",
  statusData: null,
  posts: [],
  backendHealthy: false
};

// ============================================================================
// 3. API SERVICE LAYER
// ============================================================================
async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
  
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), options.timeout || 35000); // 35s for Render cold start

  // Display cold start indicator if request takes > 4s
  const slowTimer = setTimeout(() => {
    showToast("Connecting to Render backend (resuming from cold start)...", 4000);
  }, 4000);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });
    clearTimeout(slowTimer);
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorBody.slice(0, 100)}`);
    }

    return await response.json();
  } catch (err) {
    clearTimeout(slowTimer);
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new Error("Backend request timed out. The Render service may be resuming.");
    }
    throw err;
  }
}

const apiClient = {
  async checkHealth() {
    return await apiRequest("/healthz", { timeout: 10000 });
  },

  async initAgent(name, domain) {
    return await apiRequest("/api/agent/init", {
      method: "POST",
      body: JSON.stringify({
        persona: {
          name: name.trim(),
          domain: domain.trim()
        }
      })
    });
  },

  async getStatus(agentId) {
    return await apiRequest(`/api/agent/status?agentId=${encodeURIComponent(agentId)}`);
  },

  async getFeed(agentId) {
    return await apiRequest(`/api/agent/feed?agentId=${encodeURIComponent(agentId)}`);
  }
};

// ============================================================================
// 4. UI RENDERERS & EVENT HANDLERS
// ============================================================================
function showToast(message, duration = 3000) {
  let toast = document.getElementById("app-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "app-toast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), duration);
}

function updateBackendStatus(healthy, message) {
  STATE.backendHealthy = healthy;
  const badge = document.getElementById("backend-status-badge");
  const text = document.getElementById("backend-status-text");
  if (!badge || !text) return;

  if (healthy) {
    badge.className = "status-badge healthy";
    text.textContent = message || "Backend Live";
  } else {
    badge.className = "status-badge connecting";
    text.textContent = message || "Connecting...";
  }
}

function renderStatus() {
  const container = document.getElementById("window-status-container");
  if (!container || !STATE.statusData) return;

  const data = STATE.statusData;
  const windowId = data.window ? data.window.windowId : "win-none";
  const windowStatus = data.window ? data.window.status : "OPEN";
  const candidateCount = data.window ? data.window.candidateCount : 0;
  const endsAt = data.window && data.window.endsAt ? data.window.endsAt : null;
  const leader = data.currentLeader;
  const lastPublishedAt = data.lastPublishedAt;

  let leaderHtml = `
    <div class="leader-box empty">
      <div class="leader-label">Current Window Leader (Score >= 75)</div>
      <div class="leader-desc">No qualified candidate yet. Discovery loop executes every 5 minutes.</div>
    </div>
  `;

  if (leader) {
    leaderHtml = `
      <div class="leader-box active">
        <div class="leader-header">
          <span class="leader-badge">Top Candidate</span>
          <span class="leader-score">${Number(leader.score).toFixed(1)} / 100</span>
        </div>
        <div class="leader-title">${escapeHtml(leader.title)}</div>
        <div class="leader-desc">${escapeHtml(leader.summary || "")}</div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="window-grid">
      <div class="stat-card">
        <span class="stat-label">Publishing Window</span>
        <span class="stat-value">${escapeHtml(windowId)}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Window Status</span>
        <span class="stat-value status-${windowStatus.toLowerCase()}">${escapeHtml(windowStatus)}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Evaluated Stories</span>
        <span class="stat-value">${candidateCount}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Next Evaluation</span>
        <span class="stat-value">${formatDate(endsAt)}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Last Published</span>
        <span class="stat-value">${formatDate(lastPublishedAt)}</span>
      </div>
    </div>
    ${leaderHtml}
  `;
}

function renderFeed() {
  const container = document.getElementById("feed-container");
  const countEl = document.getElementById("feed-count");
  if (!container) return;

  if (!STATE.posts || STATE.posts.length === 0) {
    if (countEl) countEl.textContent = "(0)";
    container.innerHTML = `
      <div class="empty-state">
        No stories published to feed yet. At window close (120 min), the highest-scoring verified leader meeting the minimum threshold will be published.
      </div>
    `;
    return;
  }

  if (countEl) countEl.textContent = `(${STATE.posts.length})`;

  container.innerHTML = STATE.posts.map(post => `
    <div class="feed-item">
      <div class="feed-meta">
        <span class="feed-id">${escapeHtml(post.id)}</span>
        <span>${formatDate(post.createdAt)}</span>
      </div>
      <div class="feed-text">${escapeHtml(post.text)}</div>
      <div class="feed-rationale">
        <strong>Editorial Rationale:</strong> ${escapeHtml(post.rationale)}
      </div>
      ${post.sources && post.sources.length ? `
        <div class="feed-sources">
          ${post.sources.map(s => `<a href="${escapeHtml(s)}" target="_blank" rel="noopener" class="source-link">${escapeHtml(formatSourceUrl(s))}</a>`).join("")}
        </div>
      ` : ""}
    </div>
  `).join("");
}

function formatSourceUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace("www.", "");
  } catch {
    return url;
  }
}

function formatDate(isoString) {
  if (!isoString) return "None";
  try {
    const d = new Date(isoString);
    const hours = String(d.getUTCHours()).padStart(2, "0");
    const mins = String(d.getUTCMinutes()).padStart(2, "0");
    const secs = String(d.getUTCSeconds()).padStart(2, "0");
    return `${hours}:${mins}:${secs} UTC`;
  } catch {
    return isoString;
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ============================================================================
// 5. POLLING & INITIALIZATION WORKFLOWS (HIGH-003 RESILIENCE)
// ============================================================================
async function refreshData() {
  if (!STATE.agentId) return;

  try {
    let statusData = null;
    try {
      statusData = await apiClient.getStatus(STATE.agentId);
    } catch (err) {
      // HIGH-003: If backend restarted and database was reset (HTTP 404), seamlessly recover
      if (err.message && err.message.includes("404")) {
        console.warn("[EchoMind Client] Agent ID not found on backend (server restart). Auto-recovering session...");
        if (STATE.personaName && STATE.personaDomain) {
          try {
            const initRes = await apiClient.initAgent(STATE.personaName, STATE.personaDomain);
            STATE.agentId = initRes.agentId;
            localStorage.setItem("echomind_agent_id", initRes.agentId);
            updateActiveSessionUI();
            statusData = await apiClient.getStatus(STATE.agentId);
          } catch (initErr) {
            console.error("[EchoMind Client] Auto-recovery failed:", initErr);
            resetSession();
            return;
          }
        } else {
          resetSession();
          return;
        }
      } else {
        throw err;
      }
    }

    const feedData = await apiClient.getFeed(STATE.agentId).catch(() => ({ posts: [] }));

    if (statusData) {
      STATE.statusData = statusData;
      renderStatus();
    }

    if (feedData && Array.isArray(feedData.posts)) {
      STATE.posts = feedData.posts;
      renderFeed();
    }
  } catch (e) {
    console.error("[EchoMind Client] Refresh error:", e);
  }
}

async function handleInitForm(e) {
  e.preventDefault();
  const nameInput = document.getElementById("persona-name");
  const domainInput = document.getElementById("persona-domain");
  const submitBtn = document.getElementById("init-submit-btn");

  const name = nameInput.value.trim();
  const domain = domainInput.value.trim();

  if (!name || !domain) {
    showToast("Please enter both persona name and technical domain.");
    return;
  }

  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="loading-spinner"></span> Initializing...`;

  try {
    const res = await apiClient.initAgent(name, domain);
    STATE.agentId = res.agentId;
    STATE.personaName = name;
    STATE.personaDomain = domain;

    localStorage.setItem("echomind_agent_id", res.agentId);
    localStorage.setItem("echomind_persona_name", name);
    localStorage.setItem("echomind_persona_domain", domain);

    showToast(`Agent initialized successfully: ${res.agentId}`);
    updateActiveSessionUI();
    await refreshData();
  } catch (err) {
    showToast(err.message || "Failed to initialize agent.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Initialize Autonomous Persona";
  }
}

function updateActiveSessionUI() {
  const sessionInfo = document.getElementById("session-info");
  const agentIdEl = document.getElementById("active-agent-id");
  const personaEl = document.getElementById("active-persona-title");

  if (STATE.agentId) {
    if (sessionInfo) sessionInfo.style.display = "block";
    if (agentIdEl) agentIdEl.textContent = STATE.agentId;
    if (personaEl) personaEl.textContent = `${STATE.personaName || "Persona"} (${STATE.personaDomain || "General Tech"})`;
  }
}

function resetSession() {
  localStorage.removeItem("echomind_agent_id");
  localStorage.removeItem("echomind_persona_name");
  localStorage.removeItem("echomind_persona_domain");
  STATE.agentId = "";
  STATE.statusData = null;
  STATE.posts = [];
  location.reload();
}

// ============================================================================
// 6. STARTUP & LIFECYCLE
// ============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  // Set configured backend label
  const backendLabel = document.getElementById("backend-url-label");
  if (backendLabel) {
    backendLabel.textContent = API_BASE_URL.replace("https://", "");
  }

  // Check health status
  try {
    const health = await apiClient.checkHealth();
    if (health && health.status === "healthy") {
      updateBackendStatus(true, "Backend Live");
    } else {
      updateBackendStatus(false, "Connecting");
    }
  } catch {
    updateBackendStatus(false, "Offline / Cold Start");
  }

  // Bind Form Event
  const form = document.getElementById("persona-init-form");
  if (form) {
    form.addEventListener("submit", handleInitForm);
  }

  const resetBtn = document.getElementById("reset-session-btn");
  if (resetBtn) {
    resetBtn.addEventListener("click", resetSession);
  }

  // Restore session
  if (STATE.agentId) {
    updateActiveSessionUI();
    await refreshData();
  }

  // Periodic polling (15s for status, 30s for health)
  setInterval(refreshData, 15000);
  setInterval(async () => {
    try {
      const h = await apiClient.checkHealth();
      updateBackendStatus(h.status === "healthy", "Backend Live");
    } catch {
      updateBackendStatus(false, "Connecting");
    }
  }, 30000);
});
