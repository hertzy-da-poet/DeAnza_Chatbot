// Small DOM helpers used by the chat screen.
import { formatMarkdown } from "./config.js";

// Add a message row and return the bubble so streaming updates can reuse it.
export function appendMessage(container, role, initialText = "") {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const msg = document.createElement("div");
  msg.className = `msg ${role}`;

  if (role === "user") {
    msg.textContent = initialText;
  } else {
    if (initialText === "__THINKING__") {
      msg.innerHTML = `
        <div class="gemini-sparkle-loader">
          <span class="sparkle-symbol" aria-hidden="true">✦</span>
          <span class="sparkle-text">Searching official De Anza sources...</span>
        </div>
      `;
    } else {
      msg.innerHTML = formatMarkdown(initialText);
    }
  }

  row.appendChild(msg);
  container.appendChild(row);
  scrollToBottom(container);
  return msg;
}

// Re-render the current bot bubble as new tokens come in.
export function updateBotMessage(msgElement, rawText) {
  if (!msgElement) return;
  msgElement.innerHTML = formatMarkdown(rawText);
}

// Update loader text during pre-response phases (searching vs preparing)
export function updateBotStatus(msgElement, statusText) {
  if (!msgElement) return;
  const textEl = msgElement.querySelector(".sparkle-text");
  if (textEl) {
    textEl.textContent = statusText;
  }
}

// Keep citation links together so answers are easier to scan.
export function groupSourceLinks(msgElement) {
  if (!msgElement) return;
  const existingGroup = msgElement.querySelector(".source-group");
  if (existingGroup) existingGroup.remove();

  const links = Array.from(msgElement.querySelectorAll("a"));
  if (!links.length) return;

  const group = document.createElement("div");
  group.className = "source-group";

  const label = document.createElement("div");
  label.className = "source-label";
  label.textContent = "Check these sources";
  group.appendChild(label);

  const list = document.createElement("div");
  list.className = "source-list";
  group.appendChild(list);

  links.forEach((link) => {
    const parent = link.parentElement;
    list.appendChild(link);
    if (parent && parent !== msgElement && !parent.textContent.trim() && parent.children.length === 0) {
      parent.remove();
    }
  });

  msgElement.appendChild(group);
}

// Keep the newest message visible during streaming.
export function scrollToBottom(container) {
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

// Let the composer grow, but cap it so it does not cover the chat.
export function autoResizeTextarea(textarea, maxHeightRatio = 0.25) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const maxHeight = window.innerHeight * maxHeightRatio;
  const targetHeight = Math.min(Math.max(textarea.scrollHeight, 44), maxHeight);
  textarea.style.height = `${targetHeight}px`;
}

// Toggle send button between Send (>) and Stop (■) states.
export function setLoadingState(sendBtn, isLoading) {
  if (sendBtn) {
    sendBtn.disabled = false;
    sendBtn.setAttribute("aria-busy", String(isLoading));
    sendBtn.setAttribute("aria-label", isLoading ? "Stop response" : "Send message");
    sendBtn.classList.toggle("is-stopping", isLoading);
    sendBtn.innerHTML = isLoading
      ? '<span class="stop-icon" aria-hidden="true">■</span>'
      : '<span class="send-icon" aria-hidden="true">&gt;</span>';
  }
}

// Swap between Chat and About without reloading the page.
export function switchView(targetViewId) {
  const allViews = document.querySelectorAll(".page-view");
  const allNavBtns = document.querySelectorAll(".nav-btn");

  allViews.forEach((view) => {
    if (view.id === targetViewId) {
      view.classList.remove("hidden");
    } else {
      view.classList.add("hidden");
    }
  });

  allNavBtns.forEach((btn) => {
    if (btn.dataset.view === targetViewId) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
}

// Quick toast notification popup for user alerts.
export function showToast(message, duration = 850) {
  let toast = document.getElementById("app-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "app-toast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("visible");

  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => {
    toast.classList.remove("visible");
  }, duration);
}
