window.tailwind = window.tailwind || {};
window.tailwind.config = {
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f5f3ff",
          100: "#ede9fe",
          200: "#ddd6fe",
          300: "#c4b5fd",
          400: "#9d92fb",
          500: "#786ef9",
          600: "#6257df",
          700: "#5146c8",
          800: "#4338a8",
          900: "#383184",
          950: "#221e4f"
        },
        accent: "#ff9a0d",
        "support-sky": "#c6e6ff",
        "support-blush": "#ffdada",
        surface: "#ffffff",
        "surface-muted": "#f8fafc",
        border: "#e2e8f0",
        "text-primary": "#0f172a",
        "text-muted": "#64748b"
      },
      boxShadow: {
        panel: "0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.04)",
        floating: "0 16px 40px rgba(15, 23, 42, 0.12)"
      },
      borderRadius: {
        panel: "0.75rem"
      }
    }
  }
};
