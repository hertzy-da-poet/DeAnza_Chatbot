// Shared frontend config and markdown helpers.

const RENDER_API_ORIGIN = "https://deanza-chatbot.onrender.com";
const VERCEL_HOST_SUFFIX = ".vercel.app";

function getApiOrigin() {
  const { hostname, port } = window.location;

  if (hostname.endsWith(VERCEL_HOST_SUFFIX)) return RENDER_API_ORIGIN;
  if ((hostname === "localhost" || hostname === "127.0.0.1") && port !== "8000") {
    return RENDER_API_ORIGIN;
  }

  return "";
}

const API_ORIGIN = getApiOrigin();

export const API_ENDPOINTS = {
  CHAT: `${API_ORIGIN}/api/chat`,
  FEEDBACK: `${API_ORIGIN}/api/feedback`,
};

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHref(href = "") {
  const value = String(href).trim();
  if (/^(https?:|mailto:|\/)/i.test(value)) return escapeHtml(value);
  return "#";
}

// Keep links safe when markdown comes back from the chatbot.
if (window.marked) {
  window.marked.use({
    renderer: {
      link(token, titleArg, textArg) {
        const href = typeof token === "object" && token !== null ? token.href : token;
        const title = typeof token === "object" && token !== null ? token.title : titleArg;
        const text = typeof token === "object" && token !== null ? token.text : textArg;
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
        return `<a href="${safeHref(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
      }
    }
  });
}

// Streaming chunks can glue headings/lists together, so clean that up before
// passing the text to marked.
export function formatMarkdown(rawText) {
  if (!rawText) return "";

  const clean = escapeHtml(rawText)
    // Put headers back on their own block.
    .replace(/(?:[^\n]|^)\s*(#{1,6}\s+)/g, "\n\n$1")
    // Split attached header descriptions.
    .replace(/(#{1,4}\s+[A-Za-z0-9\s/\\-]+?):\s+([A-Za-z])/g, "$1\n$2")
    // Separate bullet lists that arrive glued to the previous sentence on the same line.
    .replace(/:(?![ \t]*\n)[ \t]*[\*\-][ \t]+/g, ":\n\n* ")
    // Same idea, but for bullets after sentence punctuation on the same line.
    .replace(/([.!?])(?![ \t]*\n)[ \t]+[\*\-][ \t]+/g, "$1\n\n* ")
    // Avoid huge gaps after normalization.
    .replace(/\n{3,}/g, "\n\n");

  return window.marked ? window.marked.parse(clean.trim()) : clean;
}
