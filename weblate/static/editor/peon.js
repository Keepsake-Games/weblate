// Copyright © Michal Čihař <michal@weblate.org>
//
// SPDX-License-Identifier: GPL-3.0-or-later

(() => {
  const COMBO_WINDOW_MS = 15 * 60 * 1000;
  const SWIPE_THRESHOLD = 110;
  const CONFETTI_HUES = [180, 200, 160, 48, 330, 280, 20];
  const MILESTONES = new Map([
    [5, () => gettext("Nice!")],
    [10, () => gettext("On a roll!")],
    [25, () => gettext("Unstoppable!")],
    [50, () => gettext("Legendary!")],
    [100, () => gettext("Translation machine!")],
  ]);

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Storage is a convenience only
    }
  }

  /* Synthesized sound, so no audio assets are needed */
  const Sound = {
    ctx: null,
    muted: storageGet("peon-muted") === "1",

    context() {
      if (this.muted) {
        return null;
      }
      if (this.ctx === null) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) {
          return null;
        }
        this.ctx = new Ctx();
      }
      if (this.ctx.state === "suspended") {
        this.ctx.resume();
      }
      return this.ctx;
    },

    tone(freq, duration, { type = "square", gain = 0.05, slide = null, delay = 0 } = {}) {
      const ctx = this.context();
      if (ctx === null) {
        return;
      }
      const start = ctx.currentTime + delay;
      const osc = ctx.createOscillator();
      const amp = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, start);
      if (slide !== null) {
        osc.frequency.exponentialRampToValueAtTime(slide, start + duration);
      }
      amp.gain.setValueAtTime(gain, start);
      amp.gain.exponentialRampToValueAtTime(0.0001, start + duration);
      osc.connect(amp).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + duration + 0.02);
    },

    key() {
      this.tone(900 + Math.random() * 500, 0.03, { gain: 0.015 });
    },

    success(combo) {
      const base = 440 * 2 ** (Math.min(combo, 24) / 12);
      this.tone(base, 0.08, { gain: 0.04 });
      this.tone(base * 1.5, 0.12, { gain: 0.04, delay: 0.06 });
    },

    reject() {
      this.tone(260, 0.18, { type: "sawtooth", gain: 0.035, slide: 110 });
    },

    skip() {
      this.tone(620, 0.12, { type: "sine", gain: 0.04, slide: 280 });
    },

    error() {
      this.tone(140, 0.25, { type: "sawtooth", gain: 0.05 });
      this.tone(147, 0.25, { type: "sawtooth", gain: 0.05 });
    },

    milestone() {
      [523, 659, 784, 1047].forEach((freq, idx) => {
        this.tone(freq, 0.16, { gain: 0.045, delay: idx * 0.07 });
      });
    },
  };

  class Peon {
    constructor(root) {
      this.root = root;
      this.deck = root.querySelector(".peon-deck");
      this.fx = root.querySelector(".peon-fx");
      this.status = root.querySelector(".peon-status");
      this.csrf = root.querySelector("[name=csrfmiddlewaretoken]").value;
      this.reduceMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;

      this.loadUrl = root.dataset.loadUrl;
      this.exitUrl = root.dataset.exitUrl;
      this.pageSize = Number.parseInt(root.dataset.pageSize, 10);
      this.offset = Number.parseInt(root.dataset.offset, 10);
      this.total = Number.parseInt(root.dataset.total, 10);
      this.lastSection = "lastSection" in root.dataset;
      this.position = this.offset - 1;

      this.current = null;
      this.loading = null;
      this.busy = false;
      this.done = 0;
      this.skipped = 0;
      this.combo = 0;
      this.bestCombo = 0;
      this.comboDeadline = 0;
      this.handled = new Set();

      this.review = root.dataset.reviewUrl
        ? {
            url: root.dataset.reviewUrl,
            every: Number.parseInt(root.dataset.reviewEvery, 10),
            size: Number.parseInt(root.dataset.reviewSize, 10),
            offset: 1 - this.pageSize,
            pool: [],
            loading: null,
            exhausted: false,
            since: 0,
          }
        : null;

      document.body.classList.add("peon-active");
      this.bind();
      this.updateMute();
      this.updateHud();
      this.show(this.nextCard());
    }

    /* Navigation between cards */

    nextCard() {
      for (const card of this.deck.querySelectorAll(".peon-card:not(.peon-gone)")) {
        if (!this.handled.has(card.dataset.unit)) {
          return card;
        }
        card.classList.add("peon-gone");
        this.position += 1;
      }
      return null;
    }

    show(card) {
      if (!card) {
        this.prefetch();
        if (this.loading !== null) {
          this.root.querySelector(".peon-loading").hidden = false;
          this.loading.then(() => {
            this.root.querySelector(".peon-loading").hidden = true;
            this.show(this.nextCard());
          });
          return;
        }
        this.position = this.total;
        this.updateHud();
        this.finish();
        return;
      }
      this.current = card;
      card.hidden = false;
      card.classList.add("peon-enter");
      card.addEventListener(
        "animationend",
        () => card.classList.remove("peon-enter"),
        { once: true },
      );
      this.root.dataset.mode = card.dataset.kind;
      this.status.textContent = card.querySelector(".peon-kind").textContent.trim();
      this.focusCurrent();
      this.prefetch();
    }

    focusCurrent() {
      if (this.current === null) {
        this.root.querySelector(".peon-finished:not([hidden])")?.focus();
        return;
      }
      const input = this.current.querySelector(".peon-input");
      if (input) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      } else {
        this.current.focus();
      }
    }

    advance() {
      const card = this.current;
      this.current = null;
      card.classList.add("peon-gone");
      if (!card.classList.contains("peon-interlude")) {
        this.position += 1;
      }
      this.updateHud();
      const next = this.nextCard();
      if (
        card.classList.contains("peon-interlude") &&
        next !== null &&
        !next.classList.contains("peon-interlude")
      ) {
        this.banner(gettext("Back to translating!"), "peon-banner-round");
      }
      this.show(next);
    }

    prefetch() {
      const remaining = this.deck.querySelectorAll(".peon-card:not(.peon-gone)").length;
      if (remaining > 3 || this.lastSection || this.loading !== null) {
        return;
      }
      this.offset += this.pageSize;
      this.loading = fetch(`${this.loadUrl}&offset=${this.offset}`, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.text();
        })
        .then((html) => {
          this.deck.insertAdjacentHTML("beforeend", html);
          if (this.deck.querySelector(".peon-end")) {
            this.lastSection = true;
          }
          if (window.initHighlight) {
            window.initHighlight(this.deck);
          }
        })
        .catch((error) => {
          this.lastSection = true;
          addAlert(error.message);
        })
        .finally(() => {
          this.loading = null;
        });
    }

    fetchReviews() {
      const review = this.review;
      if (review === null || review.loading !== null || review.exhausted) {
        return;
      }
      review.offset += this.pageSize;
      review.loading = fetch(`${review.url}&offset=${review.offset}`, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.text();
        })
        .then((html) => {
          const holder = document.createElement("template");
          holder.innerHTML = html;
          review.pool.push(...holder.content.querySelectorAll(".peon-card"));
          if (holder.content.querySelector(".peon-end")) {
            review.exhausted = true;
          }
        })
        .catch(() => {
          review.exhausted = true;
        })
        .finally(() => {
          review.loading = null;
        });
    }

    maybeReviewRound(card) {
      const review = this.review;
      if (review === null || card.dataset.kind !== "translate") {
        return;
      }
      review.since += 1;
      if (review.pool.length < review.size && review.since >= review.every - 3) {
        this.fetchReviews();
      }
      if (review.since < review.every) {
        return;
      }
      const picked = [];
      while (picked.length < review.size && review.pool.length) {
        const candidate = review.pool.shift();
        const unit = candidate.dataset.unit;
        if (unit !== card.dataset.unit && !this.handled.has(unit)) {
          picked.push(candidate);
        }
      }
      if (!picked.length) {
        return;
      }
      review.since = 0;
      const next = [...this.deck.querySelectorAll(".peon-card:not(.peon-gone)")].find(
        (item) => item !== card && !this.handled.has(item.dataset.unit),
      );
      for (const item of picked) {
        item.classList.add("peon-interlude");
        if (next === undefined) {
          this.deck.append(item);
        } else {
          next.before(item);
        }
      }
      Sound.milestone();
      this.banner(gettext("Review round!"), "peon-banner-round");
    }

    finish() {
      this.current = null;
      this.root.dataset.mode = "finished";
      const finished = this.root.querySelector(".peon-finished");
      finished.querySelector(".peon-finished-stats").textContent = interpolate(
        gettext("%(done)s done, %(skipped)s skipped, best combo %(best)s."),
        { done: this.done, skipped: this.skipped, best: this.bestCombo },
        true,
      );
      finished.hidden = false;
      finished.focus();
      if (this.done > 0) {
        Sound.milestone();
        this.burst(finished, 60);
      }
    }

    dropOut() {
      window.location.href = this.current?.dataset.fullUrl || this.exitUrl;
    }

    /* Actions */

    submit() {
      const card = this.current;
      if (card === null || this.busy) {
        return;
      }
      const inputs = [...card.querySelectorAll(".peon-input")];
      if (inputs.some((input) => input.value.trim() === "")) {
        this.miss(card, gettext("Type the translation first."));
        return;
      }
      const form = card.querySelector(".peon-form");
      const data = new FormData(form);
      data.set("action", card.dataset.submit);
      card.classList.add("peon-saving");
      this.busy = true;
      this.post(card, data)
        .then((result) => {
          if (result.done) {
            for (const input of inputs) {
              input.defaultValue = input.value;
            }
            this.hit(card, "up");
          } else {
            this.miss(card);
          }
        })
        .finally(() => {
          this.busy = false;
          card.classList.remove("peon-saving");
        });
    }

    swipe(side) {
      const card = this.current;
      if (card === null || card.dataset.kind !== "review") {
        return;
      }
      const action = card.dataset[side];
      if (!action) {
        this.miss(card, gettext("You can not do that with this suggestion."));
        return;
      }
      const data = new FormData(card.querySelector(".peon-form"));
      data.set("action", action);
      data.set("suggestion", card.dataset.suggestion);
      /* Reviews are optimistic so swiping stays instant */
      this.post(card, data).then((result) => {
        if (!result.done) {
          Sound.error();
        }
      });
      this.hit(card, side, action === "delete" || action === "downvote");
    }

    skip() {
      const card = this.current;
      if (card === null || this.busy) {
        return;
      }
      this.skipped += 1;
      Sound.skip();
      this.fly(card, "down");
      this.advance();
    }

    post(card, data) {
      data.set("csrfmiddlewaretoken", this.csrf);
      return fetch(card.dataset.saveUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": this.csrf,
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
        body: new URLSearchParams(data),
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.json();
        })
        .then((result) => {
          const sum = card.querySelector("[name=translationsum]");
          if (sum && result.translationsum) {
            sum.value = result.translationsum;
          }
          for (const message of result.messages) {
            addAlert(message.text, message.kind, 6000);
          }
          return result;
        })
        .catch((error) => {
          addAlert(error.message);
          return { done: false, messages: [] };
        });
    }

    /* Juice */

    hit(card, direction, negative = false) {
      this.done += 1;
      this.combo += 1;
      this.bestCombo = Math.max(this.bestCombo, this.combo);
      this.comboDeadline = performance.now() + COMBO_WINDOW_MS;

      if (negative) {
        Sound.reject();
      } else {
        Sound.success(this.combo);
      }
      this.burst(card, Math.min(12 + this.combo * 2, 48), negative);
      this.floatText(card, `+${this.combo}`, negative ? "peon-float-negative" : "");
      this.shake(Math.min(2 + this.combo / 4, 10));
      this.pop(this.root.querySelector(".peon-combo"));

      this.maybeReviewRound(card);

      const milestone = MILESTONES.get(this.combo);
      if (milestone) {
        Sound.milestone();
        this.banner(milestone());
      }

      this.fly(card, direction);
      this.handled.add(card.dataset.unit);
      this.advance();
    }

    miss(card, text = null) {
      Sound.error();
      card.classList.remove("peon-miss");
      void card.offsetWidth;
      card.classList.add("peon-miss");
      if (text) {
        addAlert(text, "warning");
      }
      this.focusCurrent();
    }

    breakCombo() {
      this.combo = 0;
      this.comboDeadline = 0;
      this.updateHud();
    }

    fly(card, direction) {
      const ghost = card.cloneNode(true);
      for (const node of [ghost, ...ghost.querySelectorAll("[id]")]) {
        node.removeAttribute("id");
      }
      ghost.setAttribute("aria-hidden", "true");
      ghost.classList.add("peon-ghost", "peon-gone", `peon-fly-${direction}`);
      ghost.style.transform = card.style.transform;
      card.after(ghost);
      card.hidden = true;
      card.style.transform = "";
      const cleanup = () => ghost.remove();
      ghost.addEventListener("animationend", cleanup, { once: true });
      setTimeout(cleanup, 1000);
    }

    shake(strength) {
      if (this.reduceMotion) {
        return;
      }
      const stage = this.root.querySelector(".peon-stage");
      stage.style.setProperty("--peon-shake", `${strength}px`);
      stage.classList.remove("peon-shake");
      void stage.offsetWidth;
      stage.classList.add("peon-shake");
    }

    pop(element) {
      element.classList.remove("peon-pop");
      void element.offsetWidth;
      element.classList.add("peon-pop");
    }

    center(element) {
      const rect = element.getBoundingClientRect();
      const base = this.fx.getBoundingClientRect();
      return {
        x: rect.left - base.left + rect.width / 2,
        y: rect.top - base.top + rect.height / 2,
      };
    }

    burst(element, count, negative = false) {
      if (this.reduceMotion) {
        return;
      }
      const { x, y } = this.center(element);
      for (let i = 0; i < count; i++) {
        const spark = document.createElement("span");
        spark.className = negative ? "peon-spark peon-spark-negative" : "peon-spark";
        const angle = Math.random() * Math.PI * 2;
        const distance = 80 + Math.random() * 220;
        spark.style.left = `${x}px`;
        spark.style.top = `${y}px`;
        spark.style.setProperty("--dx", `${Math.cos(angle) * distance}px`);
        spark.style.setProperty("--dy", `${Math.sin(angle) * distance}px`);
        spark.style.setProperty("--hue", `${170 + Math.random() * 40}`);
        spark.style.animationDuration = `${450 + Math.random() * 400}ms`;
        spark.addEventListener("animationend", () => spark.remove(), { once: true });
        this.fx.append(spark);
      }
    }

    floatText(element, text, extraClass = "") {
      const { x, y } = this.center(element);
      const node = document.createElement("span");
      node.className = `peon-float ${extraClass}`;
      node.textContent = text;
      node.style.left = `${x}px`;
      node.style.top = `${y}px`;
      node.addEventListener("animationend", () => node.remove(), { once: true });
      this.fx.append(node);
    }

    banner(text, extraClass = "") {
      const node = document.createElement("div");
      node.className = `peon-banner ${extraClass}`;
      node.textContent = text;
      node.addEventListener("animationend", () => node.remove(), { once: true });
      this.fx.append(node);
      this.status.textContent = text;
    }

    typed(input, text) {
      Sound.key();
      if (this.reduceMotion) {
        return;
      }
      const card = this.current;
      card.style.setProperty("--kx", `${(Math.random() - 0.5) * 3}px`);
      card.style.setProperty("--ky", `${(Math.random() - 0.5) * 3}px`);
      this.pop(card.querySelector(".peon-form"));

      const layer = input.parentElement.querySelector(".peon-input-fx");
      if (!layer) {
        return;
      }
      const { x, y } = this.caretPoint(input);
      const letter = [...(text || "")].pop();
      if (letter && letter.trim()) {
        for (let i = 0; i < 2; i++) {
          const ghost = document.createElement("span");
          ghost.className = "peon-letter";
          ghost.textContent = letter;
          this.launch(ghost, layer, x, y, 60, 130, 120);
        }
      }
      for (let i = 0; i < 7; i++) {
        const confetti = document.createElement("span");
        confetti.className = "peon-confetti";
        this.launch(confetti, layer, x, y, 40, 120, 720);
      }
    }

    launch(node, layer, x, y, min, max, spin) {
      const angle = Math.random() * Math.PI * 2;
      const distance = min + Math.random() * (max - min);
      const hue = CONFETTI_HUES[Math.floor(Math.random() * CONFETTI_HUES.length)];
      node.style.setProperty("--hue", String(hue));
      node.style.left = `${x}px`;
      node.style.top = `${y}px`;
      node.style.setProperty("--dx", `${Math.cos(angle) * distance}px`);
      node.style.setProperty("--dy", `${Math.sin(angle) * distance}px`);
      node.style.setProperty("--rot", `${(Math.random() - 0.5) * spin}deg`);
      node.addEventListener("animationend", () => node.remove(), { once: true });
      layer.append(node);
    }

    caretPoint(input) {
      /* Mirror the textarea in a hidden div to find where the caret is */
      const style = window.getComputedStyle(input);
      const mirror = document.createElement("div");
      for (const name of [
        "boxSizing",
        "width",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "borderTopWidth",
        "borderRightWidth",
        "borderBottomWidth",
        "borderLeftWidth",
        "borderStyle",
        "fontFamily",
        "fontSize",
        "fontWeight",
        "fontStyle",
        "letterSpacing",
        "lineHeight",
        "textTransform",
        "wordSpacing",
        "tabSize",
        "direction",
        "textAlign",
      ]) {
        mirror.style[name] = style[name];
      }
      Object.assign(mirror.style, {
        position: "absolute",
        visibility: "hidden",
        top: "0",
        left: "-9999px",
        whiteSpace: "pre-wrap",
        overflowWrap: "break-word",
      });
      mirror.textContent = input.value.slice(0, input.selectionEnd);
      const marker = document.createElement("span");
      marker.textContent = "\u200b";
      mirror.append(marker);
      document.body.append(mirror);
      const x =
        input.offsetLeft +
        Number.parseFloat(style.borderLeftWidth) +
        marker.offsetLeft -
        input.scrollLeft;
      const y =
        input.offsetTop +
        Number.parseFloat(style.borderTopWidth) +
        marker.offsetTop +
        marker.offsetHeight / 2 -
        input.scrollTop;
      mirror.remove();
      return { x, y };
    }

    insertTerm(term, input = null) {
      const card = this.current;
      const target =
        input ||
        card?.querySelector(".peon-input:focus") ||
        card?.querySelector(".peon-input");
      if (!target || term.disabled) {
        return;
      }
      target.focus();
      target.setRangeText(
        term.dataset.value,
        target.selectionStart,
        target.selectionEnd,
        "end",
      );
      target.dispatchEvent(new Event("input", { bubbles: true }));
      this.pop(term);
    }

    copySource(input) {
      const card = this.current;
      const inputs = [...card.querySelectorAll(".peon-input")];
      const plurals = [...card.querySelectorAll(".peon-source-plural")].map(
        (template) => template.content.textContent,
      );
      const idx = inputs.indexOf(input);
      const text = plurals[Math.min(idx > 0 ? 1 : 0, plurals.length - 1)] || "";
      input.setRangeText(text, input.selectionStart, input.selectionEnd, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    updateHud() {
      const handled = Math.min(this.position, this.total);
      const percent = this.total ? (100 * handled) / this.total : 100;
      const progress = this.root.querySelector(".peon-progress");
      progress.setAttribute("aria-valuenow", String(handled));
      this.root.querySelector(".peon-progress-bar").style.width = `${percent}%`;
      this.root.querySelector(".peon-done-count").textContent = String(handled);
      this.root.querySelector(".peon-combo-count").textContent = String(this.combo);
      this.root.querySelector(".peon-combo").dataset.heat = String(
        Math.min(Math.floor(this.combo / 5), 4),
      );
    }

    tick() {
      if (this.comboDeadline && performance.now() >= this.comboDeadline) {
        this.breakCombo();
      }
    }

    updateMute() {
      const button = this.root.querySelector(".peon-mute");
      button.setAttribute("aria-pressed", String(!Sound.muted));
      this.root.classList.toggle("peon-muted", Sound.muted);
    }

    toggleMute() {
      Sound.muted = !Sound.muted;
      storageSet("peon-muted", Sound.muted ? "1" : "0");
      this.updateMute();
    }

    /* Input */

    bind() {
      document.addEventListener("keydown", (event) => this.onKey(event), true);

      this.root.addEventListener("input", (event) => {
        if (event.target.classList.contains("peon-input")) {
          this.typed(event.target, event.data);
        }
      });

      this.root.addEventListener("click", (event) => {
        const term = event.target.closest(".peon-term");
        if (term) {
          this.insertTerm(term);
          return;
        }
        const button = event.target.closest("[data-peon]");
        if (!button) {
          return;
        }
        const action = button.dataset.peon;
        if (action === "submit") {
          this.submit();
        } else if (action === "skip") {
          this.skip();
        } else {
          this.swipe(action);
        }
      });

      this.root.querySelector(".peon-mute").addEventListener("click", () => {
        this.toggleMute();
        this.focusCurrent();
      });

      document.addEventListener("focusin", (event) => {
        const toasts = document.getElementById("popup-toasts");
        if (!this.root.contains(event.target) && !toasts?.contains(event.target)) {
          this.focusCurrent();
        }
      });

      window.addEventListener("beforeunload", (event) => {
        const dirty = [...this.root.querySelectorAll(".peon-card:not(.peon-gone) .peon-input")].some(
          (input) => input.value !== input.defaultValue,
        );
        if (dirty) {
          event.preventDefault();
          event.returnValue = gettext(
            "There are some unsaved changes, are you sure you want to leave?",
          );
        }
      });

      this.bindSwipe();
      window.setInterval(() => this.tick(), 5000);
    }

    onKey(event) {
      if (event.isComposing) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        this.dropOut();
        return;
      }
      if (event.altKey && event.code === "KeyM") {
        event.preventDefault();
        this.toggleMute();
        return;
      }
      const card = this.current;
      if (card === null) {
        return;
      }
      const mod = event.ctrlKey || event.metaKey;

      if (card.dataset.kind === "translate") {
        const input = event.target.closest?.(".peon-input");
        const onCard = input || event.target === card;
        if (event.key === "Enter" && !event.shiftKey && !event.altKey && onCard) {
          event.preventDefault();
          this.submit();
        } else if (mod && event.key === "ArrowRight") {
          event.preventDefault();
          this.skip();
        } else if (mod && event.key === "1" && input) {
          event.preventDefault();
          this.copySource(input);
        } else if (mod && /^[2-9]$/.test(event.key) && input) {
          const term = card.querySelectorAll(".peon-term")[Number(event.key) - 2];
          if (term) {
            event.preventDefault();
            this.insertTerm(term, input);
          }
        }
        return;
      }

      if (mod || event.altKey || event.target.closest?.("input, textarea, select")) {
        return;
      }
      const keys = {
        ArrowRight: "right",
        l: "right",
        d: "right",
        ArrowLeft: "left",
        h: "left",
        a: "left",
        ArrowDown: "skip",
        j: "skip",
        s: "skip",
      };
      const action = keys[event.key];
      if (!action) {
        return;
      }
      event.preventDefault();
      if (event.repeat) {
        return;
      }
      if (action === "skip") {
        this.skip();
      } else {
        this.swipe(action);
      }
    }

    bindSwipe() {
      let drag = null;

      this.deck.addEventListener("pointerdown", (event) => {
        const card = event.target.closest(".peon-card");
        if (
          card !== this.current ||
          card.dataset.kind !== "review" ||
          event.target.closest("button, a") ||
          event.button !== 0
        ) {
          return;
        }
        drag = { card, x: event.clientX, dx: 0, id: event.pointerId };
        card.setPointerCapture(event.pointerId);
        card.classList.add("peon-dragging");
      });

      this.deck.addEventListener("pointermove", (event) => {
        if (drag === null || event.pointerId !== drag.id) {
          return;
        }
        drag.dx = event.clientX - drag.x;
        drag.card.style.transform = `translateX(${drag.dx}px) rotate(${drag.dx / 18}deg)`;
        drag.card.dataset.lean =
          drag.dx > SWIPE_THRESHOLD / 2 ? "right" : drag.dx < -SWIPE_THRESHOLD / 2 ? "left" : "";
      });

      const release = (event) => {
        if (drag === null || event.pointerId !== drag.id) {
          return;
        }
        const { card, dx } = drag;
        drag = null;
        card.classList.remove("peon-dragging");
        delete card.dataset.lean;
        if (Math.abs(dx) >= SWIPE_THRESHOLD) {
          this.swipe(dx > 0 ? "right" : "left");
        } else {
          card.style.transform = "";
        }
      };
      this.deck.addEventListener("pointerup", release);
      this.deck.addEventListener("pointercancel", release);
    }
  }

  function bindStart(form) {
    const review = form.querySelector("#peon-job-review");
    const count = form.querySelector(".peon-job-count-review");
    form.addEventListener("change", (event) => {
      const reviewable = event.target.dataset?.reviewable;
      if (review === null || reviewable === undefined) {
        return;
      }
      review.disabled = reviewable === "0";
      if (count !== null) {
        count.textContent = review.disabled
          ? count.dataset.none
          : interpolate(
              ngettext(
                "%s suggestion waiting",
                "%s suggestions waiting",
                Number(reviewable),
              ),
              [reviewable],
            );
      }
    });
    document.addEventListener("keydown", (event) => {
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.target.closest("input[type=text], textarea, select")
      ) {
        return;
      }
      const jobs = form.querySelectorAll(".peon-job");
      const job = jobs[Number.parseInt(event.key, 10) - 1];
      if (job) {
        event.preventDefault();
        job.click();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("peon");
    if (root !== null) {
      new Peon(root);
    }
    const start = document.getElementById("peon-start");
    if (start !== null) {
      bindStart(start);
    }
  });
})();
