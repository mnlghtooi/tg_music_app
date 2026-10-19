/* Mini-player quick scrubbing: hold track area, drag horizontally, release to seek.
 * The existing full-player seek and playback buttons are intentionally untouched. */
(function (root) {
  'use strict';
  const HOLD_MS = 350;
  const MOVE_SLOP = 12;

  function durationOf(audio) {
    const duration = audio.duration;
    return typeof duration === 'number' && Number.isFinite(duration) && duration > 0 ? duration : 0;
  }
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
  }
  // Moving the finger across the full track region maps to a relative change
  // in song position. Starting a hold does not jump to the finger's x coordinate.
  function scrubTarget(start, deltaX, width, duration) {
    if (!duration || !Number.isFinite(width) || width <= 0) return 0;
    return clamp(start + deltaX * duration / width, 0, duration);
  }

  function mount({ trigger, panel, slider, elapsed, total, closeButton,
                   audio, openPlayer, formatTime, onError, holdMs = HOLD_MS }) {
    let gesture = null;
    let holdTimer = null;
    let swallowClick = false;
    let swallowTimer = null;
    let editingSlider = false;

    function time(n) { return formatTime(Math.max(0, n || 0)); }
    function sync() {
      const duration = durationOf(audio);
      slider.disabled = duration === 0;
      slider.max = String(duration || 1);
      total.textContent = time(duration);
      if (!gesture?.active && !editingSlider) {
        slider.value = String(clamp(audio.currentTime || 0, 0, duration || 1));
        elapsed.textContent = time(audio.currentTime);
      }
    }
    function close() {
      panel.hidden = true;
      editingSlider = false;
      sync();
    }
    function commit(position) {
      const duration = durationOf(audio);
      if (!duration) {
        onError('Перемотка станет доступна после загрузки трека.');
        return;
      }
      try {
        audio.currentTime = clamp(position, 0, duration);
      } catch (error) {
        onError('Не удалось перемотать эту запись.');
      }
      sync();
    }
    function clearHold() {
      if (holdTimer !== null) clearTimeout(holdTimer);
      holdTimer = null;
    }
    function releaseGesture() {
      clearHold();
      gesture = null;
      trigger.classList.remove('mini-scrubbing');
    }
    function onPointerDown(e) {
      if (e.isPrimary === false || (e.pointerType === 'mouse' && e.button !== 0)) return;
      if (gesture) releaseGesture();
      gesture = {
        id: e.pointerId, x: e.clientX, y: e.clientY,
        start: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
        preview: audio.currentTime || 0, active: false
      };
      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (!gesture) return;
        if (!durationOf(audio)) {
          onError('Дождись загрузки трека — затем можно перемотать.');
          releaseGesture();
          return;
        }
        gesture.active = true;
        trigger.classList.add('mini-scrubbing');
        panel.hidden = false;
        sync();
        try { trigger.setPointerCapture?.(gesture.id); } catch (_) { /* WebView */ }
      }, holdMs);
    }
    function onPointerMove(e) {
      if (!gesture || e.pointerId !== gesture.id) return;
      if (!gesture.active) {
        // Let vertical scrolling or ordinary swipes cancel the hold.
        if (Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > MOVE_SLOP) {
          releaseGesture();
        }
        return;
      }
      const duration = durationOf(audio);
      gesture.preview = scrubTarget(
        gesture.start, e.clientX - gesture.x,
        trigger.getBoundingClientRect().width || 1, duration
      );
      slider.value = String(gesture.preview);
      elapsed.textContent = time(gesture.preview);
    }
    function onPointerUp(e) {
      if (!gesture || e.pointerId !== gesture.id) return;
      const isScrubbing = gesture.active;
      const target = gesture.preview;
      releaseGesture();
      if (isScrubbing) {
        // A long press on the button can otherwise generate a click and open
        // the full player on the same release.
        swallowClick = true;
        clearTimeout(swallowTimer);
        swallowTimer = setTimeout(() => { swallowClick = false; }, 450);
        commit(target);
      }
    }
    function onPointerCancel(e) {
      if (gesture && e.pointerId === gesture.id) releaseGesture();
    }
    trigger.addEventListener('pointerdown', onPointerDown);
    trigger.addEventListener('pointermove', onPointerMove);
    trigger.addEventListener('pointerup', onPointerUp);
    trigger.addEventListener('pointercancel', onPointerCancel);
    trigger.addEventListener('contextmenu', e => e.preventDefault());
    trigger.addEventListener('click', e => {
      if (swallowClick) {
        swallowClick = false;
        clearTimeout(swallowTimer);
        e.preventDefault();
        e.stopPropagation();
      } else {
        openPlayer();
      }
    });
    slider.addEventListener('input', () => {
      editingSlider = true;
      elapsed.textContent = time(Number(slider.value));
    });
    slider.addEventListener('change', () => {
      editingSlider = false;
      commit(Number(slider.value));
    });
    slider.addEventListener('blur', () => { editingSlider = false; sync(); });
    closeButton.addEventListener('click', close);
    // Explicit track switches clear the quick-scrub UI.
    audio.addEventListener('emptied', close);
    audio.addEventListener('durationchange', sync);
    audio.addEventListener('loadedmetadata', sync);
    audio.addEventListener('timeupdate', sync);
    sync();
    return { close, sync };
  }

  const api = { mount, durationOf, scrubTarget };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MiniSeek = api;
})(typeof window !== 'undefined' ? window : globalThis);
