// Chat screen wiring: view changes, theme state, prompt clicks, and requests.
import { streamChat } from "./api.js?v=4";
import {
  appendMessage,
  updateBotMessage,
  updateBotStatus,
  autoResizeTextarea,
  scrollToBottom,
  setLoadingState,
  switchView,
  showToast
} from "./ui.js?v=6";

const chatContainer = document.getElementById("chat-container");
const scrollContainer = document.querySelector(".content-shell");
const inputForm = document.getElementById("input-form");
const inputBox = document.getElementById("input-box");
const sendBtn = document.getElementById("send-btn");
const navBtns = document.querySelectorAll(".nav-btn");
const promptBtns = document.querySelectorAll("[data-prompt]");
const modeTabs = document.querySelectorAll(".mode-tab");
const welcomePanel = document.querySelector(".welcome-panel");
const newChatBtn = document.getElementById("new-chat-btn");
const recentsList = document.getElementById("recents-list");
const recentsEmpty = document.getElementById("recents-empty");
const clearRecentsBtn = document.getElementById("clear-recents-btn");
const brandHome = document.getElementById("brand-home");
const themeToggles = document.querySelectorAll(".theme-switch");
const themeIcons = document.querySelectorAll(".theme-icon");
const typingHeadline = document.querySelector(".typing-headline");
const typingText = typingHeadline?.querySelector(".typing-text");
const sideRail = document.querySelector(".side-rail");
const scrollTopBtn = document.getElementById("scroll-top-btn");
const mobileMenuOpen = document.getElementById("mobile-menu-open");
const mobileMenuClose = document.getElementById("mobile-menu-close");
const mobileMenuBackdrop = document.getElementById("mobile-menu-backdrop");

const state = {
  history: [],
  messages: [],
  conversations: [],
  activeConversationId: null,
  abortController: null,
  hasStartedChat: false,
  isStreaming: false,
  isSubmitting: false,
  allowSubmitOnce: false,
  activeBotMessage: null,
  promptHistory: [],
  historyIndex: -1,
  isUserScrolledUp: false
};

