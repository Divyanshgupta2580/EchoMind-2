/**
 * EchoMind Autonomous News Publisher — Frontend Client Layer
 * Multi-Agent Dashboard, Responsive Cards, Dark/Light Theme System,
 * Server-Side 5-Agent Limit Confirmation Modal, and Health Status Management.
 */

// ============================================================================
// 1. CENTRAL API BASE URL CONFIGURATION
// ============================================================================
const DEFAULT_API_BASE_URL = "https://echomind-ltwo.onrender.com";

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
  theme: localStorage.getItem("echomind_theme") || "dark",
  statusData: null,
  posts: [],
  agents: [],
  maxAgents: 5,
  backendHealthy: false,
  pendingInitPayload: null
};

// ============================================================================
// 3. API SERVICE LAYER
// ============================================================================
async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), options.timeout || 35000);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorBody.slice(0, 100)}`);
    }

    return await response.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new Error("Backend request timed out.");
    }
    throw err;
  }
}

const apiClient = {
  async checkHealth() {
    return await apiRequest("/healthz", { timeout: 10000 });
  },

  async getAgents(activeAgentId = "") {
    return await apiRequest(`/api/agents?activeAgentId=${encodeURIComponent(activeAgentId)}`, { timeout: 15000 });
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
// 4. THEME SYSTEM (DARK & LIGHT MODE)
// ============================================================================
function initTheme() {
  const savedTheme = localStorage.getItem("echomind_theme");
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  const initialTheme = savedTheme || (prefersLight ? "light" : "dark");
  setTheme(initialTheme);
}

function setTheme(theme) {
  STATE.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("echomind_theme", theme);

  const toggleBtn = document.getElementById("theme-toggle-btn");
  const moonIcon = document.getElementById("theme-icon-moon");
  const sunIcon = document.getElementById("theme-icon-sun");

  if (theme === "light") {
    if (moonIcon) moonIcon.style.display = "block";
    if (sunIcon) sunIcon.style.display = "none";
    if (toggleBtn) toggleBtn.setAttribute("aria-label", "Switch to dark mode");
  } else {
    if (moonIcon) moonIcon.style.display = "none";
    if (sunIcon) sunIcon.style.display = "block";
    if (toggleBtn) toggleBtn.setAttribute("aria-label", "Switch to light mode");
  }
}

function toggleTheme() {
  const newTheme = STATE.theme === "dark" ? "light" : "dark";
  setTheme(newTheme);
}

// ============================================================================
// 5. UI RENDERERS
// ============================================================================
function showToast(message, duration = 3000) {
  let toast = document.getElementById("alert-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "alert-toast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), duration);
}

function updateBackendStatus(state, message) {
  const dot = document.getElementById("backend-status-dot");
  const text = document.getElementById("backend-status-text");
  if (!dot || !text) return;

  if (state === "connected") {
    dot.className = "status-dot healthy";
    text.textContent = message || "Connected";
  } else if (state === "connecting") {
    dot.className = "status-dot";
    text.textContent = message || "Connecting...";
  } else {
    dot.className = "status-dot error";
    text.textContent = message || "Backend unavailable";
  }
}

function renderAgentsList() {
  const grid = document.getElementById("agents-grid");
  const badge = document.getElementById("agents-count-badge");
  if (!grid) return;

  const count = STATE.agents.length;
  if (badge) badge.textContent = `${count} / ${STATE.maxAgents}`;

  if (count === 0) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1; padding: 1.5rem;">
        No autonomous agents initialized yet. Create a persona below to begin.
      </div>
    `;
    return;
  }

  grid.innerHTML = STATE.agents.map(a => {
    const isSelected = (a.agentId === STATE.agentId);
    const status = a.status || {};
    const leader = status.currentLeader;
    const leaderScore = leader && leader.score ? Number(leader.score).toFixed(1) : "None";
    const candidateCount = status.candidateCount || 0;
    const windowStatus = status.windowStatus || "OPEN";

    return `
      <div class="agent-card ${isSelected ? 'active-selected' : ''}" data-agent-id="${escapeHtml(a.agentId)}">
        <div>
          <div class="agent-card-header">
            <div class="agent-card-title">${escapeHtml(a.name)}</div>
            <span class="badge ${isSelected ? 'active' : 'running'}">${isSelected ? 'Active' : 'Running'}</span>
          </div>
          <div class="agent-card-domain">${escapeHtml(a.domain)}</div>
          <div class="agent-card-metrics">
            <div class="metric-item">
              <span>Leader Score:</span>
              <strong>${leaderScore !== "None" ? leaderScore + " / 100" : "None (<75)"}</strong>
            </div>
            <div class="metric-item">
              <span>Candidates:</span>
              <strong>${candidateCount} stories</strong>
            </div>
            <div class="metric-item">
              <span>Window:</span>
              <strong>${escapeHtml(windowStatus)}</strong>
            </div>
          </div>
        </div>
        <div class="agent-card-action">
          <button type="button" class="btn-view-agent" onclick="selectAgent('${escapeHtml(a.agentId)}')">
            ${isSelected ? 'Inspecting' : 'View Agent'}
          </button>
        </div>
      </div>
    `;
  }).join("");
}

