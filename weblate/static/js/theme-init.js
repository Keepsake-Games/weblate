// Copyright © Michal Čihař <michal@weblate.org>
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Resolve the "auto" color theme before the first paint. Bootstrap styles its
// components by the data-bs-theme attribute, and loader-bootstrap.js only sets it
// once the deferred scripts run, so dark mode pages briefly rendered light. This
// loads synchronously from <head>, before any content exists.
(() => {
  const root = document.documentElement;
  const theme = root.getAttribute("data-bs-theme");
  if (!theme || theme === "auto") {
    root.setAttribute(
      "data-bs-theme",
      window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light",
    );
  }
})();