function createConversationId() {
  return `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function summarizeChatTitle(query) {
  const cleaned = query
    .replace(/\s+/g, " ")
    .replace(/[?!]+$/g, "")
    .trim();

  const lower = cleaned.toLowerCase();
  const knownTitles = [
    ["transfer admission guarantee", "TAG transfer guarantee"],
    ["financial aid", "Financial aid help"],
    ["fafsa", "FAFSA and fee waivers"],
    ["counselor", "Counseling appointment"],
    ["add, drop", "Add / drop deadlines"],
    ["refund deadlines", "Refund deadlines"],
    ["prerequisites", "Course prerequisites"],
    ["pass/no pass", "Pass / No Pass policy"],
    ["international", "International student help"],
    ["promise", "College Promise"],
    ["tutoring", "Tutoring support"]
  ];
  const match = knownTitles.find(([keyword]) => lower.includes(keyword));
  if (match) return match[1];

  const compact = cleaned
    .replace(/^what (are|is|does)\s+/i, "")
    .replace(/^how (do|does|can)\s+/i, "")
    .replace(/^where (can|do)\s+/i, "")
    .replace(/\bat de anza\b/ig, "")
    .trim();

  if (!compact) return "New De Anza chat";
  return compact.length > 38 ? `${compact.slice(0, 35).trim()}...` : compact;
}

const STORAGE_CONVERSATIONS_KEY = "da_saved_conversations";
const STORAGE_ACTIVE_CHAT_KEY = "da_active_chat_id";

function saveConversationsToStorage() {
  try {
    const validChats = state.conversations
      .filter((c) => Array.isArray(c.messages) && c.messages.length > 0)
      .slice(0, 30);
    localStorage.setItem(STORAGE_CONVERSATIONS_KEY, JSON.stringify(validChats));
    if (state.activeConversationId) {
      localStorage.setItem(STORAGE_ACTIVE_CHAT_KEY, state.activeConversationId);
    } else {
      localStorage.setItem(STORAGE_ACTIVE_CHAT_KEY, "");
    }
  } catch (e) {
    console.warn("Failed to save conversations to localStorage:", e);
  }
}

function loadConversationsFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_CONVERSATIONS_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return false;

    state.conversations = parsed;
    const savedActiveId = localStorage.getItem(STORAGE_ACTIVE_CHAT_KEY);

    if (savedActiveId === "") {
      renderRecents();
      return true;
    }

    const targetChat = (savedActiveId && state.conversations.find((c) => c.id === savedActiveId))
      || state.conversations[0];

    if (targetChat && targetChat.messages && targetChat.messages.length > 0) {
      renderConversation(targetChat);
      return true;
    } else {
      renderRecents();
      return true;
    }
  } catch (err) {
    console.warn("Failed to load conversations from localStorage:", err);
    return false;
  }
}

function getActiveConversation() {
  return state.conversations.find((chat) => chat.id === state.activeConversationId) || null;
}

function syncActiveConversation() {
  const activeChat = getActiveConversation();
  if (!activeChat) return;
  activeChat.history = [...state.history];
  activeChat.messages = [...state.messages];
  activeChat.updatedAt = Date.now();
  saveConversationsToStorage();
}

function ensureActiveConversation(query) {
  if (state.activeConversationId) return getActiveConversation();

  const chat = {
    id: createConversationId(),
    title: summarizeChatTitle(query),
    history: [],
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now()
  };

  state.conversations.unshift(chat);
  state.activeConversationId = chat.id;
  renderRecents();
  return chat;
}

function renderRecents() {
  if (!recentsList || !recentsEmpty) return;
  recentsList.innerHTML = "";
  recentsEmpty.hidden = state.conversations.length > 0;

  state.conversations.forEach((chat) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rail-chip recent-chat-chip";
    btn.dataset.chatId = chat.id;
    btn.textContent = chat.title;
    if (chat.id === state.activeConversationId) btn.classList.add("active");
    btn.addEventListener("click", () => openRecentChat(chat.id));
    recentsList.appendChild(btn);
  });
}

function clearVisibleMessages() {
  chatContainer.querySelectorAll(".message-row").forEach((row) => row.remove());
}

function resetToEmptyChat() {
  clearVisibleMessages();
  state.history = [];
  state.messages = [];
  state.promptHistory = [];
  state.historyIndex = -1;
  state.activeConversationId = null;
  state.hasStartedChat = false;
  welcomePanel?.classList.remove("welcome-panel-hidden");
  renderRecents();
  saveConversationsToStorage();
  if (scrollContainer) scrollContainer.scrollTop = 0;
}

function renderConversation(chat) {
  clearVisibleMessages();
  state.history = [...chat.history];
  state.messages = [...chat.messages];
  state.promptHistory = (chat.messages || [])
    .filter((m) => m.role === "user")
    .map((m) => m.content);
  state.historyIndex = -1;
  state.activeConversationId = chat.id;
  state.hasStartedChat = state.messages.length > 0;

  if (state.hasStartedChat) {
    welcomePanel?.classList.add("welcome-panel-hidden");
  } else {
    welcomePanel?.classList.remove("welcome-panel-hidden");
  }

  state.messages.forEach((message) => {
    appendMessage(chatContainer, message.role, message.content);
  });

  renderRecents();
  switchView("chat-view");
  closeMobileMenu();
  scrollToBottom(scrollContainer);
  inputBox?.focus();
}

function openRecentChat(chatId) {
  if (isRequestActive()) {
    if (chatId === state.activeConversationId) {
      switchView("chat-view");
      closeMobileMenu();
      scrollToBottom(scrollContainer);
      inputBox?.focus();
      return;
    }
    showToast("Please wait for the current response to finish, or stop it before switching chats.");
    return;
  }
  syncActiveConversation();
  const chat = state.conversations.find((item) => item.id === chatId);
  if (chat) {
    renderConversation(chat);
    saveConversationsToStorage();
  }
}

function startNewChat() {
  if (isRequestActive()) {
    showToast("Please wait for the current response to finish, or stop it before starting a new chat.");
    inputBox?.focus();
    return;
  }

  syncActiveConversation();
  switchView("chat-view");
  closeMobileMenu();

  const currentIsEmpty = !state.activeConversationId && state.messages.length === 0 && !state.hasStartedChat;
  if (!currentIsEmpty) resetToEmptyChat();

  inputBox?.focus();
}

function getInitialTheme() {
  const savedTheme = localStorage.getItem("anza-theme");
  if (savedTheme === "dark" || savedTheme === "light") return savedTheme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  const isDark = theme === "dark";
  themeToggles.forEach((toggle) => {
    toggle.setAttribute("aria-checked", String(isDark));
    toggle.setAttribute("aria-label", isDark ? "Switch to day mode" : "Switch to night mode");
  });
  themeIcons.forEach((icon) => {
    icon.textContent = isDark ? "☾" : "☀";
  });
}

applyTheme(getInitialTheme());
renderRecents();

function startTypingHeadline() {
  if (!typingHeadline || !typingText) return;

  const text = typingHeadline.dataset.text || "Start with a question.";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion) {
    typingText.textContent = text;
    return;
  }

  let index = 0;
  let isDeleting = false;

  function tick() {
    typingText.textContent = text.slice(0, index);

    if (!isDeleting && index < text.length) {
      index += 1;
      window.setTimeout(tick, 72);
      return;
    }

    if (!isDeleting) {
      isDeleting = true;
      window.setTimeout(tick, 4000);
      return;
    }

    if (index > 0) {
      index -= 1;
      window.setTimeout(tick, 42);
      return;
    }

    isDeleting = false;
    window.setTimeout(tick, 520);
  }

  tick();
}

startTypingHeadline();

themeToggles.forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const nextTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("anza-theme", nextTheme);
    applyTheme(nextTheme);
  });
});

function initMobileNavScroll() {
  return;
}

initMobileNavScroll();

function isMobileMenuLayout() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function openMobileMenu() {
  if (!isMobileMenuLayout()) return;
  document.body.classList.add("mobile-menu-open");
}

function closeMobileMenu() {
  document.body.classList.remove("mobile-menu-open");
}

mobileMenuOpen?.addEventListener("click", openMobileMenu);
mobileMenuClose?.addEventListener("click", closeMobileMenu);
mobileMenuBackdrop?.addEventListener("click", closeMobileMenu);
window.addEventListener("resize", () => {
  if (!isMobileMenuLayout()) closeMobileMenu();
});

function initScrollTracking() {
  if (!scrollContainer) return;

  // Instantly catch user scrolling up with mouse wheel or trackpad
  scrollContainer.addEventListener("wheel", (e) => {
    if (e.deltaY < -4) {
      state.isUserScrolledUp = true;
    }
  }, { passive: true });

  // Instantly catch user scrolling up with touch gestures
  let touchStartY = 0;
  scrollContainer.addEventListener("touchstart", (e) => {
    if (e.touches && e.touches[0]) {
      touchStartY = e.touches[0].clientY;
    }
  }, { passive: true });

  scrollContainer.addEventListener("touchmove", (e) => {
    if (e.touches && e.touches[0]) {
      const touchY = e.touches[0].clientY;
      if (touchY - touchStartY > 10) {
        state.isUserScrolledUp = true;
      }
    }
  }, { passive: true });

  // Monitor scroll position to resume auto-scroll when user reaches bottom
  scrollContainer.addEventListener("scroll", () => {
    if (scrollTopBtn) {
      scrollTopBtn.classList.toggle("visible", scrollContainer.scrollTop > 360);
    }
    const distanceFromBottom =
      scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight;

    // Resume auto-scroll when user scrolls back within 20px of the bottom
    if (distanceFromBottom <= 20) {
      state.isUserScrolledUp = false;
    }
  }, { passive: true });

  scrollTopBtn?.addEventListener("click", () => {
    scrollContainer.scrollTo({ top: 0, behavior: "smooth" });
  });
}

initScrollTracking();

function getModePlaceholder(tab) {
  const isMobile = window.matchMedia("(max-width: 640px)").matches;
  return isMobile && tab.dataset.mobilePlaceholder
    ? tab.dataset.mobilePlaceholder
    : tab.dataset.placeholder;
}

function syncActiveModePlaceholder() {
  if (!inputBox) return;
  const activeTab = document.querySelector(".mode-tab.active");
  const placeholder = activeTab ? getModePlaceholder(activeTab) : "";
  if (placeholder) inputBox.placeholder = placeholder;
}

syncActiveModePlaceholder();
window.addEventListener("resize", syncActiveModePlaceholder);

function submitPrompt(text) {
  if (!inputBox || !inputForm) return;
  if (isRequestActive()) {
    inputBox.focus();
    return;
  }
  state.isSubmitting = true;
  state.allowSubmitOnce = true;
  setRequestLock(true);
  switchView("chat-view");
  inputBox.value = text;
  autoResizeTextarea(inputBox);
  inputForm.requestSubmit();
}

function markChatStarted() {
  if (state.hasStartedChat) return;
  state.hasStartedChat = true;
  if (welcomePanel) {
    welcomePanel.classList.add("welcome-panel-hidden");
  }
}

function setPromptButtonsDisabled(isDisabled) {
  promptBtns.forEach((btn) => {
    btn.disabled = isDisabled;
    btn.setAttribute("aria-disabled", String(isDisabled));
  });
}

function isRequestActive() {
  return state.isStreaming || state.isSubmitting;
}

function setRequestLock(isLocked) {
  state.isSubmitting = isLocked && !state.isStreaming;
  document.body.classList.toggle("chat-request-active", isLocked);
  setPromptButtonsDisabled(isLocked);
  setLoadingState(sendBtn, isLocked);
  if (inputBox) {
    inputBox.readOnly = isLocked;
    inputBox.setAttribute("aria-disabled", String(isLocked));
  }
}

function clearRequestLock() {
  state.isStreaming = false;
  state.isSubmitting = false;
  state.allowSubmitOnce = false;
  state.activeBotMessage = null;
  state.abortController = null;
  setRequestLock(false);
}

function stopActiveResponse() {
  if (!isRequestActive()) return;

  const activeMessage = state.activeBotMessage;
  state.abortController?.abort();

  clearRequestLock();

  if (activeMessage && (activeMessage.textContent.includes("Searching official De Anza sources") || activeMessage.textContent.includes("Preparing the answer") || !activeMessage.textContent.trim())) {
    updateBotMessage(activeMessage, "Stopped.");
  }

  inputBox?.focus();
}

sendBtn?.addEventListener("click", (e) => {
  if (isRequestActive()) {
    e.preventDefault();
    e.stopPropagation();
    stopActiveResponse();
  }
});

navBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn === newChatBtn) {
      startNewChat();
      return;
    }

    const targetView = btn.dataset.view;
    switchView(targetView);
    closeMobileMenu();
    if (targetView === "chat-view" && inputBox) {
      inputBox.focus();
    }
  });
});

function clearAllRecents() {
  if (isRequestActive()) {
    showToast("Please wait for the current response to finish, or stop it before clearing chats.");
    return;
  }

  if (!state.conversations.length && !state.hasStartedChat && !state.activeConversationId) {
    showToast("No recent chats to clear.");
    return;
  }

  state.conversations = [];
  resetToEmptyChat();
  try {
    localStorage.removeItem(STORAGE_CONVERSATIONS_KEY);
    localStorage.removeItem(STORAGE_ACTIVE_CHAT_KEY);
  } catch (e) {
    console.warn("Failed to clear localStorage:", e);
  }
  showToast("Recent chats cleared.");
}

clearRecentsBtn?.addEventListener("click", clearAllRecents);

brandHome?.addEventListener("click", () => {
  if (isMobileMenuLayout()) {
    switchView("chat-view");
    closeMobileMenu();
    return;
  }
  switchView("chat-view");
  if (scrollContainer) scrollContainer.scrollTop = 0;
  if (inputBox) inputBox.focus();
});

promptBtns.forEach((btn) => {
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    if (isRequestActive()) return;
    const prompt = btn.dataset.prompt;
    closeMobileMenu();
    if (prompt) submitPrompt(prompt);
  });
});

modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    modeTabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const placeholder = getModePlaceholder(tab);
    if (inputBox && placeholder) {
      inputBox.placeholder = placeholder;
      inputBox.focus();
    }
  });
});

window.askQuestion = submitPrompt;

inputBox.addEventListener("input", () => {
  autoResizeTextarea(inputBox);
});

inputBox.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (isRequestActive()) return;
    inputForm.requestSubmit();
    return;
  }

  if (e.key === "ArrowUp") {
    if (!state.promptHistory.length) return;
    if (state.historyIndex === -1 && inputBox.value !== "") return;

    e.preventDefault();
    if (state.historyIndex === -1) {
      state.historyIndex = state.promptHistory.length - 1;
    } else if (state.historyIndex > 0) {
      state.historyIndex -= 1;
    }

    inputBox.value = state.promptHistory[state.historyIndex] || "";
    autoResizeTextarea(inputBox);
    return;
  }

  if (e.key === "ArrowDown") {
    if (state.historyIndex === -1) return;

    e.preventDefault();
    if (state.historyIndex < state.promptHistory.length - 1) {
      state.historyIndex += 1;
      inputBox.value = state.promptHistory[state.historyIndex];
    } else {
      state.historyIndex = -1;
      inputBox.value = "";
    }

    autoResizeTextarea(inputBox);
    return;
  }
});

inputForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  if (state.isStreaming || (state.isSubmitting && !state.allowSubmitOnce)) {
    inputBox.focus();
    return;
  }

  const query = inputBox.value.trim();
  if (!query) {
    clearRequestLock();
    return;
  }

  const activeChat = ensureActiveConversation(query);

  state.allowSubmitOnce = false;
  state.isSubmitting = true;
  setRequestLock(true);

  if (state.abortController) {
    state.abortController.abort();
  }
  state.abortController = new AbortController();

  markChatStarted();
  appendMessage(chatContainer, "user", query);
  state.messages.push({ role: "user", content: query });
  if (!state.promptHistory.length || state.promptHistory[state.promptHistory.length - 1] !== query) {
    state.promptHistory.push(query);
  }
  state.historyIndex = -1;
  syncActiveConversation();
  state.isUserScrolledUp = false;
  scrollToBottom(scrollContainer);
  inputBox.value = "";
  inputBox.style.height = "44px";
  state.isStreaming = true;
  state.isSubmitting = false;
  setRequestLock(true);

  const botMsgElement = appendMessage(chatContainer, "bot", "__THINKING__");
  state.activeBotMessage = botMsgElement;
  scrollToBottom(scrollContainer);
  let fullResponse = "";

  await streamChat({
    message: query,
    history: state.history,
    signal: state.abortController.signal,
    onStatus: (status) => {
      if (status === "searching") {
        updateBotStatus(botMsgElement, "Searching official De Anza sources...");
      } else if (status === "preparing") {
        updateBotStatus(botMsgElement, "Preparing the answer...");
      }
    },
    onToken: (token) => {
      fullResponse += token;
      updateBotMessage(botMsgElement, fullResponse);
      if (!state.isUserScrolledUp) {
        scrollToBottom(scrollContainer);
      }
    },
    onDone: () => {
      if (fullResponse.trim()) {
        state.history.push({ role: "user", content: query });
        state.history.push({ role: "assistant", content: fullResponse });
        state.messages.push({ role: "bot", content: fullResponse });
        if (activeChat) {
          activeChat.history = [...state.history];
          activeChat.messages = [...state.messages];
          activeChat.updatedAt = Date.now();
        }
        saveConversationsToStorage();
      }
      renderRecents();
      clearRequestLock();
      inputBox.focus();
    },
    onError: (err) => {
      const errorText = err?.message || "Sorry, I hit a connection issue while reaching the assistant. Please try again.";
      updateBotMessage(botMsgElement, errorText);
      state.messages.push({ role: "bot", content: errorText });
      syncActiveConversation();
      saveConversationsToStorage();
      console.error("Chat Error:", err);
      clearRequestLock();
      inputBox.focus();
    }
  });
});

function loadConversationFromURL() {
  const params = new URLSearchParams(window.location.search);
  const rawHistory = params.get("history");
  if (!rawHistory) return false;

  try {
    const parsed = JSON.parse(decodeURIComponent(rawHistory));
    if (!Array.isArray(parsed) || parsed.length === 0) return false;

    const firstUserMsg = parsed.find((m) => m.role === "user");
    const chatTitle = firstUserMsg ? summarizeChatTitle(firstUserMsg.content) : "Imported chat";

    const chat = {
      id: createConversationId(),
      title: chatTitle,
      history: parsed.map((m) => ({
        role: m.role === "bot" ? "assistant" : m.role,
        content: m.content,
      })),
      messages: parsed.map((m) => ({
        role: m.role === "assistant" ? "bot" : m.role,
        content: m.content,
      })),
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    state.conversations.unshift(chat);
    renderConversation(chat);
    saveConversationsToStorage();
    return true;
  } catch (err) {
    console.error("Failed to load conversation from URL:", err);
    return false;
  }
}

const loadedFromUrl = loadConversationFromURL();
if (!loadedFromUrl) {
  loadConversationsFromStorage();
}