function selectAgent(agentId) {
  if (!agentId || agentId === STATE.agentId) return;

  const target = STATE.agents.find(a => a.agentId === agentId);
  if (!target) return;

  STATE.agentId = target.agentId;
  STATE.personaName = target.name;
  STATE.personaDomain = target.domain;

  localStorage.setItem("echomind_agent_id", target.agentId);
  localStorage.setItem("echomind_persona_name", target.name);
  localStorage.setItem("echomind_persona_domain", target.domain);

  updateActiveSessionUI();
  renderAgentsList();
  refreshData();
}
window.selectAgent = selectAgent;

function renderStatus() {
  const container = document.getElementById("status-container");
  if (!container || !STATE.statusData) return;

  const data = STATE.statusData;
  const windowId = data.window ? data.window.windowId : "win-none";
  const windowStatus = data.window ? data.window.status : "OPEN";
  const candidateCount = data.window ? data.window.candidateCount : 0;
  const endsAt = data.window && data.window.endsAt ? data.window.endsAt : null;
  const leader = data.currentLeader;
  const lastPublishedAt = data.lastPublishedAt;

  let leaderHtml = `
    <div class="leader-box">
      <div class="leader-header">
        <span class="leader-badge">Window Leader</span>
        <span class="leader-score" style="font-size: 0.9rem; color: var(--text-muted);">None (< 75.0)</span>
      </div>
      <div class="leader-desc">No qualified candidate yet. Discovery loop evaluates every 5 minutes.</div>
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
    <div class="card">
      <div class="card-title">
        <span>Publishing Window</span>
        <span class="badge ${windowStatus === 'OPEN' ? 'open' : 'published'}">${escapeHtml(windowStatus)}</span>
      </div>
      <div class="window-grid">
        <div class="stat-card">
          <span class="stat-label">Window ID</span>
          <span class="stat-value">${escapeHtml(windowId)}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Evaluated Stories</span>
          <span class="stat-value">${candidateCount}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Window Closes</span>
          <span class="stat-value">${formatDate(endsAt)}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Last Published</span>
          <span class="stat-value">${formatDate(lastPublishedAt)}</span>
        </div>
      </div>
      ${leaderHtml}
    </div>
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
        No stories published to feed yet. At window close (120 min), the highest-scoring verified leader meeting the minimum threshold (75.0) will be published.
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

function updateActiveSessionUI() {
  const sessionInfo = document.getElementById("session-info");
  const agentIdEl = document.getElementById("active-agent-id");
  const personaEl = document.getElementById("active-persona-title");

  if (STATE.agentId) {
    if (sessionInfo) sessionInfo.style.display = "block";
    if (agentIdEl) agentIdEl.textContent = STATE.agentId;
    if (personaEl) personaEl.textContent = `${STATE.personaName || "Persona"} (${STATE.personaDomain || "General Tech"})`;
  } else {
    if (sessionInfo) sessionInfo.style.display = "none";
  }
}

// ============================================================================
// 6. POLLING & INITIALIZATION WORKFLOWS
// ============================================================================
async function refreshData() {
  try {
    // 1. Fetch all agents
    const agentsRes = await apiClient.getAgents(STATE.agentId).catch(() => null);
    if (agentsRes && Array.isArray(agentsRes.agents)) {
      STATE.agents = agentsRes.agents;
      STATE.maxAgents = agentsRes.maxAgents || 5;
      renderAgentsList();

      // If active agent is missing or was rotated out, select the first available agent
      if (STATE.agentId && !STATE.agents.some(a => a.agentId === STATE.agentId)) {
        if (STATE.agents.length > 0) {
          const newest = STATE.agents[STATE.agents.length - 1];
          STATE.agentId = newest.agentId;
          STATE.personaName = newest.name;
          STATE.personaDomain = newest.domain;
          localStorage.setItem("echomind_agent_id", newest.agentId);
          localStorage.setItem("echomind_persona_name", newest.name);
          localStorage.setItem("echomind_persona_domain", newest.domain);
        } else {
          STATE.agentId = "";
          localStorage.removeItem("echomind_agent_id");
        }
        updateActiveSessionUI();
      }
    }

    if (!STATE.agentId && STATE.agents.length > 0) {
      const first = STATE.agents[0];
      STATE.agentId = first.agentId;
      STATE.personaName = first.name;
      STATE.personaDomain = first.domain;
      localStorage.setItem("echomind_agent_id", first.agentId);
      localStorage.setItem("echomind_persona_name", first.name);
      localStorage.setItem("echomind_persona_domain", first.domain);
      updateActiveSessionUI();
    }

    if (STATE.agentId) {
      const [statusData, feedData] = await Promise.all([
        apiClient.getStatus(STATE.agentId).catch(() => null),
        apiClient.getFeed(STATE.agentId).catch(() => ({ posts: [] }))
      ]);

      if (statusData) {
        STATE.statusData = statusData;
        renderStatus();
      }

      if (feedData && Array.isArray(feedData.posts)) {
        STATE.posts = feedData.posts;
        renderFeed();
      }
    }
  } catch (e) {
    console.error("[EchoMind Client] Refresh error:", e);
  }
}

async function handleInitForm(e) {
  e.preventDefault();
  const nameInput = document.getElementById("persona-name");
  const domainInput = document.getElementById("persona-domain");

  const name = nameInput.value.trim();
  const domain = domainInput.value.trim();

  if (!name || !domain) {
    showToast("Please enter both persona name and technical domain.");
    return;
  }

  // Check 5-agent limit confirmation modal
  if (STATE.agents.length >= 5) {
    const oldest = STATE.agents[0];
    const oldestName = oldest ? oldest.name : "the oldest agent";
    showCapacityModal(name, domain, oldestName);
    return;
  }

  await executeAgentCreation(name, domain);
}

function showCapacityModal(name, domain, oldestName) {
  STATE.pendingInitPayload = { name, domain };
  const modal = document.getElementById("delete-modal");
  const modalBody = document.getElementById("modal-body");
  if (modalBody) {
    modalBody.textContent = `You already have 5 autonomous agents. Creating ${name} will permanently remove the oldest agent, ${oldestName}.`;
  }
  if (modal) modal.classList.add("open");
}

function hideCapacityModal() {
  STATE.pendingInitPayload = null;
  const modal = document.getElementById("delete-modal");
  if (modal) modal.classList.remove("open");
}

async function executeAgentCreation(name, domain) {
  const submitBtn = document.getElementById("init-submit-btn");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="loading-spinner"></span> Initializing...`;
  }

  try {
    const res = await apiClient.initAgent(name, domain);
    STATE.agentId = res.agentId;
    STATE.personaName = name;
    STATE.personaDomain = domain;

    localStorage.setItem("echomind_agent_id", res.agentId);
    localStorage.setItem("echomind_persona_name", name);
    localStorage.setItem("echomind_persona_domain", domain);

    showToast(`Autonomous Persona '${name}' initialized: ${res.agentId}`);
    updateActiveSessionUI();
    await refreshData();
  } catch (err) {
    showToast(err.message || "Failed to initialize agent.");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Initialize Autonomous Persona";
    }
    hideCapacityModal();
  }
}

// ============================================================================
// 7. STARTUP & LIFECYCLE
// ============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  // 1. Initialize Theme
  initTheme();

  // Theme toggle button click
  const themeToggleBtn = document.getElementById("theme-toggle-btn");
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", toggleTheme);
  }

  // 2. Set configured backend label
  const backendLabel = document.getElementById("backend-url-label");
  if (backendLabel) {
    backendLabel.textContent = API_BASE_URL.replace("https://", "");
  }

  // 3. Health check status
  try {
    updateBackendStatus("connecting", "Connecting...");
    const health = await apiClient.checkHealth();
    if (health && health.status === "healthy") {
      updateBackendStatus("connected", "Connected");
    } else {
      updateBackendStatus("error", "Backend unavailable");
    }
  } catch {
    updateBackendStatus("error", "Backend unavailable");
  }

  // 4. Modal event listeners
  const cancelBtn = document.getElementById("modal-cancel-btn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", hideCapacityModal);
  }

  const confirmBtn = document.getElementById("modal-confirm-btn");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", async () => {
      if (STATE.pendingInitPayload) {
        await executeAgentCreation(STATE.pendingInitPayload.name, STATE.pendingInitPayload.domain);
      }
    });
  }

  // 5. Form listener
  const form = document.getElementById("persona-init-form");
  if (form) {
    form.addEventListener("submit", handleInitForm);
  }

  // 6. Restore session & load agents
  if (STATE.agentId) {
    updateActiveSessionUI();
  }
  await refreshData();

  // 7. Periodic polling (15s for data, 30s for healthz)
  setInterval(refreshData, 15000);
  setInterval(async () => {
    try {
      const h = await apiClient.checkHealth();
      if (h && h.status === "healthy") {
        updateBackendStatus("connected", "Connected");
      } else {
        updateBackendStatus("error", "Backend unavailable");
      }
    } catch {
      updateBackendStatus("error", "Backend unavailable");
    }
  }, 30000);
});
